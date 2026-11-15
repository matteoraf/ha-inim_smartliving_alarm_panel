"""Constants for the Inim Alarm integration."""

DOMAIN = "inim_smartliving_alarm"

# Platforms to be set up for this integration
PLATFORMS = ["alarm_control_panel", "binary_sensor", "button", "sensor", "switch"]

# Configuration keys used in config_flow.py and __init__.py
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PIN = "pin"
CONF_BOARD = "board"
CONF_USERNAME = "username"  # SmartLAN/G only
CONF_PASSWORD = "password"  # SmartLAN/G only
CONF_FOUND_BOARD = "found_board"  # setup: the board picked from a network search
CONF_POLLING_INTERVAL = "polling_interval"
CONF_PANEL_NAME = "panel_name"

# Keys for limiting imported entities and events
CONF_LIMIT_AREAS = "limit_areas"
CONF_LIMIT_ZONES = "limit_zones"
CONF_LIMIT_SCENARIOS = "limit_scenarios"
CONF_EVENT_LOG_SIZE = "event_log_size"
CONF_READER_NAMES = "reader_names"


# Scenario mapping keys (stored in ConfigEntry options, set by user in config flow)
CONF_SCENARIO_ARM_HOME = "scenario_arm_home"
CONF_SCENARIO_ARM_AWAY = "scenario_arm_away"
CONF_SCENARIO_ARM_NIGHT = "scenario_arm_night"
CONF_SCENARIO_ARM_VACATION = "scenario_arm_vacation"
CONF_SCENARIO_DISARM = "scenario_disarm"

# Board types (CONF_BOARD)
BOARD_SMARTLAN_SI = "smartlan_si"
BOARD_SMARTLAN_G = "smartlan_g"  # experimental: tested against a fake panel only

# Setup refuses panels with older firmware; 5.x is accepted with a warning
MIN_FIRMWARE_MAJOR = 5

# Defaults
DEFAULT_POLLING_INTERVAL = 30  # seconds
DEFAULT_PORT = 5004  # SmartLAN/SI default; also proposed for SmartLAN/G (no default)
DEFAULT_PANEL_NAME = "Inim Smartliving"  # Default if user doesn't provide one

# Limits of the entities to create. A new setup proposes the panel's own
# counts (areas A, zone numbers 1-2L); these defaults apply only to an entry
# without a stored limit and mean "everything". Users can reduce them.
MAX_LIMIT_AREAS = 15  # areas on the SmartLiving 10100
MAX_LIMIT_ZONES = 200  # zone numbers 1-2L; 2L = 200 on the SmartLiving 10100
DEFAULT_LIMIT_AREAS = MAX_LIMIT_AREAS
DEFAULT_LIMIT_ZONES = MAX_LIMIT_ZONES
DEFAULT_LIMIT_SCENARIOS = 30

# Default limiys for imported events
DEFAULT_EVENT_LOG_SIZE = 50  # Default number of events to store
SYSTEM_MAX_EVENT_LOG_SIZE = 100  # System limit of events to store, increasing this may cause exceeding the maximum size of 16384 bytes for the sensor attribute

# Data keys for hass.data[DOMAIN][entry.entry_id]
DATA_API_CLIENT = "api_client"
DATA_COORDINATOR = "coordinator"
DATA_INITIAL_PANEL_CONFIG = "initial_panel_config"
# Static configuration fetched by config_flow (e.g., zone names, scenario names)
# will be stored in entry.data and entry.options by the config_flow itself.
# For example, config_flow might store the result of get_initial_panel_configuration()
# in entry.data["initial_panel_config"]

# --- Keys for data structures ---
# These keys help access specific parts of the data fetched by the API
# and stored in initial_panel_config or provided by the coordinator.

# From initial_panel_config (entry.data[DATA_INITIAL_PANEL_CONFIG])
KEY_INIT_KEYBOARDS = "keyboard_names"
KEY_INIT_KEYBOARD_NAMES = "names"

KEY_INIT_SYSTEM_INFO = "system_info"
KEY_INIT_SYSTEM_INFO_TYPE = "type"
KEY_INIT_SYSTEM_INFO_VERSION = "version"

KEY_INIT_SCENARIOS = "scenarios"  # Dict containing list of scenario names
KEY_INIT_SCENARIO_NAMES = "names"  # List of scenario names (str)

KEY_INIT_SCENARIO_ACTIVATIONS = (
    "scenario_activations"  # List of dicts, each detailing a scenario's actions
)
KEY_INIT_SCENARIO_ACTIVATION_INDEX = (
    "scenario_index"  # Key for 0-indexed scenario number in activation details
)
KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS = (
    "area_actions"  # Dict within activation detail: {area_id_str: "action_str"}
)

KEY_INIT_ZONES = "zones"  # Dict containing list of zone names
KEY_INIT_ZONE_NAMES = "zone_names"  # List of zone names (str)

KEY_INIT_ZONES_CONFIG = "zones_config"  # Dict containing detailed zone configurations
KEY_INIT_ZONES_CONFIG_DETAILED = (
    "zones_config_detailed"  # List of dicts, each a detailed zone config
)
KEY_INIT_TERMINAL_RECORDS_RAW = (
    "raw_hex_part2_full_response"  # in zones_config: logical terminal records, hex
)
KEY_INIT_ZONE_CONFIG_INDEX = (
    "zone_index"  # Key for 0-indexed zone number in detailed config
)
KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS = (
    "assigned_areas"  # Key for list of area_ids (int) a zone belongs to
)

KEY_INIT_AREAS = "areas"  # Dict containing list of area names
KEY_INIT_AREA_NAMES = "names"  # List of area names (str)

# From coordinator.data (live status snapshot)
KEY_LIVE_ACTIVE_SCENARIO = "active_scenario"  # Dict with current active scenario info
KEY_LIVE_ACTIVE_SCENARIO_NUMBER = "active_scenario_number"  # 0-indexed int

KEY_LIVE_AREAS_STATUS = "areas_status"  # Dict with current area statuses
KEY_LIVE_AREA_STATUSES_MAP = (
    "area_statuses"  # {area_id: "armed" (any mode) / "disarmed" / "unknown"}
)
KEY_LIVE_AREA_MODES_MAP = "area_modes"  # {area_id: an AREA_MODE_* value or "unknown"}
KEY_LIVE_ALARM_AREAS_LIST = "alarm_areas"  # areas (int) in alarm now
KEY_LIVE_TAMPER_AREAS_LIST = "tamper_areas"  # areas (int) in tamper now
KEY_LIVE_TRIGGERED_AREAS_LIST = "triggered_areas"  # areas (int) with alarm memory
KEY_LIVE_TAMPER_MEMORY_AREAS_LIST = "tamper_memory_areas"  # areas with tamper memory
KEY_LIVE_AUTO_ARM_AREAS_LIST = "auto_arm_areas"  # areas with auto-arm enabled

# Area arming modes as reported in KEY_LIVE_AREA_MODES_MAP
AREA_MODE_TOTAL = "total"
AREA_MODE_PARTIAL = "partial"
AREA_MODE_INSTANT = "instant"
AREA_MODE_DISARMED = "disarmed"

KEY_LIVE_ZONES_STATUS = "zones_status"  # Dict with current zone statuses
KEY_LIVE_ZONE_STATUSES_MAP = (
    "zone_statuses"  # Dict within zones_status: {zone_id_1_based_str: "status_str"}
)
KEY_LIVE_ZONES_EXCLUDED_STATUS = "zones_excluded_status"
KEY_LIVE_ZONE_EXCLUDED_STATUSES_MAP = "zone_excluded_statuses"

KEY_LIVE_ZONES_TRIGGERED_STATUS = "zones_triggered_status"
KEY_LIVE_ZONE_TRIGGERED_STATUSES_MAP = "zone_triggered_statuses"
KEY_LIVE_ZONE_TAMPER_MEMORY_STATUSES_MAP = "zone_tamper_memory_statuses"  # {zone: bool}
KEY_LIVE_ZONE_TEST_STATUSES_MAP = "zone_test_statuses"  # in zones_excluded_status

KEY_PROCESSED_EVENTS = "processed_events"  # List of new events from last poll
KEY_LATEST_EVENT_INDEX_VAL = (
    "latest_event_index_val"  # Panel's absolute latest event index
)

# General usage keys for constructed/parsed data (often used internally in utils or entities)
# These might overlap with above if the structure is already ideal.
# These are more for semantic clarity within the utility function if needed.
KEY_SCENARIO_ID = "id"  # Typically scenario_index_0_based
KEY_SCENARIO_NAME = "name"
KEY_SCENARIO_AREAS_CONTROLLED = (
    "areas_controlled"  # List of area_ids this scenario affects
)

KEY_ZONE_ID = "id"  # Typically zone_index_0_based or zone_id_1_based
KEY_ZONE_NAME = "name"
KEY_ZONE_STATUS = "status"  # Live status string like "alarmed", "clear"
KEY_ZONE_ASSIGNED_AREAS = "assigned_areas"  # Re-using from init if structure matches

KEY_AREA_ID = "id"  # Typically area_id_1_based
KEY_AREA_NAME = "name"

# Zone states as reported in KEY_LIVE_ZONE_STATUSES_MAP
ZONE_STATE_CLEAR = "clear"  # at rest
ZONE_STATE_ALARMED = "alarmed"  # open
ZONE_STATE_SHORT = "short"  # short circuit
ZONE_STATE_TAMPER = "tamper"
ZONE_TAMPER_STATES = (ZONE_STATE_SHORT, ZONE_STATE_TAMPER)

# Zone states that prevent a scenario from being activated
PROBLEM_ZONE_STATES_FOR_ARMING = [ZONE_STATE_ALARMED, *ZONE_TAMPER_STATES]
# --- End Data Structure Keys ---

# --- Sensor Entity Suffixes/Keys ---
SENSOR_LAST_EVENT_LOG_SUFFIX = "event_log"  # For the new event log sensor

# Define the new service name as a constant
SERVICE_ALARM_RESET = "alarm_reset"
SERVICE_REREAD_PANEL_CONFIGURATION = "reread_panel_configuration"
