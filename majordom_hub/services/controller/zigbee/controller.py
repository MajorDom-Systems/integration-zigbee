from zigpy_znp.zigbee.application import ControllerApplication
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH, CONF_DATABASE

from typing import Type, override

from majordom_hub.services.controller.framework.abstract_controller import AbstractController

from .mapper import ZigBeeMapper
from .model import ZBDevice, ZBParameter


class ZigBeeController(AbstractController):
    _zigbee_device_path: str
    _zigbe_db: str
    _application: ControllerApplication
    _majordom_discoveries: dict

    _mapper = ZigBeeMapper()


    @property
    def name(self) -> str:
        return "ZigBee"

    @property
    def discoveries(self):
        return self._majordom_discoveries

    @property
    @override
    def device_type(self) -> Type[ZBDevice]:
        return ZBDevice

    @property
    @override
    def parameter_state(self) -> Type[ZBParameter]:
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

    async def stop(self):
        if self._application:
            await self._application.shutdown()

    async def identify(self, device: ZBDevice):
        if not self._application:
            raise ValueError()

        ieee = self._mapper.convert_str_to_eui64(device.integration_data.ieee)

        if not (zdevice := self._application.devices.get(ieee)):
            raise ValueError()
        if not zdevice.is_initialized:
            raise ValueError()

        for endpoint_id, endpoint in zdevice.endpoints.items():
            if endpoint_id == 0:  # zdo endpoint
                continue

            if hasattr(endpoint, "identify"):
                await endpoint.identify.identify(10)  # 10 - identification time
