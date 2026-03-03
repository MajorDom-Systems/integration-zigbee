import asyncio
import pytest

from starlette.websockets import WebSocketDisconnect

# @pytest.mark.asyncio
# async def test_open_netrowk(async_client, crud, get_user_bearer):
#     user = await crud.create_user()
#     r = await async_client.post("/v1/api/device/open_network?time=10", headers=get_user_bearer(user.id))
#     await asyncio.sleep(10)
#     assert r.status_code == 200


@pytest.mark.asyncio
async def test_discovery_paired(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json() == []


@pytest.mark.asyncio
async def test_discovery_unpaired(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    await async_client.post("v1/api/device/open_network?time=15", headers=get_user_bearer(user.id))
    await asyncio.sleep(15)
    r = await async_client.get("v1/api/device/discoveries", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json() != []

@pytest.mark.asyncio
async def test_pair(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    room = await crud.create_room()

    data = {
        'name': 'Test Device',
        'note': 'test note',
        'icon': 'test icon',
        'category': 'test category',
        'room_id': room.id.hex,
        'discovery_id': "c17efe96-b199-5a9c-ae42-321121dfbe25",
        'credentials': None,
    }

    r = await async_client.post("/v1/api/device", json=data, headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()


@pytest.mark.asyncio
async def test_controll_command(crud, async_client_ws_connect, async_client, get_user_bearer):
    user = await crud.create_user()
    command = {
        'type': 'device_command',
        'data': {
            'device_id': "c17efe96-b199-5a9c-ae42-321121dfbe25",
            'parameter_id': "f98ccaf3-38e7-5d63-8f3e-fb68c317689e",
            'value': None,
        }
    }
    message = None
    try:
        async with async_client_ws_connect(user.id) as ws:
            while True:
                await ws.send_json(command)
                async with asyncio.timeout(1):
                    message = await ws.receive_json()
                if message['type'] == 'majordom_did_connect_device':
                    continue
                else:
                    break
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get('type') == 'majordom_did_receive_event', message

@pytest.mark.asyncio
async def test_controll_attribute(crud, async_client_ws_connect, async_client, get_user_bearer):
    user = await crud.create_user()
    command = {
        'type': 'device_command',
        'data': {
            'device_id': "c17efe96-b199-5a9c-ae42-321121dfbe25",
            'parameter_id': "aa496e03-5086-5d33-9b94-4634c2171fa1",
            'value': 5,
        }
    }
    message = None
    try:
        async with async_client_ws_connect(user.id) as ws:
            while True:
                await ws.send_json(command)
                async with asyncio.timeout(1):
                    message = await ws.receive_json()
                if message['type'] == 'majordom_did_connect_device':
                    continue
                else:
                    break
    except WebSocketDisconnect as e:
        assert e.code == 1000
    assert message and message.get('type') == 'majordom_did_receive_event', message

@pytest.mark.asyncio
async def test_events():
    pass

@pytest.mark.asyncio
async def test_unpair(async_client, crud, get_user_bearer):
    user = await crud.create_user()
    r = await async_client.delete("/v1/api/device/c17efe96-b199-5a9c-ae42-321121dfbe25", headers=get_user_bearer(user.id))
    assert r.status_code == 200, r.json()
