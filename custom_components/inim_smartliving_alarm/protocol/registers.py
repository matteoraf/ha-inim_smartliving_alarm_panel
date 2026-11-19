"""RAM addresses used by the integration."""

# Identification
MODEL_STRING = 0x4000  # 12 ASCII bytes, e.g. "6.07 01050  "
MODEL_STRING_LENGTH = 12
SERIAL_NUMBER = 0xFEA0  # production serial number, 16 ASCII digits
SERIAL_NUMBER_LENGTH = 16
HARDWARE_SIGNATURE = 0xFECC  # 2 bytes, B2 55
BRAND = 0xFED2  # 2 bytes LE, 0 = standard Inim

# Status registers (lengths depend on the model, see PanelModel)
AREA_STATUS = 0x2000
ZONE_STATE = 0x2001
ZONE_INCLUSION = 0x2002
ZONE_MEMORY = 0x2003
COMMAND_RESULT = 0x2004  # 1 byte
FAULTS = 0x2005  # fault flags, supply voltage, then the panel state flags
FAULTS_LENGTH = 6
PANEL_STATE_OFFSET = 4  # panel state flags (stato_centrale): bytes 4-5 of FAULTS
PANEL_STATE_LENGTH = 2

# Command registers (written with the PIN first)
ARM_AREAS = 0x2006
SET_OUTPUT = 0x2007
RESET_ALARM_MEMORY = 0x2008
BYPASS_ZONE = 0x2009
ACTIVATE_SCENARIO = 0x200A
STOP_SIRENS_OR_CALLS = 0x200B

# Registers whose request fields carry parameters (see frames.py)
NOT_READY_ZONES = 0x1FF9
ACTIVE_SCENARIO = 0x1FFB
OUTPUT_DETAIL = 0x1FFD  # 8 bytes per output terminal (firmware 6)
EVENT_POINTERS = 0x1FFE  # 4 bytes: head, tail (u16 LE each)
EVENT_POINTERS_LENGTH = 4

# Label pointers: 2 bytes LE each, plus the model's label bank
LABELS_AREAS = 0x4014
LABELS_ZONES = 0x4016
LABELS_CODES = 0x4018
LABELS_READERS = 0x401A
LABELS_KEYPADS = 0x401C
LABELS_EXPANDERS = 0x401E
LABELS_KEYS = 0x4020
LABELS_PHONES = 0x4022
LABELS_SCENARIOS = 0x4024
LABELS_TIMERS = 0x4026
LABELS_FIXED_OUTPUTS = 0x4028
LABELS_SIRENS = 0x402E
LABELS_VIDEO_SENSORS = 0x403A
