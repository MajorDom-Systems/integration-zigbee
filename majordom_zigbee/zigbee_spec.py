from typing import Any, NamedTuple

from majordom_integration_sdk.schemas.parameter import ParameterUnit, ParameterVisibility


class MainParameterSpec(NamedTuple):
    """Value of MAIN_PARAMETER_BY_CLUSTER, keyed by cluster_id: what makes a sensible one-tap
    main_parameter for a device exposing that cluster. Iteration order is priority order — the
    first cluster below that a device exposes wins.

    - command main (default): ``target_id`` is a command id; ``default_arguments`` are sent with it
      (None = the command takes no arguments, e.g. OnOff.toggle).
    - attribute main (``is_attribute=True``): ``target_id`` is an attribute id; a tap writes it. For
      an enum attribute (e.g. FanControl.fan_mode) the tap **cycles** through ``cycle`` (an ordered
      value subset, e.g. off/on for a toggle) or the param's full valid_values when ``cycle`` is None.
    """

    target_id: int
    default_arguments: dict[str, Any] | None = None
    is_attribute: bool = False
    cycle: list[int] | None = None


# Command/field names are from zigpy's cluster definitions. FanControl (0x0202) has no commands —
# it's driven by the fan_mode attribute, so its one-tap is an ATTRIBUTE main that cycles off/on.
MAIN_PARAMETER_BY_CLUSTER: dict[int, MainParameterSpec] = {
    0x0006: MainParameterSpec(0x02, None),  # OnOff.toggle
    0x0008: MainParameterSpec(
        0x04, {"level": 254, "transition_time": 0}
    ),  # LevelControl.move_to_level_with_on_off (full)
    0x0300: MainParameterSpec(
        0x06, {"hue": 0, "saturation": 254, "transition_time": 0, "options_mask": 0, "options_override": 0}
    ),  # Color.move_to_hue_and_saturation
    0x0102: MainParameterSpec(0x05, {"percentage_lift_value": 100}),  # WindowCovering.go_to_lift_percentage (open)
    0x0101: MainParameterSpec(0x00, {"pin_code": None}),  # DoorLock.lock_door
    # FanControl.fan_mode attribute — tap cycles Off(0) <-> On(4) (a toggle).
    0x0202: MainParameterSpec(0x0000, is_attribute=True, cycle=[0x00, 0x04]),
}


SYSTEM_CLUSTERS: set[int] = {
    0x0000,  # Basic
    0x0003,  # Identify
    0x0004,  # Groups
    0x0005,  # Scenes
    0x000A,  # Time
    0x000B,  # RSSI
    0x0019,  # OTA
    0x1000,  # Touchlink
}


# --- Visibility curation (see docs/device-integration/parameter-visibility recipe) ------------
# Zigbee attributes carry a Report flag, which is a decent "this is a live reading" signal, so the
# controller keeps `reportable -> user` as a fallback. Curation refines it:
#   - EVERYDAY_CONTROL_ATTRIBUTES: writable everyday controls -> user (+ forced control role, since
#     zigpy sometimes under-declares access, e.g. FanControl.fan_mode has no flags at all)
#   - USER_READINGS: readings that must show even if a device doesn't mark them reportable -> user
#   - metadata (divisors/multipliers/bounds/tolerances, see is_metadata_attribute) -> system, even
#     if reportable, and reused as metadata sources (min/max/step) for the real parameters

EVERYDAY_CONTROL_ATTRIBUTES: set[tuple[int, int]] = {
    (0x0202, 0x0000),  # FanControl.fan_mode (zigpy declares no access flags — force it)
    (0x0201, 0x0011),  # Thermostat.occupied_cooling_setpoint (the everyday "set the temp")
    (0x0201, 0x0012),  # Thermostat.occupied_heating_setpoint
    (0x0201, 0x001C),  # Thermostat.system_mode (off/heat/cool/auto)
}

USER_READINGS: set[tuple[int, int]] = {
    (0x0006, 0x0000),  # OnOff.on_off
    (0x0008, 0x0000),  # LevelControl.current_level
    (0x0201, 0x0000),  # Thermostat.local_temperature
    (0x0402, 0x0000),  # TemperatureMeasurement.measured_value
    (0x0405, 0x0000),  # RelativeHumidity.measured_value
    (0x0400, 0x0000),  # IlluminanceMeasurement.measured_value
    (0x0102, 0x0008),  # WindowCovering.current_position_lift_percentage
    (0x0102, 0x0009),  # WindowCovering.current_position_tilt_percentage
    (0x0001, 0x0021),  # PowerConfiguration.battery_percentage_remaining
    (0x0500, 0x0002),  # IasZone.zone_status
    (0x0101, 0x0000),  # DoorLock.lock_state
    (0x0101, 0x0003),  # DoorLock.door_state
    # Energy: the primary readings only (see CONFIG_HEAVY_CLUSTERS); the min/max/overload/phase-B/C
    # variants stay hidden.
    (0x0B04, 0x0304),  # ElectricalMeasurement.ac_frequency
    (0x0B04, 0x0505),  # ElectricalMeasurement.rms_voltage
    (0x0B04, 0x0508),  # ElectricalMeasurement.rms_current
    (0x0B04, 0x050B),  # ElectricalMeasurement.active_power
    (0x0B04, 0x0510),  # ElectricalMeasurement.power_factor
    (0x0702, 0x0000),  # Metering.current_summation_delivered
    (0x0702, 0x0400),  # Metering.instantaneous_demand
}

# Per-attribute visibility overrides (win over everything). For the handful of readings that would
# otherwise land in the wrong bucket via the reportable fallback.
VISIBILITY_OVERRIDES: dict[tuple[int, int], ParameterVisibility] = {
    (0x0300, 0x0003): ParameterVisibility.system,  # Color.current_x (CIE machine encoding — redundant with hue/sat)
    (0x0300, 0x0004): ParameterVisibility.system,  # Color.current_y
    (0x0201, 0x0007): ParameterVisibility.setting,  # Thermostat.pi_cooling_demand (diagnostic, not everyday)
    (0x0201, 0x0008): ParameterVisibility.setting,  # Thermostat.pi_heating_demand
}

# Clusters where `reportable` is a poor "user reading" signal because most reportable attributes
# are config/security/metadata: DoorLock (enable_* flags, event masks, credential counts) and the
# electrical clusters (dozens of min/max/overload/phase-B/C variants). Only curated USER_READINGS/
# EVERYDAY reach `user`; the rest fall to setting/system.
CONFIG_HEAVY_CLUSTERS: set[int] = {0x0101, 0x0B04, 0x0702}  # DoorLock, ElectricalMeasurement, Metering

# Commands that are everyday one-tap actions (-> user). Every other command on a non-system cluster
# defaults to `setting` (advanced: schedule/credential/log management). ids from zigpy definitions.
EVERYDAY_COMMANDS: set[tuple[int, int]] = {
    (0x0006, 0x00),
    (0x0006, 0x01),
    (0x0006, 0x02),  # OnOff off / on / toggle
    (0x0008, 0x00),
    (0x0008, 0x04),  # LevelControl move_to_level / _with_on_off
    (0x0300, 0x06),
    (0x0300, 0x0A),  # Color move_to_hue_and_saturation / move_to_color_temp
    (0x0102, 0x00),
    (0x0102, 0x01),
    (0x0102, 0x02),
    (0x0102, 0x05),  # WindowCovering up/down/stop/go_to_lift%
    (0x0101, 0x00),
    (0x0101, 0x01),  # DoorLock lock_door / unlock_door (credential/schedule cmds stay setting)
}

# Attributes that are scaling constants / bounds / counts rather than user-facing readings. They
# go to `system` and feed the metadata resolver. Curated set first; a conservative name heuristic
# (below) catches the long tail (e.g. ElectricalMeasurement's ~18 *_divisor/*_multiplier attrs).
METADATA_ATTRIBUTES: set[tuple[int, int]] = {
    (0x0400, 0x0001),  # Illuminance.min_measured_value
    (0x0400, 0x0002),  # Illuminance.max_measured_value
    (0x0402, 0x0001),  # Temperature.min_measured_value
    (0x0402, 0x0002),  # Temperature.max_measured_value
    (0x0405, 0x0001),  # Humidity.min_measured_value
    (0x0405, 0x0002),  # Humidity.max_measured_value
}

_METADATA_NAME_SUFFIXES: tuple[str, ...] = ("_divisor", "_multiplier")
_METADATA_NAME_EXACT: frozenset[str] = frozenset(
    {"min_measured_value", "max_measured_value", "tolerance", "min_level", "max_level"}
)


def is_metadata_attribute(cluster_id: int, attribute_id: int, name: str) -> bool:
    """Whether an attribute is a scaling constant / bound / count (metadata) rather than a
    user-facing reading — curated set first, conservative name heuristic as fallback."""
    if (cluster_id, attribute_id) in METADATA_ATTRIBUTES:
        return True
    return name.endswith(_METADATA_NAME_SUFFIXES) or name in _METADATA_NAME_EXACT


class MetadataSource(NamedTuple):
    """Sibling attributes whose runtime VALUES provide a parameter's min/max — the device's own
    limit attributes (the ones we hide as metadata via ``is_metadata_attribute``). Priority 1 in the
    resolver: runtime sibling value > wire-type default. Mirrors Matter's ``MetadataSource``."""

    min_attr: int | None = None
    max_attr: int | None = None


# (cluster_id, attribute_id) of a shown parameter -> the sibling min/max limit attributes on the
# same cluster whose runtime values bound it. The mirror of Matter's METADATA_SOURCES.
METADATA_SOURCES: dict[tuple[int, int], MetadataSource] = {
    (0x0008, 0x0000): MetadataSource(0x0002, 0x0003),  # LevelControl.current_level <- min/max_level
    (0x0400, 0x0000): MetadataSource(0x0001, 0x0002),  # Illuminance.measured_value <- min/max_measured_value
    (0x0402, 0x0000): MetadataSource(0x0001, 0x0002),  # Temperature.measured_value <- min/max_measured_value
    (0x0403, 0x0000): MetadataSource(0x0001, 0x0002),  # Pressure.measured_value <- min/max_measured_value
    (0x0404, 0x0000): MetadataSource(0x0001, 0x0002),  # Flow.measured_value <- min/max_measured_value
    (0x0405, 0x0000): MetadataSource(0x0001, 0x0002),  # Humidity.measured_value <- min/max_measured_value
    # Thermostat setpoints bounded by the device's absolute-limit attributes.
    (0x0201, 0x0011): MetadataSource(0x0007, 0x0008),  # occupied_cooling_setpoint <- abs_min/max_cool_setpoint_limit
    (0x0201, 0x0012): MetadataSource(0x0003, 0x0004),  # occupied_heating_setpoint <- abs_min/max_heat_setpoint_limit
}


def resolve_metadata_bounds(
    cluster_id: int,
    attribute_id: int,
    get_value,
    default_min,
    default_max,
) -> tuple[Any, Any, list[int]]:
    """Metadata priority 1: override a parameter's min/max with the device's own limit attributes'
    runtime VALUES. ``get_value(attr_id)`` returns the cached sibling value (or None if the device
    doesn't report it). Returns ``(min, max, missing)`` where ``missing`` lists the expected source
    attribute ids that had no value — the caller warns so a quirk / omitted limit surfaces in logs."""
    source = METADATA_SOURCES.get((cluster_id, attribute_id))
    if source is None:
        return default_min, default_max, []
    min_v, max_v = default_min, default_max
    missing: list[int] = []
    for attr_id, is_min in ((source.min_attr, True), (source.max_attr, False)):
        if attr_id is None:
            continue
        resolved = get_value(attr_id)
        if resolved is not None:
            if is_min:
                min_v = resolved
            else:
                max_v = resolved
        else:
            missing.append(attr_id)
    return min_v, max_v, missing


# (cluster_id, attribute_id) -> ParameterUnit
ATTRIBUTE_UNITS: dict[tuple[int, int], ParameterUnit] = {
    # -------------------------
    # General
    # -------------------------
    # Level Control (0x0008)
    (0x0008, 0x0000): ParameterUnit.percentage,  # current_level
    (0x0008, 0x0011): ParameterUnit.percentage,  # on_level
    (0x0008, 0x0014): ParameterUnit.percentage,  # default_move_rate
    (0x0008, 0x4000): ParameterUnit.percentage,  # start_up_current_level
    # -------------------------
    # Lighting (0x0300)
    # -------------------------
    # Color Control (0x0300)
    (0x0300, 0x0001): ParameterUnit.percentage,  # current_hue
    (0x0300, 0x0003): ParameterUnit.percentage,  # current_saturation
    (0x0300, 0x0007): ParameterUnit.mired,  # color_temperature_mireds
    (0x0300, 0x4010): ParameterUnit.plain,  # color_temp_physical_min_mireds
    (0x0300, 0x4011): ParameterUnit.plain,  # color_temp_physical_max_mireds
    (0x0300, 0x4012): ParameterUnit.plain,  # couple_color_temp_to_level_min_mireds
    (0x0300, 0x4013): ParameterUnit.plain,  # start_up_color_temperature
    # -------------------------
    # Measurement & Sensing
    # -------------------------
    # Illuminance Measurement (0x0400)
    (0x0400, 0x0000): ParameterUnit.lux,
    (0x0400, 0x0001): ParameterUnit.lux,
    (0x0400, 0x0002): ParameterUnit.lux,
    # Temperature Measurement (0x0402)
    (0x0402, 0x0000): ParameterUnit.celsius,
    (0x0402, 0x0001): ParameterUnit.celsius,
    (0x0402, 0x0002): ParameterUnit.celsius,
    (0x0402, 0x0010): ParameterUnit.celsius,  # tolerance
    # Pressure Measurement (0x0403)
    (0x0403, 0x0000): ParameterUnit.pascal,  # hPa — nearest in enum
    (0x0403, 0x0001): ParameterUnit.pascal,
    (0x0403, 0x0002): ParameterUnit.pascal,
    (0x0403, 0x0010): ParameterUnit.pascal,
    (0x0403, 0x0011): ParameterUnit.pascal,
    (0x0403, 0x0012): ParameterUnit.pascal,
    # Flow Measurement (0x0404)
    (0x0404, 0x0000): ParameterUnit.m3h,  # flow, m³/h
    (0x0404, 0x0001): ParameterUnit.mps,
    (0x0404, 0x0002): ParameterUnit.mps,
    # Relative Humidity (0x0405)
    (0x0405, 0x0000): ParameterUnit.percentage,
    (0x0405, 0x0001): ParameterUnit.percentage,
    (0x0405, 0x0002): ParameterUnit.percentage,
    (0x0405, 0x0010): ParameterUnit.percentage,
    # Leaf Wetness (0x0407)
    (0x0407, 0x0000): ParameterUnit.percentage,
    (0x0407, 0x0001): ParameterUnit.percentage,
    (0x0407, 0x0002): ParameterUnit.percentage,
    # Soil Moisture (0x0408)
    (0x0408, 0x0000): ParameterUnit.percentage,
    (0x0408, 0x0001): ParameterUnit.percentage,
    (0x0408, 0x0002): ParameterUnit.percentage,
    # Wind Speed (0x040C)
    (0x040C, 0x0000): ParameterUnit.mps,
    (0x040C, 0x0001): ParameterUnit.mps,
    (0x040C, 0x0002): ParameterUnit.mps,
    # Carbon Monoxide (0x040A)
    (0x040A, 0x0000): ParameterUnit.ppm,
    (0x040A, 0x0001): ParameterUnit.ppm,
    (0x040A, 0x0002): ParameterUnit.ppm,
    # Carbon Dioxide / TVOC (0x040D)
    (0x040D, 0x0000): ParameterUnit.ppm,
    (0x040D, 0x0001): ParameterUnit.ppm,
    (0x040D, 0x0002): ParameterUnit.ppm,
    # PM2.5 (0x042A)
    (0x042A, 0x0000): ParameterUnit.ugm3,  # PM2.5, µg/m³
    (0x042A, 0x0001): ParameterUnit.ppm,
    (0x042A, 0x0002): ParameterUnit.ppm,
    # Electrical Conductivity (0x040B)
    (0x040B, 0x0000): ParameterUnit.plain,  # µS/cm — not in enum
    # -------------------------
    # HVAC
    # -------------------------
    # Thermostat (0x0201)
    (0x0201, 0x0000): ParameterUnit.celsius,  # local_temperature
    (0x0201, 0x0001): ParameterUnit.celsius,  # outdoor_temperature
    (0x0201, 0x0003): ParameterUnit.celsius,  # abs_min_heat_setpoint_limit
    (0x0201, 0x0004): ParameterUnit.celsius,  # abs_max_heat_setpoint_limit
    (0x0201, 0x0005): ParameterUnit.celsius,  # abs_min_cool_setpoint_limit
    (0x0201, 0x0006): ParameterUnit.celsius,  # abs_max_cool_setpoint_limit
    (0x0201, 0x0007): ParameterUnit.percentage,  # pi_cooling_demand
    (0x0201, 0x0008): ParameterUnit.percentage,  # pi_heating_demand
    (0x0201, 0x0010): ParameterUnit.celsius,  # local_temperature_calibration
    (0x0201, 0x0011): ParameterUnit.celsius,  # occupied_cooling_setpoint
    (0x0201, 0x0012): ParameterUnit.celsius,  # occupied_heating_setpoint
    (0x0201, 0x0013): ParameterUnit.celsius,  # unoccupied_cooling_setpoint
    (0x0201, 0x0014): ParameterUnit.celsius,  # unoccupied_heating_setpoint
    (0x0201, 0x0015): ParameterUnit.celsius,  # min_heat_setpoint_limit
    (0x0201, 0x0016): ParameterUnit.celsius,  # max_heat_setpoint_limit
    (0x0201, 0x0017): ParameterUnit.celsius,  # min_cool_setpoint_limit
    (0x0201, 0x0018): ParameterUnit.celsius,  # max_cool_setpoint_limit
    (0x0201, 0x0019): ParameterUnit.celsius,  # min_setpoint_dead_band
    # -------------------------
    # Electrical Measurement (0x0B04)
    # -------------------------
    (0x0B04, 0x0304): ParameterUnit.hertz,  # ac_frequency
    (0x0B04, 0x0305): ParameterUnit.hertz,  # ac_frequency_min
    (0x0B04, 0x0306): ParameterUnit.hertz,  # ac_frequency_max
    (0x0B04, 0x0505): ParameterUnit.volt,  # rms_voltage
    (0x0B04, 0x0506): ParameterUnit.volt,  # rms_voltage_min
    (0x0B04, 0x0507): ParameterUnit.volt,  # rms_voltage_max
    (0x0B04, 0x0508): ParameterUnit.ampere,  # rms_current
    (0x0B04, 0x0509): ParameterUnit.ampere,  # rms_current_min
    (0x0B04, 0x050A): ParameterUnit.ampere,  # rms_current_max
    (0x0B04, 0x050B): ParameterUnit.watt,  # active_power
    (0x0B04, 0x050C): ParameterUnit.watt,  # active_power_min
    (0x0B04, 0x050D): ParameterUnit.watt,  # active_power_max
    (0x0B04, 0x050E): ParameterUnit.watt,  # reactive_power
    (0x0B04, 0x050F): ParameterUnit.watt,  # apparent_power (VA → watt approximation)
    (0x0B04, 0x0510): ParameterUnit.percentage,  # power_factor
    (0x0B04, 0x0605): ParameterUnit.volt,  # phase_b rms_voltage
    (0x0B04, 0x0608): ParameterUnit.ampere,  # phase_b rms_current
    (0x0B04, 0x060B): ParameterUnit.watt,  # phase_b active_power
    (0x0B04, 0x0705): ParameterUnit.volt,  # phase_c rms_voltage
    (0x0B04, 0x0708): ParameterUnit.ampere,  # phase_c rms_current
    (0x0B04, 0x070B): ParameterUnit.watt,  # phase_c active_power
    # -------------------------
    # Metering (0x0702)
    # -------------------------
    (0x0702, 0x0000): ParameterUnit.kwh,  # current_summation_delivered
    (0x0702, 0x0001): ParameterUnit.joule,  # current_summation_received
    (0x0702, 0x0002): ParameterUnit.joule,  # current_max_demand_delivered
    (0x0702, 0x0003): ParameterUnit.joule,  # current_max_demand_received
    (0x0702, 0x0400): ParameterUnit.watt,  # instantaneous_demand
    (0x0702, 0x0200): ParameterUnit.joule,  # current_day_consumption_delivered
    (0x0702, 0x0201): ParameterUnit.joule,  # previous_day_consumption_delivered
    # -------------------------
    # Closures
    # -------------------------
    # Window Covering (0x0102)
    (0x0102, 0x0008): ParameterUnit.percentage,  # current_position_lift_percentage
    (0x0102, 0x0009): ParameterUnit.percentage,  # current_position_tilt_percentage
    (0x0102, 0x000E): ParameterUnit.percentage,  # current_position_lift_percent100ths
    (0x0102, 0x000F): ParameterUnit.percentage,  # current_position_tilt_percent100ths
    # -------------------------
    # Device Temperature (0x0002)
    # -------------------------
    (0x0002, 0x0000): ParameterUnit.celsius,  # current_temperature
    (0x0002, 0x0001): ParameterUnit.celsius,  # min_temp_experienced
    (0x0002, 0x0002): ParameterUnit.celsius,  # max_temp_experienced
    (0x0002, 0x0010): ParameterUnit.celsius,  # low_temp_threshold
    (0x0002, 0x0011): ParameterUnit.celsius,  # high_temp_threshold
    # -------------------------
    # Power Configuration (0x0001)
    # -------------------------
    (0x0001, 0x0000): ParameterUnit.volt,  # mains_voltage
    (0x0001, 0x0001): ParameterUnit.hertz,  # mains_frequency
    (0x0001, 0x0010): ParameterUnit.volt,  # mains_voltage_min_threshold
    (0x0001, 0x0011): ParameterUnit.volt,  # mains_voltage_max_threshold
    (0x0001, 0x0020): ParameterUnit.volt,  # battery_voltage
    (0x0001, 0x0021): ParameterUnit.percentage,  # battery_percentage_remaining
}

# (cluster_id, attribute_id) -> min_step
ATTRIBUTE_MIN_STEP: dict[tuple[int, int], int | float] = {
    # Level Control (0x0008)
    (0x0008, 0x0000): 1,  # current_level
    # Color Control (0x0300)
    (0x0300, 0x0001): 1,  # current_hue
    (0x0300, 0x0003): 1,  # current_saturation
    (0x0300, 0x0007): 1,  # color_temperature_mireds
    # Thermostat (0x0201)
    (0x0201, 0x0010): 0.1,  # local_temperature_calibration
    (0x0201, 0x0011): 0.5,  # occupied_cooling_setpoint
    (0x0201, 0x0012): 0.5,  # occupied_heating_setpoint
    (0x0201, 0x0013): 0.5,  # unoccupied_cooling_setpoint
    (0x0201, 0x0014): 0.5,  # unoccupied_heating_setpoint
    # Temperature Measurement (0x0402)
    (0x0402, 0x0000): 0.01,  # resolution 0.01°C в ZCL
    # Relative Humidity (0x0405)
    (0x0405, 0x0000): 0.01,  # resolution 0.01%
    # Pressure Measurement (0x0403)
    (0x0403, 0x0000): 0.1,
    # Window Covering (0x0102)
    (0x0102, 0x0008): 1,
    (0x0102, 0x0009): 1,
    # Electrical Measurement (0x0B04)
    (0x0B04, 0x0505): 0.1,  # rms_voltage
    (0x0B04, 0x0508): 0.01,  # rms_current
    (0x0B04, 0x050B): 0.1,  # active_power
    # Metering (0x0702)
    (0x0702, 0x0000): 0.001,  # kWh
    (0x0702, 0x0400): 1,  # instantaneous_demand W
}


def get_unit(cluster_id: int, attribute_id: int) -> ParameterUnit:
    return ATTRIBUTE_UNITS.get((cluster_id, attribute_id), ParameterUnit.plain)


def get_min_step(cluster_id: int, attribute_id: int) -> int | float | None:
    return ATTRIBUTE_MIN_STEP.get((cluster_id, attribute_id))
