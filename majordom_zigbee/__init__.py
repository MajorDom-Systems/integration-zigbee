"""Zigbee integration for MajorDom.

Bridges Zigbee devices into the MajorDom language via zigpy. `ZigBeeController` is the entry
point the Hub (or the SDK's standalone dev runner) instantiates and drives.
"""

from majordom_zigbee.controller import ZigBeeController

__all__ = ["ZigBeeController"]
