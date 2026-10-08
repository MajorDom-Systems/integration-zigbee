"""Unit tests driving ZigBeeController directly against the in-memory zigpy stub."""

from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import conftest
import pytest
import zigpy.zcl
from conftest import DEVICE_ID, PARAM_ATTRIBUTE_ID, PARAM_COMMAND_ID
from majordom_integration_sdk.schemas.command import DeviceCommand
from majordom_integration_sdk.schemas.parameter import Parameter, ParameterDataType
from zigpy.zcl.clusters.closures import DoorLock, WindowCovering
from zigpy.zcl.clusters.general import LevelControl
from zigpy.zcl.clusters.hvac import Fan
from zigpy.zcl.clusters.lighting import Color

from majordom_zigbee.model import ZBParameter
from majordom_zigbee.zigbee_spec import MAIN_PARAMETER_BY_CLUSTER


async def _wait_for(predicate, timeout: float = 3.0):
    import asyncio

    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_discovers_joining_device(zigbee):
    controller, output, _repo, _app = zigbee
    await controller.start_pairing_window(5)  # permit-join -> the stub simulates a device joining

    await _wait_for(lambda: bool(output.received_discoveries))
    discovery = output.received_discoveries[-1]
    assert str(discovery.id) == DEVICE_ID
    assert discovery.integration == "ZigBee"
    assert UUID(DEVICE_ID) in controller.discoveries


async def _discover(controller, output):
    await controller.start_pairing_window(5)
    await _wait_for(lambda: bool(output.received_discoveries))
    return output.received_discoveries[-1]


async def _seed_provisional(repository, discovery):
    """The Hub creates the device row before calling pair_device (persistence is the Hub's job)."""
    from majordom_zigbee.model import ZBDeviceIntegrationData, ZBDeviceState

    async with repository.session() as repo:
        await repo.save(
            ZBDeviceState(
                id=discovery.id,
                name="Mock Device",
                room_id=UUID(int=1),
                transport=discovery.transport,
                integration="ZigBee",
                manufacturer=None,
                parameters=[],
                integration_data=ZBDeviceIntegrationData(),
            )
        )


async def test_pairs_a_joined_device(zigbee):
    controller, output, repository, _app = zigbee
    from majordom_zigbee.model import ZBDevice

    discovery = await _discover(controller, output)
    await _seed_provisional(repository, discovery)

    await controller.pair_device(discovery, None)

    # the discovery is consumed and the device is now tracked as connected
    assert discovery.id not in controller.discoveries
    assert discovery.id in controller._connected_devices
    # persistence: the controller mapped the device's clusters into parameters
    async with repository.session() as repo:
        device = await repo.get(discovery.id, as_=ZBDevice)
        state = await repo.state(discovery.id)
    assert device is not None and device.integration_data.ieee
    assert state is not None and len(state.parameters) > 0


async def _pair(zigbee):
    controller, output, repository, app = zigbee
    discovery = await _discover(controller, output)
    await _seed_provisional(repository, discovery)
    await controller.pair_device(discovery, None)
    return discovery


async def _param(repository, device_id, parameter_id):
    from majordom_zigbee.model import ZBDeviceState, ZBParameter

    async with repository.session() as repo:
        state = await repo.state(device_id, as_=ZBDeviceState)
    param = next(p for p in state.parameters if str(p.id) == parameter_id)
    return ZBParameter.model_validate(param.model_dump())


async def _device(repository, device_id):
    from majordom_zigbee.model import ZBDevice

    async with repository.session() as repo:
        return await repo.get(device_id, as_=ZBDevice)


async def test_sends_a_command_parameter(zigbee):
    from majordom_integration_sdk.schemas.command import DeviceCommand

    controller, output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)
    parameter = await _param(repository, discovery.id, PARAM_COMMAND_ID)

    # a ZCL command parameter (toggle) — no exception means the command reached the cluster
    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(PARAM_COMMAND_ID), value=None), device, parameter
    )


async def test_sends_an_attribute_write(zigbee):
    from majordom_integration_sdk.schemas.command import DeviceCommand

    controller, output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)
    parameter = await _param(repository, discovery.id, PARAM_ATTRIBUTE_ID)

    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(PARAM_ATTRIBUTE_ID), value=0), device, parameter
    )


async def test_unpairs_a_device(zigbee):
    controller, output, repository, app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)

    await controller.unpair(device)
    # the device is gone from the zigpy network
    from conftest import MOCK_IEEE

    assert MOCK_IEEE not in app.devices


def _move_ids(controller) -> tuple[str, list[str]]:
    """LevelControl.move's parameter id and its field (sub-parameter) ids on the mock device's endpoint 1."""
    device_id = UUID(DEVICE_ID)
    mapper = controller._mapper
    move = LevelControl.ServerCommandDefs.move
    return str(mapper.command_parameter_uuid(device_id, 1, LevelControl.cluster_id, move.id)), [
        str(mapper.command_field_uuid(device_id, 1, LevelControl.cluster_id, move.id, i))
        for i in range(len(move.schema.fields))
    ]


async def test_command_with_arguments_is_a_struct(zigbee):
    controller, _output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    param_id, field_ids = _move_ids(controller)
    parameter = await _param(repository, discovery.id, param_id)

    assert parameter.data_type is ParameterDataType.struct
    fields = {f["name"]: f for f in parameter.fields}
    assert [str(f["id"]) for f in parameter.fields] == field_ids
    assert list(fields) == ["move_mode", "rate", "options_mask", "options_override"]
    # keys are the values sent to the device, values are display labels
    move_mode = Parameter.model_validate(fields["move_mode"])
    assert move_mode.valid_values == {member.value: member.name for member in LevelControl.MoveMode}


async def test_argument_less_command_stays_none(zigbee):
    _controller, _output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    parameter = await _param(repository, discovery.id, PARAM_COMMAND_ID)  # OnOff.toggle

    assert parameter.data_type is ParameterDataType.none
    assert parameter.fields is None


async def _send_move(zigbee, value_by_field: dict[str, Any]) -> Any:
    controller, _output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)
    move = LevelControl.ServerCommandDefs.move
    param_id, _ = _move_ids(controller)
    parameter = await _param(repository, discovery.id, param_id)
    command_mock = cast(AsyncMock, zigpy.zcl.Cluster.command)
    command_mock.reset_mock()

    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(param_id), value=value_by_field), device, parameter
    )

    command_mock.assert_awaited_once()
    call = command_mock.await_args
    assert call is not None and call.args == (move.id,)
    # zigpy must accept the arguments as-is (it builds the wire struct from these kwargs)
    return move.schema(**call.kwargs)


async def test_struct_command_value_keyed_by_field_id_reaches_device(zigbee):
    controller, *_ = zigbee
    _, (move_mode, rate, options_mask, options_override) = _move_ids(controller)
    down = LevelControl.MoveMode.Down.value

    sent = await _send_move(zigbee, {move_mode: down, rate: 5, options_mask: 0, options_override: 0})

    assert sent.move_mode is LevelControl.MoveMode.Down
    assert sent.rate == 5


async def test_struct_command_value_keyed_by_field_name_still_works(zigbee):
    sent = await _send_move(zigbee, {"move_mode": 0, "rate": 7, "options_mask": 0, "options_override": 0})

    assert sent.move_mode is LevelControl.MoveMode.Up
    assert sent.rate == 7


@pytest.mark.parametrize(
    ("cluster", "command_id"),
    [(LevelControl, 0x04), (Color, 0x06), (WindowCovering, 0x05), (DoorLock, 0x00)],
    ids=lambda v: v.__name__ if isinstance(v, type) else str(v),
)
async def test_main_command_with_arguments_is_a_struct_button(zigbee, monkeypatch, cluster, command_id):
    monkeypatch.setattr(conftest, "MOCK_INPUT_CLUSTERS", [cluster.cluster_id])
    controller, _output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)
    zbcommand = cluster.server_commands[command_id]
    mapper = controller._mapper
    param_id = str(mapper.command_parameter_uuid(discovery.id, 1, cluster.cluster_id, command_id))
    names = {
        str(mapper.command_field_uuid(discovery.id, 1, cluster.cluster_id, command_id, i)): field.name
        for i, field in enumerate(zbcommand.schema.fields)
    }
    expected = MAIN_PARAMETER_BY_CLUSTER[cluster.cluster_id].default_arguments

    assert str(device.main_parameter) == param_id
    stored = await _param(repository, discovery.id, param_id)
    main = ZBParameter.model_validate(stored.model_dump(mode="json"))  # the stored round trip
    assert main.data_type is ParameterDataType.struct
    assert main.can_be_main_parameter
    # a single dict default_value is a one-value button keyed by sub-parameter id
    assert isinstance(main.default_value, dict)
    assert {names[key]: value for key, value in main.default_value.items()} == expected

    cycle = main.main_cycle
    assert cycle is not None and len(cycle) == 1
    command_mock = cast(AsyncMock, zigpy.zcl.Cluster.command)
    command_mock.reset_mock()
    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(param_id), value=cycle[0]), device, main
    )
    call = command_mock.await_args
    assert call is not None and call.args == (command_id,) and call.kwargs == expected
    zbcommand.schema(**call.kwargs)


async def test_attribute_enum_valid_values_map_value_to_label(zigbee, monkeypatch):
    monkeypatch.setattr(conftest, "MOCK_INPUT_CLUSTERS", [Fan.cluster_id])
    controller, _output, repository, _app = zigbee
    discovery = await _pair(zigbee)
    device = await _device(repository, discovery.id)
    fan_mode_id = Fan.AttributeDefs.fan_mode.id
    param_id = str(controller._mapper.attribute_parameter_uuid(discovery.id, 1, Fan.cluster_id, fan_mode_id))
    stored = await _param(repository, discovery.id, param_id)
    fan_mode = ZBParameter.model_validate(stored.model_dump(mode="json"))  # the stored round trip

    valid_values = fan_mode.valid_values
    assert valid_values is not None
    assert valid_values == {member.value: member.name for member in Fan.FanMode}
    # a reported value and the main off/on cycle both land on valid_values keys
    assert controller._mapper.normalize_zigbee_value(Fan.FanMode.On) in valid_values
    assert str(device.main_parameter) == param_id
    assert fan_mode.main_cycle == [Fan.FanMode.Off.value, Fan.FanMode.On.value]
    assert set(fan_mode.main_cycle or ()) <= set(valid_values)

    write_mock = cast(AsyncMock, zigpy.zcl.Cluster.write_attributes)
    write_mock.reset_mock()
    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(param_id), value=Fan.FanMode.On.value),
        device,
        fan_mode,
    )
    write_mock.assert_awaited_once_with({fan_mode_id: Fan.FanMode.On.value})
