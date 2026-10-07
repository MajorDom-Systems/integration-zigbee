"""Backend-agnostic zigpy radio selection.

zigpy talks to the coordinator dongle through a per-silicon *radio library* (bellows for Silicon
Labs EZSP, zigpy-znp for TI Z-Stack, zigpy-deconz for deCONZ, ...). This module maps every
supported radio to its zigpy ``ControllerApplication`` and resolves the right one for the attached
dongle — either from the dialect the hub bound (``ezsp``, ``znp``, …) or by probing each installed
library. Swapping dongles therefore needs only
the matching library installed, never a source change.

Add support for a new radio by appending one ``Radio`` entry below — no other code changes.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

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


# Probed in this order during auto-detection — most common coordinators first.
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
        "cc",
        "zigpy_cc.zigbee.application.ControllerApplication",
        "zigpy-cc",
        "Texas Instruments Z-Stack legacy (deprecated)",
    ),
)

_BY_KEY: dict[str, Radio] = {r.key: r for r in RADIOS}
#: Friendly aliases so common product/stack names resolve to a radio key.
_ALIASES: dict[str, str] = {
    "bellows": "ezsp",
    "silabs": "ezsp",
    "ember": "ezsp",
    "emberznet": "ezsp",
    "skyconnect": "ezsp",
    "ti": "znp",
    "zstack": "znp",
    "z-stack": "znp",
    "conbee": "deconz",
    "raspbee": "deconz",
}


def _normalize(name: str) -> str:
    name = name.strip().lower()
    return _ALIASES.get(name, name)


def _load(radio: Radio) -> type[ControllerApplication] | None:
    """Import a radio's ControllerApplication, or None if its library isn't installed."""
    module_path, _, cls_name = radio.application.rpartition(".")
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        log.debug("zigbee radio %r unavailable (%s not installed)", radio.key, radio.package)
        return None
    return getattr(module, cls_name)


async def detect(device_config: dict) -> tuple[Radio, type[ControllerApplication]] | None:
    """The first installed radio library that answers on ``device_config``."""
    for radio in RADIOS:
        if (app := _load(radio)) is None:
            continue
        try:
            # A non-matching radio typically raises (wrong protocol on the port) rather than
            # returning False — either way we move on to the next candidate.
            if await app.probe(device_config):
                return radio, app
        except Exception as exc:  # noqa: BLE001 — any probe failure just means "not this radio"
            log.debug("zigbee radio %r did not match: %s", radio.key, exc)
    return None


async def resolve_application(
    device_config: dict,
    requested: str | None = None,
) -> type[ControllerApplication]:
    """Return the zigpy ``ControllerApplication`` class for the attached coordinator.

    ``requested``: a radio key/alias to force one library, or ``None``/``"auto"`` to probe every
    installed radio against ``device_config`` and use the first that answers.
    """
    if requested and _normalize(requested) != "auto":
        key = _normalize(requested)
        radio = _BY_KEY.get(key)
        if radio is None:
            raise ValueError(f"Unknown zigbee radio {requested!r}. Known: {', '.join(_BY_KEY)}, or auto.")
        app = _load(radio)
        if app is None:
            raise RuntimeError(
                f"Zigbee radio {radio.key!r} was selected but {radio.package!r} is not installed. "
                f"Install it with: pip install 'majordom-zigbee[{radio.key}]'."
            )
        log.info("zigbee radio pinned to %r (%s)", radio.key, radio.description)
        return app

    if found := await detect(device_config):
        radio, app = found
        log.info("zigbee radio auto-detected as %r (%s)", radio.key, radio.description)
        return app
    raise RuntimeError(
        f"Could not auto-detect the zigbee radio on {device_config.get(CONF_DEVICE_PATH, '?')}. "
        f"Install its library, e.g. pip install 'majordom-zigbee[deconz]'."
    )
