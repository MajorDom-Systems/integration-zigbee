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

Zigbee needs a physical 802.15.4 radio (a Zigbee coordinator such as a SkyConnect, ConBee, or a
zigpy-znp/bellows-supported dongle) at a serial path. Then:

```python
# run.py
import asyncio

from majordom_integration_sdk.dev import run_controller
from majordom_zigbee import ZigBeeController

# Opens the network, watches for joining devices, logs events. Ctrl-C to stop.
asyncio.run(run_controller(ZigBeeController, db_path="devices.db"))
```

```sh
poetry run python run.py
```

To use the integration in **standalone mode** — discover, pair, control, or fetch a device
programmatically — build the dependencies yourself and call the controller directly. Visit the
[MajorDom integration docs](https://docs.majordom.io/device-integration/standalone) for more
details, like the dependency structure and receiving discoveries/events in standalone mode by
implementing a delegate.

```python
import asyncio

from majordom_integration_sdk.dev import build_dependencies
from majordom_integration_sdk.schemas.device import ProvidedCredentials
from majordom_zigbee import ZigBeeController

async def main():
    deps = build_dependencies(integration=ZigBeeController.name, db_path="devices.db")
    controller = ZigBeeController(deps)
    await controller.start()
    # ... await controller.pair_device(discovery, ProvidedCredentials(...)), send_command, fetch ...
    await controller.stop()

asyncio.run(main())
```

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

### Notes

The device/parameter ids are derived from the device's IEEE address via the SDK's UUID helpers, so
they're stable across restarts and namespaced per integration.

## License

See [LICENSE](LICENSE). For commercial licensing or partnership inquiries regarding MajorDom,
contact us via [parker-industries.org/partnership](https://parker-industries.org/partnership).
