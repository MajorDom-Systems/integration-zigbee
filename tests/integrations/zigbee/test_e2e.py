"""
Mocked ZigBee controller tests — no real hardware required.

Device: IEEE 00:11:22:33:44:55:66:77
  discovery_id / device_id: 3ad0d5e0-e32d-5b43-ac8f-150aa969c63d
  Endpoints: 1 → OnOff (cluster 6), LevelControl (cluster 8)

Key parameter IDs (device-scoped via the framework UUID helpers — see ZigBeeMapper):
  toggle command  : 71f0a643-94d1-5def-b4ff-c5a394fafb63
  on_time attr    : d20e6f38-6ad2-5f51-a7b0-0f812b58e5cd  (Read|Write → control)
"""

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

DISCOVERY_ID = "3ad0d5e0-e32d-5b43-ac8f-150aa969c63d"
# parameter_uuid(DISCOVERY_ID, "command_1/6/2")  — toggle
PARAM_COMMAND_ID = "71f0a643-94d1-5def-b4ff-c5a394fafb63"
# parameter_uuid(DISCOVERY_ID, "attribute_1/6/16385")  — on_time (Read|Write)
PARAM_ATTRIBUTE_ID = "d20e6f38-6ad2-5f51-a7b0-0f812b58e5cd"


@pytest.mark.asyncio
async def test_discovery_unpaired_mocked(
    async_client_mocked, async_client_ws_connect_mocked, crud, get_user_bearer_mocked
):
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
