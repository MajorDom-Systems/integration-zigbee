import asyncio
import json

from enum import Enum
from uuid import UUID
from typing import Type, override
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH, CONF_DATABASE
from zigpy.device import Device as ZPDevice  # ZP - ZigPy
from zigpy.zcl.clusters.general import Identify
from zigpy.zcl.foundation import ZCLAttributeAccess
from zigpy_znp.zigbee.application import ControllerApplication

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.device import  Discovery
from majordom_hub.schemas.parameter import ParameterRole, ParameterDataType, ParameterVisibility
from majordom_hub.services.controller.framework.abstract_controller import AbstractController

from .mapper import ZigBeeMapper
from .model import (
    Parameter,
    ZBDevice,
    ZBDeviceIntegrationData,
    ZBDeviceState,
    ZBParameter,
    ZBParameterIntegrationData,
    ZBParameterState,
    ZBParameterType
)
from .listener import ZigBeeListener
from .zigbee_spec import SYSTEM_CLUSTERS, get_unit, get_min_step


class ZigBeeController(AbstractController):
    _zigbee_device_path: str
    _zigbe_db: str
    # _application: ControllerApplication
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
        self._zigbee_device_path = "/dev/ttyACM0"
        self._zigbe_db = "/home/zigbee/zigbee.db"
        config = {
            CONF_DEVICE: {
                CONF_DEVICE_PATH: self._zigbee_device_path
            },
            CONF_DATABASE: self._zigbe_db
        }
        self._application = await ControllerApplication.new(config=config, auto_form=True)
        listener = ZigBeeListener(self)
        self._application.add_listener(listener)
        for device_id, zbdevice in self._connected_devices.items():
            async with self.dependencies.make_device_repository() as device_repo:
                if not device_repo.get(device_id, ZBDevice):
                    continue
            await self._subscribe(device_id, zbdevice)

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
        for endpoint in zbdevice.non_zdo_endpoints:
            for cluster_id, cluster in endpoint.in_clusters.items():
                for attribute_id in cluster.attributes.keys():
                    values, failures = await cluster.read_attributes([attribute_id])
                    if failures:
                        value = None
                        print(failures)
                    else:
                        value = values.get(attribute_id)
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

        for endpoint in zbdevice.non_zdo_endpoints:
            cluster = endpoint.in_clusters.get(Identify.cluster_id)
            if not cluster:
                continue
            await cluster.identify(10)  # 10 - identification time

    async def send_command(self, command,  device: ZBDevice, parameter: ZBParameter):
        if not self._application:
            raise ValueError()
        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)
        if not (zbdevice := self._application.devices.get(ieee)):
            raise ValueError()
        if not (endpoint := zbdevice.endpoints.get(parameter.integration_data.endpoint_id)):
            raise ValueError()
        if not (cluster := endpoint.in_clusters.get(parameter.integration_data.cluster_id)):
            raise ValueError()
        # cluster = zbdevice.find_cluster(parameter.integration_data.cluster_id)
        if parameter.integration_data.type is ZBParameterType.attribute:
            if parameter.role != ParameterRole.control:
                raise ValueError()
            print(parameter.name, command.value)
            r = await cluster.write_attributes({parameter.integration_data.attribute_id: command.value})
            print(r)
        else:
            zbcommand = cluster.commands_by_name.get(parameter.name)
            if not zbcommand:
                raise ValueError()
            await cluster.command(zbcommand.id, command.value) if command.value else await cluster.command(zbcommand.id)

    async def pair_device(self, discovery: Discovery, credentials):
        async with self.dependencies.make_device_repository() as device_repository:
            device = await device_repository.state(discovery.id, ZBDeviceState)
            zbdevice = self._connected_devices.get(discovery.id)
            self._majordom_discoveries.pop(discovery.id)
            assert device
            assert zbdevice

            if not device.integration_data.ieee:
                device.integration_data = ZBDeviceIntegrationData(ieee=self._mapper.convert_eui64_to_str(zbdevice.ieee))
            parameters: list[ZBParameterState] = list()
            for endpoint in zbdevice.non_zdo_endpoints:
                for cluster in endpoint.clusters:
                    if cluster.cluster_id in SYSTEM_CLUSTERS:  # skip system clusters
                        continue
                    for attribute_id, attribute in cluster.attributes.items():
                        value = b''
                        
                        visibility = ParameterVisibility.system
                        if attribute_id < 0xF000:  # next manufacturer specifik and global/system attributes
                            if attribute.access & ZCLAttributeAccess.Report:
                                visibility = ParameterVisibility.user
                            elif attribute.access & ZCLAttributeAccess.Write:
                                visibility = ParameterVisibility.setting
                        
                        if attribute.access & ZCLAttributeAccess.Read:
                            values, failures = await cluster.read_attributes([attribute_id])
                            if failures:
                                print(failures)
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

                        parameters.append(ZBParameterState(
                            id=self._mapper.create_uuid_id(f"attribute_{endpoint.endpoint_id}/{cluster.cluster_id}/{attribute_id}"),
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
                        ))
                    for command in cluster.commands:
                        fields: list[Parameter] = []
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
                            
                            fields.append(Parameter(
                                id=self._mapper.create_uuid_id(
                                    f"field_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}/{i}"
                                ),
                                name=field.name,
                                data_type=self._mapper.parse_zigbee_data_type(field.type),
                                role=ParameterRole.control,
                                visibility=ParameterVisibility.setting,
                                min_value=min_value,
                                max_value=max_value,
                                valid_values=valid_values,
                                integration_data=None,
                            ))
                        parameters.append(ZBParameterState(
                            id=self._mapper.create_uuid_id(f"command_{endpoint.endpoint_id}/{cluster.cluster_id}/{command.id}"),
                            name=command.name,
                            data_type=ParameterDataType.none,
                            role=ParameterRole.control,
                            fields=json.loads(json.dumps([f.model_dump(mode='json') for f in fields])) if fields else None,
                            visibility=ParameterVisibility.setting,
                            integration_data=ZBParameterIntegrationData(
                                endpoint_id=endpoint.endpoint_id,
                                cluster_id=cluster.cluster_id,
                                command_id=command.id,
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
        for endpoint in device.non_zdo_endpoints:    
            for cluster in endpoint.in_clusters.values():
                listener = ZigBeeListener(self, device_id, cluster)
                cluster.add_listener(listener)

    async def _remove_discovery(self, discovery_id, ieee):
        await asyncio.sleep(300)  # 5 minutes
        if discovery_id not in self._majordom_discoveries:
            return
        await self._application.remove(ieee)
        self._majordom_discoveries.pop(discovery_id)
        self._connected_devices.pop(discovery_id)
        