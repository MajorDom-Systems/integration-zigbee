"""ZigBeeSettings accept only what zigpy accepts, so a bad value is refused instead of stopping Zigbee."""

import pytest
from pydantic import ValidationError

from majordom_zigbee.controller import ZigBeeSettings


@pytest.mark.parametrize("tx_power", [-10, 0, 20, None])
def test_transmit_power_within_zigpys_range_is_accepted(tx_power):
    assert ZigBeeSettings(tx_power=tx_power).tx_power == tx_power


@pytest.mark.parametrize("tx_power", [-11, -20, 21])
def test_transmit_power_zigpy_rejects_is_refused(tx_power):
    with pytest.raises(ValidationError):
        ZigBeeSettings(tx_power=tx_power)
