import asyncio

from uuid import UUID
from typing import Type, override
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH, CONF_DATABASE
from zigpy.device import Device as ZPDevice  # ZP - ZigPy
from zigpy.zcl.clusters.general import Identify
from zigpy_znp.zigbee.application import ControllerApplication

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.device import  Discovery
from majordom_hub.schemas.parameter import ParameterRole, ParameterDataType
from majordom_hub.services.controller.framework.abstract_controller import AbstractController

from .mapper import ZigBeeMapper
from .model import ZBDevice, ZBDeviceIntegrationData, ZBDeviceState, ZBParameter, ZBParameterIntegrationData, ZBParameterState, ZBParameterType
from .listener import ZigBeeListener


class ZigBeeController(AbstractController):
    _zigbee_device_path: str
    _zigbe_db: str
    _application: ControllerApplication
    _majordom_discoveries: dict[UUID, Discovery] = dict()
    _connected_devices: dict[UUID, ZPDevice] = dict()

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
        config = {
            CONF_DEVICE: {
                CONF_DEVICE_PATH: self._zigbee_device_path
            },
            CONF_DATABASE: self._zigbe_db
        }
        self._application = ControllerApplication(config)
        await self._application.startup(auto_form=True)
        listener = ZigBeeListener(self)
        self._application.add_listener(listener)
        for device in self._application.devices.values():
            if device.is_initialized:
                listener.device_initialized(device)

    async def stop(self):
        if self._application:
            await self._application.shutdown()

    async def open_network(self, secs: int) -> None:
        if not self._application:
            raise ValueError()
        await self._application.permit(secs)

    async def fetch(self, device: ZBDevice) -> None:
        if not self._application:
            raise ValueError()

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ValueError()
        if not zbdevice.is_initialized:
            raise ValueError()
        events: list[DeviceParameterChangedEvent] = list()
        for endpoint_id, endpoint in zbdevice.endpoints.items():
            if endpoint_id is 0:  # zdo endpoint
                continue
            for cluster_id, cluster in endpoint.in_clusters.items():
                for attribute_id in cluster.attributes.keys():
                    value = await cluster.read_attributes([attribute_id])
                    events.append(DeviceParameterChangedEvent(
                        device_id=device.id,
                        parameter_id=self._mapper.create_uuid_id(f"attribute_{endpoint_id}/{cluster_id}/{attribute_id}"),
                        value=value
                    ))
                for command_id in cluster.commands.keys():
                    events.append(DeviceParameterChangedEvent(
                        device_id=device.id,
                        parameter_id=self._mapper.create_uuid_id(f"command_{endpoint_id}/{cluster_id}/{command_id}"),
                        value=None
                    ))

        await self.dependencies.output.controller_did_receive_device_events(self, events)

    async def identify(self, device: ZBDevice):
        if not self._application:
            raise ValueError()

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zbdevice := self._application.devices.get(ieee)):
            raise ValueError()
        if not zbdevice.is_initialized:
            raise ValueError()

        for endpoint_id, endpoint in zbdevice.endpoints.items():
            if endpoint_id == 0:  # zdo endpoint
                continue
            cluster = endpoint.in_clusters.get(Identify.cluster_id)
            if not cluster:
                continue
            await cluster.identify(10)  # 10 - identification time

    async def send_command(self, command, device: ZBDevice, parameter: ZBParameter):
        if not self._application:
            raise ValueError()
        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)
        if not (zbdevice := self._application.devices.get(ieee)):
            raise ValueError()
        if not (endpoint := zbdevice.endpoints.get(parameter.integration_data.endpoint_id)):
            raise ValueError()
        if not (cluster := endpoint.in_clusters.get(parameter.integration_data.cluster_id)):
            raise ValueError()
        if parameter.integration_data.type is ZBParameterType.attribute:
            if parameter.role != ParameterRole.control:
                raise ValueError()
            await cluster.write_attributes({parameter.integration_data.attribute_id: command.value})
        else:
            if not (zbcommand := getattr(cluster, parameter.name, None)):
                raise ValueError()
            await zbcommand(command.value)

    async def pair_device(self, discovery: Discovery, credentials):
        async with self.dependencies.make_device_repository() as device_repository:
            device = await device_repository.state(discovery.id, ZBDeviceState)
            zbdevice = self._connected_devices.get(discovery.id)
            self._majordom_discoveries.pop(discovery.id)
            assert device
            assert zbdevice

            if not device.integration_data:
                device.integration_data = ZBDeviceIntegrationData(ieee=str(zbdevice.ieee))
            parameters: list[ZBParameterState] = list()
            for endpoint_id, endpoint in zbdevice.endpoints.items():
                for cluster_id, cluster in endpoint.in_clusters.items():
                    discovered = await cluster.discover_attributes_extended()
                    for attribute_id, data_type, access in discovered:
                        value = await cluster.read_attributes([attribute_id])
                        attribute = await cluster.attributes.get(attribute_id)
                        parameters.append(ZBParameterState(
                            id=self._mapper.create_uuid_id(f"attribute_{endpoint_id}/{cluster_id}/{attribute_id}"),
                            name=attribute.name,
                            data_type=self._mapper.parse_zigbee_data_type(data_type),
                            role=self._mapper.parse_zigbee_attribute_access(access),
                            integration_data=ZBParameterIntegrationData(
                                endpoint_id=endpoint_id,
                                cluster_id=cluster_id,
                                attribute_id=attribute_id,
                                type=ZBParameterType.attribute,
                            ),
                        value=bytes(value),
                        ))
                    for command_id, command in cluster.commands.items():
                        parameters.append(ZBParameterState(
                            id=self._mapper.create_uuid_id(f"command_{endpoint_id}/{cluster_id}/{command_id}"),
                            name=command.name,
                            data_type=ParameterDataType.none,
                            role=ParameterRole.event,
                            integration_data=ZBParameterIntegrationData(
                                endpoint_id=endpoint_id,
                                cluster_id=cluster_id,
                                command_id=command_id,
                                type=ZBParameterType.command,
                            ),
                            value=b''
                        ))
            device.parameters = parameters
            await device_repository.save(device, discovery.id)
            await self.dependencies.output.controller_did_connect_device(self, discovery.id)

    async def unpair(self, device: ZBDevice):
        if not self._application:
            raise ValueError()
        await self._application.remove(self._mapper.convert_str_to_eui64(device.integration_data.ieee))
        self._connected_devices.pop(self._mapper.create_uuid_id(device.integration_data.ieee))

    async def _subscribe(self, device_id, device: ZPDevice):
        for endpoint_id, endpoint in device.endpoints.items():
            if endpoint_id == 0:  # zdo endpoint
                continue
            
            for cluster in endpoint.in_clusters.values():
                listener = ZigBeeListener(self, device_id, cluster)
                cluster.add_listener(listener)
