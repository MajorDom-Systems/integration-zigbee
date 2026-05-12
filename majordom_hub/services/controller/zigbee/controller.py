import asyncio
import json
import logging

from enum import Enum
from typing import Type, override
from uuid import UUID
from zigpy.config import CONF_DATABASE, CONF_DEVICE, CONF_DEVICE_PATH
from zigpy.device import Device as ZPDevice, Cluster  # ZP - ZigPy
from zigpy.types import EUI64
from zigpy.zcl.clusters.general import Identify
from zigpy.zcl.foundation import ZCLAttributeAccess
from zigpy_znp.zigbee.application import ControllerApplication

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.device import CredentialsType, CredentialsValue, Discovery, NonEmptyStr
from majordom_hub.schemas.command import DeviceCommand
from majordom_hub.schemas.parameter import ParameterDataType, ParameterRole, ParameterVisibility
from majordom_hub.services.controller.framework.abstract_controller import AbstractController

from .exceptions import ZBConnectionError, ZBUnexpectedError
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


class ZigBeeController(AbstractController):
    _zigbee_device_path: str
    _zigbe_db: str
    # _application: ControllerApplication
    _majordom_discoveries: dict[UUID, Discovery] = dict()  # MJ discovery metadata
    _awaiting_zb_discoveries: dict[UUID, ZPDevice] = dict()  # connected to zigbee network but not set up in majordom yet. We can use less RAM if we store only IEEE data instead of the entire device. Should I add this?
    _connected_devices: dict[UUID, ZPDevice] = dict()  # fully connected. We can use less RAM if we store only IEEE data instead of the entire device. Should I add this?

    _mapper = ZigBeeMapper()

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
        config = {CONF_DEVICE: {CONF_DEVICE_PATH: self._zigbee_device_path}, CONF_DATABASE: self._zigbe_db}

        # Starting zigbee stack
        self._application = await ControllerApplication.new(config=config, auto_form=True)
        self._application.add_listener(self)


        async with self.dependencies.make_device_repository() as device_repo:
            # Subscribe to attribute updates and add the device to _connected_devices 
            # if the Zigbee device is in the majordom database, otherwise start a discovery cycle.
            for zbdevice in self._application.devices.values():
                if zbdevice.nwk == 0x0000:
                    continue
                device_id = self._mapper.create_uuid_id(self._mapper.convert_eui64_to_str(zbdevice.ieee))
                if await device_repo.get(device_id, ZBDevice):
                    self._connected_devices[device_id] = zbdevice
                    await self._subscribe(device_id, zbdevice)
                else:
                    asyncio.create_task(self._disconnect_unpaired_discovery(device_id, zbdevice.ieee))
                    await zbdevice.initialize()

            # Checking if all devices in our system are still connected to ZigBee
            for device in await device_repo.get_all(self.name, ZBDevice):
                ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)
                if self._application.get_device(ieee):
                    continue
                # TODO: Remove device from majordom database.
                # TODO: Send an error message because the device was deleted from the ZigBee database.

    async def stop(self):
        if self._application:
            await self._application.shutdown()
        self._majordom_discoveries.clear()
        self._awaiting_zb_discoveries.clear()
        self._connected_devices.clear()

    async def start_pairing_window(self, seconds: int) -> None:
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        await self._application.permit(seconds)

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
                for attribute_id in cluster.attributes.keys():
                    values, failures = await cluster.read_attributes([attribute_id])
                    if failures:
                        value = None
                        logging.error(failures)
                    else:
                        value = values.get(attribute_id)
                    events.append(
                        DeviceParameterChangedEvent(
                            device_id=device.id,
                            parameter_id=self._mapper.create_uuid_id(f"{device.id.__str__()}_attribute_{endpoint_id}/{cluster_id}/{attribute_id}"),
                            value=value,
                        )
                    )
                for command_id in cluster.commands.keys():
                    events.append(
                        DeviceParameterChangedEvent(
                            device_id=device.id,
                            parameter_id=self._mapper.create_uuid_id(f"{device.id.__str__()}_command_{endpoint_id}/{cluster_id}/{command_id}"),
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
            r = await cluster.write_attributes({parameter.integration_data.attribute_id: command.value})
            logging.info(r)
        else:
            zbcommand = cluster.commands_by_name.get(parameter.name)
            if not zbcommand:
                raise ZBUnexpectedError(f"Command {parameter.name} not found in cluster")
            await cluster.command(zbcommand.id, command.value) if command.value else await cluster.command(zbcommand.id)

    async def pair_device(self, discovery: Discovery, credentials: CredentialsValue | None):  # Break down into submethods
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
            for endpoint in zbdevice.non_zdo_endpoints:
                for cluster in endpoint.clusters:
                    for attribute_id, attribute in cluster.attributes.items():
                        value = b""

                        visibility = ParameterVisibility.system
                        if attribute_id < 0xF000 or cluster.cluster_id not in SYSTEM_CLUSTERS:  # next manufacturer specifik and global/system attributes
                            if attribute.access & ZCLAttributeAccess.Report:
                                visibility = ParameterVisibility.user
                            elif attribute.access & ZCLAttributeAccess.Write:
                                visibility = ParameterVisibility.setting

                        if attribute.access & ZCLAttributeAccess.Read:
                            values, failures = await cluster.read_attributes([attribute_id])
                            if failures:
                                logging.error(failures)
                            else:
                                temp = values.get(attribute_id)
                                if temp is not None:
                                    value = attribute.type(temp).serialize()

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
                            valid_values = {member.name: str(member.value) for member in attribute.type}

                        parameters.append(
                            ZBParameterState(
                                id=self._mapper.create_uuid_id(f"{device.id.__str__()}attribute_{endpoint.endpoint_id}/{cluster.cluster_id}/{attribute_id}"),
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
                                valid_values = {member.name: str(member.value) for member in field.type}

                            fields.append(
                                Parameter(
                                    id=self._mapper.create_uuid_id(f"{device.id.__str__()}_field_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}/{i}"),
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
                                id=self._mapper.create_uuid_id(f"{device.id.__str__()}_command_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}"),
                                name=command.name,
                                data_type=ParameterDataType.none,
                                role=ParameterRole.control,
                                fields=json.loads(json.dumps([f.model_dump(mode="json") for f in fields])) if fields else None,
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
            await device_repository.save(device, discovery.id)
            await self.dependencies.output.controller_did_connect_device(self, discovery.id)

    async def unpair(self, device: ZBDevice):
        if not self._application:
            raise ZBConnectionError("ZigBee application is not started")
        await self._application.remove(self._mapper.convert_str_to_eui64(device.integration_data.ieee))
        self._connected_devices.pop(self._mapper.create_uuid_id(device.integration_data.ieee))

    # ZigBee Listener:

    def device_joined(self, device: ZPDevice):
        """
        Called only after the device is joined to ZigBee network(after start_pairing_window).
        """
        discovery_id = self._mapper.create_uuid_id(self._mapper.convert_eui64_to_str(device.ieee))
        # Zigbee doesn't have discovery. All devices are connected to the network automatically after opening the network.
        # We keep them in the waiting list until they are paired in majordom, then we move them to the connected list.
        # If they are not paired within 5 minutes, we disconnect them from the network.
        asyncio.create_task(self._disconnect_unpaired_discovery(discovery_id, device.ieee))

    def device_initialized(self, device: ZPDevice):
        """
        Called only after the device is fully initialized in a ZigBee network.
        """
        discovery_id = self._mapper.create_uuid_id(self._mapper.convert_eui64_to_str(device.ieee))
        if discovery_id in self.discoveries:
            asyncio.create_task(self._subscribe(discovery_id, device))
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

        asyncio.create_task(self.dependencies.output.controller_did_receive_discovery(self, discovery))

        # TODO: listen for "left", "disconnected", "stopped", etc


    # Private:

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

    def _get_device_main_parameter(self, device_id: UUID, zbdevice: ZPDevice) -> UUID | None:
        for endpoint in zbdevice.non_zdo_endpoints:
            if endpoint.in_clusters.get(0x0006):  # OnOff
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/6/2")
            if endpoint.in_clusters.get(0x0008):  # LevelControl
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/8/0")
            if endpoint.in_clusters.get(0x0300):  # ColorControl
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/300/7")
            if endpoint.in_clusters.get(0x0102):  # WindowCovering
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/102/8")
            if endpoint.in_clusters.get(0x0201):  # Termostat
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/201/18")
            if endpoint.in_clusters.get(0x0202):  # FanContorl
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/202/0")
            if endpoint.in_clusters.get(0x0101):  # LockState
                return self._mapper.create_uuid_id(f"{device_id}_command_{endpoint.endpoint_id}/101/0")

        return None
