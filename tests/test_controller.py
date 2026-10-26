"""Unit tests driving ZigBeeController directly against the in-memory zigpy stub."""

import asyncio
import threading
import time
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import conftest
import pytest
import pytest_asyncio
import zhaquirks
import zigpy.application
import zigpy.device
import zigpy.state as app_state
import zigpy.types as t
import zigpy.zcl
import zigpy.zdo.types as zdo_t
from conftest import DEVICE_ID, PARAM_ATTRIBUTE_ID, PARAM_COMMAND_ID, wait_for
from majordom_integration_sdk import Peripherals
from majordom_integration_sdk.runtime import PeripheralState, Pinned, Runtime, StatusKind
from majordom_integration_sdk.schemas.command import DeviceCommand
from majordom_integration_sdk.schemas.parameter import Parameter, ParameterDataType
from majordom_integration_sdk.testing import RecordingControllerOutput, build_test_platform
from zigpy.config import CONF_DEVICE, CONF_DEVICE_BAUDRATE, CONF_DEVICE_FLOW_CONTROL, CONF_DEVICE_PATH
from zigpy.zcl.clusters.closures import DoorLock, WindowCovering
from zigpy.zcl.clusters.general import LevelControl, OnOff
from zigpy.zcl.clusters.homeautomation import ElectricalMeasurement
from zigpy.zcl.clusters.hvac import Fan
from zigpy.zcl.clusters.lighting import Color
from zigpy.zcl.clusters.smartenergy import Metering
from zigpy.zcl.foundation import Status as ZCLStatus

import majordom_zigbee
from majordom_zigbee import ZigBeeController, ZigpyDriver
from majordom_zigbee.exceptions import ZBConnectionError
from majordom_zigbee.listener import ZBAttributeUpdatedListener
from majordom_zigbee.model import ZBDeviceState, ZBParameter
from majordom_zigbee.zigbee_spec import MAIN_PARAMETER_BY_CLUSTER

_NO_RADIO = patch("bellows.zigbee.application.ControllerApplication.new", side_effect=OSError("no radio"))


async def test_discovers_joining_device(zigbee):
    controller, output, _repo, _app = zigbee
    await controller.start_pairing_window(5)  # permit-join -> the stub simulates a device joining

    await wait_for(lambda: bool(output.received_discoveries))
    discovery = output.received_discoveries[-1]
    assert str(discovery.id) == DEVICE_ID
    assert discovery.integration == "zigbee"
    assert UUID(DEVICE_ID) in controller.discoveries


async def _discover(controller, output):
    await controller.start_pairing_window(5)
    await wait_for(lambda: bool(output.received_discoveries))
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
                integration="zigbee",
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


async def _restart_with_the_device_last_seen(zigbee, monkeypatch, hours_ago: float | None, *, mains: bool = False):
    """Pairs the mock device, then starts Zigbee again on a network whose database says when it last heard from it."""
    controller, output, _repo, _app = zigbee
    discovery = await _pair(zigbee)
    await wait_for(lambda: discovery.id in output.connected_devices)
    await controller.stop()  # the Hub marks its devices unavailable
    output.connected_devices.clear()

    async def startup(app, *, auto_form: bool = False):  # zigpy loads the network's devices from its database
        device = conftest._make_mock_zb_device(app)
        if mains and (node_desc := device.node_desc):
            node_desc.mac_capability_flags |= zdo_t.NodeDescriptor.MACCapabilityFlags.MainsPowered
        device.last_seen = None if hours_ago is None else time.time() - hours_ago * 60 * 60

    monkeypatch.setattr(conftest._MockZigpyApp, "startup", startup)
    await controller.start()
    return discovery


@pytest.mark.parametrize(
    ("mains", "hours_ago", "connected"),
    [(True, 1, True), (True, 3, False), (False, 5, True), (False, 7, False), (False, None, False)],
)
async def test_a_start_reports_the_paired_devices_seen_lately_connected(
    zigbee, monkeypatch, mains, hours_ago, connected
):
    """Home Assistant's rule: seen within 2 h (mains) or 6 h (battery). One that left or died stays unavailable."""
    _controller, output, _repo, _app = zigbee
    discovery = await _restart_with_the_device_last_seen(zigbee, monkeypatch, hours_ago, mains=mains)
    assert output.connected_devices == ([discovery.id] if connected else [])
    assert output.lost_devices == []  # the Hub holds them unavailable already


async def test_any_message_from_a_device_seen_long_ago_brings_it_back(zigbee, monkeypatch):
    controller, output, _repo, _app = zigbee
    discovery = await _restart_with_the_device_last_seen(zigbee, monkeypatch, 7)
    assert output.connected_devices == []
    app = controller._application
    app.listener_event("handle_message", app.get_device(conftest.MOCK_IEEE), 260, OnOff.cluster_id, 1, 1, b"")
    await wait_for(lambda: output.connected_devices == [discovery.id])


def _flip_the_switch(app: zigpy.application.ControllerApplication):
    on_off = app.get_device(conftest.MOCK_IEEE).endpoints[1].in_clusters[OnOff.cluster_id]
    on_off._update_attribute(OnOff.AttributeDefs.on_off.id, t.Bool.true)


async def test_a_freshly_paired_devices_reports_reach_the_hub(zigbee):
    _controller, output, _repo, app = zigbee
    discovery = await _pair(zigbee)
    await wait_for(lambda: discovery.id in output.connected_devices)  # the fetch after pairing went through
    output.events.clear()
    _flip_the_switch(app)
    await wait_for(lambda: bool(output.events))
    assert [event.device_id for event in output.events] == [discovery.id]


async def test_a_report_reaches_the_hub_once_however_often_the_device_rejoined(zigbee):
    controller, output, _repo, app = zigbee
    discovery = await _pair(zigbee)
    await wait_for(lambda: discovery.id in output.connected_devices)
    for _ in range(3):  # power cuts, a parent router rebooting: zigpy keeps a plain device's object
        controller.device_initialized(app.get_device(conftest.MOCK_IEEE))
    await asyncio.sleep(0.05)
    output.events.clear()
    _flip_the_switch(app)
    await asyncio.sleep(0.05)
    assert len(output.events) == 1


async def test_a_discovery_is_paired_as_zigpy_last_built_it(zigbee):
    controller, output, repository, app = zigbee
    discovery = await _discover(controller, output)
    rebuilt = conftest._make_mock_zb_device(app)  # interviewed again, and built anew (as with a quirk)
    controller.device_initialized(rebuilt)
    await _seed_provisional(repository, discovery)
    await controller.pair_device(discovery, None)
    assert controller._connected_devices[discovery.id] is rebuilt


async def test_the_unpaired_timer_removes_a_device_that_never_became_a_discovery(zigbee):
    controller, output, _repo, app = zigbee  # its interview failed at start, or it joined twice within the timer
    app.remove = remove = AsyncMock()
    with patch("majordom_zigbee.controller.asyncio.sleep", AsyncMock()):
        await controller._disconnect_unpaired_discovery(UUID(DEVICE_ID), conftest.MOCK_IEEE)
    remove.assert_awaited_once_with(conftest.MOCK_IEEE)
    assert output.lost_discoveries == []  # the Hub never heard of it


async def test_the_unpaired_timer_removes_a_discovery_and_tells_the_hub(zigbee):
    controller, output, _repo, app = zigbee
    discovery = await _discover(controller, output)
    with patch("majordom_zigbee.controller.asyncio.sleep", AsyncMock()):
        await controller._disconnect_unpaired_discovery(discovery.id, conftest.MOCK_IEEE)
    assert conftest.MOCK_IEEE not in app.devices
    assert discovery.id not in controller.discoveries
    assert output.lost_discoveries == [discovery.id]


async def test_stop_ends_an_attribute_report_in_flight(zigbee):
    controller, output, _repo, app = zigbee
    cluster = conftest._make_mock_zb_device(app).endpoints[1].in_clusters[OnOff.cluster_id]

    async def busy(*_):  # the Hub still taking it
        await asyncio.Event().wait()

    output.controller_did_receive_events = busy
    before = asyncio.all_tasks()
    ZBAttributeUpdatedListener(controller, UUID(DEVICE_ID), cluster).attribute_updated(0, True, None)
    (report,) = asyncio.all_tasks() - before
    await controller.stop()
    assert report.cancelled()


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


# Tuya TS0001 dry-contact relay: the simple descriptor lists Metering and ElectricalMeasurement,
# but the firmware implements neither. Metering answers UNSUPPORTED_ATTRIBUTE per attribute, while
# ElectricalMeasurement is rejected as a whole cluster (a Default Response with UNSUPPORTED_CLUSTER).
_TS0001_INPUT_CLUSTERS = [0x0000, 0x0003, 0x0004, 0x0005, OnOff.cluster_id, Metering.cluster_id]
_TS0001_INPUT_CLUSTERS += [ElectricalMeasurement.cluster_id, 0xE000, 0xE001]
_TS0001_OUTPUT_CLUSTERS = [0x000A, 0x0019]  # Time, Ota (client side)
_TS0001_UNSUPPORTED_STATUS = {
    Metering.cluster_id: ZCLStatus.UNSUPPORTED_ATTRIBUTE,
    ElectricalMeasurement.cluster_id: ZCLStatus.UNSUPPORTED_CLUSTER,
}


async def _ts0001_read_attributes(cluster: zigpy.zcl.Cluster, attributes: list[int], **_kwargs):
    if (status := _TS0001_UNSUPPORTED_STATUS.get(cluster.cluster_id)) is not None:
        return {}, dict.fromkeys(attributes, status)
    return {}, {}


async def test_ts0001_exposes_only_supported_clusters(zigbee, monkeypatch):
    monkeypatch.setattr(conftest, "MOCK_INPUT_CLUSTERS", _TS0001_INPUT_CLUSTERS)
    monkeypatch.setattr(conftest, "MOCK_OUTPUT_CLUSTERS", _TS0001_OUTPUT_CLUSTERS)
    controller, output, repository, _app = zigbee
    with patch.object(zigpy.zcl.Cluster, "read_attributes", new=_ts0001_read_attributes):
        discovery = await _pair(zigbee)
        await wait_for(lambda: bool(output.events))  # the background fetch after pairing
    device = await _device(repository, discovery.id)
    async with repository.session() as repo:
        parameters = (await repo.state(discovery.id, as_=ZBDeviceState)).parameters
    clusters = {p.integration_data.cluster_id for p in parameters}

    # unimplemented clusters expose nothing, client-side (output) clusters have no attributes to read,
    # and the attribute-less Tuya clusters 0xE000/0xE001 (no quirk applied here) add nothing either
    assert clusters == {0x0000, 0x0003, 0x0004, 0x0005, OnOff.cluster_id}
    # fetch reports values only for the parameters pairing exposed
    assert {e.parameter_id for e in output.events} <= {p.id for p in parameters}

    # the relay itself stays exposed and controllable
    mapper = controller._mapper
    on_off_id = str(mapper.attribute_parameter_uuid(discovery.id, 1, OnOff.cluster_id, OnOff.AttributeDefs.on_off.id))
    toggle_id = str(mapper.command_parameter_uuid(discovery.id, 1, OnOff.cluster_id, OnOff.ServerCommandDefs.toggle.id))
    assert {on_off_id, toggle_id} <= {str(p.id) for p in parameters}
    toggle = await _param(repository, discovery.id, toggle_id)
    await controller.send_command(
        DeviceCommand(device_id=discovery.id, parameter_id=UUID(toggle_id), value=None), device, toggle
    )


@pytest_asyncio.fixture
async def running(zigpy_apps, tmp_path, monkeypatch):
    """Zigbee run by the runtime on a pinned radio, which it reopens without waiting."""
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0, 0, 0))
    controller, pinned = ZigBeeController(build_test_platform()), Pinned(conftest.RADIO)
    output = RecordingControllerOutput()
    runtime = Runtime(tmp_path, [pinned, controller], platform=controller.platform, controller_output=output)
    await runtime.start()
    await wait_for(lambda: runtime.status(controller).state == PeripheralState.RUNNING)
    yield controller, runtime, pinned, output
    await runtime.stop()


async def test_a_lost_radio_is_reopened_in_place(running, zigpy_apps):
    controller, runtime, _pinned, output = running
    controller.connection_lost(ConnectionResetError("the service in front of it restarted"))

    await wait_for(lambda: len(zigpy_apps) == 2 and controller._application is zigpy_apps[1])
    assert runtime.status(controller).state == PeripheralState.RUNNING
    assert not output.error_log
    await wait_for(lambda: controller._reconnecting is None)


async def test_a_call_while_the_radio_reconnects_says_so(zigbee, monkeypatch):
    controller, _output, _repo, _app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (10,))
    controller.connection_lost(ConnectionResetError("the service in front of it restarted"))
    await wait_for(lambda: controller._application is None)
    with pytest.raises(ZBConnectionError, match="The Zigbee radio is reconnecting"):
        await controller.start_pairing_window(60)


async def test_a_lost_radios_devices_are_unavailable_until_it_is_reopened(zigbee, monkeypatch):
    controller, output, _repo, _app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0,))
    discovery = await _pair(zigbee)
    await wait_for(lambda: discovery.id in output.connected_devices)
    output.connected_devices.clear()
    is_back = asyncio.Event()

    async def startup(app, *, auto_form: bool = False):  # the radio answers again, and its devices with it
        await is_back.wait()
        conftest._make_mock_zb_device(app).last_seen = time.time()

    monkeypatch.setattr(conftest._MockZigpyApp, "startup", startup)
    controller.connection_lost(ConnectionResetError("the service in front of it restarted"))
    await wait_for(lambda: output.lost_devices == [discovery.id])
    assert output.connected_devices == []
    is_back.set()
    await wait_for(lambda: output.connected_devices == [discovery.id])


async def test_a_radio_that_drops_soon_after_each_reopen_fails_zigbee(zigbee, zigpy_apps, monkeypatch):
    controller, output, _repo, _app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0, 0))
    for opened in (2, 3):  # a flaky hub or cable: the radio answers, then drops again a moment later
        controller.connection_lost(ConnectionResetError("dropped"))
        await wait_for(lambda opened=opened: len(zigpy_apps) == opened and controller._application is zigpy_apps[-1])
    assert not output.error_log
    controller.connection_lost(ConnectionResetError("dropped"))
    await wait_for(lambda: bool(output.error_log))
    assert [error.is_still_running for error in output.errors.values()] == [False]
    assert len(zigpy_apps) == 3


async def test_a_radio_that_held_after_its_reopen_has_every_try_again(zigbee, zigpy_apps, monkeypatch):
    controller, output, _repo, _app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0,))
    monkeypatch.setattr(ZigBeeController, "STABLE_RUN", 0)  # each reopen held long enough
    for opened in (2, 3, 4):
        controller.connection_lost(ConnectionResetError("dropped"))
        await wait_for(lambda opened=opened: len(zigpy_apps) == opened and controller._application is zigpy_apps[-1])
    assert not output.error_log


async def test_a_radio_that_stays_lost_fails_zigbee(running):
    controller, runtime, _pinned, output = running
    with _NO_RADIO as new:
        controller.connection_lost(ConnectionResetError("gone"))
        await wait_for(lambda: runtime.status(controller).kind == StatusKind.RETRYING)
    assert runtime.status(controller).detail.startswith("Lost the connection to the Zigbee radio.")
    assert new.await_count == len(ZigBeeController.RECONNECT_DELAYS)
    assert [error.is_still_running for error in output.errors.values()] == [False]


async def test_an_unplugged_radio_only_parks_zigbee(running, monkeypatch):
    controller, runtime, pinned, output = running
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (1,))
    with _NO_RADIO:
        controller.connection_lost(ConnectionResetError("unplugged"))  # zigpy at once, the USB scanner a poll later
        await pinned.unpin("zigbee")
        await wait_for(lambda: runtime.status(controller).kind == StatusKind.NEEDS_UNBOUND)
        await asyncio.sleep(0.1)
    assert not output.error_log
    await pinned.pin(conftest.RADIO)
    await wait_for(lambda: runtime.status(controller).state == PeripheralState.RUNNING)


async def test_a_radio_lost_while_zigbee_starts_is_reopened_once_it_started(zigpy_apps, monkeypatch):
    async def startup(app, *, auto_form: bool = False):
        conftest._make_mock_zb_device(app)  # a device the Hub doesn't know: the start interviews it

    async def initialize(zbdevice):  # the radio goes while the start goes through the network's devices
        if len(zigpy_apps) == 1:
            zbdevice.application.connection_lost(ConnectionResetError("lost"))
        await asyncio.sleep(0.05)

    monkeypatch.setattr(conftest._MockZigpyApp, "startup", startup)
    monkeypatch.setattr(zigpy.device.Device, "initialize", initialize)
    controller = ZigBeeController(build_test_platform())
    controller.output = output = RecordingControllerOutput()
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    await controller.start()
    try:
        assert controller._application is zigpy_apps[0]  # the start went on with it
        await wait_for(lambda: len(zigpy_apps) == 2 and controller._application is zigpy_apps[1])
        assert not output.error_log
    finally:
        await controller.stop()


async def test_a_radio_lost_again_while_it_reopens_is_reopened_afresh(zigbee, zigpy_apps, monkeypatch):
    controller, output, _repo, _app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0, 0))

    async def startup(app, *, auto_form: bool = False):
        if len(zigpy_apps) == 2:  # the first reopen never gets through
            await asyncio.Event().wait()

    monkeypatch.setattr(conftest._MockZigpyApp, "startup", startup)
    controller.connection_lost(ConnectionResetError("lost"))
    await wait_for(lambda: len(zigpy_apps) == 2)
    reopening = controller._reconnecting
    controller.connection_lost(ConnectionResetError("lost again"))
    await wait_for(lambda: len(zigpy_apps) == 3 and controller._application is zigpy_apps[2])
    assert reopening is not None and reopening.cancelled()
    assert not output.error_log


async def test_a_radio_lost_while_zigbee_stops_isnt_reopened(zigbee, zigpy_apps, monkeypatch):
    controller, _output, _repo, app = zigbee
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0,))

    async def interview():  # cut short by the stop, as the radio goes
        try:
            await asyncio.Event().wait()
        finally:
            app.connection_lost(ConnectionResetError("lost while stopping"))

    controller._create_task(interview())
    await asyncio.sleep(0)
    await controller.stop()
    await asyncio.sleep(0.05)
    assert controller._application is None
    assert len(zigpy_apps) == 1


async def test_stopping_is_not_reported_as_a_lost_radio(zigbee):
    controller, output, _repo, app = zigbee

    async def shutdown(*_, **__):  # as with bellows: closing the radio reports the connection lost
        app.connection_lost(ConnectionResetError("closed"))

    app.shutdown = shutdown
    await controller.stop()
    await asyncio.sleep(0.05)
    assert not output.errors
    assert controller._reconnecting is None


async def test_a_stop_cut_short_while_closing_the_radio_lets_go_of_it(zigbee):
    controller, output, _repo, app = zigbee
    await _discover(controller, output)

    async def shutdown(**_):  # a vanished radio's I/O never returns
        await asyncio.Event().wait()

    app.shutdown = shutdown
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):  # as the runtime abandons a stop after its STOP_TIMEOUT
            await controller.stop()
    del app.shutdown  # the fixture's own stop() returns
    assert controller._application is None
    assert not controller.discoveries


async def test_a_start_that_cant_open_the_radio_stops_cleanly():
    controller = ZigBeeController(build_test_platform())
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    with _NO_RADIO, pytest.raises(OSError):
        await controller.start()
    await controller.stop()  # as the runtime does after a failed start


async def test_a_restart_that_cant_open_the_radio_leaves_the_last_one_closed(zigbee):
    controller, _output, _repo, app = zigbee
    await controller.stop()
    app.shutdown = shutdown = AsyncMock()
    with _NO_RADIO, pytest.raises(OSError):
        await controller.start()
    await controller.stop()
    shutdown.assert_not_awaited()


@pytest.mark.parametrize(
    ("params", "device"),
    [
        ({}, {}),
        ({"baud": 460800, "flow_control": True}, {CONF_DEVICE_BAUDRATE: 460800, CONF_DEVICE_FLOW_CONTROL: "hardware"}),
        ({"baud": 38400, "flow_control": False}, {CONF_DEVICE_BAUDRATE: 38400, CONF_DEVICE_FLOW_CONTROL: None}),
    ],
)
async def test_the_radio_opens_with_its_endpoints_serial_settings(zigpy_apps, params, device):
    controller = ZigBeeController(build_test_platform())
    controller.peripherals = Peripherals({"zigbee": replace(conftest.RADIO, params=params)})
    await controller.start()
    try:
        (app,) = zigpy_apps
        assert app.given_config and app.given_config[CONF_DEVICE] == {CONF_DEVICE_PATH: conftest.RADIO.url, **device}
    finally:
        await controller.stop()


class _Radio(conftest._MockZigpyApp):
    """A radio started by zigpy's own new() and startup(), with its database: it refuses to connect, or hangs while
    zigpy starts the network."""

    startup = zigpy.application.ControllerApplication.startup
    shutdown = zigpy.application.ControllerApplication.shutdown
    hangs = False
    is_connected = False

    async def connect(self, *_):
        if not self.hangs:
            raise OSError("radio refused")
        self.is_connected = True

    async def disconnect(self, *_):
        self.is_connected = False

    async def load_network_info(self, *, load_devices=False):
        await asyncio.Event().wait()


@pytest.fixture
async def radios():
    """The radios the controller opens, in order; any left open is closed after the test."""
    opened: list[_Radio] = []

    class Radio(_Radio):
        def __init__(self, config: dict[str, Any]):
            super().__init__(config)
            opened.append(self)

    with patch("majordom_zigbee.controller.application_class", return_value=Radio):
        yield opened
    for radio in opened:
        if radio._dblistener is not None and radio._dblistener.running:
            await radio._dblistener.shutdown()  # its thread would keep the test process alive


async def test_a_start_the_radio_refuses_leaves_nothing_open_once_stopped(radios):
    controller = ZigBeeController(build_test_platform())
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    with pytest.raises(OSError):
        await controller.start()
    await controller.stop()
    (radio,) = radios
    assert radio._dblistener is not None and not radio._dblistener.running


async def test_a_start_cut_short_leaves_nothing_open_once_stopped(radios, monkeypatch):
    monkeypatch.setattr(_Radio, "hangs", True)
    controller = ZigBeeController(build_test_platform())
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    start = asyncio.create_task(controller.start())
    await wait_for(lambda: bool(radios) and radios[0].is_connected)
    start.cancel()  # the start timeout, or the user restarting or disabling Zigbee meanwhile
    await asyncio.gather(start, return_exceptions=True)
    await controller.stop()
    (radio,) = radios
    assert not radio.is_connected
    assert radio._dblistener is not None and not radio._dblistener.running


async def test_a_stop_while_the_reconnect_closes_the_lost_radio_still_closes_its_database(radios, monkeypatch):
    monkeypatch.setattr(ZigBeeController, "RECONNECT_DELAYS", (0,))
    closing = asyncio.Event()

    async def startup(app, *, auto_form: bool = False):
        app.state.network_info = app_state.NetworkInfo(
            channel=t.uint8_t(15),
            pan_id=t.PanId(0x1234),
            extended_pan_id=t.ExtendedPanId.convert(conftest.EXTENDED_PAN_ID),
        )

    async def disconnect(*_):  # closing a port that just vanished takes a while
        closing.set()
        await asyncio.sleep(0.2)

    monkeypatch.setattr(_Radio, "startup", startup)
    monkeypatch.setattr(_Radio, "disconnect", disconnect)
    controller = ZigBeeController(build_test_platform())
    controller.output = RecordingControllerOutput()
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    await controller.start()
    (lost,) = radios
    lost.connection_lost(ConnectionResetError("unplugged"))  # zigpy at once
    await closing.wait()  # the reconnect closes the lost radio
    await controller.stop()  # the USB scanner noticed the unplug a poll later
    await wait_for(lambda: lost._dblistener is not None and not lost._dblistener.running)


async def test_the_quirks_load_off_the_event_loop(zigpy_apps, monkeypatch):
    monkeypatch.setattr(majordom_zigbee.controller, "_quirks_loaded", False)
    on_the_loop: list[bool] = []
    # Seconds of imports (3 s on a Mac): on the loop they would stall the Hub's API and every other integration
    monkeypatch.setattr(
        zhaquirks, "setup", lambda: on_the_loop.append(threading.current_thread() is threading.main_thread())
    )
    controller = ZigBeeController(build_test_platform())
    controller.peripherals = Peripherals({"zigbee": conftest.RADIO})
    await controller.start()
    await controller.stop()
    assert on_the_loop == [False]


def test_the_package_exports_its_controller_and_usb_driver():
    driver, controller = majordom_zigbee.peripherals(build_test_platform())
    assert isinstance(driver, ZigpyDriver) and isinstance(controller, ZigBeeController)
