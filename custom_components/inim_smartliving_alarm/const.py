"""Constants for the Inim Alarm integration."""

from .protocol import smartlan

DOMAIN = "inim_smartliving_alarm"

# Platforms to be set up for this integration
PLATFORMS = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "climate",
    "light",
    "select",
    "sensor",
    "switch",
]

# Configuration keys used in config_flow.py and __init__.py
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PIN = "pin"
CONF_BOARD = "board"
CONF_USERNAME = "username"  # SmartLAN/G only
CONF_PASSWORD = "password"  # SmartLAN/G only
CONF_FOUND_BOARD = "found_board"  # setup: the board picked from a network search
# Removed option: the single polling interval, replaced by the polling groups
CONF_POLLING_INTERVAL = "polling_interval"
CONF_PANEL_NAME = "panel_name"

# The areas, zones and scenarios that get entities: lists of 1-based numbers
# in the entry options
CONF_AREAS = "areas"
CONF_ZONES = "zones"
CONF_SCENARIOS = "scenarios"
CONF_OUTPUTS = "outputs"  # output numbers: terminals 1-L, then O1-O3
# Keypads (1-based) whose thermostat gets entities; absent until the keypads
# have been checked once (at setup, or at the first start of an older entry)
CONF_THERMOSTATS = "thermostats"
# Counts stored by versions before the lists ("the first N"); migrated to them
CONF_LIMIT_AREAS = "limit_areas"
CONF_LIMIT_ZONES = "limit_zones"
CONF_LIMIT_SCENARIOS = "limit_scenarios"
CONF_EVENT_LOG_SIZE = "event_log_size"
CONF_READER_NAMES = "reader_names"  # removed option (names come from the panel)
# How often each polling group is read, in seconds (see polling.py); stored
# only when set otherwise than the default
CONF_POLL_INTERVAL_ZONES = "poll_interval_zones"
CONF_POLL_INTERVAL_AREAS = "poll_interval_areas"
CONF_POLL_INTERVAL_INCLUSION = "poll_interval_inclusion"
CONF_POLL_INTERVAL_EVENTS = "poll_interval_events"
CONF_POLL_INTERVAL_FAULTS = "poll_interval_faults"
CONF_POLL_INTERVAL_THERMOSTATS = "poll_interval_thermostats"
# Slow polling mode (SmartLAN/SI): the reading interval in seconds, and the
# minutes after which the mode turns itself off (0: until turned off);
# stored only when set otherwise than the default
CONF_SLOW_POLLING_INTERVAL = "slow_polling_interval"
CONF_SLOW_POLLING_DURATION = "slow_polling_duration"


# Scenario mapping keys (stored in ConfigEntry options, set by user in config flow)
CONF_SCENARIO_ARM_HOME = "scenario_arm_home"
CONF_SCENARIO_ARM_AWAY = "scenario_arm_away"
CONF_SCENARIO_ARM_NIGHT = "scenario_arm_night"
CONF_SCENARIO_ARM_VACATION = "scenario_arm_vacation"
CONF_SCENARIO_DISARM = "scenario_disarm"

# Board types (CONF_BOARD)
BOARD_SMARTLAN_SI = smartlan.BOARD_SMARTLAN_SI
BOARD_SMARTLAN_G = smartlan.BOARD_SMARTLAN_G

# Setup refuses firmware older than 5.1 and models without their own layout

# Defaults
DEFAULT_POLL_INTERVAL_ZONES = 1.0  # seconds
DEFAULT_POLL_INTERVAL_AREAS = 2.0
DEFAULT_POLL_INTERVAL_INCLUSION = 30.0
DEFAULT_POLL_INTERVAL_EVENTS = 10.0
DEFAULT_POLL_INTERVAL_FAULTS = 60.0
DEFAULT_POLL_INTERVAL_THERMOSTATS = 60.0
DEFAULT_SLOW_POLLING_INTERVAL = 30.0  # seconds
DEFAULT_SLOW_POLLING_DURATION = 60.0  # minutes
DEFAULT_PORT = 5004  # SmartLAN/SI default; also proposed for SmartLAN/G (no default)
DEFAULT_PANEL_NAME = "Inim Smartliving"  # Default if user doesn't provide one

# Counts an entry migrated from the limits uses when none was stored:
# everything
MAX_LIMIT_AREAS = 15  # areas on the SmartLiving 10100
MAX_LIMIT_ZONES = 200  # zone numbers 1-2L; 2L = 200 on the SmartLiving 10100
DEFAULT_LIMIT_AREAS = MAX_LIMIT_AREAS
DEFAULT_LIMIT_ZONES = MAX_LIMIT_ZONES
DEFAULT_LIMIT_SCENARIOS = 30

# Default limits for imported events
DEFAULT_EVENT_LOG_SIZE = 50  # Default number of events to store
SYSTEM_MAX_EVENT_LOG_SIZE = 100  # System limit of events to store, increasing this may cause exceeding the maximum size of 16384 bytes for the sensor attribute

# Data keys for hass.data[DOMAIN][entry.entry_id]
DATA_PANEL = "panel"  # the entry's SmartLivingPanel
DATA_COORDINATOR = "coordinator"
DATA_ALARM_PANEL = "alarm_panel"  # the entry's alarm panel entity
DATA_SETUP_OPTIONS = "setup_options"  # the options the entry was set up with
# entry.data key: the panel configuration read at setup or Re-read, as
# stored by stored_config
DATA_INITIAL_PANEL_CONFIG = "initial_panel_config"

# Area arming modes as entity attributes (attribute_values.area_mode_text)
AREA_MODE_TOTAL = "total"
AREA_MODE_PARTIAL = "partial"
AREA_MODE_INSTANT = "instant"
AREA_MODE_DISARMED = "disarmed"

# Zone states as entity attributes (attribute_values.zone_state_text)
ZONE_STATE_CLEAR = "clear"  # at rest
ZONE_STATE_ALARMED = "alarmed"  # open
ZONE_STATE_SHORT = "short"  # short circuit
ZONE_STATE_TAMPER = "tamper"
ZONE_TAMPER_STATES = (ZONE_STATE_SHORT, ZONE_STATE_TAMPER)

# Zone states that prevent a scenario from being activated
PROBLEM_ZONE_STATES_FOR_ARMING = [ZONE_STATE_ALARMED, *ZONE_TAMPER_STATES]

# --- Sensor Entity Suffixes/Keys ---
SENSOR_LAST_EVENT_LOG_SUFFIX = "event_log"  # For the new event log sensor

# Define the new service name as a constant
SERVICE_ALARM_RESET = "alarm_reset"
SERVICE_REREAD_PANEL_CONFIGURATION = "reread_panel_configuration"
SERVICE_ARM_AREA = "arm_area"
SERVICE_ACTIVATE_SCENARIO = "activate_scenario"
# Fired when zones are not ready for arming areas or activating a scenario
EVENT_ZONES_NOT_READY = f"{DOMAIN}_zones_not_ready"
ATTR_AREAS = "areas"
ATTR_MODE = "mode"
ATTR_SCENARIO = "scenario"
ATTR_FORCE_ARM = "force"  # arm even when zones are not ready
# arm_area modes (also the values of the area switch's arm mode option)
ARM_MODE_TOTAL = "total"
ARM_MODE_PARTIAL = "partial"
ARM_MODE_INSTANT = "instant"
ARM_MODE_DISARM = "disarm"
# Option: the mode every area switch arms in (total/partial/instant)
CONF_AREA_SWITCHES_DEFAULT_ARM_MODE = "area_switches_default_arm_mode"
DEFAULT_AREA_SWITCHES_ARM_MODE = ARM_MODE_TOTAL
# Option: the mode of single areas' switches, {"3": "partial"}; areas not
# listed use CONF_AREA_SWITCHES_DEFAULT_ARM_MODE
CONF_AREA_SWITCH_ARM_MODE_BY_AREA = "area_switch_arm_mode_by_area"
AREA_SWITCH_USE_DEFAULT = "default"
# Option: what to do when zones are not ready for arming
CONF_ZONES_NOT_READY = "zones_not_ready"
ZONES_NOT_READY_REFUSE = "refuse"
ZONES_NOT_READY_WARN = "warn"
DEFAULT_ZONES_NOT_READY = ZONES_NOT_READY_REFUSE
