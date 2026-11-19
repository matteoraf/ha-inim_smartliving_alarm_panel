"""Decoding of the panel configuration: labels and EEPROM records."""

from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum, StrEnum

from .commands import ArmMode, encode_pin
from .errors import ProtocolError
from .model import (
    FIXED_OUTPUTS,
    LABEL_LENGTH,
    PanelModel,
)

UNUSED_SLOT = 0xFF

# Characters 0x80-0xBD of Inim's character set; 0xBE-0xFF are not mapped
_CHARSET = "ΓΔΘΛΞΠΣΦΨΩÉáéíóőöúűüàâçêèëîïôûùõÀÁÃÂÊÍÓÕÔÚäÄĄČĘĖĮŠŲŪŽąčęėįšųūž"


def decode_label(raw: bytes) -> str:
    """Decode one label (space- or NUL-padded)."""
    chars = []
    for byte in raw:
        if byte < 0x80:
            chars.append(chr(byte))
        elif byte - 0x80 < len(_CHARSET):
            chars.append(_CHARSET[byte - 0x80])
        else:
            chars.append("?")
    return "".join(chars).strip(" \x00")


def decode_labels(data: bytes, count: int) -> list[str]:
    """Decode count consecutive 16-byte labels."""
    _check_length(data, count * LABEL_LENGTH, "labels")
    return [
        decode_label(data[i * LABEL_LENGTH : (i + 1) * LABEL_LENGTH])
        for i in range(count)
    ]


def label_address(pointer: bytes, model: PanelModel) -> int:
    """EEPROM address of a label table from its 2-byte RAM pointer."""
    _check_length(pointer, 2, "label pointer")
    return int.from_bytes(pointer, "little") + model.label_bank


@dataclass(frozen=True, slots=True)
class PhysicalMap:
    """Which logical terminal sits in each physical slot."""

    terminal_by_slot: tuple[int | None, ...]

    def slot_of(self, terminal: int) -> int | None:
        """Physical slot of a logical terminal (first match), if connected."""
        try:
            return self.terminal_by_slot.index(terminal)
        except ValueError:
            return None


def decode_physical_map(data: bytes, model: PanelModel) -> PhysicalMap:
    """Decode the physical-to-logical terminal map (one byte per slot)."""
    _check_length(data, model.physical_slots, "physical map")
    return PhysicalMap(tuple(None if b == UNUSED_SLOT else b for b in data))


class TerminalType(IntEnum):
    """Type of a logical terminal (byte 11 of its record)."""

    INPUT = 0
    OUTPUT = 1
    IN_OUT = 2
    DOUBLE = 3
    NULL = 4
    OUT_DIMMING = 5
    OUT_DAC = 6
    OUT_ANALOG = 7
    OUT_NOT_ENABLED = 8


ZONE_TYPES = frozenset((TerminalType.INPUT, TerminalType.IN_OUT, TerminalType.DOUBLE))
OUTPUT_TYPES = frozenset(
    (
        TerminalType.OUTPUT,
        TerminalType.IN_OUT,
        TerminalType.OUT_DIMMING,
        TerminalType.OUT_DAC,
        TerminalType.OUT_ANALOG,
    )
)


# Output terminals the output command switches (on/off or a 0-100 level)
SWITCHED_OUTPUT_TYPES = frozenset(
    (TerminalType.OUTPUT, TerminalType.IN_OUT, TerminalType.OUT_DIMMING)
)


class OutputKind(StrEnum):
    """How an output is switched."""

    ON_OFF = "on_off"
    MONOSTABLE = "monostable"  # switches itself off after its time
    DIMMER = "dimmer"  # a level 0-100


# Output options (terminal record byte 10, fixed-output record byte 1)
OUTPUT_OPTION_BITS = {
    "normally_closed": 0,
    "monostable": 1,
    "buzzer": 2,
    "flashing": 3,
    "keep_on_restoral": 4,
    "toggle": 5,
    "dimmer": 6,
    "relay_use": 7,
}
FIXED_OUTPUT_RECORD_LENGTH = 2  # monostable time, options


@dataclass(frozen=True, slots=True)
class OutputConfig:
    """An output's programming: monostable time and options (raw bytes)."""

    time: int
    options: int
    dimmer_type: bool = False  # a terminal of the dimmer output type

    @property
    def kind(self) -> OutputKind:
        """Dimmer, monostable or plain on/off."""
        if self.dimmer_type or self.flags["dimmer"]:
            return OutputKind.DIMMER
        if self.flags["monostable"]:
            return OutputKind.MONOSTABLE
        return OutputKind.ON_OFF

    @property
    def flags(self) -> dict[str, bool]:
        """The output options by name."""
        return {
            name: bool(self.options >> bit & 1)
            for name, bit in OUTPUT_OPTION_BITS.items()
        }

    @property
    def monostable_time(self) -> "PanelTime | None":
        """How long a monostable output stays on; None for other kinds."""
        if self.kind is not OutputKind.MONOSTABLE:
            return None
        return decode_time(self.time)


def terminal_output_number(terminal: int, model: PanelModel) -> int:
    """Output number of a logical terminal (0-based), in the panel's own
    numbering: terminals 1-L, then O1-O3 as L+1 to L+3.
    """
    if not 0 <= terminal < model.terminals:
        raise ValueError(f"terminal out of range: {terminal}")
    return terminal + 1


def fixed_output_number(fixed: int, model: PanelModel) -> int:
    """Output number of a fixed output (1-3 for O1-O3)."""
    if not 1 <= fixed <= FIXED_OUTPUTS:
        raise ValueError(f"fixed output out of range: {fixed}")
    return model.terminals + fixed


def output_address(number: int, physical_map: PhysicalMap, model: PanelModel) -> int:
    """The output command's OUTPUT byte for an output number.

    0-2 always mean O1-O3, so a terminal output is addressed by a physical
    slot of 3 or more; one wired only to slots 0-2 cannot be switched
    (ValueError), nor can a terminal not in any slot.
    """
    if model.terminals < number <= model.terminals + FIXED_OUTPUTS:
        return number - model.terminals - 1
    if not 1 <= number <= model.terminals:
        raise ValueError(f"output number out of range: {number}")
    for slot, terminal in enumerate(physical_map.terminal_by_slot):
        if slot >= FIXED_OUTPUTS and terminal == number - 1:
            return slot
    raise ValueError(f"terminal {number} is not in a physical slot of 3 or more")


HOME_AUTOMATION_BIT = 0x01  # of an extra output options byte


def decode_home_automation(data: bytes, model: PanelModel) -> tuple[bool, ...]:
    """Which outputs the installer marked as home automation outputs.

    From the extra output options (L + 3 bytes): terminals 0 to L-1, then
    O1-O3.
    """
    _check_length(data, model.terminals + FIXED_OUTPUTS, "extra output options")
    return tuple(bool(byte & HOME_AUTOMATION_BIT) for byte in data)


def decode_fixed_outputs(data: bytes) -> list[OutputConfig]:
    """Decode the records of the fixed outputs O1-O3 (2 bytes each)."""
    _check_length(data, FIXED_OUTPUTS * FIXED_OUTPUT_RECORD_LENGTH, "fixed outputs")
    return [
        OutputConfig(time=data[2 * index], options=data[2 * index + 1])
        for index in range(FIXED_OUTPUTS)
    ]


class Balancing(IntEnum):
    """Balancing of a terminal's input (byte 5 of its record)."""

    NORMALLY_OPEN = 0
    NORMALLY_CLOSED = 1
    SINGLE_EOL = 2
    DOUBLE_EOL = 3
    DOUBLE_ZONE = 4
    DOUBLE_ZONE_EOL = 5
    PERSONALIZED = 6  # analogue thresholds in bytes 0-4


class SensorKind(StrEnum):
    """Kind of sensor on a terminal (byte 6 of its record).

    It decides how bytes 6-8 of the terminal's zone records are read.
    """

    GENERIC = "generic"
    ROLLER_BLIND = "roller_blind"
    SHOCK = "shock"


# Terminal record offsets and the sensor kind bits of byte 6
TERMINAL_BALANCING_OFFSET = 5
TERMINAL_SENSOR_OFFSET = 6
TERMINAL_OUTPUT_TIME_OFFSET = 9
TERMINAL_OUTPUT_OPTIONS_OFFSET = 10
TERMINAL_TYPE_OFFSET = 11
_ROLLER_BLIND_BIT = 0x80
_SHOCK_BIT = 0x40


def _known[E: IntEnum](enum: type[E], value: int) -> E | int:
    """The enum member of a value, or the value itself if it has none."""
    try:
        return enum(value)
    except ValueError:
        return value


@dataclass(frozen=True, slots=True)
class Terminal:
    """A logical terminal record (12 bytes).

    Values the enums do not know are kept as numbers.
    """

    terminal_type: TerminalType | int
    balancing: Balancing | int
    roller_blind: bool
    shock_sensor: bool
    output_options: int  # byte 10; bit 6 = dimmer
    output_time: int = 0  # byte 9: monostable time (bits 0-6 value, bit 7 unit)

    @property
    def sensor_kind(self) -> SensorKind:
        """Roller-blind, shock or generic sensor.

        The programming sets one of the two bits. Should both be set, the
        roller blind is taken, as the installer software reads the byte
        (bit 7 before bit 6).
        """
        if self.roller_blind:
            return SensorKind.ROLLER_BLIND
        if self.shock_sensor:
            return SensorKind.SHOCK
        return SensorKind.GENERIC

    @property
    def has_zone(self) -> bool:
        """Whether the terminal is (also) a zone."""
        return self.terminal_type in ZONE_TYPES

    @property
    def has_second_zone(self) -> bool:
        """Whether the terminal has a second zone at index terminal + L."""
        return self.terminal_type == TerminalType.DOUBLE

    @property
    def has_output(self) -> bool:
        """Whether the terminal is (also) an output."""
        return self.terminal_type in OUTPUT_TYPES

    @property
    def output(self) -> "OutputConfig | None":
        """The terminal's output programming, if it is an output that can be
        switched (on/off, monostable or dimmer; not DAC or analogue).
        """
        if self.terminal_type not in SWITCHED_OUTPUT_TYPES:
            return None
        return OutputConfig(
            time=self.output_time,
            options=self.output_options,
            dimmer_type=self.terminal_type == TerminalType.OUT_DIMMING,
        )


def decode_terminals(data: bytes, model: PanelModel) -> list[Terminal]:
    """Decode the L logical terminal records."""
    size = model.terminal_record_length
    _check_length(data, model.terminals * size, "terminal records")
    terminals = []
    for index in range(model.terminals):
        record = data[index * size : (index + 1) * size]
        sensor = record[TERMINAL_SENSOR_OFFSET]
        terminals.append(
            Terminal(
                terminal_type=_known(TerminalType, record[TERMINAL_TYPE_OFFSET]),
                balancing=_known(Balancing, record[TERMINAL_BALANCING_OFFSET]),
                roller_blind=bool(sensor & _ROLLER_BLIND_BIT),
                shock_sensor=bool(sensor & _SHOCK_BIT),
                output_options=record[TERMINAL_OUTPUT_OPTIONS_OFFSET],
                output_time=record[TERMINAL_OUTPUT_TIME_OFFSET],
            )
        )
    return terminals


class ZoneType(IntEnum):
    """Zone type (byte 2 of a zone record)."""

    INSTANT = 0
    DELAYED = 1
    VIEWABLE_DELAYED = 2
    ROUTE = 3
    H24 = 4
    AUTOMATION = 5
    ARM = 6
    DISARM = 7
    SWITCH = 8
    ARM_DISARM = 9
    PATROL = 10
    EMERGENCY_EXIT_WITHOUT_CALL = 11
    EMERGENCY_EXIT = 12


class TimeUnit(StrEnum):
    """Unit of a zone time, as the panel's programming shows it."""

    SECONDS = "seconds"
    MINUTES = "minutes"


@dataclass(frozen=True, slots=True)
class PanelTime:
    """A zone time: a value and the panel's unit for it."""

    value: int
    unit: TimeUnit


def decode_time(byte: int) -> PanelTime:
    """A time byte: bits 0-6 the value, bit 7 the unit (set: minutes)."""
    return PanelTime(byte & 0x7F, TimeUnit.MINUTES if byte & 0x80 else TimeUnit.SECONDS)


# Steps of the alarm pulse duration (zone record byte 6, firmware 6): bit 7
# clear, 15 ms; set, 10 s (the units the installer software offers)
ALARM_PULSE_DURATION_STEP = timedelta(milliseconds=15)
ALARM_PULSE_DURATION_LONG_STEP = timedelta(seconds=10)


def decode_alarm_pulse_duration(byte: int) -> timedelta:
    """The alarm pulse duration byte: bits 0-6 a count of steps, bit 7 long."""
    step = ALARM_PULSE_DURATION_LONG_STEP if byte & 0x80 else ALARM_PULSE_DURATION_STEP
    return (byte & 0x7F) * step


# Zone record offsets
ZONE_TYPE_OFFSET = 2
ZONE_OPTIONS_OFFSET = 3
ZONE_OPTIONS2_OFFSET = 4
ZONE_CYCLES_OFFSET = 5
ZONE_TIME_OFFSET = 6  # alarm pulse duration, or the shock sensitivity
ZONE_WINDOW_OFFSET = 7
ZONE_PULSES_OFFSET = 8
# Alarm cycles value meaning "no limit"
UNLIMITED_ALARM_CYCLES = 15


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    """A zone record.

    Byte 6 means the alarm pulse duration for zones of a generic sensor and the
    inertial sensitivity for shock sensors (unused for roller blinds): the
    terminal's sensor kind decides which property applies.
    """

    areas: frozenset[int]
    zone_type: ZoneType | int  # values the enum does not know stay numbers
    options: int  # byte 3
    options2: int  # byte 4
    cycles: int  # byte 5
    time: int  # byte 6
    window: int  # byte 7
    pulse_count: int  # byte 8: pulses in the window that raise the alarm

    @property
    def alarm_cycles(self) -> int | None:
        """How many times the zone can raise an alarm; None for no limit."""
        return None if self.cycles == UNLIMITED_ALARM_CYCLES else self.cycles

    @property
    def alarm_pulse_duration(self) -> timedelta:
        """Alarm pulse duration (zones of a generic sensor)."""
        return decode_alarm_pulse_duration(self.time)

    @property
    def shock_sensitivity(self) -> int:
        """Inertial sensitivity, 0-40 (zones of a shock sensor)."""
        return self.time

    @property
    def pulse_window_time(self) -> PanelTime:
        """Time in which pulses are counted."""
        return decode_time(self.window)

    @property
    def flags(self) -> dict[str, bool]:
        """The zone's programming options, by name (see ZONE_OPTION_FLAGS)."""
        return zone_flags(self.options, self.options2)

    @property
    def interior(self) -> bool:
        """Interior zone."""
        return self.flags["interior"]

    @property
    def unbypassable(self) -> bool:
        """Zone that cannot be bypassed."""
        return self.flags["unbypassable"]

    @property
    def hold_up(self) -> bool:
        """Hold-up (robbery) zone."""
        return self.flags["hold_up"]


# Zone record options (byte 3) and options 2 (byte 4): name -> (byte, bit).
# Bits that apply to wireless zones only (options bits 5-7, options 2 bit 7)
# are left out: their meaning depends on the device.
ZONE_OPTION_FLAGS = {
    "interior": (3, 0),
    "autobypass": (3, 1),
    "unbypassable": (3, 2),
    "chime": (3, 3),
    "test_programmed": (3, 4),
    "autobypass_no_reinclude": (4, 0),
    "no_arming_if_not_ready": (4, 1),
    "entry_time_2": (4, 2),
    "last_exit_zone": (4, 3),
    "reinclude_on_disarm": (4, 4),
    "hold_up": (4, 5),
    "fault_zone": (4, 6),
}


def zone_flags(options: int, options2: int) -> dict[str, bool]:
    """The named options of a zone record's bytes 3 and 4."""
    value = {ZONE_OPTIONS_OFFSET: options, ZONE_OPTIONS2_OFFSET: options2}
    return {
        name: bool(value[byte] >> bit & 1)
        for name, (byte, bit) in ZONE_OPTION_FLAGS.items()
    }


def decode_zone_records(data: bytes, model: PanelModel) -> list[ZoneConfig]:
    """Decode the 2L zone records."""
    size = model.zone_record_length
    _check_length(data, model.zones * size, "zone records")
    zones = []
    for index in range(model.zones):
        record = data[index * size : (index + 1) * size]
        mask = int.from_bytes(record[0:2], "little")
        zones.append(
            ZoneConfig(
                areas=_areas(mask, model),
                zone_type=_known(ZoneType, record[ZONE_TYPE_OFFSET]),
                options=record[ZONE_OPTIONS_OFFSET],
                options2=record[ZONE_OPTIONS2_OFFSET],
                cycles=record[ZONE_CYCLES_OFFSET],
                time=record[ZONE_TIME_OFFSET],
                window=record[ZONE_WINDOW_OFFSET],
                pulse_count=record[ZONE_PULSES_OFFSET],
            )
        )
    return zones


# Area record offsets, and the bits of its options byte
AREA_EXIT_TIME_OFFSET = 0
AREA_ENTRY_TIME_OFFSET = 1
AREA_ENTRY_TIME_2_OFFSET = 2
AREA_PRE_ARM_WARNING_OFFSET = 3  # byte 4 is reserved
AREA_PATROL_OFFSET = 5
AREA_OPTIONS_OFFSET = 6
_AREA_TIMER_MASK = 0x0F
_AREA_RESET_MEMORY_BIT = 0x20
_AREA_AUTO_ARM_PARTIAL_BIT = 0x40
_AREA_CLEAR_CALL_QUEUE_BIT = 0x80


@dataclass(frozen=True, slots=True)
class AreaConfig:
    """An area record: its delays and options.

    The three times are time bytes (see decode_time); entry time 2 is not
    used when its byte is 0. The pre-arm warning and patrol times are in
    minutes.
    """

    exit_time_byte: int
    entry_time_byte: int
    entry_time_2_byte: int
    pre_arm_warning: int  # minutes
    patrol: int  # minutes
    options: int

    @property
    def exit_time(self) -> PanelTime:
        """Time to leave after arming."""
        return decode_time(self.exit_time_byte)

    @property
    def entry_time(self) -> PanelTime:
        """Time to disarm after a delayed zone opens."""
        return decode_time(self.entry_time_byte)

    @property
    def entry_time_2(self) -> PanelTime | None:
        """The second entry time (for zones set to use it); None if not used."""
        return decode_time(self.entry_time_2_byte) if self.entry_time_2_byte else None

    @property
    def timer_program(self) -> int | None:
        """The arm/disarm timer program; None for none."""
        return self.options & _AREA_TIMER_MASK or None

    @property
    def reset_alarm_memory_on_arming(self) -> bool:
        """Whether arming resets the alarm memory."""
        return bool(self.options & _AREA_RESET_MEMORY_BIT)

    @property
    def auto_arm_partial(self) -> bool:
        """Whether the area arms itself in partial mode."""
        return bool(self.options & _AREA_AUTO_ARM_PARTIAL_BIT)

    @property
    def clear_call_queue_on_disarm(self) -> bool:
        """Whether disarming clears the queue of phone calls."""
        return bool(self.options & _AREA_CLEAR_CALL_QUEUE_BIT)


def decode_area_records(data: bytes, model: PanelModel) -> list[AreaConfig]:
    """Decode the A area records."""
    size = model.area_record_length
    _check_length(data, model.areas * size, "area records")
    areas = []
    for index in range(model.areas):
        record = data[index * size : (index + 1) * size]
        areas.append(
            AreaConfig(
                exit_time_byte=record[AREA_EXIT_TIME_OFFSET],
                entry_time_byte=record[AREA_ENTRY_TIME_OFFSET],
                entry_time_2_byte=record[AREA_ENTRY_TIME_2_OFFSET],
                pre_arm_warning=record[AREA_PRE_ARM_WARNING_OFFSET],
                patrol=record[AREA_PATROL_OFFSET],
                options=record[AREA_OPTIONS_OFFSET],
            )
        )
    return areas


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """A scenario record: what it does to each area."""

    area_modes: dict[int, ArmMode]  # areas it changes; others are kept
    icon: int
    output: int  # 0 none, 1..L logical terminal + 1, L+1..L+3 O1-O3, F+ siren


def decode_scenario_records(data: bytes, model: PanelModel) -> list[ScenarioConfig]:
    """Decode the 30 scenario records."""
    size = model.scenario_record_length
    _check_length(data, model.scenarios * size, "scenario records")
    scenarios = []
    for index in range(model.scenarios):
        record = data[index * size : (index + 1) * size]
        modes = {}
        for area in range(1, model.areas + 1):
            byte = record[(area - 1) // 2]
            value = byte >> 4 if area % 2 == 0 else byte & 0x0F
            if value in ArmMode and value != ArmMode.KEEP:
                modes[area] = ArmMode(value)
        scenarios.append(ScenarioConfig(modes, icon=record[-2], output=record[-1]))
    return scenarios


@dataclass(frozen=True, slots=True)
class Peripherals:
    """The keypads, readers, expanders and sirens enabled on the panel.

    0-based device numbers, the same as the index into their labels.
    """

    keypads: tuple[int, ...]
    readers: tuple[int, ...]
    expanders: tuple[int, ...]
    sirens: tuple[int, ...]


def decode_peripherals(data: bytes, model: PanelModel) -> Peripherals:
    """Decode the enabled-device bitmaps (little-endian, bit q = device q).

    Bits beyond the model's count of a device kind are ignored.
    """
    _check_length(data, model.peripherals_length, "peripherals")

    def enabled(start: int, length: int, count: int) -> tuple[int, ...]:
        bitmap = int.from_bytes(data[start : start + length], "little")
        return tuple(q for q in range(min(count, 8 * length)) if bitmap >> q & 1)

    return Peripherals(
        readers=enabled(0, 4, model.readers),
        expanders=enabled(4, 6, model.expanders),
        keypads=enabled(10, 2, model.keypads),
        sirens=enabled(12, 2, model.sirens),
    )


def refuses_arming_when_not_ready(options: bytes) -> bool:
    """Whether the installer set the panel to refuse arming with zones not ready.

    Without it the panel arms with zones that are not at rest.
    """
    if len(options) < 2:
        raise ProtocolError(f"general options: {len(options)} bytes, 2 needed")
    return bool(options[1] & 0x20)


def find_code_index(pin_table: bytes, pin: str, model: PanelModel) -> int | None:
    """Index of the user code with this PIN, or None.

    Only the index is returned, never another code's PIN.
    """
    _check_length(pin_table, model.pin_table_length, "PIN table")
    wanted = encode_pin(pin)
    size = model.pin_length
    for index in range(model.codes):
        if pin_table[index * size : (index + 1) * size] == wanted:
            return index
    return None


@dataclass(frozen=True, slots=True)
class UserCode:
    """A user code record (without its PIN)."""

    areas: frozenset[int]  # areas the code may operate
    options: int  # byte 48
    outputs: frozenset[int]  # logical terminals the code may drive
    fixed_outputs: frozenset[int]  # 1-3 for O1-O3
    code_type: int | None  # 0 standard, 1 manager, 2 master; None before FW 5.1

    @property
    def remote_access(self) -> bool:
        """Whether the code may be used remotely (options bit 5)."""
        return bool(self.options & 0x20)


CODE_OUTPUT_MASK_OFFSET = 50


def code_output_mask(record: bytes, model: PanelModel) -> bytes:
    """The output mask of a user code record, raw (see decode_output_mask)."""
    _check_length(record, model.code_record_length, "code record")
    return record[
        CODE_OUTPUT_MASK_OFFSET : CODE_OUTPUT_MASK_OFFSET
        + model.code_output_mask_length
    ]


def decode_output_mask(
    mask: bytes, model: PanelModel
) -> tuple[frozenset[int], frozenset[int]]:
    """(logical terminals 0-based, fixed outputs 1-3) a user code may drive.

    Byte 0 bits 0-2 are O1-O3; then one bit per logical terminal.
    """
    _check_length(mask, model.code_output_mask_length, "output mask")
    fixed = frozenset(n + 1 for n in range(FIXED_OUTPUTS) if mask[0] >> n & 1)
    terminals = frozenset(
        8 * k + b
        for k, byte in enumerate(mask[1:])
        for b in range(8)
        if byte >> b & 1 and 8 * k + b < model.terminals
    )
    return terminals, fixed


def decode_code_record(record: bytes, model: PanelModel) -> UserCode:
    """Decode one user code record."""
    terminals, fixed = decode_output_mask(code_output_mask(record, model), model)
    type_offset = CODE_OUTPUT_MASK_OFFSET + model.code_output_mask_length
    return UserCode(
        areas=_areas(int.from_bytes(record[0:2], "little"), model),
        options=record[48],
        outputs=terminals,
        fixed_outputs=fixed,
        code_type=record[type_offset] if len(record) > type_offset else None,
    )


def area_mask(areas: frozenset[int]) -> int:
    """Bit mask (bit n-1 = area n), e.g. for the active-scenario request."""
    mask = 0
    for area in areas:
        mask |= 1 << (area - 1)
    return mask


def _areas(mask: int, model: PanelModel) -> frozenset[int]:
    return frozenset(a for a in range(1, model.areas + 1) if mask >> (a - 1) & 1)


def _check_length(data: bytes, length: int, what: str) -> None:
    if len(data) != length:
        raise ProtocolError(f"{what}: expected {length} bytes, got {len(data)}")
