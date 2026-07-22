# integration-zigbee

A [MajorDom](https://majordom.io) integration — bridges **Zigbee** devices into the MajorDom
language.

Built for the **MajorDom Hub**, but it doesn't need it: this is a standalone, standardized
library for Zigbee that you can use on its own (see **Run it standalone** below). Built on the
[MajorDom Integration SDK](https://github.com/MajorDom-Systems/integration-sdk). The entry point
is `ZigBeeController` (`majordom_zigbee/controller.py`), which the Hub — or the SDK's dev runner —
instantiates and drives through its lifecycle: pairing → commands → teardown.

- **Other protocols:** browse the [MajorDom integrations](https://github.com/orgs/MajorDom-Systems/repositories?q=integration-).
- **Create your own:** start from the [integration template](https://github.com/MajorDom-Systems/integration-template).

## Documentation

Full integration-author docs — the controller lifecycle, data models, storing data, discovery,
and a worked example — live at **[docs.majordom.io](https://docs.majordom.io/device-integration)**.

## Development

```sh
poetry install && poetry run poe install
```

| Task | Description |
|------|-------------|
| `poe check` | Full quality pipeline (ruff, ty, pytest, poetry build/check) |
| `poe check --ci` | Same, plus `git diff --exit-code` |

Work lands on `develop`; `master` is protected and released via **Actions → Release**. Tests drive
the controller with the SDK's test doubles against a simulated `zigpy` device — no radio required
(see `tests/`).

## Run it standalone (without the Hub)

`majordom-zigbee` is a standalone library — import it into your own app, or run **just this
integration** interactively (discover, pair, control, and inspect devices from a prompt) with no Hub.
It needs a Zigbee coordinator radio (a SkyConnect, ConBee, or a zigpy-znp/bellows-supported dongle)
at a serial path.

See **[Standalone mode](https://docs.majordom.io/device-integration/standalone)** for the interactive
CLI, watch mode, and the programmatic API.

## About this integration

- **Protocol / platform:** Zigbee via `zigpy` (with `bellows` / `zigpy-znp` radio libraries).
- **Transport(s):** Zigbee (IEEE 802.15.4).
- **Supported devices:** Zigbee Home Automation devices — lights, plugs, switches, sensors.
- **Credentials needed to pair:** none — devices join during an explicit pairing window.

### Required harness

- **Hardware adapters:** a Zigbee coordinator radio (SkyConnect / ConBee / a `zigpy`-supported
  dongle) — the Hub assigns its OS device path via `dependencies.hardware_interfaces`
  (e.g. `/dev/ttyACM0`).
- **Third-party software services:** none — `zigpy` speaks to the radio directly.
- **OS / permissions:** serial-port access to the radio.

### Protocol stack (OSI)

| OSI layer | Protocol | Implemented by |
|-----------|----------|----------------|
| Application (7) | Zigbee Cluster Library (ZCL) | **this integration** (via `zigpy`) |
| Network (3) | Zigbee NWK / APS | library (`zigpy` · radio firmware) |
| Data link / Physical (1–2) | IEEE 802.15.4 | radio adapter (harness) |

### Progress

- [x] `start_pairing_window` implemented (Zigbee requires an explicit join window)
- [x] Discovery of joining devices; `controller_did_receive_discovery` called
- [x] Re-discovery of already-paired devices on reconnect (`controller_did_connect_device`)
- [x] Device pairing
- [x] Device schema mapped: endpoints/clusters → parameter list with per-parameter metadata
- [x] Hub → Device control (`send_command` — commands and attribute writes)
- [x] Device → Hub event subscription (`controller_did_receive_events`)
- [x] `identify`
- [x] `unpair`
- [x] `fetch`
- [x] Availability tracking while running (`controller_did_lose_device` / `last_error`)
- [x] Graceful shutdown in `stop`
- [x] Tests pass against a simulated `zigpy` device (`tests/`)

### Parameter metadata sources & priority

Every parameter's UX metadata is resolved from several sources. Two independent axes, each with its
own priority ladder (first match wins). See also the
[parameter-visibility recipe](https://docs.majordom.io/device-integration/parameter-visibility).

**Visibility / role / unit** — resolved by `classify_attribute()` in `zigbee_spec.py`:

| # | Source | What it is |
|---|--------|-----------|
| 1 | `OUR_ATTRIBUTE_UX` (`VISIBILITY_OVERRIDES`, `USER_READINGS`, `EVERYDAY_CONTROL_ATTRIBUTES`) | our hand curation — a human's call wins over everything |
| — | metadata / manufacturer-on-system-cluster | forced **system** (safety; scaling constants & bounds stay hidden) |
| 2 | **v2 quirk entity metadata** | per-device judgment from a loaded `zhaquirks` `QuirkBuilder` (`quirk_ux_map()`), runtime |
| 3 | `ZHA_ATTRIBUTE_UX` | standard-cluster judgment **harvested** from `zha` (`scripts/harvest_zha.py`, vendored — `zha` is not a runtime dep) |
| 4 | **fallback policy** | heuristic (reportable → user, writable → setting); **logs a warning** so uncurated attrs surface. Flip `_FALLBACK_HIDE_UNCURATED` to hide-by-default once coverage is validated on real devices. |

**Bounds (`min`/`max`/`step`)** — a separate ladder (`resolve_metadata_bounds()`):

1. the device's own limit attributes' **runtime values** (`METADATA_SOURCES`) — ground truth for *this* device;
2. spec tables (`ATTRIBUTE_MIN_STEP`, wire-type range);
3. wire-type default. A missing expected limit is logged (quirk detection).

**Quirks.** `zhaquirks.setup()` runs once at controller startup so joined devices are presented in
quirked form (manufacturer clusters decoded into named/typed attributes; v2 entity metadata
attached). This requires the `zigpy` 2.x stack.

**Drift.** `scripts/check_zha_drift.py` re-harvests `zha` and diffs against the vendored artifact via
the SDK's `diff_specs`, tiering changes ADD / REMOVE / **RECLASSIFY** (high-risk — changes what
current users already see). CI opens a Dependabot-style refresh PR on drift.

### Notes

The device/parameter ids are derived from the device's IEEE address via the SDK's UUID helpers, so
they're stable across restarts and namespaced per integration.

## License

See [LICENSE](LICENSE). For commercial licensing or partnership inquiries regarding MajorDom,
contact us via [parker-industries.org/partnership](https://parker-industries.org/partnership).
