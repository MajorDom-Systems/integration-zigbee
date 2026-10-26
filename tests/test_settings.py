"""ZigBeeController.Settings: the user sets only what zigpy accepts; the network's own values are shown, never set."""

import pytest
from conftest import EXTENDED_PAN_ID, RADIO, wait_for
from majordom_integration_sdk import Peripherals
from majordom_integration_sdk.runtime import PeripheralState, Pinned, Runtime
from majordom_integration_sdk.testing import build_test_platform
from pydantic import ValidationError
from zigpy.config import CONF_NWK, CONF_NWK_TX_POWER

from majordom_zigbee.controller import ZigBeeController


@pytest.mark.parametrize("tx_power", [-10, 0, 20, None])
def test_transmit_power_within_zigpys_range_is_accepted(tx_power):
    assert ZigBeeController.Settings(tx_power=tx_power).tx_power == tx_power


@pytest.mark.parametrize("tx_power", [-11, -20, 21])
def test_transmit_power_zigpy_rejects_is_refused(tx_power):
    with pytest.raises(ValidationError):
        ZigBeeController.Settings(tx_power=tx_power)


def test_the_network_is_shown_read_only_and_its_key_never():
    fields = ZigBeeController.Settings.model_json_schema()["properties"]
    assert [name for name, field in fields.items() if field.get("readOnly")] == ["channel", "pan_id", "extended_pan_id"]
    assert "network_key" not in fields


async def test_the_radios_network_fills_in_the_settings(zigpy_apps):
    settings = ZigBeeController.Settings(channel=25, tx_power=8)  # a channel kept from an older network
    controller = ZigBeeController(build_test_platform(), settings=settings)
    controller.peripherals = Peripherals({"zigbee": RADIO})
    await controller.start()
    try:
        (app,) = zigpy_apps
        assert app.given_config and app.given_config[CONF_NWK] == {CONF_NWK_TX_POWER: 8}
        assert controller.settings == ZigBeeController.Settings(
            channel=15, pan_id=0x1234, extended_pan_id=EXTENDED_PAN_ID, tx_power=8
        )
    finally:
        await controller.stop()


async def test_a_change_to_the_network_is_refused(zigpy_apps, tmp_path):
    controller = ZigBeeController(build_test_platform())
    runtime = Runtime(tmp_path, [Pinned(RADIO), controller], platform=controller.platform)
    await runtime.start()
    try:
        await wait_for(lambda: runtime.status(controller).state == PeripheralState.RUNNING)
        with pytest.raises(ValidationError, match="frozen"):
            await runtime.update_settings(controller, {"channel": 25})
        shown = controller.settings.model_dump(mode="json")
        await runtime.update_settings(controller, shown | {"tx_power": 4})  # the form sent back with a new power
        await wait_for(lambda: len(zigpy_apps) == 2 and runtime.status(controller).state == PeripheralState.RUNNING)
        assert controller.settings.channel == 15 and controller.settings.tx_power == 4
    finally:
        await runtime.stop()
