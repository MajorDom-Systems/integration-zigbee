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
    # brightness level), keyed by field name; a Hub-sent `struct` value is keyed by sub-parameter id,
    # and send_command accepts both. This only carries *what to send*. send_command applies it when a
    # command arrives with no value (i.e. the user tapped the main parameter). Mirrors Matter's
    # default_arguments.
    default_arguments: dict | None = None


class ZBDevice(Device):
    integration_data: ZBDeviceIntegrationData


class ZBParameter(Parameter):
    integration_data: ZBParameterIntegrationData


class ZBParameterState(ParameterState):
    integration_data: ZBParameterIntegrationData


class ZBDeviceState(ZBDevice, DeviceState):
    parameters: list[ZBParameterState]
