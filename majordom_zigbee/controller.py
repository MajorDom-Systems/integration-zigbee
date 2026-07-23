import asyncio
import contextlib
import json
import logging
from enum import Enum
from typing import ClassVar, Literal, cast, override
from uuid import UUID

import zigpy.endpoint
import zigpy.zcl
from majordom_integration_sdk.controller import AbstractController
from majordom_integration_sdk.schemas.command import DeviceCommand
from majordom_integration_sdk.schemas.device import CredentialsType, Discovery, NonEmptyStr, ProvidedCredentials
from majordom_integration_sdk.schemas.event import DeviceParameterChange
from majordom_integration_sdk.schemas.parameter import (
    ParameterDataType,
    ParameterRole,
    ParameterUnit,
    ParameterVisibility,
    next_main_parameter_value,
)
from zigpy.config import CONF_DATABASE, CONF_DEVICE, CONF_DEVICE_PATH
from zigpy.device import Device as ZPDevice  # ZP - ZigPy
from zigpy.types import EUI64
from zigpy.zcl.clusters.general import Identify
from zigpy.zcl.foundation import Status as ZCLStatus
from zigpy.zcl.foundation import ZCLAttributeAccess, ZCLAttributeDef

from majordom_zigbee._serial import port_holder

from .exceptions import ZBConnectionError, ZBOperationError, ZBUnexpectedError
from .listener import ZBAttributeUpdatedListener
from .mapper import ZigBeeMapper
from .model import (
    Parameter,
    ZBDevice,
    ZBDeviceIntegrationData,
    ZBDeviceState,
    ZBParameter,
    ZBParameterIntegrationData,
    ZBParameterState,
    ZBParameterType,
)
from .zigbee_spec import (
    EVERYDAY_COMMANDS,
    MAIN_PARAMETER_BY_CLUSTER,
    SYSTEM_CLUSTERS,
    MainParameterSpec,
    UxSpec,
    classify_attribute,
    get_min_step,
    get_unit,
    resolve_metadata_bounds,
    unit_from_zha,
)

log = logging.getLogger(__name__)

# ZCL read_attributes request max payload: 254 bytes (EZSP LVBytes limit)
# Header=3 bytes + 2 bytes/attr → theoretical max 125 attr IDs per request.
# In practice, reading too many attributes at once keeps the serial line busy
# long enough to trigger NCP ACK timeouts.
_MAX_ATTRS_PER_REQUEST = 25
# Delay between attribute read chunks to let bellows send ASH ACKs.
_INTER_CHUNK_DELAY = 0.05

_quirks_loaded = False


_SETTING_ENTITY_TYPES = frozenset({"config", "diagnostic"})
_SENSOR_PLATFORMS = frozenset({"sensor", "binary_sensor"})


def quirk_ux_map(zbdevice: ZPDevice) -> dict[tuple[int, int, str], UxSpec]:
    """Per-attribute UX judgment carried by a v2 QuirkBuilder, keyed (endpoint_id, cluster_id,
    attribute_name). This is the runtime, device-specific tier of the classification ladder (above
    harvested zha, below our hand overrides). Empty for non-quirked / v1-only devices.

    entity_type (standard/config/diagnostic) -> visibility; entity_platform -> role; unit/
    device_class -> ParameterUnit. Only metadata entries that target a single attribute are used.
    """
    quirk_def = getattr(zbdevice, "_quirk_definition", None)
    if quirk_def is None:
        return {}
    out: dict[tuple[int, int, str], UxSpec] = {}
    for meta in getattr(quirk_def, "entity_metadata", ()):
        attr_name = getattr(meta, "attribute_name", None)
        if not attr_name:
            continue  # command buttons / composite entities carry no single attribute
        entity_type = getattr(getattr(meta, "entity_type", None), "value", None)
        platform = getattr(getattr(meta, "entity_platform", None), "value", None)
        visibility = ParameterVisibility.setting if entity_type in _SETTING_ENTITY_TYPES else ParameterVisibility.user
        role = ParameterRole.sensor if platform in _SENSOR_PLATFORMS else ParameterRole.control
        unit = unit_from_zha(
            getattr(getattr(meta, "device_class", None), "value", getattr(meta, "device_class", None)),
            getattr(meta, "unit", None),
        )
        out[(meta.endpoint_id, meta.cluster_id, attr_name)] = UxSpec(visibility, role, unit)
    return out


def _ensure_quirks_loaded() -> None:
    """Register zhaquirks into zigpy's process-global registry, once. Must run before the
    radio interviews devices so a joined device is presented in its quirked form —
    manufacturer clusters decoded into named/typed attributes and v2 entity metadata
    attached. setup() imports every zhaquirks module, so guard against repeat cost."""
    global _quirks_loaded
    if _quirks_loaded:
        return
    import zhaquirks

    zhaquirks.setup()
    _quirks_loaded = True
    log.debug("[QUIRKS] zhaquirks registered into zigpy registry")


def _zb_path(
    device: ZBDevice | None = None,
    zbdevice: ZPDevice | None = None,
    endpoint: zigpy.endpoint.Endpoint | None = None,
    cluster: zigpy.zcl.Cluster | None = None,
    attr_id: int | None = None,
    attr_ids: list[int] | None = None,
    error: Exception | None = None,
    attr_only: bool = False,
) -> str:
    parts: list[str] = []
    if device is not None:
        model = zbdevice.model if zbdevice is not None else None
        parts.append(f"device={device.id}" + (f"({model})" if model else ""))
    if endpoint is not None:
        ep_type = getattr(endpoint.device_type, "name", None)
        parts.append(f"endpoint={endpoint.endpoint_id}" + (f"({ep_type})" if ep_type else ""))
    if cluster is not None and not attr_only:
        parts.append(f"cluster={cluster.cluster_id}({cluster.name})")
    if attr_id is not None:
        attr_name = getattr(cluster.attributes.get(attr_id) if cluster else None, "name", None)
        parts.append(f"attr={attr_id}" + (f"({attr_name})" if attr_name else ""))
    if error is not None:
        parts.append(f"error={type(error).__name__}{' details=' + str(error) if str(error) else ''}")
    if attr_ids is not None:
        attr_ids = sorted(attr_ids)
        names = [getattr(cluster.attributes.get(a) if cluster else None, "name", None) for a in attr_ids]
        long = len(names) > 1
        glue = ",\n\t" if long else " "
        parts.append(
            f"attrs={'\n\t' if long else ''}"
            f"{glue.join(f'{a}({n})' if n else str(a) for a, n in zip(attr_ids, names, strict=False))}"
        )
    return " ".join(parts)


def _check_zcl_failures(
    failures: dict[int | None, ZCLStatus],
    cluster: zigpy.zcl.Cluster,
    log_prefix: str,
    *,
    device: "ZBDevice | None" = None,
    zbdevice: "ZPDevice | None" = None,
    endpoint: "zigpy.endpoint.Endpoint | None" = None,
    raise_errors: bool = True,
) -> None:
    """Handle ZCL-level failures from read_attributes or write_attributes.

    Registers unsupported attributes on the cluster and logs them at DEBUG.
    Raises ZBOperationError for any other non-SUCCESS status.

    Pass failures as {attr_id: status} (read_attributes) or
    {record.attrid: record.status for record in result[0]} (write_attributes).
    A None key means a global device-level status (write_attributes global failure).
    """
    cluster_path = _zb_path(device, zbdevice, endpoint, cluster)
    unsupported = [
        attr_id
        for attr_id, status in failures.items()
        if attr_id is not None and status == ZCLStatus.UNSUPPORTED_ATTRIBUTE
    ]
    errors = {
        attr_id: status
        for attr_id, status in failures.items()
        if status != ZCLStatus.SUCCESS and status != ZCLStatus.UNSUPPORTED_ATTRIBUTE
    }

    for attr_id in unsupported:
        # zigpy 2.0 resolves the id via find_attribute, which raises for ids the cluster
        # definition doesn't know (a device can report unsupported for a nonstandard id).
        with contextlib.suppress(KeyError, ValueError):
            cluster.add_unsupported_attribute(attr_id)
    if unsupported:
        names = [_zb_path(cluster=cluster, attr_id=a, attr_only=True) for a in unsupported]
        long = len(names) > 1
        log.debug(
            f"{log_prefix} unsupported attributes {cluster_path}: {'\n\t' if long else ''}{(',\n\t').join(names)}"
        )

    if not errors:
        return
    details = ",\n\t".join(
        f"{_zb_path(cluster=cluster, attr_id=attr_id, attr_only=True) if attr_id is not None else 'global'}"
        f"={getattr(status, 'name', status)}"  # ZCLStatus enum, or a raw int for unknown codes
        for attr_id, status in errors.items()
    )
    if raise_errors:
        raise ZBOperationError(f"{log_prefix} ZCL failures:\n\t{details}")
    else:
        log.error(f"{log_prefix} ZCL failures:\n\t{details}")


class ZigBeeController(AbstractController):
    """Bridges the Hub to Zigbee devices through a USB coordinator radio via zigpy.

    zigpy owns the Zigbee network, the ZCL, and device interviews; this controller adapts it
    to the Hub's AbstractController contract. Zigbee has no separate discovery step — a device
    is on the network the moment it joins the permit-join window. See readme.md.
    """

    _ZIGBEE_STACK: ClassVar[Literal["bellows", "znp"]] = "bellows"

    _zigbee_device_path: str
    _zigbe_db: str
    _majordom_discoveries: dict[UUID, Discovery]  # discovery metadata surfaced to the Hub
    _awaiting_zb_discoveries: dict[UUID, ZPDevice]  # on the network, discovered, not yet paired in majordom
    _connected_devices: dict[UUID, ZPDevice]  # paired in majordom
    # (the ZPDevice values could be shrunk to just the IEEE address to save memory, if needed.)

    _mapper: ZigBeeMapper
    _tasks: set[asyncio.Task]

    def __init__(self, dependencies: AbstractController.Dependencies):
        super().__init__(dependencies)
        # Mapper is wired with the framework's UUID generators so every Zigbee id is namespaced
        # consistently under the integration and device (see ZigBeeMapper).
        self._mapper = ZigBeeMapper(self.device_uuid, self.parameter_uuid)
        self._majordom_discoveries: dict[UUID, Discovery] = {}
        self._awaiting_zb_discoveries: dict[UUID, ZPDevice] = {}
        self._connected_devices: dict[UUID, ZPDevice] = {}
        self._availability: dict[UUID, bool] = {}  # device_id -> last signalled availability
        self._tasks: set[asyncio.Task] = set()

    # -------------------------------------------------------------------------
    # AbstractController interface
    # -------------------------------------------------------------------------

    name = "ZigBee"

    @property
    def discoveries(self) -> dict[UUID, Discovery]:
        return self._majordom_discoveries

    @property
    @override
    def device_type(self) -> type[ZBDevice]:
        return ZBDevice

    @property
    @override
    def parameter_type(self) -> type[ZBParameter]:
        return ZBParameter

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self):
        self._zigbee_device_path = self.dependencies.hardware_interfaces[0]
        self._zigbe_db = str(self.documents_folder / "zigbee.db")
        log.debug("[START] port=%s  db=%s", self._zigbee_device_path, self._zigbe_db)
        config = {
            CONF_DEVICE: {CONF_DEVICE_PATH: self._zigbee_device_path},
            CONF_DATABASE: self._zigbe_db,
        }

        # Register per-device quirks before the radio starts interviewing devices.
        _ensure_quirks_loaded()

        # Starting zigbee stack
        match self._ZIGBEE_STACK:
            case "bellows":
                from bellows.zigbee.application import ControllerApplication
            case "znp":
                from zigpy_znp.zigbee.application import ControllerApplication
        try:
            self._application = await ControllerApplication.new(config=config, auto_form=True)
        except Exception as e:
            if "locked" in str(e).lower() or "permission" in str(e).lower():
                holder = port_holder(self._zigbee_device_path)
                msg = f"{self._zigbee_device_path} is locked"
                if holder:
                    msg += f" by: {holder}"
                raise PermissionError(msg) from e
            raise
        self._application.add_listener(self)
        log.debug("[READY] connected to %s", self._zigbee_device_path)

        async with self.dependencies.make_device_repository() as device_repo:
            # Subscribe to attribute updates and add the device to _connected_devices
            # if the Zigbee device is in the majordom database, otherwise start a discovery cycle.
            for zbdevice in self._application.devices.values():
                if zbdevice.nwk == 0x0000:
                    continue
                device_id = self._mapper.device_uuid_from_ieee(self._mapper.convert_eui64_to_str(zbdevice.ieee))
                if await device_repo.get(device_id, ZBDevice):
                    self._connected_devices[device_id] = zbdevice
                    self._availability[device_id] = True  # baseline for mid-session transitions
                    await self._subscribe(device_id, zbdevice)
                    log.debug("[KNOWN] ieee=%s  nwk=0x%04X", zbdevice.ieee, zbdevice.nwk)
                else:
                    self._create_task(self._disconnect_unpaired_discovery(device_id, zbdevice.ieee))
                    log.debug(
                        "[UNKNOWN] ieee=%s  nwk=0x%04X — not in DB, starting disconnect timer",
                        zbdevice.ieee,
                        zbdevice.nwk,
                    )
                    try:
                        await zbdevice.initialize()
                    except Exception:
                        log.exception("[UNKNOWN] initialize failed for ieee=%s", zbdevice.ieee)

            # Any device in our DB that isn't on the network anymore is marked unavailable on boot.
            for device in await device_repo.get_all(as_=ZBDevice):
                ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)
                if self._application.get_device(ieee):
                    continue
                device.available = False
                device.last_error = f"Device {device.name} is no longer connected to the ZigBee network"
                self._availability[device.id] = False  # baseline for mid-session transitions
                await device_repo.save(device, device.id)

    async def stop(self):
        log.debug("[STOP] shutting down")
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._application:
            await self._application.shutdown()
        self._majordom_discoveries.clear()
        self._awaiting_zb_discoveries.clear()
        self._connected_devices.clear()

    # -------------------------------------------------------------------------
    # Hub -> device operations
    # -------------------------------------------------------------------------

    async def start_pairing_window(self, duration_sec: int) -> None:
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        log.debug("[PERMIT-JOIN] opening for %ds", duration_sec)
        await self._application.permit(duration_sec)

    async def pair_device(
        self, discovery: Discovery, credentials: ProvidedCredentials | None
    ):  # Break down into submethods
        async with self.dependencies.make_device_repository() as device_repository:
            device = await device_repository.state(discovery.id, ZBDeviceState)
            self._majordom_discoveries.pop(discovery.id)
            zbdevice = self._awaiting_zb_discoveries.pop(discovery.id)
            self._connected_devices[discovery.id] = zbdevice

            assert device
            assert zbdevice

            if not device.integration_data.ieee:
                device.integration_data = ZBDeviceIntegrationData(ieee=self._mapper.convert_eui64_to_str(zbdevice.ieee))
            parameters: list[ZBParameterState] = list()
            # v2 QuirkBuilder entity judgment for this device (empty for non-quirked devices).
            quirk_ux = quirk_ux_map(zbdevice)
            for endpoint in zbdevice.non_zdo_endpoints:
                for cluster in endpoint.clusters:
                    for attribute_id, attribute in cluster.attributes.items():
                        if cluster.is_attribute_unsupported(attribute_id):
                            continue

                        value = b""

                        # Classification via the priority ladder (see the zigbee README): our hand
                        # overrides > v2 quirk metadata > harvested zha judgment > fallback heuristic
                        # (which warns). Metadata and manufacturer-on-system-cluster attrs are hidden.
                        spec, source = classify_attribute(
                            cluster.cluster_id,
                            attribute_id,
                            attribute.name,
                            writable=bool(attribute.access & ZCLAttributeAccess.Write),
                            reportable=bool(attribute.access & ZCLAttributeAccess.Report),
                            quirk_ux=quirk_ux.get((endpoint.endpoint_id, cluster.cluster_id, attribute.name)),
                        )
                        visibility = spec.visibility
                        role = (
                            spec.role
                            if spec.role is not None
                            else self._mapper.parse_zigbee_attribute_access(attribute.access)
                        )
                        if source.startswith("fallback"):
                            log.warning(
                                "[PAIR] uncurated attribute %s -> %s (%s); add to OUR_ATTRIBUTE_UX "
                                "or refresh the zha harvest",
                                _zb_path(cluster=cluster, attr_id=attribute_id, attr_only=True),
                                visibility.value,
                                source,
                            )

                        data_type = self._mapper.parse_zigbee_data_type(attribute.zcl_type)
                        min_value = None
                        max_value = None
                        # Annotated so the untyped-enum comprehension below (member.name is Unknown)
                        # matches Parameter.valid_values' declared type.
                        valid_values: dict[int | float | str, str] | None = None
                        min_step = get_min_step(cluster.cluster_id, attribute_id)
                        # Our spec tables win; the harvested/quirk unit fills gaps get_unit() leaves plain.
                        unit = get_unit(cluster.cluster_id, attribute_id)
                        if unit is ParameterUnit.plain and spec.unit is not None:
                            unit = spec.unit

                        if hasattr(attribute.type, "min_value"):
                            min_value = attribute.type.min_value
                        if hasattr(attribute.type, "max_value"):
                            max_value = attribute.type.max_value

                        # Metadata priority 1: the device's own limit attributes (runtime values) win
                        # over the wire-type default. cluster.get returns the cached sibling value.
                        min_value, max_value, missing_bounds = resolve_metadata_bounds(
                            cluster.cluster_id, attribute_id, cluster.get, min_value, max_value
                        )
                        for missing_attr in missing_bounds:
                            log.debug(
                                "[PAIR] metadata source %s not reported for %s — quirk or unsupported; "
                                "using wire-type default",
                                _zb_path(cluster=cluster, attr_id=missing_attr, attr_only=True),
                                _zb_path(cluster=cluster, attr_id=attribute_id, attr_only=True),
                            )

                        if issubclass(attribute.type, Enum) and data_type != ParameterDataType.bool:
                            valid_values = {member.name: str(member.value) for member in attribute.type}

                        parameters.append(
                            ZBParameterState(
                                id=self._mapper.attribute_parameter_uuid(
                                    device.id, endpoint.endpoint_id, cluster.cluster_id, attribute_id
                                ),
                                name=attribute.name,
                                data_type=data_type,
                                visibility=visibility,
                                min_value=min_value,
                                max_value=max_value,
                                min_step=min_step,
                                unit=unit,
                                valid_values=valid_values,
                                role=role,
                                integration_data=ZBParameterIntegrationData(
                                    endpoint_id=endpoint.endpoint_id,
                                    cluster_id=cluster.cluster_id,
                                    attribute_id=attribute_id,
                                    type=ZBParameterType.attribute,
                                ),
                                value=value,
                            )
                        )
                    for command in cluster.commands:
                        fields: list[Parameter] = []

                        # Command visibility: system-cluster commands hidden; everyday one-tap
                        # actions -> user; every other command (schedule/credential/log
                        # management) -> setting.
                        if cluster.cluster_id in SYSTEM_CLUSTERS:
                            visibility = ParameterVisibility.system
                        elif (cluster.cluster_id, command.id) in EVERYDAY_COMMANDS:
                            visibility = ParameterVisibility.user
                        else:
                            visibility = ParameterVisibility.setting

                        for i, field in enumerate(command.schema.fields):
                            min_value = None
                            max_value = None
                            valid_values: dict[int | float | str, str] | None = None

                            if hasattr(field.type, "min_value"):
                                min_value = field.type.min_value
                            if hasattr(field.type, "max_value"):
                                max_value = field.type.max_value
                            if isinstance(field.type, type) and issubclass(field.type, Enum):
                                valid_values = {member.name: str(member.value) for member in field.type}

                            fields.append(
                                Parameter(
                                    id=self._mapper.command_field_uuid(
                                        device.id, endpoint.endpoint_id, cluster.cluster_id, command.id, i
                                    ),
                                    name=field.name,
                                    data_type=self._mapper.parse_zigbee_data_type(field.type),
                                    role=ParameterRole.control,
                                    visibility=ParameterVisibility.setting,
                                    min_value=min_value,
                                    max_value=max_value,
                                    valid_values=valid_values,
                                    integration_data=None,
                                )
                            )
                        parameters.append(
                            ZBParameterState(
                                id=self._mapper.command_parameter_uuid(
                                    device.id, endpoint.endpoint_id, cluster.cluster_id, command.id
                                ),
                                name=command.name,
                                data_type=ParameterDataType.none,
                                role=ParameterRole.control,
                                fields=json.loads(json.dumps([f.model_dump(mode="json") for f in fields]))
                                if fields
                                else None,
                                visibility=visibility,
                                integration_data=ZBParameterIntegrationData(
                                    endpoint_id=endpoint.endpoint_id,
                                    cluster_id=cluster.cluster_id,
                                    command_id=command.id,
                                    type=ZBParameterType.command,
                                ),
                                value=b"",
                            )
                        )
            device.parameters = parameters
            main_parameter_id, main_spec = self._get_device_main_parameter(device.id, zbdevice)
            device.main_parameter = main_parameter_id
            if main_parameter_id and main_spec is not None:
                # Attach the one-tap send info to the chosen main parameter; drop the main parameter
                # if the target command/attribute wasn't actually exposed.
                main_parameter = next((p for p in parameters if p.id == main_parameter_id), None)
                if main_parameter is None:
                    device.main_parameter = None
                elif main_spec.is_attribute:
                    # Enum attribute main: store the cycle subset for the send path to rotate through.
                    main_parameter.integration_data.main_cycle = main_spec.cycle
                elif main_spec.default_arguments is not None:
                    main_parameter.integration_data.default_arguments = main_spec.default_arguments
            log.debug(
                f"[PAIR] mapped schema {_zb_path(device, zbdevice)}\n\t"
                + "\n\t".join(
                    f"  {p.role.value:8} {p.visibility.value:8} {p.data_type.value:10} {p.name}  id={p.id}"
                    for p in parameters
                ),
            )
            await device_repository.save(device, discovery.id)
            # fetch runs in background; controller_did_connect_device fires after fetch completes
            self._create_task(self._fetch_after_pair(device, zbdevice))

    async def unpair(self, device: ZBDevice):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        log.debug("[UNPAIR] ieee=%s", device.integration_data.ieee)
        await self._application.remove(self._mapper.convert_str_to_eui64(device.integration_data.ieee))
        self._connected_devices.pop(self._mapper.device_uuid_from_ieee(device.integration_data.ieee))

    async def identify(self, device: ZBDevice):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ZBUnexpectedError(f"Device {ieee} not found in ZigBee network")
        if not zbdevice.is_initialized:
            raise ZBUnexpectedError(f"Device {ieee} is not initialized")

        for endpoint in zbdevice.non_zdo_endpoints:
            cluster = endpoint.in_clusters.get(Identify.cluster_id)
            if not cluster:
                continue
            await cluster.identify(10)  # 10 - identification time

    async def fetch(self, device: ZBDevice) -> None:
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ZBUnexpectedError(f"Device {ieee} not found in ZigBee network")
        if not zbdevice.is_initialized:
            raise ZBUnexpectedError(f"Device {ieee} is not initialized")
        events: list[DeviceParameterChange] = list()
        log.debug("[FETCH] start device=%s(%s)", device.id, zbdevice.model)
        t0 = asyncio.get_event_loop().time()
        for endpoint in zbdevice.non_zdo_endpoints:
            for cluster_id, cluster in endpoint.in_clusters.items():
                readable_ids = [
                    attr_id for attr_id in cluster.attributes if not cluster.is_attribute_unsupported(attr_id)
                ]
                attr_values = await self._read_cluster_attributes(
                    device, zbdevice, endpoint, cluster, readable_ids, log_prefix="[FETCH]", timeout=2
                )

                for attribute_id in cluster.attributes:
                    if cluster.is_attribute_unsupported(attribute_id):
                        continue
                    events.append(
                        DeviceParameterChange(
                            device_id=device.id,
                            parameter_id=self._mapper.attribute_parameter_uuid(
                                device.id, endpoint.endpoint_id, cluster_id, attribute_id
                            ),
                            value=self._mapper.normalize_zigbee_value(attr_values.get(attribute_id)),
                        )
                    )
                for command in cluster.commands:
                    events.append(
                        DeviceParameterChange(
                            device_id=device.id,
                            parameter_id=self._mapper.command_parameter_uuid(
                                device.id, endpoint.endpoint_id, cluster_id, command.id
                            ),
                            value=None,
                        )
                    )

        log.debug(
            "[FETCH] done device=%s(%s) duration=%.2fs", device.id, zbdevice.model, asyncio.get_event_loop().time() - t0
        )
        await self.dependencies.output.controller_did_receive_events(self, events)

    async def send_command(self, command: DeviceCommand, device: ZBDevice, parameter: ZBParameter):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ZBUnexpectedError(f"Device {ieee} not found in ZigBee network")
        if not (endpoint := zbdevice.endpoints.get(parameter.integration_data.endpoint_id)):
            raise ZBUnexpectedError(f"Endpoint {parameter.integration_data.endpoint_id} not found")
        # endpoints[0] is the device's ZDO; a real parameter always lives on a numbered Endpoint.
        # Narrowing here fixes it for every downstream use (in_clusters, _zb_path, _check_zcl_failures).
        if not isinstance(endpoint, zigpy.endpoint.Endpoint):
            raise ZBUnexpectedError(
                f"Endpoint {parameter.integration_data.endpoint_id} is the ZDO, not a device endpoint"
            )
        if not (cluster := endpoint.in_clusters.get(parameter.integration_data.cluster_id)):
            raise ZBUnexpectedError(f"Cluster {parameter.integration_data.cluster_id} not found")

        if parameter.integration_data.type is ZBParameterType.attribute:
            if parameter.role != ParameterRole.control:
                raise ZBUnexpectedError(f"Parameter '{parameter.name}' is not a control parameter")
            attr_id = parameter.integration_data.attribute_id
            if command.value is not None:
                value = command.value
            else:
                # Value-less send = the user tapped this attribute main parameter (standalone
                # mode — under the Hub the relay pre-derives the value). Cycle through the
                # device-local curated subset first, else the SDK derivation (default_value
                # set / valid_values / bool).
                cycle = parameter.integration_data.main_cycle or parameter.main_cycle
                value = next_main_parameter_value(cluster.get(attr_id), cycle) if cycle else None
                if value is None:
                    raise ZBUnexpectedError(f"No value to send for main parameter '{parameter.name}'")
            try:
                result = await cluster.write_attributes({attr_id: value})
            except Exception as e:
                raise ZBConnectionError(
                    f"[CMD] write_attributes transport error "
                    f"{_zb_path(device, zbdevice, endpoint, cluster, attr_id, error=e)}"
                ) from None
            _check_zcl_failures(
                {r.attrid: r.status for r in result[0]},
                cluster,
                "[CMD]",
                device=device,
                zbdevice=zbdevice,
                endpoint=endpoint,
            )
            log.info(f"[CMD] write_attributes {_zb_path(device, zbdevice, endpoint, cluster, attr_id)}")
        else:
            zbcommand = cluster.commands_by_name.get(parameter.name)
            if not zbcommand:
                raise ZBUnexpectedError(f"Command {parameter.name} not found in cluster")
            # A value-less send (e.g. tapping the main parameter) falls back to the arguments this
            # command was set up with as a main parameter — see integration_data.default_arguments.
            arguments = command.value if command.value is not None else parameter.integration_data.default_arguments
            try:
                if isinstance(arguments, dict):
                    result = await cluster.command(zbcommand.id, **arguments)
                elif arguments is not None:
                    result = await cluster.command(zbcommand.id, arguments)
                else:
                    result = await cluster.command(zbcommand.id)
            except Exception as e:
                raise ZBConnectionError(
                    f"[CMD] command error {_zb_path(device, zbdevice, endpoint, cluster, error=e)}"
                ) from None
            # cluster.command returns the DefaultResponse or cluster-specific response;
            # check status field if present (DefaultResponse carries it)
            if hasattr(result, "status") and result.status != ZCLStatus.SUCCESS:
                raise ZBOperationError(
                    f"[CMD] command failure {_zb_path(device, zbdevice, endpoint, cluster)}: "
                    f"status={result.status.name}"
                )

    # -------------------------------------------------------------------------
    # Device -> Hub: Zigbee network events (zigpy listener) & availability
    # -------------------------------------------------------------------------

    def device_joined(self, device: ZPDevice):
        """A device joined the Zigbee network (only happens while the permit-join window is open)."""
        log.debug("[JOIN] ieee=%s  nwk=0x%04X", device.ieee, device.nwk)
        device_id = self._mapper.device_uuid_from_ieee(self._mapper.convert_eui64_to_str(device.ieee))
        # Zigbee has no separate "discovery": a device is on the network as soon as it joins.
        # We hold it in the awaiting list and disconnect it if it isn't paired in majordom within
        # the window (see _disconnect_unpaired_discovery). An already-paired device that merely
        # rejoined is handled in device_initialized, not here.
        self._create_task(self._disconnect_unpaired_discovery(device_id, device.ieee))

    def device_initialized(self, device: ZPDevice):
        """A device finished interviewing and is ready to talk. Fires both for a brand-new join
        and when an already-paired device rejoins the network after being offline."""
        log.debug(
            "[INIT] ieee=%s  nwk=0x%04X  model=%r  manufacturer=%r",
            device.ieee,
            device.nwk,
            device.model,
            device.manufacturer,
        )
        device_id = self._mapper.device_uuid_from_ieee(self._mapper.convert_eui64_to_str(device.ieee))

        # Already paired in majordom -> a mid-session reconnect: re-subscribe and mark it back
        # online instead of surfacing it as a fresh discovery.
        if device_id in self._connected_devices:
            self._connected_devices[device_id] = device
            self._create_task(self._subscribe(device_id, device))
            self._create_task(self._set_availability(device_id, True))
            return

        # Awaiting pairing and re-initialized -> just (re)subscribe.
        if device_id in self.discoveries:
            self._create_task(self._subscribe(device_id, device))
            return

        # Otherwise it's a new, not-yet-paired device -> surface it as a discovery.
        discovery = Discovery(
            id=device_id,
            integration=NonEmptyStr(self.name),
            expected_credentials_options=[CredentialsType.none],
            expiration=None,
            transport=NonEmptyStr("ZIGBEE"),
            device_manufacturer=None,
            device_name=NonEmptyStr(device.name),
            device_category=None,
            device_icon=None,
        )
        self._majordom_discoveries[device_id] = discovery
        self._awaiting_zb_discoveries[device_id] = device
        log.debug("[DISCOVERY] ieee=%s  discovery_id=%s", device.ieee, device_id)
        self._create_task(self.dependencies.output.controller_did_receive_discovery(self, discovery))

    def device_left(self, device: ZPDevice):
        """A device left the Zigbee network. If it's one of ours, mark it unavailable so the app
        reflects it; the pairing stays in the DB so it comes back online on rejoin."""
        log.debug("[LEFT] ieee=%s  nwk=0x%04X", device.ieee, device.nwk)
        device_id = self._mapper.device_uuid_from_ieee(self._mapper.convert_eui64_to_str(device.ieee))
        if device_id in self._connected_devices:
            self._create_task(self._set_availability(device_id, False))

    async def _set_availability(self, device_id: UUID, available: bool) -> None:
        """Single funnel for availability transitions — dedupes so the Hub is only told on an
        actual change, and translates it into the framework's connect / lose callbacks."""
        if self._availability.get(device_id) == available:
            return
        self._availability[device_id] = available
        if available:
            await self.dependencies.output.controller_did_connect_device(self, device_id)
        else:
            await self.dependencies.output.controller_did_lose_device(self, device_id)

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _fetch_after_pair(self, device: ZBDevice, zbdevice: ZPDevice) -> None:
        log.debug("[PAIR] starting background fetch device=%s(%s)", device.id, zbdevice.model)
        try:
            await self.fetch(device)
        except Exception as e:
            log.warning(
                "[PAIR] fetch failed for device=%s(%s), skipping connect signal: %s", device.id, zbdevice.model, e
            )
            return
        await self._set_availability(device.id, True)
        log.debug("[PAIR] background fetch done, device connected device=%s(%s)", device.id, zbdevice.model)

    async def _read_cluster_attributes(
        self,
        device: ZBDevice,
        zbdevice: ZPDevice,
        endpoint: zigpy.endpoint.Endpoint,
        cluster: zigpy.zcl.Cluster,
        ids: list[int],
        *,
        only_cache: bool = False,
        log_prefix: str = "[READ]",
        timeout: float | None = None,
    ) -> dict[int, object]:
        attr_values: dict[int, object] = {}
        chunks = [ids[i : i + _MAX_ATTRS_PER_REQUEST] for i in range(0, len(ids), _MAX_ATTRS_PER_REQUEST)]
        for chunk in chunks:
            try:
                # read_attributes accepts attribute names/ids/defs; we only pass ids, and list
                # invariance is the only reason the plain list[int] doesn't fit the wider param type.
                values, failures = await cluster.read_attributes(
                    cast("list[int | str | ZCLAttributeDef]", chunk), only_cache=only_cache, timeout=timeout
                )
            except Exception as e:
                log.error(
                    f"{log_prefix} read_attributes error "
                    f"{_zb_path(device, zbdevice, endpoint, cluster, attr_ids=chunk, error=e)}"
                )
                await asyncio.sleep(_INTER_CHUNK_DELAY)  # let bellows send pending ASH ACKs
                continue
            await asyncio.sleep(_INTER_CHUNK_DELAY)  # pace chunks to prevent NCP ACK timeout
            attr_values.update(values)
            _check_zcl_failures(
                failures,
                cluster,
                log_prefix,
                device=device,
                zbdevice=zbdevice,
                endpoint=endpoint,
                raise_errors=False,
            )
        return attr_values

    def _create_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _subscribe(self, device_id: UUID, device: ZPDevice):
        for endpoint in device.non_zdo_endpoints:
            for cluster in endpoint.in_clusters.values():
                listener = ZBAttributeUpdatedListener(self, device_id, cluster)
                cluster.add_listener(listener)

    async def _disconnect_unpaired_discovery(self, discovery_id: UUID, ieee: EUI64):
        await asyncio.sleep(300)  # 5 minutes
        if discovery_id not in self._majordom_discoveries and discovery_id in self._connected_devices:
            # if the device was connected
            return
        await self._application.remove(ieee)
        self._majordom_discoveries.pop(discovery_id)
        self._awaiting_zb_discoveries.pop(discovery_id)

    def _get_device_main_parameter(
        self, device_id: UUID, zbdevice: ZPDevice
    ) -> tuple[UUID | None, MainParameterSpec | None]:
        """Pick the parameter used for the device's one-tap action on the room view, in cluster
        priority order (see MAIN_PARAMETER_BY_CLUSTER). Returns the parameter id and its spec
        (command or attribute main), or (None, None) if the device has no sensible one-tap action."""
        for endpoint in zbdevice.non_zdo_endpoints:
            for cluster_id, spec in MAIN_PARAMETER_BY_CLUSTER.items():
                if endpoint.in_clusters.get(cluster_id):
                    if spec.is_attribute:
                        param_id = self._mapper.attribute_parameter_uuid(
                            device_id, endpoint.endpoint_id, cluster_id, spec.target_id
                        )
                    else:
                        param_id = self._mapper.command_parameter_uuid(
                            device_id, endpoint.endpoint_id, cluster_id, spec.target_id
                        )
                    return param_id, spec
        return None, None
