#!/usr/bin/env python3
"""Harvest zha's standard-cluster entity judgment into ``majordom_zigbee/zigbee_spec_zha.py``.

DEV / BUILD TOOL — run in a throwaway venv that has ``zha`` installed. ``zha`` is **not** a
runtime dependency of this integration: we consume it here as a data source and vendor the
result. See the parameter-visibility recipe and the zigbee README's priority ladder.

What it takes (the genuinely additive "judgment" that isn't in zigpy): per standard
``(cluster_id, attribute_id)``, zha's ``entity_category`` (→ our visibility), the entity
platform (→ our role), and unit/device_class (→ our ParameterUnit). What it drops: names,
types, valid_values, raw bounds — we already derive those from zigpy directly.

Usage:
    python scripts/harvest_zha.py                 # writes the generated module
    python scripts/harvest_zha.py --stdout        # print instead (for CI diffing)
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections import defaultdict
from importlib.metadata import version

import zigpy.zcl

_ZHA_VERSION = version("zha")

# Platform modules whose import fires the @register_entity side effects into ENTITY_REGISTRY.
_PLATFORMS = (
    "sensor",
    "binary_sensor",
    "switch",
    "number",
    "select",
    "climate",
    "lock",
    "cover",
    "fan",
    "light",
    "button",
    "siren",
)

# zha unit strings / device_classes -> our ParameterUnit value. Anything unmapped -> "plain".
_UNIT_BY_STRING = {
    "°C": "celsius",
    "%": "percentage",
    "V": "volt",
    "A": "ampere",
    "W": "watt",
    "Hz": "hertz",
    "lx": "lux",
    "kWh": "kwh",
    "ppm": "ppm",
    "µg/m³": "ugm3",
    "Pa": "pascal",
    "hPa": "pascal",
    "kPa": "pascal",
    "K": "kelvin",
    "mired": "mired",
    "m³/h": "m3h",
    "s": "second",
}
_UNIT_BY_DEVICE_CLASS = {
    "temperature": "celsius",
    "humidity": "percentage",
    "battery": "percentage",
    "illuminance": "lux",
    "power": "watt",
    "voltage": "volt",
    "current": "ampere",
    "energy": "kwh",
    "pressure": "pascal",
    "frequency": "hertz",
}
# Platforms that write the device (control) vs. read it (sensor).
_CONTROL_PLATFORMS = {"switch", "number", "select", "climate", "lock", "cover", "fan", "light", "button", "siren"}


def _unit_for(dc: str | None, unit: str | None) -> str:
    if unit and unit in _UNIT_BY_STRING:
        return _UNIT_BY_STRING[unit]
    if dc and dc in _UNIT_BY_DEVICE_CLASS:
        return _UNIT_BY_DEVICE_CLASS[dc]
    return "plain"


def _enum_value(x: object) -> str | None:
    # HA enums carry str values; plain strings pass through unchanged.
    return str(getattr(x, "value", x)) if x is not None else None


def harvest() -> tuple[dict[tuple[int, int], tuple[str, str, str]], list[str]]:
    for p in _PLATFORMS:
        importlib.import_module(f"zha.application.platforms.{p}")
    from zha.application.platforms import ENTITY_REGISTRY

    reg = zigpy.zcl.Cluster._registry
    # Collect every (visibility, role, unit) vote per key, then reduce with a fixed policy.
    votes: dict[tuple[int, int], list[tuple[str, str, str]]] = defaultdict(list)
    skipped: list[str] = []

    for cluster_id, classes in ENTITY_REGISTRY.items():
        cluster_cls = reg.get(cluster_id)
        for cls in classes:
            attr_name = getattr(cls, "_attribute_name", None)
            platform = cls.__module__.split(".")[-1]
            if attr_name is None:
                # Composite entities (light/climate/cover) map a whole cluster, not one attr.
                skipped.append(f"{cluster_id:#06x} {platform}:{cls.__name__} (no single attribute)")
                continue
            if cluster_cls is None or attr_name not in cluster_cls.attributes_by_name:
                skipped.append(f"{cluster_id:#06x} {platform}:{attr_name} (id unresolved)")
                continue
            attr_id = cluster_cls.attributes_by_name[attr_name].id
            ec = _enum_value(getattr(cls, "_attr_entity_category", None))
            visibility = "user" if ec is None else "setting"  # config/diagnostic -> settings tap
            role = "control" if platform in _CONTROL_PLATFORMS else "sensor"
            unit = _unit_for(
                _enum_value(getattr(cls, "_attr_device_class", None)),
                getattr(cls, "_attr_native_unit_of_measurement", None),
            )
            votes[(cluster_id, attr_id)].append((visibility, role, unit))

    # Reduce collisions (one cluster attr can back several entities across device types):
    #   visibility: most-visible wins (user > setting)   role: control > sensor
    #   unit:       first non-plain
    out: dict[tuple[int, int], tuple[str, str, str]] = {}
    for key, vs in votes.items():
        visibility = "user" if any(v[0] == "user" for v in vs) else "setting"
        role = "control" if any(v[1] == "control" for v in vs) else "sensor"
        unit = next((v[2] for v in vs if v[2] != "plain"), "plain")
        out[key] = (visibility, role, unit)
    return out, skipped


def render(data: dict[tuple[int, int], tuple[str, str, str]]) -> str:
    lines = [
        '"""GENERATED by scripts/harvest_zha.py — DO NOT EDIT BY HAND.',
        "",
        f"Source: zha=={_ZHA_VERSION}  (Apache-2.0). Harvested standard-cluster entity",
        "judgment (entity_category -> visibility, platform -> role, unit). Refresh by re-running",
        "the harvester; put hand overrides in zigbee_spec.py (OUR_ATTRIBUTE_UX), which win on merge.",
        '"""',
        "",
        "# (cluster_id, attribute_id) -> (visibility, role, unit)",
        "ZHA_ATTRIBUTE_UX: dict[tuple[int, int], tuple[str, str, str]] = {",
    ]
    for (cid, aid), (vis, role, unit) in sorted(data.items()):
        lines.append(f"    ({cid:#06x}, {aid:#06x}): ({vis!r}, {role!r}, {unit!r}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="print instead of writing the module")
    ap.add_argument("--out", default="majordom_zigbee/zigbee_spec_zha.py")
    args = ap.parse_args()

    data, skipped = harvest()
    text = render(data)
    if args.stdout:
        sys.stdout.write(text)
    else:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"[harvest_zha] zha=={_ZHA_VERSION}: wrote {len(data)} entries -> {args.out}", file=sys.stderr)
    print(f"[harvest_zha] {len(skipped)} composite/unresolved entities skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
