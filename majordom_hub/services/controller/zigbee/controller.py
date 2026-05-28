import asyncio
import json
import logging
from enum import Enum
from typing import ClassVar, Literal, Type, override
from uuid import UUID

import zigpy.endpoint
import zigpy.zcl
from zigpy.config import CONF_DATABASE, CONF_DEVICE, CONF_DEVICE_PATH
from zigpy.device import Device as ZPDevice  # ZP - ZigPy
from zigpy.types import EUI64
from zigpy.zcl.clusters.general import Identify
from zigpy.zcl.foundation import Status as ZCLStatus
from zigpy.zcl.foundation import ZCLAttributeAccess

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.command import DeviceCommand
from majordom_hub.schemas.device import CredentialsType, CredentialsValue, Discovery, NonEmptyStr
from majordom_hub.schemas.parameter import ParameterDataType, ParameterRole, ParameterVisibility
from majordom_hub.services.controller.framework.abstract_controller import AbstractController
from majordom_hub.utils.serial import port_holder

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
from .zigbee_spec import SYSTEM_CLUSTERS, get_min_step, get_unit

log = logging.getLogger(__name__)

# ZCL read_attributes request max payload: 254 bytes (EZSP LVBytes limit)
# Header=3 bytes + 2 bytes/attr → max 125 attr IDs per request
_MAX_ATTRS_PER_REQUEST = 125


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
        parts.append(
            f"error={type(error).__name__}{' details=' + str(error) if str(error) else ''}"
        )
    if attr_ids is not None:
        attr_ids = sorted(attr_ids)
        names = [
            getattr(cluster.attributes.get(a) if cluster else None, "name", None) for a in attr_ids
        ]
        long = len(names) > 1
        glue = ",\n\t" if long else " "
        parts.append(
            f"attrs={'\n\t' if long else ''}{glue.join(f'{a}({n})' if n else str(a) for a, n in zip(attr_ids, names))}"
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
        f"{_zb_path(cluster=cluster, attr_id=attr_id, attr_only=True) if attr_id is not None else 'global'}={status.name}"  # type: ignore[union-attr]
        for attr_id, status in errors.items()
    )
    if raise_errors:
        raise ZBOperationError(f"{log_prefix} ZCL failures:\n\t{details}")
    else:
        log.error(f"{log_prefix} ZCL failures:\n\t{details}")


class ZigBeeController(AbstractController):
    _ZIGBEE_STACK: ClassVar[Literal["bellows", "znp"]] = "bellows"

    _zigbee_device_path: str
    _zigbe_db: str
    # _application: ControllerApplication
    _majordom_discoveries: dict[UUID, Discovery] = dict()  # MJ discovery metadata
    _awaiting_zb_discoveries: dict[UUID, ZPDevice] = (
        dict()
    )  # connected to zigbee network but not set up in majordom yet. We can use less RAM if we store only IEEE data instead of the entire device. Should I add this?
    _connected_devices: dict[UUID, ZPDevice] = (
        dict()
    )  # fully connected. We can use less RAM if we store only IEEE data instead of the entire device. Should I add this?

    _mapper = ZigBeeMapper()
    _tasks: set[asyncio.Task] = set()

    @property
    def name(self) -> str:
        return "ZigBee"

    @property
    def discoveries(self) -> dict[UUID, Discovery]:
        return self._majordom_discoveries

    @property
    @override
    def device_type(self) -> Type[ZBDevice]:
        return ZBDevice

    @property
    @override
    def parameter_type(self) -> Type[ZBParameter]:
        return ZBParameter

    async def start(self):
        self._zigbee_device_path = self.dependencies.hardware_interfaces[0]
        self._zigbe_db = str(self.documents_folder / "zigbee.db")
        log.debug("[START] port=%s  db=%s", self._zigbee_device_path, self._zigbe_db)
        config = {
            CONF_DEVICE: {CONF_DEVICE_PATH: self._zigbee_device_path},
            CONF_DATABASE: self._zigbe_db,
        }

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
                device_id = self._mapper.create_uuid_id(
                    self._mapper.convert_eui64_to_str(zbdevice.ieee)
                )
                if await device_repo.get(device_id, ZBDevice):
                    self._connected_devices[device_id] = zbdevice
                    await self._subscribe(device_id, zbdevice)
                    log.debug("[KNOWN] ieee=%s  nwk=0x%04X", zbdevice.ieee, zbdevice.nwk)
                else:
                    self._create_task(self._disconnect_unpaired_discovery(device_id, zbdevice.ieee))
                    await zbdevice.initialize()
                    log.debug(
                        "[UNKNOWN] ieee=%s  nwk=0x%04X — not in DB, starting disconnect timer",
                        zbdevice.ieee,
                        zbdevice.nwk,
                    )

            # Checking if all devices in our system are still connected to ZigBee
            for device in await device_repo.get_all(self.name, ZBDevice):
                ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)
                if self._application.get_device(ieee):
                    continue
                device.available = False
                device.last_error = (
                    f"Device {device.name} is no longer connected to the ZigBee network"
                )
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

    async def start_pairing_window(self, duration_sec: int) -> None:
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        log.debug("[PERMIT-JOIN] opening for %ds", duration_sec)
        await self._application.permit(duration_sec)

    async def fetch(self, device: ZBDevice) -> None:
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ZBUnexpectedError(f"Device {ieee} not found in ZigBee network")
        if not zbdevice.is_initialized:
            raise ZBUnexpectedError(f"Device {ieee} is not initialized")
        events: list[DeviceParameterChangedEvent] = list()
        for endpoint in zbdevice.non_zdo_endpoints:
            for cluster_id, cluster in endpoint.in_clusters.items():
                readable_ids = [
                    attr_id
                    for attr_id in cluster.attributes.keys()
                    if attr_id not in cluster.unsupported_attributes
                ]
                attr_values = await self._read_cluster_attributes(
                    device, zbdevice, endpoint, cluster, readable_ids, log_prefix="[FETCH]"
                )

                for attribute_id in cluster.attributes.keys():
                    if attribute_id in cluster.unsupported_attributes:
                        continue
                    events.append(
                        DeviceParameterChangedEvent(
                            device_id=device.id,
                            parameter_id=self._mapper.create_uuid_id(
                                f"{device.id}_attribute_{endpoint.endpoint_id}/{cluster_id}/{attribute_id}"
                            ),
                            value=attr_values.get(attribute_id),
                        )
                    )
                for command_id in cluster.commands.keys():
                    events.append(
                        DeviceParameterChangedEvent(
                            device_id=device.id,
                            parameter_id=self._mapper.create_uuid_id(
                                f"{device.id}_command_{endpoint.endpoint_id}/{cluster_id}/{command_id}"
                            ),
                            value=None,
                        )
                    )

        await self.dependencies.output.controller_did_receive_device_events(self, events)

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

    async def send_command(self, command: DeviceCommand, device: ZBDevice, parameter: ZBParameter):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ZBUnexpectedError(f"Device {ieee} not found in ZigBee network")
        if not (endpoint := zbdevice.endpoints.get(parameter.integration_data.endpoint_id)):
            raise ZBUnexpectedError(f"Endpoint {parameter.integration_data.endpoint_id} not found")
        if not (cluster := endpoint.in_clusters.get(parameter.integration_data.cluster_id)):
            raise ZBUnexpectedError(f"Cluster {parameter.integration_data.cluster_id} not found")

        if parameter.integration_data.type is ZBParameterType.attribute:
            if parameter.role != ParameterRole.control:
                raise ZBUnexpectedError(f"Parameter '{parameter.name}' is not a control parameter")
            attr_id = parameter.integration_data.attribute_id
            try:
                result = await cluster.write_attributes({attr_id: command.value})
            except Exception as e:
                raise ZBConnectionError(
                    f"[CMD] write_attributes transport error {_zb_path(device, zbdevice, endpoint, cluster, attr_id, error=e)}"
                ) from None
            _check_zcl_failures(
                {r.attrid: r.status for r in result[0]},
                cluster,
                "[CMD]",
                device=device,
                zbdevice=zbdevice,
                endpoint=endpoint,
            )
            log.info(
                f"[CMD] write_attributes {_zb_path(device, zbdevice, endpoint, cluster, attr_id)}"
            )
        else:
            zbcommand = cluster.commands_by_name.get(parameter.name)
            if not zbcommand:
                raise ZBUnexpectedError(f"Command {parameter.name} not found in cluster")
            try:
                result = (
                    await cluster.command(zbcommand.id, command.value)
                    if command.value is not None
                    else await cluster.command(zbcommand.id)
                )
            except Exception as e:
                raise ZBOperationError(
                    f"[CMD] command error {_zb_path(device, zbdevice, endpoint, cluster, error=e)}"
                ) from None
            # cluster.command returns the DefaultResponse or cluster-specific response;
            # check status field if present (DefaultResponse carries it)
            if hasattr(result, "status") and result.status != ZCLStatus.SUCCESS:
                raise ZBOperationError(
                    f"[CMD] command failure {_zb_path(device, zbdevice, endpoint, cluster)}: status={result.status.name}"
                )

    async def pair_device(
        self, discovery: Discovery, credentials: CredentialsValue | None
    ):  # Break down into submethods
        async with self.dependencies.make_device_repository() as device_repository:
            device = await device_repository.state(discovery.id, ZBDeviceState)
            self._majordom_discoveries.pop(discovery.id)
            zbdevice = self._awaiting_zb_discoveries.pop(discovery.id)
            self._connected_devices[discovery.id] = zbdevice

            assert device
            assert zbdevice

            if not device.integration_data.ieee:
                device.integration_data = ZBDeviceIntegrationData(
                    ieee=self._mapper.convert_eui64_to_str(zbdevice.ieee)
                )
            parameters: list[ZBParameterState] = list()
            for endpoint in zbdevice.non_zdo_endpoints:
                for cluster in endpoint.clusters:
                    readable_ids = [
                        attr_id
                        for attr_id, attr in cluster.attributes.items()
                        if attr.access & ZCLAttributeAccess.Read
                        and attr_id not in cluster.unsupported_attributes
                    ]

                    cached_ids = [a for a in readable_ids if a in cluster._attr_cache]
                    live_ids = [a for a in readable_ids if a not in cluster._attr_cache]
                    attr_values = await self._read_cluster_attributes(
                        device,
                        zbdevice,
                        endpoint,
                        cluster,
                        cached_ids,
                        only_cache=True,
                        log_prefix="[PAIR]",
                    )
                    attr_values |= await self._read_cluster_attributes(
                        device, zbdevice, endpoint, cluster, live_ids, log_prefix="[PAIR]"
                    )

                    for attribute_id, attribute in cluster.attributes.items():
                        if attribute_id in cluster.unsupported_attributes:
                            continue

                        value = b""

                        visibility = ParameterVisibility.system
                        if (
                            attribute_id < 0xF000 or cluster.cluster_id not in SYSTEM_CLUSTERS
                        ):  # next manufacturer specifik and global/system attributes
                            if attribute.access & ZCLAttributeAccess.Report:
                                visibility = ParameterVisibility.user
                            elif attribute.access & ZCLAttributeAccess.Write:
                                visibility = ParameterVisibility.setting

                        if attribute.access & ZCLAttributeAccess.Read:
                            raw = attr_values.get(attribute_id)
                            if raw is not None:
                                value = attribute.type(raw).serialize()

                        data_type = self._mapper.parse_zigbee_data_type(attribute.zcl_type)
                        min_value = None
                        max_value = None
                        valid_values = None
                        min_step = get_min_step(cluster.cluster_id, attribute_id)
                        unit = get_unit(cluster.cluster_id, attribute_id)

                        if hasattr(attribute.type, "min_value"):
                            min_value = attribute.type.min_value
                        if hasattr(attribute.type, "max_value"):
                            max_value = attribute.type.max_value

                        if issubclass(attribute.type, Enum) and data_type != ParameterDataType.bool:
                            valid_values = {
                                member.name: str(member.value) for member in attribute.type
                            }

                        parameters.append(
                            ZBParameterState(
                                id=self._mapper.create_uuid_id(
                                    f"{device.id.__str__()}attribute_{endpoint.endpoint_id}/{cluster.cluster_id}/{attribute_id}"
                                ),
                                name=attribute.name,
                                data_type=data_type,
                                visibility=visibility,
                                min_value=min_value,
                                max_value=max_value,
                                min_step=min_step,
                                unit=unit,
                                valid_values=valid_values,
                                role=self._mapper.parse_zigbee_attribute_access(attribute.access),
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
                        visibility = ParameterVisibility.user

                        if cluster.cluster_id in SYSTEM_CLUSTERS:
                            visibility = ParameterVisibility.system

                        for i, field in enumerate(command.schema.fields):
                            min_value = None
                            max_value = None
                            valid_values = None

                            if hasattr(field.type, "min_value"):
                                min_value = field.type.min_value
                            if hasattr(field.type, "max_value"):
                                max_value = field.type.max_value
                            if isinstance(field.type, type) and issubclass(field.type, Enum):
                                valid_values = {
                                    member.name: str(member.value) for member in field.type
                                }

                            fields.append(
                                Parameter(
                                    id=self._mapper.create_uuid_id(
                                        f"{device.id.__str__()}_field_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}/{i}"
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
                                id=self._mapper.create_uuid_id(
                                    f"{device.id.__str__()}_command_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}"
                                ),
                                name=command.name,
                                data_type=ParameterDataType.none,
                                role=ParameterRole.control,
                                fields=json.loads(
                                    json.dumps([f.model_dump(mode="json") for f in fields])
                                )
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
            device.main_parameter = self._get_device_main_parameter(device.id, zbdevice)
            log.debug(
                f"[PAIR] mapped schema {_zb_path(device, zbdevice)}\n\t"
                + "\n\t".join(
                    f"  {p.role.value:8} {p.visibility.value:8} {p.data_type.value:10} {p.name}  id={p.id}"
                    for p in parameters
                ),
            )
            await device_repository.save(device, discovery.id)
            await self.dependencies.output.controller_did_connect_device(self, discovery.id)

    async def unpair(self, device: ZBDevice):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        log.debug("[UNPAIR] ieee=%s", device.integration_data.ieee)
        await self._application.remove(
            self._mapper.convert_str_to_eui64(device.integration_data.ieee)
        )
        self._connected_devices.pop(self._mapper.create_uuid_id(device.integration_data.ieee))

    # ZigBee Listener:

    def device_joined(self, device: ZPDevice):
        """
        Called only after the device is joined to ZigBee network(after start_pairing_window).
        """
        log.debug("[JOIN] ieee=%s  nwk=0x%04X", device.ieee, device.nwk)
        discovery_id = self._mapper.create_uuid_id(self._mapper.convert_eui64_to_str(device.ieee))
        # Zigbee doesn't have discovery. All devices are connected to the network automatically after opening the network.
        # We keep them in the waiting list until they are paired in majordom, then we move them to the connected list.
        # If they are not paired within 5 minutes, we disconnect them from the network.
        self._create_task(self._disconnect_unpaired_discovery(discovery_id, device.ieee))

    def device_initialized(self, device: ZPDevice):
        """
        Called only after the device is fully initialized in a ZigBee network.
        """
        log.debug(
            "[INIT] ieee=%s  nwk=0x%04X  model=%r  manufacturer=%r",
            device.ieee,
            device.nwk,
            device.model,
            device.manufacturer,
        )
        discovery_id = self._mapper.create_uuid_id(self._mapper.convert_eui64_to_str(device.ieee))
        if discovery_id in self.discoveries:
            self._create_task(self._subscribe(discovery_id, device))
            return
        discovery = Discovery(
            id=discovery_id,
            integration=NonEmptyStr(self.name),
            credentials=CredentialsType.none,
            expiration=None,
            transport=NonEmptyStr("ZIGBEE"),
            device_manufacturer=None,
            device_name=NonEmptyStr(device.name),
            device_category=None,
            device_icon=None,
        )

        self._majordom_discoveries[discovery_id] = discovery
        self._awaiting_zb_discoveries[discovery_id] = device
        log.debug("[DISCOVERY] ieee=%s  discovery_id=%s", device.ieee, discovery_id)
        self._create_task(
            self.dependencies.output.controller_did_receive_discovery(self, discovery)
        )

        # TODO: listen for "left", "disconnected", "stopped", etc

    # Private:

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
    ) -> dict[int, object]:
        attr_values: dict[int, object] = {}
        chunks = [
            ids[i : i + _MAX_ATTRS_PER_REQUEST] for i in range(0, len(ids), _MAX_ATTRS_PER_REQUEST)
        ]
        for chunk in chunks:
            try:
                values, failures = await cluster.read_attributes(chunk, only_cache=only_cache)
            except Exception as e:
                log.error(
                    f"{log_prefix} read_attributes error {_zb_path(device, zbdevice, endpoint, cluster, attr_ids=chunk, error=e)}"
                )
                continue
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
        if (
            discovery_id not in self._majordom_discoveries
            and discovery_id in self._connected_devices
        ):
            # if the device was connected
            return
        await self._application.remove(ieee)
        self._majordom_discoveries.pop(discovery_id)
        self._awaiting_zb_discoveries.pop(discovery_id)

    def _get_device_main_parameter(self, device_id: UUID, zbdevice: ZPDevice) -> UUID | None:
        for endpoint in zbdevice.non_zdo_endpoints:
            if endpoint.in_clusters.get(0x0006):  # OnOff
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/6/2"
                )
            if endpoint.in_clusters.get(0x0008):  # LevelControl
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/8/0"
                )
            if endpoint.in_clusters.get(0x0300):  # ColorControl
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/300/7"
                )
            if endpoint.in_clusters.get(0x0102):  # WindowCovering
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/102/8"
                )
            if endpoint.in_clusters.get(0x0201):  # Termostat
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/201/18"
                )
            if endpoint.in_clusters.get(0x0202):  # FanContorl
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/202/0"
                )
            if endpoint.in_clusters.get(0x0101):  # LockState
                return self._mapper.create_uuid_id(
                    f"{device_id}_command_{endpoint.endpoint_id}/101/0"
                )

        return None
