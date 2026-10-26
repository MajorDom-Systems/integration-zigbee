"""Backend-agnostic zigpy radio selection.

zigpy talks to the coordinator dongle through a per-silicon *radio library* (bellows for Silicon
Labs EZSP, zigpy-znp for TI Z-Stack, zigpy-deconz for deCONZ, ...). This module maps every
supported radio to its zigpy ``ControllerApplication``: the controller takes the one the dialect the
hub bound (``ezsp``, ``znp``, …) names, and ``ZigpyDriver`` probes the installed libraries to tell
which one a stick speaks. Swapping dongles therefore needs only the matching library installed, never a
source change.

Add support for a new radio by appending one ``Radio`` entry below, and its key to ``PROBED`` when a stick speaks it.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from majordom_integration_sdk import Endpoint, UsbDriver
from zigpy.application import ControllerApplication
from zigpy.config import CONF_DEVICE_PATH

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Radio:
    """One supported zigpy radio backend."""

    key: str  #: short id, also the dialect of the hub's ``zigbee`` link
    application: str  #: dotted path to its zigpy ControllerApplication
    package: str  #: the pip package (and extra) that provides it
    description: str  #: human-readable hardware hint


RADIOS: tuple[Radio, ...] = (
    Radio(
        "ezsp",
        "bellows.zigbee.application.ControllerApplication",
        "bellows",
        "Silicon Labs EmberZNet via EZSP (SkyConnect, Sonoff ZBDongle-E, ...)",
    ),
    Radio(
        "znp",
        "zigpy_znp.zigbee.application.ControllerApplication",
        "zigpy-znp",
        "Texas Instruments Z-Stack via ZNP (CC2652, Sonoff ZBDongle-P, ...)",
    ),
    Radio(
        "deconz",
        "zigpy_deconz.zigbee.application.ControllerApplication",
        "zigpy-deconz",
        "dresden elektronik deCONZ (ConBee, RaspBee)",
    ),
    Radio(
        "xbee",
        "zigpy_xbee.zigbee.application.ControllerApplication",
        "zigpy-xbee",
        "Digi XBee",
    ),
    Radio(
        "zigate",
        "zigpy_zigate.zigbee.application.ControllerApplication",
        "zigpy-zigate",
        "ZiGate",
    ),
    Radio(
        "ziggurat",
        "zigpy_ziggurat.zigbee.application.ControllerApplication",
        "zigpy-ziggurat",
        "Ziggurat, the open-source host-side Zigbee stack, through its server",
    ),
    Radio(
        "blz",
        "zigpy_blz.zigbee.application.ControllerApplication",
        "zigpy-blz",
        "Bouffalo Lab BL70x via BLZ",
    ),
)

#: Every dialect a zigpy radio library speaks: what the controller's ``zigbee`` need accepts.
ZIGPY_DIALECTS: tuple[str, ...] = tuple(r.key for r in RADIOS)

_BY_KEY: dict[str, Radio] = {r.key: r for r in RADIOS}

#: What ``ZigpyDriver`` probes a stick for, in order: the quickest to tell first, ezsp last for the EFR32 sticks the
#: SDK's Silabs driver didn't identify. Not ziggurat: it speaks through a server's WebSocket, never a stick.
PROBED: tuple[Radio, ...] = tuple(_BY_KEY[key] for key in ("deconz", "zigate", "xbee", "blz", "znp", "ezsp"))


def _load(radio: Radio) -> type[ControllerApplication] | None:
    """Import a radio's ControllerApplication, or None if its library isn't installed."""
    module_path, _, cls_name = radio.application.rpartition(".")
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        log.debug(f"zigbee radio {radio.key!r} unavailable ({radio.package} not installed)")
        return None
    return getattr(module, cls_name)


async def detect(device_config: dict) -> tuple[Radio, type[ControllerApplication]] | None:
    """The first installed radio library that answers on ``device_config``."""
    for radio in PROBED:
        if (app := _load(radio)) is None:
            continue
        try:
            # A non-matching radio typically raises (wrong protocol on the port) rather than
            # returning False — either way we move on to the next candidate.
            if await app.probe(device_config):
                return radio, app
        except Exception as exc:  # noqa: BLE001 — any probe failure just means "not this radio"
            log.debug(f"zigbee radio {radio.key!r} did not match: {exc}")
    return None


def application_class(dialect: str) -> type[ControllerApplication]:
    """The zigpy ``ControllerApplication`` of the radio library speaking ``dialect``, which the need took."""
    radio = _BY_KEY[dialect]
    if (app := _load(radio)) is None:
        raise RuntimeError(
            f"Zigbee radio {radio.key!r} needs {radio.package!r}: pip install 'majordom-zigbee[{radio.key}]'."
        )
    return app


class ZigpyDriver(UsbDriver):
    """The sticks only zigpy tells apart (ConBee, ZiGate, XBee, ...), for the runtime's USB scanner: the first
    installed radio library that answers on a stick names its firmware, which serves that dialect of ``zigbee``."""

    name = "zigpy"
    firmwares = {radio.key: [Endpoint("zigbee", radio.key, "{device}")] for radio in PROBED}

    async def identify(self, path: str) -> str | None:
        found = await detect({CONF_DEVICE_PATH: path})
        return found[0].key if found else None
