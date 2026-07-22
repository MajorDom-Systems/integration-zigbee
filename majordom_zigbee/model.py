from enum import StrEnum

from majordom_integration_sdk.schemas.base import Base
from majordom_integration_sdk.schemas.device import Device, DeviceState, Parameter, ParameterState
from pydantic import BaseModel


class ZBParameterType(StrEnum):
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
    # carries *what to send*. send_command applies it when a command arrives with no value (i.e. the
    # user tapped the main parameter). Mirrors Matter's default_arguments.
    default_arguments: dict | None = None
    # For an *attribute* main parameter (enum), the ordered subset of values a one-tap cycles
    # through (e.g. [off, on] for a fan). None -> the send path cycles the param's full valid_values.
    main_cycle: list[int] | None = None


class ZBDevice(Device):
    integration_data: ZBDeviceIntegrationData


class ZBParameter(Parameter):
    integration_data: ZBParameterIntegrationData


class ZBParameterState(ParameterState):
    integration_data: ZBParameterIntegrationData


class ZBDeviceState(ZBDevice, DeviceState):
    parameters: list[ZBParameterState]
