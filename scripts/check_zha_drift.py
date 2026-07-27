#!/usr/bin/env python3
"""CI drift check for the vendored zha harvest — the Dependabot-style refresher.

Re-harvests zha (must be installed in the CI/throwaway venv) and diffs the result against the
committed ``zigbee_spec_zha.py``. Exit codes let CI decide what to do:

    0  no drift
    1  drift, low/medium risk only (ADD/REMOVE)      -> open an auto-refresh PR
    2  drift includes RECLASSIFY (HIGH risk)         -> PR flagged for human review

Run: python scripts/check_zha_drift.py
"""

from __future__ import annotations

import sys

from majordom_integration_sdk.spec_drift import diff_specs
from zigpy.zcl import Cluster

from majordom_zigbee.mapper import ZigBeeMapper
from majordom_zigbee.zigbee_spec_zha import ZHA_ATTRIBUTE_UX as COMMITTED
from scripts.harvest_zha import harvest

# parse_zigbee_data_type doesn't touch the uuid generators — pass no-ops so the report classifies
# data types with the exact same logic the runtime mapper uses.
_MAPPER = ZigBeeMapper(device_uuid=lambda _s: None, parameter_uuid=lambda _d, _s: None)  # type: ignore[arg-type,return-value]


def _mapped_data_type(attr: object) -> str:
    """MajorDom ``ParameterDataType`` for a zigpy attribute, via the runtime mapper. The unit in the
    harvested tuple is HA's semantic judgment; this is the orthogonal ZCL wire-type axis zigpy
    supplies. ``"unknown"`` when zigpy resolves the id but not a classifiable type."""
    zcl_type = getattr(attr, "zcl_type", None) or getattr(attr, "type", None)
    if zcl_type is None:
        return "unknown"
    try:
        return _MAPPER.parse_zigbee_data_type(zcl_type).value
    except Exception:  # noqa: BLE001 - a type lookup must never break the drift report
        return "unknown"


def _key_label(key: tuple[int, int]) -> str:
    """Resolve a ``(cluster_id, attribute_id)`` key to ``cluster.attribute [data_type]`` via the
    zigpy ZCL registry, so the drift PR names what changed (``temperature.measured_value [integer]``)
    instead of only ``(1026, 0)``. Falls back to the hex id / ``unknown`` for anything zigpy can't
    classify."""
    cid, aid = key
    cluster = Cluster._registry.get(cid)
    cluster_name = getattr(cluster, "ep_attribute", None) or getattr(cluster, "name", None) or f"0x{cid:04x}"
    attr = getattr(cluster, "attributes", {}).get(aid) if cluster is not None else None
    attr_name = getattr(attr, "name", None) or f"0x{aid:04x}"
    return f"{cluster_name}.{attr_name} [{_mapped_data_type(attr)}]"


def main() -> int:
    current, _skipped = harvest()
    report = diff_specs(current, COMMITTED)
    print(report.render(source="zha", key_label=_key_label), file=sys.stderr)
    if report.is_empty:
        return 0
    return 2 if report.has_high_risk else 1


if __name__ == "__main__":
    raise SystemExit(main())
