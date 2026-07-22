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

from majordom_zigbee.zigbee_spec_zha import ZHA_ATTRIBUTE_UX as COMMITTED
from scripts.harvest_zha import harvest


def main() -> int:
    current, _skipped = harvest()
    report = diff_specs(current, COMMITTED)
    print(report.render(source="zha"), file=sys.stderr)
    if report.is_empty:
        return 0
    return 2 if report.has_high_risk else 1


if __name__ == "__main__":
    raise SystemExit(main())
