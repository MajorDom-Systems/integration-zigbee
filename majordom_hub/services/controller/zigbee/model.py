from uuid import UUID

from majordom_hub.schemas.device import Device, Parameter

class ZBDeviceIntegrationData():
    pass


class ZBDevice(Device):
    integration_data: ZBDeviceIntegrationData

class ZBParameter(Parameter):
    pass