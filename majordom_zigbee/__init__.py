"""Zigbee integration for MajorDom.

Bridges Zigbee devices into the MajorDom language via zigpy. `peripherals` is what the Hub (or
the SDK's standalone dev runner) runs: `ZigBeeController`, the entry point it drives, and
`ZigpyDriver`, which identifies the sticks only zigpy knows.
"""

from majordom_integration_sdk import Peripheral, Platform

from majordom_zigbee.controller import ZigBeeController
from majordom_zigbee.radio import ZigpyDriver


def peripherals(platform: Platform) -> list[Peripheral]:
    return [ZigpyDriver(), ZigBeeController(platform)]


__all__ = ["ZigBeeController", "ZigpyDriver", "peripherals"]
