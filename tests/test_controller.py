"""Unit tests driving ZigBeeController directly against the in-memory zigpy stub."""

from uuid import UUID

from conftest import DEVICE_ID, PARAM_ATTRIBUTE_ID, PARAM_COMMAND_ID


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
