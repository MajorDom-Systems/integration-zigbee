import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.mark.asyncio
async def test_discovery_paired(async_client, crud, get_user_bearer):
    # TODO: fully pair a device XD
    user = await crud.create_user()
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200 and r.json() == [], f"Sth is wrong, {r.json()}"


@pytest.mark.asyncio
async def test_discovery_unpaired(async_client, crud, get_user_bearer):
    # test_device_name?
    user = await crud.create_user()
    await async_client.post("v1/api/device/start_pairing_window?time=30", headers=get_user_bearer(user.id))
    await asyncio.sleep(30)
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json() != {}  # test_device_name in here?
    # TODO: fix assert syntax


@pytest.mark.asyncio
async def test_pair(async_client, crud, get_user_bearer):
    # is device expecting to be paired? is theere a discovery after start_pairing_window?

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
    assert r.status_code == 200, r.json()  # NOTE: that prints r.json if assert fails; it doesn't check the json contents!


@pytest.mark.asyncio
async def test_control_command(crud, async_client_ws_connect, async_client, get_user_bearer):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": "c17efe96-b199-5a9c-ae42-321121dfbe25",
            "parameter_id": "f98ccaf3-38e7-5d63-8f3e-fb68c317689e",
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
async def test_controll_attribute(crud, async_client_ws_connect, async_client, get_user_bearer):
    user = await crud.create_user()
    command = {
        "type": "device_command",
        "data": {
            "device_id": "c17efe96-b199-5a9c-ae42-321121dfbe25",
            "parameter_id": "74b12f8c-679f-5630-833a-551cc29aa0b1",
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


@pytest.mark.asyncio
async def test_events():
    raise NotImplementedError()  # TODO: test that when an attribute is updated, the correct event is sent to majordom


@pytest.mark.asyncio
async def test_unpair(async_client, crud, get_user_bearer):
    # where is `c17efe96-b199-5a9c-ae42-321121dfbe25` from?
    user = await crud.create_user()
    r = await async_client.delete("/v1/api/device/c17efe96-b199-5a9c-ae42-321121dfbe25", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
