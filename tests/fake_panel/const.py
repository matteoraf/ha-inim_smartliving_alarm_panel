"""Memory layout of the fake panel: a SmartLiving 1050 on firmware 6.x by
default (the module constants), or another model through a Layout.

These values are kept separate from the integration on purpose, so that tests
compare the integration against an independent description of the panel.
"""

from dataclasses import dataclass, field, replace

# Panel size
NUM_AREAS = 10
NUM_TERMINALS = 50  # logical terminals; zone index space is 2 * NUM_TERMINALS
NUM_ZONES = 2 * NUM_TERMINALS
NUM_PHYSICAL_SLOTS = 130
NUM_CODES = 50
NUM_SCENARIOS = 30
NUM_KEYPADS = 10
EVENT_RING_SIZE = 1000  # event log ring on the 1050 with firmware 6.x
LABEL_LENGTH = 16
# Some of the panel's label characters outside ASCII, as the panel encodes them
LABEL_CHARACTERS = {"É": 0x8A, "é": 0x8C, "à": 0x94, "è": 0x98, "ù": 0x9E, "ä": 0xAA}
MAX_READ_LENGTH = 250  # longest single read on SmartLAN/SI
MAX_READ_LENGTH_G = 2000  # longest single read on SmartLAN/G

# Wire values of the DEVICE byte (SmartLAN/SI encoding)
DEVICE_RAM = 0x00
DEVICE_EEPROM = 0x01
DEVICE_FLASH = 0x02
DEVICE_KEYPAD = 0x01  # with a peripheral opcode (0x10 / 0x11)

# Logical device codes, sent as-is by SmartLAN/G clients
LOGICAL_EEPROM = 0xFF
LOGICAL_FLASH = 0xFE
LOGICAL_EEPROM_NO_PROG = 0xFD

OP_READ = 0x00
OP_WRITE = 0x01
OP_PERIPHERAL_READ = 0x10
OP_PERIPHERAL_WRITE = 0x11

# RAM registers
RAM_FIRMWARE = 0x4000
FIRMWARE_LENGTH = 12
RAM_BRAND = 0xFED2
RAM_HW_SIGNATURE = 0xFECC
RAM_SERIAL_NUMBER = 0xFEA0  # production serial area; 52 bytes reach the brand word
SERIAL_NUMBER_LENGTH = 16  # ASCII digits at the start of the area
# Panel state flags (stato_centrale), 2 bytes; also bytes 4-5 of RAM_FAULTS
RAM_PANEL_STATE = 0x0D28
PANEL_STATE_NO_AREA_ARMED = 0x08  # byte 1, bit 3
PANEL_STATE_PROGRAMMING = 0x40  # byte 1, bit 6
PANEL_STATE_NO_VOICE_BOARD = 0x80  # byte 1, bit 7
# Bits of unknown meaning, as read on a 1050 with firmware 6.07 while idle
PANEL_STATE_UNKNOWN_BYTE_0 = 0x0A
PANEL_STATE_UNKNOWN_BITS = 0x14
RAM_AREA_STATUS = 0x2000
RAM_ZONE_STATE = 0x2001
RAM_ZONE_INCLUSION = 0x2002
RAM_ZONE_MEMORY = 0x2003
RAM_COMMAND_RESULT = 0x2004
RAM_FAULTS = 0x2005
RAM_CMD_ARM = 0x2006
RAM_CMD_OUTPUT = 0x2007
RAM_CMD_RESET = 0x2008
RAM_CMD_BYPASS = 0x2009
RAM_CMD_SCENARIO = 0x200A
RAM_CMD_STOP = 0x200B
RAM_NOT_READY = 0x1FF9
RAM_ACTIVE_SCENARIO = 0x1FFB
RAM_OUTPUT_DETAIL = 0x1FFD
RAM_EVENT_POINTERS = 0x1FFE
RAM_EVENT_TEXT = 0x1FFF

# Label pointer table (each entry: 2-byte little-endian offset into EEPROM bank 1)
RAM_LABELS_AREAS = 0x4014
RAM_LABELS_ZONES = 0x4016
RAM_LABELS_CODES = 0x4018
RAM_LABELS_READERS = 0x401A
RAM_LABELS_KEYPADS = 0x401C
RAM_LABELS_EXPANDERS = 0x401E
RAM_LABELS_KEYS = 0x4020
RAM_LABELS_PHONES = 0x4022
RAM_LABELS_SCENARIOS = 0x4024
RAM_LABELS_TIMERS = 0x4026
RAM_LABELS_OUTPUTS = 0x4028
RAM_LABELS_SIRENS = 0x402E
RAM_LABELS_VIDEO_SENSORS = 0x403A  # firmware 6
LABEL_BANK = 0x10000

# Status read lengths
AREA_STATUS_LENGTH = NUM_AREAS // 2 + 1 + 10  # 16
AREA_MODE_BYTES = NUM_AREAS // 2 + 1  # 6
ZONE_STATE_LENGTH = NUM_TERMINALS // 2 + 1  # 26
ZONE_BITMAP_LENGTH = NUM_TERMINALS // 4 + 1  # 13
ZONE_INCLUSION_LENGTH = 2 * ZONE_BITMAP_LENGTH  # 26
ZONE_MEMORY_LENGTH = (
    ZONE_BITMAP_LENGTH + (NUM_TERMINALS // 8 + 1) + (NUM_TERMINALS // 8 + 2)
)  # 28
OUTPUT_IMAGE_OFFSET = ZONE_BITMAP_LENGTH + NUM_TERMINALS // 8 + 1  # 20
FAULTS_LENGTH = 6
EVENT_TEXT_LENGTH = 366

# EEPROM structures (24-bit addresses)
# Enabled devices, one bit each, at the same address on every model:
# kind -> (offset in the block, bytes)
EEPROM_PERIPHERALS = 0x00000
# General options; byte 1 bit 5: refuse arming with zones not ready
EEPROM_GENERAL_OPTIONS = 0x00017  # firmware 6; 0x00015 on firmware 5.1
PERIPHERAL_BITMAPS = {
    "readers": (0, 4),
    "expanders": (4, 6),
    "keypads": (10, 2),
    "sirens": (12, 2),
}
EEPROM_PINS = 0x061F4
PIN_LENGTH = 6
EEPROM_CODES = 0x0632C
CODE_RECORD_LENGTH = 59
CODE_OUTPUT_MASK_OFFSET = 50
CODE_OUTPUT_MASK_LENGTH = 8
EEPROM_ZONES = 0x05450
ZONE_RECORD_LENGTH = 9
EEPROM_TERMINALS = 0x057D4
TERMINAL_RECORD_LENGTH = 12
TERMINAL_TYPE_OFFSET = 11
EEPROM_PHYSICAL_MAP = 0x078FD
EEPROM_SCENARIO_MODES = 0x073D8
SCENARIO_RECORD_LENGTH = 8
EEPROM_EVENTS = 0x19204
EVENT_RECORD_LENGTH = 9

# Peripheral registers
KEYPAD_TEMPERATURE = 0x075A

# Terminal types (byte 11 of a logical terminal record)
TERMINAL_INPUT = 0
TERMINAL_OUTPUT = 1
TERMINAL_IN_OUT = 2
TERMINAL_DOUBLE = 3
TERMINAL_NULL = 4
TERMINAL_OUT_DIMMING = 5

# Area arming nibbles
MODE_NONE = 0
MODE_AWAY = 1
MODE_STAY = 2
MODE_INSTANT = 3
MODE_DISARMED = 4

# Zone states (2 bits per zone in RAM_ZONE_STATE)
ZONE_SHORT = 0
ZONE_REST = 1
ZONE_ALARM = 2
ZONE_TAMPER = 3

# Command results (RAM_COMMAND_RESULT)
RESULT_OK = 0
RESULT_INVALID_CODE = 1
RESULT_OPEN_ZONES = 2  # arming / scenario
RESULT_ALREADY_IN_STATE = 2  # bypass / output
RESULT_CODE_NOT_ENABLED = 3  # arming / scenario
RESULT_SLOT_OUT_OF_RANGE = 3  # bypass / output
RESULT_SLOT_NOT_CONNECTED = 4
RESULT_NULL_TERMINAL = 5
RESULT_NOT_ENABLED = 6  # zone cannot be bypassed / output not enabled
RESULT_DOUBLE_ZONE = 8
RESULT_OUTPUT_LOCKED = 7  # output: its areas are armed

# Event codes (bits 0-6 of the last byte of a 9-byte event record)
EVENT_ZONE_ALARM = 0
EVENT_ZONE_BYPASS = 5
EVENT_AREA_ARMED = 10
EVENT_AREA_DISARMED = 12
EVENT_AREA_RESET = 13
EVENT_SCENARIO = 30
EVENT_ACTIVATION = 0x80
# Firmware 5.1 numbers its events differently from code 12 on (it has no
# "area disarmed" event): firmware 6 code -> firmware 5.1 code
FW51_EVENT_CODES = {
    EVENT_ZONE_ALARM: 0,
    EVENT_ZONE_BYPASS: 5,
    EVENT_AREA_ARMED: 10,
    EVENT_AREA_RESET: 12,
    EVENT_SCENARIO: 29,
}
LOCATION_NETWORK = 0xFE
NO_SCENARIO = 0xFF


# ------------------------------------------------------------- model layouts


@dataclass(frozen=True)
class Layout:
    """Counts, record sizes and EEPROM addresses of one panel model (FW 6.x).

    Typed independently of the integration's own model profiles, so that
    tests compare the two.
    """

    model: str  # model field of the firmware string, e.g. "01050"
    areas: int
    terminals: int
    physical_slots: int
    codes: int
    keypads: int
    readers: int
    expanders: int
    keys: int
    timers: int
    code_record_length: int
    code_output_mask_length: int
    scenario_record_length: int
    eeprom_pins: int
    eeprom_codes: int
    eeprom_zones: int
    eeprom_terminals: int
    eeprom_physical_map: int
    eeprom_areas: int  # area records, 7 bytes each
    eeprom_scenario_modes: int
    eeprom_events: int
    event_ring_size: int
    # pointer register -> (offset in the label bank, number of labels)
    labels: dict[int, tuple[int, int]] = field(hash=False, compare=False)
    label_bank: int = LABEL_BANK  # bank 0 on the 505 and 515
    # Bank 0 setup block: peripherals (2 more bytes of video sensors on
    # firmware 6), then the general options
    peripherals_length: int = 22
    general_options_address: int = EEPROM_GENERAL_OPTIONS
    firmware_51: bool = False  # firmware 5.1 event numbering
    # O1-O3 records (monostable time, options), 2 bytes each
    eeprom_fixed_outputs: int = 0x05A2C
    # Extra output options, L + 3 bytes (bit 0: home automation); firmware 6
    eeprom_extra_output_options: int | None = 0x08C7D

    @property
    def zones(self) -> int:
        """Zone index space: 2L."""
        return 2 * self.terminals

    @property
    def area_mode_bytes(self) -> int:
        return self.areas // 2 + 1

    @property
    def zone_state_length(self) -> int:
        return self.terminals // 2 + 1

    @property
    def zone_bitmap_length(self) -> int:
        return self.terminals // 4 + 1

    @property
    def terminal_bitmap_length(self) -> int:
        return self.terminals // 8 + 1

    @property
    def zone_memory_length(self) -> int:
        return (
            self.zone_bitmap_length
            + self.terminal_bitmap_length
            + self.terminals // 8
            + 2
        )

    @property
    def output_image_offset(self) -> int:
        return self.zone_bitmap_length + self.terminal_bitmap_length


def _packed_labels(
    counts: dict[int, int], start: int = 0
) -> dict[int, tuple[int, int]]:
    """Label blocks one after the other from an offset of the label bank."""
    labels, offset = {}, start
    for register, count in counts.items():
        labels[register] = (offset, count)
        offset += count * LABEL_LENGTH
    return labels


LAYOUT_1050 = Layout(
    model="01050",
    areas=NUM_AREAS,
    terminals=NUM_TERMINALS,
    physical_slots=NUM_PHYSICAL_SLOTS,
    codes=NUM_CODES,
    keypads=NUM_KEYPADS,
    readers=20,
    expanders=20,
    keys=100,
    timers=10,
    code_record_length=CODE_RECORD_LENGTH,
    code_output_mask_length=CODE_OUTPUT_MASK_LENGTH,
    scenario_record_length=SCENARIO_RECORD_LENGTH,
    eeprom_pins=EEPROM_PINS,
    eeprom_codes=EEPROM_CODES,
    eeprom_zones=EEPROM_ZONES,
    eeprom_terminals=EEPROM_TERMINALS,
    eeprom_physical_map=EEPROM_PHYSICAL_MAP,
    eeprom_areas=0x06126,
    eeprom_scenario_modes=EEPROM_SCENARIO_MODES,
    eeprom_events=EEPROM_EVENTS,
    event_ring_size=EVENT_RING_SIZE,
    # Areas, zones, keypads and scenarios match captured requests; the
    # others are placed in the gaps between them.
    labels={
        RAM_LABELS_AREAS: (0x0000, NUM_AREAS),
        RAM_LABELS_ZONES: (0x00A0, NUM_ZONES),
        RAM_LABELS_CODES: (0x06E0, NUM_CODES),
        RAM_LABELS_READERS: (0x0A00, 20),
        RAM_LABELS_KEYPADS: (0x0B40, NUM_KEYPADS),
        RAM_LABELS_EXPANDERS: (0x0BE0, 20),
        RAM_LABELS_KEYS: (0x0D20, 100),
        RAM_LABELS_PHONES: (0x1360, 15),
        RAM_LABELS_SCENARIOS: (0x1450, NUM_SCENARIOS),
        RAM_LABELS_TIMERS: (0x1630, 10),
        RAM_LABELS_OUTPUTS: (0x16D0, 3),
        RAM_LABELS_SIRENS: (0x1700, 10),
        RAM_LABELS_VIDEO_SENSORS: (0x17A0, 10),
    },
)

# SmartLiving 10100, firmware 6.x
LAYOUT_10100 = Layout(
    model="10100",
    areas=15,
    terminals=100,
    physical_slots=240,
    codes=100,
    keypads=15,
    readers=30,
    expanders=40,
    keys=150,
    timers=20,
    code_record_length=65,
    code_output_mask_length=14,
    scenario_record_length=10,
    eeprom_pins=0x0A4CD,
    eeprom_codes=0x0A731,
    eeprom_zones=0x089C5,
    eeprom_terminals=0x090CD,
    eeprom_physical_map=0x0CF75,
    eeprom_areas=0x0A36B,
    eeprom_scenario_modes=0x0C81F,
    eeprom_events=0x19204,
    event_ring_size=1000,
    eeprom_fixed_outputs=0x0957D,
    eeprom_extra_output_options=0x0E7B9,
    labels=_packed_labels(
        {
            RAM_LABELS_AREAS: 15,
            RAM_LABELS_ZONES: 200,
            RAM_LABELS_CODES: 100,
            RAM_LABELS_READERS: 30,
            RAM_LABELS_KEYPADS: 15,
            RAM_LABELS_EXPANDERS: 40,
            RAM_LABELS_KEYS: 150,
            RAM_LABELS_PHONES: 15,
            RAM_LABELS_SCENARIOS: NUM_SCENARIOS,
            RAM_LABELS_TIMERS: 20,
            RAM_LABELS_OUTPUTS: 3,
            RAM_LABELS_SIRENS: 10,
            RAM_LABELS_VIDEO_SENSORS: 10,
        }
    ),
)


# Where each layout keeps its labels (bank 0 on the 505 and 515, and on
# every model on firmware 5.1): the start of the panel's label area
LABELS_505_515_FW6 = 0x072A0
LABELS_FW51 = 0x08100


def _small_labels(
    areas: int, zones: int, expanders: int, start: int = LABELS_505_515_FW6
) -> dict[int, tuple[int, int]]:
    """Labels of a 505 or 515: in bank 0, in the panel's label area.

    Firmware 6 adds the video sensors' labels.
    """
    video = {RAM_LABELS_VIDEO_SENSORS: 10} if start == LABELS_505_515_FW6 else {}
    return _packed_labels(
        {
            RAM_LABELS_AREAS: areas,
            RAM_LABELS_ZONES: zones,
            RAM_LABELS_CODES: 30,
            RAM_LABELS_READERS: 10,
            RAM_LABELS_KEYPADS: 5,
            RAM_LABELS_EXPANDERS: expanders,
            RAM_LABELS_KEYS: 50,
            RAM_LABELS_PHONES: 15,
            RAM_LABELS_SCENARIOS: NUM_SCENARIOS,
            RAM_LABELS_TIMERS: 10,
            RAM_LABELS_OUTPUTS: 3,
            RAM_LABELS_SIRENS: 10,
            **video,
        },
        start=start,
    )


# SmartLiving 505, firmware 6.x
LAYOUT_505 = Layout(
    model="00505",
    areas=5,
    terminals=10,
    physical_slots=40,
    codes=30,
    keypads=5,
    readers=10,
    expanders=4,
    keys=50,
    timers=10,
    code_record_length=54,
    code_output_mask_length=3,
    scenario_record_length=5,
    eeprom_pins=0x03132,
    eeprom_codes=0x031F2,
    eeprom_zones=0x028BD,
    eeprom_terminals=0x02971,
    eeprom_physical_map=0x03DE0,
    eeprom_areas=0x030E3,
    eeprom_scenario_modes=0x03B0A,
    eeprom_events=0x0EE5C,
    event_ring_size=500,
    labels=_small_labels(areas=5, zones=20, expanders=5),
    label_bank=0,
    eeprom_fixed_outputs=0x029E9,
    eeprom_extra_output_options=0x04CCE,
)

# SmartLiving 515, firmware 6.x
LAYOUT_515 = Layout(
    model="00515",
    areas=5,
    terminals=20,
    physical_slots=70,
    codes=30,
    keypads=5,
    readers=10,
    expanders=10,
    keys=50,
    timers=10,
    code_record_length=55,
    code_output_mask_length=4,
    scenario_record_length=5,
    eeprom_pins=0x038C7,
    eeprom_codes=0x03987,
    eeprom_zones=0x02F07,
    eeprom_terminals=0x0306F,
    eeprom_physical_map=0x04595,
    eeprom_areas=0x03859,
    eeprom_scenario_modes=0x042BF,
    eeprom_events=0x0EE5C,
    event_ring_size=500,
    labels=_small_labels(areas=5, zones=40, expanders=10),
    label_bank=0,
    eeprom_fixed_outputs=0x0315F,
    eeprom_extra_output_options=0x054BF,
)


# Firmware 5.1x: other addresses, the setup block 2 bytes lower, other event
# numbering; the 1050 has its labels in bank 0 and 500 events
_FW51 = {
    "peripherals_length": 20,
    "general_options_address": 0x00015,
    "firmware_51": True,
    "eeprom_extra_output_options": None,  # not known on firmware 5.1
}

LAYOUT_1050_FW51 = replace(
    LAYOUT_1050,
    eeprom_pins=0x052AA,
    eeprom_codes=0x053E2,
    eeprom_zones=0x0451A,
    eeprom_terminals=0x0489E,
    eeprom_physical_map=0x069B3,
    eeprom_areas=0x051DC,
    eeprom_scenario_modes=0x0648E,
    eeprom_events=0x0EE5C,
    event_ring_size=500,
    labels={
        register: (LABELS_FW51 + offset, count)
        for register, (offset, count) in LAYOUT_1050.labels.items()
        if register != RAM_LABELS_VIDEO_SENSORS
    },
    label_bank=0,
    eeprom_fixed_outputs=0x04AF6,
    **_FW51,
)

LAYOUT_505_FW51 = replace(
    LAYOUT_505,
    eeprom_pins=0x029DB,
    eeprom_codes=0x02A9B,
    eeprom_zones=0x0217A,
    eeprom_terminals=0x0222E,
    eeprom_physical_map=0x03689,
    eeprom_areas=0x0298C,
    eeprom_scenario_modes=0x033B3,
    eeprom_events=0x0EE5C,
    eeprom_fixed_outputs=0x022A6,
    labels=_small_labels(areas=5, zones=20, expanders=5, start=LABELS_FW51),
    **_FW51,
)
