"""ZigBeeController.Settings accept only what zigpy accepts, so a bad value is refused instead of stopping Zigbee."""

import pytest
from conftest import RADIO
from majordom_integration_sdk import Peripherals
from majordom_integration_sdk.testing import build_test_platform
from pydantic import ValidationError
from zigpy.config import CONF_NWK, CONF_NWK_CHANNEL, CONF_NWK_TX_POWER

from majordom_zigbee.controller import ZigBeeController


@pytest.mark.parametrize("tx_power", [-10, 0, 20, None])
def test_transmit_power_within_zigpys_range_is_accepted(tx_power):
    assert ZigBeeController.Settings(tx_power=tx_power).tx_power == tx_power


@pytest.mark.parametrize("tx_power", [-11, -20, 21])
def test_transmit_power_zigpy_rejects_is_refused(tx_power):
    with pytest.raises(ValidationError):
        ZigBeeController.Settings(tx_power=tx_power)


async def test_the_settings_it_is_given_set_up_the_network(zigpy_apps):
    settings = ZigBeeController.Settings(channel=25, tx_power=8)
    controller = ZigBeeController(build_test_platform(), settings=settings)
    controller.peripherals = Peripherals({"zigbee": RADIO})
    await controller.start()
    try:
        (app,) = zigpy_apps
        assert app.given_config and app.given_config[CONF_NWK] == {CONF_NWK_CHANNEL: 25, CONF_NWK_TX_POWER: 8}
    finally:
        await controller.stop()
