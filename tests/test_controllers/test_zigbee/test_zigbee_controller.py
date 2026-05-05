import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.mark.asyncio
async def test_discovery_unpaired(async_client, crud, get_user_bearer):
    discovery_id = "c17efe96-b199-5a9c-ae42-321121dfbe25"
    user = await crud.create_user()
    r = await async_client.post("v1/api/device/start_pairing_window?duration_sec=30", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
    await asyncio.sleep(30)
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200 and discovery_id in r.json(), r.json()


@pytest.mark.asyncio
async def test_pair(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    room = await crud.create_room()

    data = {
        "name": "Test Device",
        "note": "test note",
        "icon": "test icon",
        "category": "test category",
        "room_id": room.id.hex,
        "discovery_id": "c17efe96-b199-5a9c-ae42-321121dfbe25",
        "credentials": None,
    }

    r = await async_client.post("/v1/api/device", json=data, headers=get_user_bearer(user.id))
    assert r.status_code == 200 and r.json() and r.json()["name"] == data.get("name"), r.json()


@pytest.mark.asyncio
async def test_discovery_paired(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200 and r.json() == {}, f"Sth is wrong, {r.json()}"


@pytest.mark.asyncio
async def test_control_command(crud, async_client_ws_connect):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": "c17efe96-b199-5a9c-ae42-321121dfbe25",
            "parameter_id": "6063563a-9c00-506c-8616-9e1b45576c71",
            "value": None,
        },
    }
    message = None
    try:
        async with async_client_ws_connect(user.id) as ws:
            await ws.send_json(command)  # should this be out of the loop?
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
async def test_controll_attribute(crud, async_client_ws_connect):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": "c17efe96-b199-5a9c-ae42-321121dfbe25",
            "parameter_id": "35963eae-bbb8-52f3-a7c6-6c59a4f1798d",
            "value": 0,
        },
    }
    message = None
    try:
        async with async_client_ws_connect(user.id) as ws:
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


"""
@pytest.mark.asyncio
async def test_events():
    raise NotImplementedError()  # TODO: test that when an attribute is updated, the correct event is sent to majordom

"""


@pytest.mark.asyncio
async def test_unpair(async_client, crud, get_user_bearer):
    device_id = "c17efe96-b199-5a9c-ae42-321121dfbe25"
    user = await crud.create_user()
    r = await async_client.delete(f"/v1/api/device/{device_id}", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
