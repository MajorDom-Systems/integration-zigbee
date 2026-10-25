"""Zigbee integration for MajorDom.

Bridges Zigbee devices into the MajorDom language via zigpy. `peripherals` is what the Hub (or
the SDK's standalone dev runner) runs: `ZigBeeController`, the entry point it drives.
"""

from majordom_integration_sdk import Peripheral, Platform

from majordom_zigbee.controller import ZigBeeController


def peripherals(platform: Platform) -> list[Peripheral]:
    return [ZigBeeController(platform)]


__all__ = ["ZigBeeController", "peripherals"]
