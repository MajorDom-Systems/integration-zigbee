"""Light probes for the parameter-UX mapping — exercise the curation code paths without
re-hardcoding every device's expected parameters (that stays manual review; see the audit script
and the docs recipe)."""

from majordom_zigbee.zigbee_spec import (
    CONFIG_HEAVY_CLUSTERS,
    EVERYDAY_COMMANDS,
    EVERYDAY_CONTROL_ATTRIBUTES,
    MAIN_PARAMETER_BY_CLUSTER,
    METADATA_SOURCES,
    USER_READINGS,
    VISIBILITY_OVERRIDES,
    is_metadata_attribute,
    resolve_metadata_bounds,
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


def test_fan_control_is_an_attribute_main_that_cycles():
    spec = MAIN_PARAMETER_BY_CLUSTER[0x0202]  # FanControl has no commands
    assert spec.is_attribute
    assert spec.target_id == 0x0000  # fan_mode
    assert spec.cycle == [0x00, 0x04]  # off <-> on toggle


def test_metadata_resolver_prefers_device_limit_values():
    # LevelControl.current_level bounded by the device's own min/max_level runtime values
    values = {0x0002: 10, 0x0003: 200}
    lo, hi, missing = resolve_metadata_bounds(0x0008, 0x0000, values.get, 0, 254)
    assert (lo, hi) == (10, 200)
    assert not missing


def test_metadata_resolver_reports_missing_sources():
    lo, hi, missing = resolve_metadata_bounds(0x0008, 0x0000, {}.get, 0, 254)
    assert (lo, hi) == (0, 254)  # falls back to wire-type defaults
    assert set(missing) == {0x0002, 0x0003}


def test_metadata_resolver_passthrough_when_no_source():
    assert resolve_metadata_bounds(0x0500, 0x0000, {}.get, 1, 2) == (1, 2, [])


def test_metadata_sources_point_at_real_metadata():
    # every declared source's target parameter is itself a real (non-metadata) reading key
    for (cluster_id, attr_id) in METADATA_SOURCES:
        assert not is_metadata_attribute(cluster_id, attr_id, "measured_value")
