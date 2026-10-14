# Zigbee integration

Bridges the Hub to Zigbee devices through a USB coordinator radio, built on
[`zigpy`](https://github.com/zigpy/zigpy) with the `bellows` (EmberZNet/Silabs) stack by
default (`znp`/Texas Instruments is selectable via `_ZIGBEE_STACK`). zigpy owns the Zigbee
network, the ZCL, and device interviews; this integration adapts it to the Hub's
`AbstractController` contract.

The radio's serial port comes from `dependencies.hardware_interfaces[0]` (overridable via
the `ZIGBEE_DEVICE_PATH` env var); zigpy's device database lives under the integration's
`documents_folder` as `zigbee.db`.

## Files

| File | Responsibility |
|---|---|
| `controller.py` | `AbstractController` implementation — lifecycle, pairing, control, fetch, and the zigpy network-event listener |
| `mapper.py` | Zigbee ↔ MajorDom conversions: UUIDs (via the framework helpers), IEEE address handling, ZCL data-type and access-permission mapping |
| `model.py` | Typed `integration_data` schemas (`ZBDevice`, `ZBParameter`, …) |
| `listener.py` | Per-cluster attribute-report listener that forwards live changes to the Hub |
| `zigbee_spec.py` | Static spec metadata: system clusters, attribute units/steps, and the main-parameter map |

## Discovery & pairing

Zigbee has no separate discovery step — a device is on the network the moment it joins.
`start_pairing_window` opens permit-join; a joining device is held as a discovery and
**disconnected from the network if it isn't paired within 5 minutes**
(`_disconnect_unpaired_discovery`). Pairing takes no credentials
(`CredentialsType.none`). At pairing, the device's endpoints/clusters are walked to build
the parameter list (attributes and commands), and a main (one-tap) parameter is chosen per
`MAIN_PARAMETER_BY_CLUSTER`.

## Availability

The zigpy listener drives availability: `device_left` marks a paired device unavailable,
and `device_initialized` on a rejoin marks it back online (both via `_set_availability`,
which dedupes and emits the framework's connect/lose callbacks). On boot, devices missing
from the network are marked unavailable. The pairing always stays in the DB so a device
comes back on rejoin.

## Parameter identity

Every parameter id is derived through the framework UUID helpers as
`parameter_uuid(device_id, "<attribute|command|field>_<endpoint>/<cluster>/<id>")`, so the
id a parameter gets at pairing is identical to the one used on fetch and in live reports.

## Tests

- `test_zigbee_controller_mocked.py` — an in-memory zigpy stub, no radio or devices needed
  (runs in CI).
- `test_zigbee_controller_hardware.py` — a real device in the self-hosted IoT cage; manual
  only. Its hardcoded device/parameter ids must be regenerated for your DUT (see the note
  at the top of that file).

## Notes

- ZCL reads are chunked (`_MAX_ATTRS_PER_REQUEST`) and paced (`_INTER_CHUNK_DELAY`) to
  avoid NCP ACK timeouts on busy serial links.
