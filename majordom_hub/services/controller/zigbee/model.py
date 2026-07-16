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
    # Args to send when this command is the device's one-tap main parameter and needs them (e.g. a
    # brightness level). A command parameter's data_type is `none`, which already satisfies
    # ParameterState.can_be_main_parameter, so no `default_value` is needed for the flag — this only
    # carries *what to send*. NOTE: nothing in the hub reads this yet (the app reads the top-level
    # `default_value`, not integration_data); it's dead until the app consumes it or the design
    # collapses onto `default_value`. Mirrors Matter's default_arguments.
    default_arguments: dict | None = None


class ZBDevice(Device):
    integration_data: ZBDeviceIntegrationData


class ZBParameter(Parameter):
    integration_data: ZBParameterIntegrationData


class ZBParameterState(ParameterState):
    integration_data: ZBParameterIntegrationData


class ZBDeviceState(ZBDevice, DeviceState):
    parameters: list[ZBParameterState]
