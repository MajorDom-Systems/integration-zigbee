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

from majordom_zigbee.zigbee_spec_zha import ZHA_ATTRIBUTE_UX as COMMITTED
from scripts.harvest_zha import harvest


def _key_label(key: tuple[int, int]) -> str:
    """Resolve a ``(cluster_id, attribute_id)`` key to ``cluster.attribute`` via the zigpy ZCL
    registry, so the drift PR names what changed (``temperature.measured_value``) instead of only
    ``(1026, 0)``. Falls back to the hex id for anything zigpy doesn't know."""
    cid, aid = key
    cluster = Cluster._registry.get(cid)
    cluster_name = getattr(cluster, "ep_attribute", None) or getattr(cluster, "name", None) or f"0x{cid:04x}"
    attr = getattr(cluster, "attributes", {}).get(aid) if cluster is not None else None
    attr_name = getattr(attr, "name", None) or f"0x{aid:04x}"
    return f"{cluster_name}.{attr_name}"


def main() -> int:
    current, _skipped = harvest()
    report = diff_specs(current, COMMITTED)
    print(report.render(source="zha", key_label=_key_label), file=sys.stderr)
    if report.is_empty:
        return 0
    return 2 if report.has_high_risk else 1


if __name__ == "__main__":
    raise SystemExit(main())
