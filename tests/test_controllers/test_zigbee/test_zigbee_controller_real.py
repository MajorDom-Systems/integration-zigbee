import asyncio
import warnings

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.hardware.iot_cage.aioiotrpc import AioIotRpc

pytestmark = pytest.mark.real_iot_device

# Zigbee test device identifiers (paired against a real device in slot --zigbee-device-idx)
_DEVICE_ID = "c17efe96-b199-5a9c-ae42-321121dfbe25"
_PARAM_COMMAND_ID = "6063563a-9c00-506c-8616-9e1b45576c71"  # OnOff toggle command
_PARAM_ATTR_ID = "35963eae-bbb8-52f3-a7c6-6c59a4f1798d"  # LevelControl attribute


@pytest.mark.asyncio
async def test_discovery_unpaired(async_client, async_client_ws_connect, crud, get_user_bearer, iot_cage: AioIotRpc | None, zigbee_device_idx: int):
    """Device must be unpaired. We power-cycle it so it re-broadcasts and is discoverable."""
    if iot_cage is not None:
        await iot_cage.factory(zigbee_device_idx)  # make sure unpaired
    else:
        warnings.warn("iot_cage is None, skipping power-cycle; device needs to be reset manually")

    user = await crud.create_user()

    message = None
    try:
        async with async_client_ws_connect(user.id) as ws:
            r = await async_client.post("v1/api/device/start_pairing_window?duration_sec=30", headers=get_user_bearer(user.id))
            assert r.status_code == 200, r.json()

            async with asyncio.timeout(30):
                # zigbee discovery might have long delay
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_discover_discovery":
                        break
                    else:
                        continue
    except WebSocketDisconnect as e:
        assert e.code == 1000

    assert message and message.get("type") == "majordom_did_discover_discovery", message

    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200 and _DEVICE_ID in r.json(), r.json()


@pytest.mark.asyncio
async def test_pair(async_client, crud, get_user_bearer, iot_cage: AioIotRpc | None, zigbee_device_idx: int):
    if iot_cage is not None:
        await iot_cage.factory(zigbee_device_idx)  # make sure unpaired
    else:
        warnings.warn("iot_cage is None, skipping power-cycle; device needs to be reset manually")
    user = await crud.create_user()
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

    if iot_cage is not None:
        # Confirm device is still powered and reachable on the cage
        state = await iot_cage.state(zigbee_device_idx)
        assert state == 1, f"Expected device slot {zigbee_device_idx} to be powered on after pair, got state={state}"


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
        await iot_cage.power(zigbee_device_idx, True)
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
    try:
        async with async_client_ws_connect(user.id) as ws:
            await ws.send_json(command)
            async with asyncio.timeout(3):
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_receive_event":
                        break
                    else:
                        continue
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get("type") == "majordom_did_receive_event", message

    if iot_cage is not None:
        await asyncio.sleep(0.5)  # let sensor event propagate
        events = iot_cage.get_events(zigbee_device_idx)
        assert events, f"Expected a sensor event on cage slot {zigbee_device_idx} after toggle command"
        await iot_cage.monitor(False)
    else:
        warnings.warn("iot_cage is None, skipping sensor event verification")


@pytest.mark.asyncio
async def test_controll_attribute(crud, async_client_ws_connect, iot_cage: AioIotRpc | None, zigbee_device_idx: int):
    """Set brightness to 0; if cage is present, verify sensor reflects the change."""
    if iot_cage is not None:
        await iot_cage.power(zigbee_device_idx, True)
        await iot_cage.monitor(True)
        iot_cage.clear_events(zigbee_device_idx)

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
    try:
        async with async_client_ws_connect(user.id) as ws:
            await ws.send_json(command)
            async with asyncio.timeout(3):
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_connect_device":
                        continue
                    else:
                        break
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get("type") == "majordom_did_receive_event", message

    if iot_cage is not None:
        await asyncio.sleep(0.5)
        events = iot_cage.get_events(zigbee_device_idx)
        assert events, f"Expected a sensor event on cage slot {zigbee_device_idx} after attribute change"
        await iot_cage.monitor(False)
    else:
        warnings.warn("iot_cage is None, skipping sensor event verification")


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
