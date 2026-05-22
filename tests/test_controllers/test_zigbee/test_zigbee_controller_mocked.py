"""
Mocked ZigBee controller tests — no real hardware required.

Device: IEEE 00:11:22:33:44:55:66:77
  discovery_id / device_id: b10d1e10-189b-5c0f-a68f-6d90c4c07d7f
  Endpoints: 1 → OnOff (cluster 6), LevelControl (cluster 8)

Key parameter IDs (pre-computed with ZigBeeMapper.create_uuid_id):
  toggle command  : b68f01bd-3483-540f-a7dd-f61164190484
  on_time attr    : 300ecd13-41bf-579e-b7bc-1311daa4f7f8  (Read|Write → control)
"""

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

DISCOVERY_ID = "b10d1e10-189b-5c0f-a68f-6d90c4c07d7f"
# uuid5(NAMESPACE_DNS, f"{DISCOVERY_ID}_command_1/6/2")  — toggle
PARAM_COMMAND_ID = "b68f01bd-3483-540f-a7dd-f61164190484"
# uuid5(NAMESPACE_DNS, f"{DISCOVERY_ID}attribute_1/6/16385")  — on_time (Read|Write)
PARAM_ATTRIBUTE_ID = "300ecd13-41bf-579e-b7bc-1311daa4f7f8"


@pytest.mark.asyncio
async def test_discovery_unpaired_mocked(async_client_mocked, async_client_ws_connect_mocked, crud, get_user_bearer_mocked):
    user = await crud.create_user()

    message = None
    try:
        async with async_client_ws_connect_mocked(user.id) as ws:
            r = await async_client_mocked.post(
                "v1/api/device/start_pairing_window?duration_sec=5",
                headers=get_user_bearer_mocked(user.id),
            )
            assert r.status_code == 200, r.json()

            async with asyncio.timeout(3):
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_discover_discovery":
                        break
                    else:
                        continue
    except WebSocketDisconnect as e:
        assert e.code == 1000

    assert message and message.get("type") == "majordom_did_discover_discovery", message

    r = await async_client_mocked.get("v1/api/device/discoveries", headers=get_user_bearer_mocked(user.id))
    assert r.status_code == 200 and DISCOVERY_ID in r.json(), r.json()


@pytest.mark.asyncio
async def test_pair_mocked(async_client_mocked, crud, get_user_bearer_mocked):
    user = await crud.create_user()
    room = await crud.create_room()

    data = {
        "name": "Mock Device",
        "note": "mocked test",
        "icon": "test icon",
        "category": "test category",
        "room_id": room.id.hex,
        "discovery_id": DISCOVERY_ID,
        "credentials": None,
    }

    r = await async_client_mocked.post("/v1/api/device", json=data, headers=get_user_bearer_mocked(user.id))
    assert r.status_code == 200 and r.json() and r.json()["name"] == data["name"], r.json()


@pytest.mark.asyncio
async def test_discovery_paired_mocked(async_client_mocked, crud, get_user_bearer_mocked):
    user = await crud.create_user()
    r = await async_client_mocked.get("v1/api/device/discoveries", headers=get_user_bearer_mocked(user.id))
    assert r.status_code == 200 and r.json() == {}, f"Sth is wrong, {r.json()}"


@pytest.mark.asyncio
async def test_control_command_mocked(crud, async_client_ws_connect_mocked):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": DISCOVERY_ID,
            "parameter_id": PARAM_COMMAND_ID,
            "value": None,
        },
    }
    message = None
    try:
        async with async_client_ws_connect_mocked(user.id) as ws:
            await ws.send_json(command)
            async with asyncio.timeout(1):
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_receive_event":
                        break
                    else:
                        continue
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get("type") == "majordom_did_receive_event", message


@pytest.mark.asyncio
async def test_control_attribute_mocked(crud, async_client_ws_connect_mocked):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": DISCOVERY_ID,
            "parameter_id": PARAM_ATTRIBUTE_ID,
            "value": 0,
        },
    }
    message = None
    try:
        async with async_client_ws_connect_mocked(user.id) as ws:
            await ws.send_json(command)
            async with asyncio.timeout(1):
                while True:
                    message = await ws.receive_json()
                    if message["type"] == "majordom_did_connect_device":
                        continue
                    else:
                        break
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get("type") == "majordom_did_receive_event", message


@pytest.mark.asyncio
async def test_unpair_mocked(async_client_mocked, crud, get_user_bearer_mocked):
    user = await crud.create_user()
    r = await async_client_mocked.delete(f"/v1/api/device/{DISCOVERY_ID}", headers=get_user_bearer_mocked(user.id))
    assert r.status_code == 200, r.json()
