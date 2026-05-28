import asyncio
import warnings

import pytest

from tests.hardware.iot_cage.aioiotrpc import AioIotRpc

pytestmark = [pytest.mark.real_iot_device, pytest.mark.asyncio(loop_scope="session")]

# Zigbee test device identifiers (paired against a real device in slot --zigbee-device-idx)
_DEVICE_ID = "d478e32a-cbb7-51bc-9ba0-0cd746b873a8"
_PARAM_COMMAND_ID = "b7bca372-5d6e-51ab-90ab-38ba57d276c2"  # OnOff toggle command
_PARAM_ATTR_ID = "0554f32e-15b5-5862-9e02-274a2167e86d"  # OnOff on_time attribute (writable integer)


@pytest.mark.asyncio
async def test_discovery_and_pairing(async_client, async_client_ws_connect, crud, get_user_bearer):
    """Zigbee specifics: no separate discovery step, under the hood it pairs right away."""

    user = await crud.create_user()

    message = None
    async with async_client_ws_connect(user.id, timeout=30) as ws:
        r = await async_client.post(
            "v1/api/device/start_pairing_window?duration_sec=120", headers=get_user_bearer(user.id)
        )
        assert r.status_code == 200, r.json()

        while True:
            message = await ws.receive_json()
            if message["type"] == "majordom_did_discover_discovery":
                break

    assert message is not None and message.get("type") == "majordom_did_discover_discovery", (
        "No discovery message received within timeout"
    )

    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
    assert _DEVICE_ID in r.json(), r.json()

    room = await crud.create_room()

    data = {
        "name": "Test Device",
        "note": "test note",
        "icon": "test icon",
        "category": "test category",
        "room_id": room.id.hex,
        "discovery_id": _DEVICE_ID,
        "credentials": None,
    }

    r = await async_client.post("/v1/api/device", json=data, headers=get_user_bearer(user.id))
    assert r.status_code == 200 and r.json() and r.json()["name"] == data.get("name"), r.json()


@pytest.mark.asyncio
async def test_discovery_paired(async_client, crud, get_user_bearer):
    # assumes device is already paired and reachable after `test_pair`
    user = await crud.create_user()
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200 and r.json() == {}, r.json()


@pytest.mark.asyncio
async def test_control_command(crud, async_client_ws_connect, iot_cage: AioIotRpc | None, zigbee_device_idx: int):
    # assumes device is already paired and reachable after `test_pair`
    """Send OnOff toggle; if cage is present, verify the sensor on the device slot changed."""
    if iot_cage is not None:
        await iot_cage.monitor(True)
        iot_cage.clear_events(zigbee_device_idx)

    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": _DEVICE_ID,
            "parameter_id": _PARAM_COMMAND_ID,
            "value": None,
        },
    }
    message = None
    async with async_client_ws_connect(user.id, timeout=10) as ws:
        await ws.send_json(command)
        while True:
            message = await ws.receive_json()
            if message["type"] == "majordom_did_receive_event":
                break
    assert message and message.get("type") == "majordom_did_receive_event", message

    if iot_cage is not None:
        await asyncio.sleep(0.5)  # let sensor event propagate
        events = iot_cage.get_events(zigbee_device_idx)
        assert events, f"Expected a sensor event on cage slot {zigbee_device_idx} after toggle command"
        await iot_cage.monitor(False)
    else:
        warnings.warn("iot_cage is None, skipping sensor event verification")


@pytest.mark.asyncio
async def test_controll_attribute(crud, async_client_ws_connect):
    # assumes device is already paired and reachable after `test_pair`
    """Write on_time attribute; verify WS event is received (no cage check - attribute write doesn't toggle relay)."""
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": _DEVICE_ID,
            "parameter_id": _PARAM_ATTR_ID,
            "value": 0,
        },
    }
    message = None
    async with async_client_ws_connect(user.id, timeout=10) as ws:
        await ws.send_json(command)
        while True:
            message = await ws.receive_json()
            if message["type"] == "majordom_did_receive_event":
                break
    assert message and message.get("type") == "majordom_did_receive_event", message


"""
@pytest.mark.asyncio
async def test_events():
    raise NotImplementedError()  # TODO: test that when an attribute is updated, the correct event is sent to majordom; needs rpc for control/sensor trigger

"""


@pytest.mark.asyncio
async def test_unpair(async_client, crud, get_user_bearer, iot_cage: AioIotRpc | None, zigbee_device_idx: int):
    user = await crud.create_user()
    r = await async_client.delete(f"/v1/api/device/{_DEVICE_ID}", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
