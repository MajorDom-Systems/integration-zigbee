"""Light probes for the parameter-UX mapping — exercise the curation code paths without
re-hardcoding every device's expected parameters (that stays manual review; see the audit script
and the docs recipe)."""

from majordom_zigbee.zigbee_spec import (
    CONFIG_HEAVY_CLUSTERS,
    EVERYDAY_COMMANDS,
    EVERYDAY_CONTROL_ATTRIBUTES,
    USER_READINGS,
    VISIBILITY_OVERRIDES,
    is_metadata_attribute,
)


def test_metadata_heuristic_catches_scaling_constants():
    assert is_metadata_attribute(0x0B04, 0x0602, "ac_voltage_divisor")
    assert is_metadata_attribute(0x0B04, 0x0603, "ac_voltage_multiplier")
    assert is_metadata_attribute(0x0402, 0x0002, "max_measured_value")


def test_metadata_heuristic_leaves_real_readings_alone():
    assert not is_metadata_attribute(0x0402, 0x0000, "measured_value")
    assert not is_metadata_attribute(0x0B04, 0x050B, "active_power")


def test_curation_sets_are_consistent():
    # a parameter can't be both an everyday writable control and a read-only user reading
    assert USER_READINGS.isdisjoint(EVERYDAY_CONTROL_ATTRIBUTES)
    # overrides don't contradict the curated user lists (an override is for the *other* cases)
    assert set(VISIBILITY_OVERRIDES).isdisjoint(USER_READINGS | EVERYDAY_CONTROL_ATTRIBUTES)


def test_config_heavy_and_commands_populated():
    # DoorLock, ElectricalMeasurement, Metering are the config-heavy clusters
    assert {0x0101, 0x0B04, 0x0702} <= CONFIG_HEAVY_CLUSTERS
    # the everyday one-tap commands include lock/unlock and on/off
    assert {(0x0101, 0x00), (0x0101, 0x01), (0x0006, 0x02)} <= EVERYDAY_COMMANDS
