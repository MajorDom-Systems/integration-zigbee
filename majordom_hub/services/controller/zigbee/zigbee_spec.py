from majordom_hub.schemas.parameter import ParameterUnit


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

# (cluster_id, attribute_id) -> ParameterUnit
ATTRIBUTE_UNITS: dict[tuple[int, int], ParameterUnit] = {

    # -------------------------
    # General
    # -------------------------

    # Level Control (0x0008)
    (0x0008, 0x0000): ParameterUnit.percentage,     # current_level
    (0x0008, 0x0011): ParameterUnit.percentage,     # on_level
    (0x0008, 0x0014): ParameterUnit.percentage,     # default_move_rate
    (0x0008, 0x4000): ParameterUnit.percentage,     # start_up_current_level

    # -------------------------
    # Lighting (0x0300)
    # -------------------------

    # Color Control (0x0300)
    (0x0300, 0x0001): ParameterUnit.percentage,     # current_hue
    (0x0300, 0x0003): ParameterUnit.percentage,     # current_saturation
    (0x0300, 0x0007): ParameterUnit.plain,          # color_temperature_mireds — TODO: add mired
    (0x0300, 0x4010): ParameterUnit.plain,          # color_temp_physical_min_mireds
    (0x0300, 0x4011): ParameterUnit.plain,          # color_temp_physical_max_mireds
    (0x0300, 0x4012): ParameterUnit.plain,          # couple_color_temp_to_level_min_mireds
    (0x0300, 0x4013): ParameterUnit.plain,          # start_up_color_temperature

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
    (0x0402, 0x0010): ParameterUnit.celsius,        # tolerance

    # Pressure Measurement (0x0403)
    (0x0403, 0x0000): ParameterUnit.pascal,         # hPa — nearest in enum
    (0x0403, 0x0001): ParameterUnit.pascal,
    (0x0403, 0x0002): ParameterUnit.pascal,
    (0x0403, 0x0010): ParameterUnit.pascal,
    (0x0403, 0x0011): ParameterUnit.pascal,
    (0x0403, 0x0012): ParameterUnit.pascal,

    # Flow Measurement (0x0404)
    (0x0404, 0x0000): ParameterUnit.mps,            # m³/h — nearest kinematic
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
    (0x042A, 0x0000): ParameterUnit.ppm,            # µg/m³ — TODO: add ugm3 
    (0x042A, 0x0001): ParameterUnit.ppm,
    (0x042A, 0x0002): ParameterUnit.ppm,

    # Electrical Conductivity (0x040B)
    (0x040B, 0x0000): ParameterUnit.plain,          # µS/cm — not in enum

    # -------------------------
    # HVAC
    # -------------------------

    # Thermostat (0x0201)
    (0x0201, 0x0000): ParameterUnit.celsius,        # local_temperature
    (0x0201, 0x0001): ParameterUnit.celsius,        # outdoor_temperature
    (0x0201, 0x0003): ParameterUnit.celsius,        # abs_min_heat_setpoint_limit
    (0x0201, 0x0004): ParameterUnit.celsius,        # abs_max_heat_setpoint_limit
    (0x0201, 0x0005): ParameterUnit.celsius,        # abs_min_cool_setpoint_limit
    (0x0201, 0x0006): ParameterUnit.celsius,        # abs_max_cool_setpoint_limit
    (0x0201, 0x0007): ParameterUnit.percentage,     # pi_cooling_demand
    (0x0201, 0x0008): ParameterUnit.percentage,     # pi_heating_demand
    (0x0201, 0x0010): ParameterUnit.celsius,        # local_temperature_calibration
    (0x0201, 0x0011): ParameterUnit.celsius,        # occupied_cooling_setpoint
    (0x0201, 0x0012): ParameterUnit.celsius,        # occupied_heating_setpoint
    (0x0201, 0x0013): ParameterUnit.celsius,        # unoccupied_cooling_setpoint
    (0x0201, 0x0014): ParameterUnit.celsius,        # unoccupied_heating_setpoint
    (0x0201, 0x0015): ParameterUnit.celsius,        # min_heat_setpoint_limit
    (0x0201, 0x0016): ParameterUnit.celsius,        # max_heat_setpoint_limit
    (0x0201, 0x0017): ParameterUnit.celsius,        # min_cool_setpoint_limit
    (0x0201, 0x0018): ParameterUnit.celsius,        # max_cool_setpoint_limit
    (0x0201, 0x0019): ParameterUnit.celsius,        # min_setpoint_dead_band

    # -------------------------
    # Electrical Measurement (0x0B04)
    # -------------------------
    (0x0B04, 0x0304): ParameterUnit.hertz,          # ac_frequency
    (0x0B04, 0x0305): ParameterUnit.hertz,          # ac_frequency_min
    (0x0B04, 0x0306): ParameterUnit.hertz,          # ac_frequency_max
    (0x0B04, 0x0505): ParameterUnit.volt,           # rms_voltage
    (0x0B04, 0x0506): ParameterUnit.volt,           # rms_voltage_min
    (0x0B04, 0x0507): ParameterUnit.volt,           # rms_voltage_max
    (0x0B04, 0x0508): ParameterUnit.ampere,         # rms_current
    (0x0B04, 0x0509): ParameterUnit.ampere,         # rms_current_min
    (0x0B04, 0x050A): ParameterUnit.ampere,         # rms_current_max
    (0x0B04, 0x050B): ParameterUnit.watt,           # active_power
    (0x0B04, 0x050C): ParameterUnit.watt,           # active_power_min
    (0x0B04, 0x050D): ParameterUnit.watt,           # active_power_max
    (0x0B04, 0x050E): ParameterUnit.watt,           # reactive_power
    (0x0B04, 0x050F): ParameterUnit.watt,           # apparent_power (VA → watt approximation)
    (0x0B04, 0x0510): ParameterUnit.percentage,     # power_factor
    (0x0B04, 0x0605): ParameterUnit.volt,           # phase_b rms_voltage
    (0x0B04, 0x0608): ParameterUnit.ampere,         # phase_b rms_current
    (0x0B04, 0x060B): ParameterUnit.watt,           # phase_b active_power
    (0x0B04, 0x0705): ParameterUnit.volt,           # phase_c rms_voltage
    (0x0B04, 0x0708): ParameterUnit.ampere,         # phase_c rms_current
    (0x0B04, 0x070B): ParameterUnit.watt,           # phase_c active_power

    # -------------------------
    # Metering (0x0702)
    # -------------------------
    (0x0702, 0x0000): ParameterUnit.joule,          # current_summation_delivered — TODO: add kwh
    (0x0702, 0x0001): ParameterUnit.joule,          # current_summation_received
    (0x0702, 0x0002): ParameterUnit.joule,          # current_max_demand_delivered
    (0x0702, 0x0003): ParameterUnit.joule,          # current_max_demand_received
    (0x0702, 0x0400): ParameterUnit.watt,           # instantaneous_demand
    (0x0702, 0x0200): ParameterUnit.joule,          # current_day_consumption_delivered
    (0x0702, 0x0201): ParameterUnit.joule,          # previous_day_consumption_delivered

    # -------------------------
    # Closures
    # -------------------------

    # Window Covering (0x0102)
    (0x0102, 0x0008): ParameterUnit.percentage,     # current_position_lift_percentage
    (0x0102, 0x0009): ParameterUnit.percentage,     # current_position_tilt_percentage
    (0x0102, 0x000E): ParameterUnit.percentage,     # current_position_lift_percent100ths
    (0x0102, 0x000F): ParameterUnit.percentage,     # current_position_tilt_percent100ths

    # -------------------------
    # Device Temperature (0x0002)
    # -------------------------
    (0x0002, 0x0000): ParameterUnit.celsius,        # current_temperature
    (0x0002, 0x0001): ParameterUnit.celsius,        # min_temp_experienced
    (0x0002, 0x0002): ParameterUnit.celsius,        # max_temp_experienced
    (0x0002, 0x0010): ParameterUnit.celsius,        # low_temp_threshold
    (0x0002, 0x0011): ParameterUnit.celsius,        # high_temp_threshold

    # -------------------------
    # Power Configuration (0x0001)
    # -------------------------
    (0x0001, 0x0000): ParameterUnit.volt,           # mains_voltage
    (0x0001, 0x0001): ParameterUnit.hertz,          # mains_frequency
    (0x0001, 0x0010): ParameterUnit.volt,           # mains_voltage_min_threshold
    (0x0001, 0x0011): ParameterUnit.volt,           # mains_voltage_max_threshold
    (0x0001, 0x0020): ParameterUnit.volt,           # battery_voltage
    (0x0001, 0x0021): ParameterUnit.percentage,     # battery_percentage_remaining
}

# (cluster_id, attribute_id) -> min_step
ATTRIBUTE_MIN_STEP: dict[tuple[int, int], int | float] = {

    # Level Control (0x0008)
    (0x0008, 0x0000): 1,                # current_level

    # Color Control (0x0300)
    (0x0300, 0x0001): 1,                # current_hue
    (0x0300, 0x0003): 1,                # current_saturation
    (0x0300, 0x0007): 1,                # color_temperature_mireds

    # Thermostat (0x0201)
    (0x0201, 0x0010): 0.1,              # local_temperature_calibration
    (0x0201, 0x0011): 0.5,              # occupied_cooling_setpoint
    (0x0201, 0x0012): 0.5,              # occupied_heating_setpoint
    (0x0201, 0x0013): 0.5,             # unoccupied_cooling_setpoint
    (0x0201, 0x0014): 0.5,              # unoccupied_heating_setpoint

    # Temperature Measurement (0x0402)
    (0x0402, 0x0000): 0.01,             # resolution 0.01°C в ZCL

    # Relative Humidity (0x0405)
    (0x0405, 0x0000): 0.01,             # resolution 0.01%

    # Pressure Measurement (0x0403)
    (0x0403, 0x0000): 0.1,

    # Window Covering (0x0102)
    (0x0102, 0x0008): 1,
    (0x0102, 0x0009): 1,

    # Electrical Measurement (0x0B04)
    (0x0B04, 0x0505): 0.1,              # rms_voltage
    (0x0B04, 0x0508): 0.01,             # rms_current
    (0x0B04, 0x050B): 0.1,              # active_power

    # Metering (0x0702)
    (0x0702, 0x0000): 0.001,            # kWh
    (0x0702, 0x0400): 1,                # instantaneous_demand W
}


def get_unit(cluster_id: int, attribute_id: int) -> ParameterUnit:
    return ATTRIBUTE_UNITS.get((cluster_id, attribute_id), ParameterUnit.plain)


def get_min_step(cluster_id: int, attribute_id: int) -> int | float | None:
    return ATTRIBUTE_MIN_STEP.get((cluster_id, attribute_id))