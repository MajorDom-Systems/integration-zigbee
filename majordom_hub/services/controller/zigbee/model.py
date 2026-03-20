from pydantic import BaseModel
from enum import Enum

from majordom_hub.schemas.device import Device, DeviceState, Parameter, ParameterState
from majordom_hub.schemas.base import Base


class ZBParameterType(str, Enum):
    attribute = "attribute"
    command = "command"


class ZBDeviceIntegrationData(Base):
    ieee: str | None = None


class ZBParameterIntegrationData(BaseModel):
    endpoint_id: int
    cluster_id: int
    attribute_id: int | None = None
    command_id: int | None = None
    type: ZBParameterType


class ZBDevice(Device):
    integration_data: ZBDeviceIntegrationData


class ZBParameter(Parameter):
    integration_data: ZBParameterIntegrationData


class ZBParameterState(ParameterState):
    integration_data: ZBParameterIntegrationData


class ZBDeviceState(ZBDevice, DeviceState):
    parameters: list[ZBParameterState]
