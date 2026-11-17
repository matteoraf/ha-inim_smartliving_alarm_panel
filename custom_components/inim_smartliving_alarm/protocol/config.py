"""Decoding of the panel configuration: labels and EEPROM records."""

from dataclasses import dataclass
from enum import IntEnum

from .commands import ArmMode, encode_pin
from .errors import ProtocolError
from .model import (
    FIXED_OUTPUTS,
    LABEL_LENGTH,
    PERIPHERALS_LENGTH,
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


@dataclass(frozen=True, slots=True)
class Terminal:
    """A logical terminal record (12 bytes)."""

    type: int  # a TerminalType value; other values are kept as they are
    balancing: int  # 0 NO, 1 NC, 2 single EOL, 3 double EOL, 4 double zone, ...
    roller_blind: bool
    shock_sensor: bool
    output_options: int  # byte 10; bit 6 = dimmer

    @property
    def has_zone(self) -> bool:
        """Whether the terminal is (also) a zone."""
        return self.type in ZONE_TYPES

    @property
    def has_second_zone(self) -> bool:
        """Whether the terminal has a second zone at index terminal + L."""
        return self.type == TerminalType.DOUBLE

    @property
    def has_output(self) -> bool:
        """Whether the terminal is (also) an output."""
        return self.type in OUTPUT_TYPES


def decode_terminals(data: bytes, model: PanelModel) -> list[Terminal]:
    """Decode the L logical terminal records."""
    size = model.terminal_record_length
    _check_length(data, model.terminals * size, "terminal records")
    terminals = []
    for index in range(model.terminals):
        record = data[index * size : (index + 1) * size]
        terminals.append(
            Terminal(
                type=record[11],
                balancing=record[5],
                roller_blind=bool(record[6] & 0x80),
                shock_sensor=bool(record[6] & 0x40),
                output_options=record[10],
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
    EMERGENCY_EXIT_NO_CALL = 11
    EMERGENCY_EXIT = 12


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    """A zone record."""

    areas: frozenset[int]
    type: int  # a ZoneType value; other values are kept as they are
    options: int  # byte 3
    options2: int  # byte 4

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


# Offsets in a zone record of its two option bytes
ZONE_OPTIONS_OFFSET = 3
ZONE_OPTIONS2_OFFSET = 4


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
                type=record[2],
                options=record[ZONE_OPTIONS_OFFSET],
                options2=record[ZONE_OPTIONS2_OFFSET],
            )
        )
    return zones


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
    _check_length(data, PERIPHERALS_LENGTH, "peripherals")

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


def decode_code_record(record: bytes, model: PanelModel) -> UserCode:
    """Decode one user code record."""
    _check_length(record, model.code_record_length, "code record")
    mask_length = model.code_output_mask_length
    output_mask = record[50 : 50 + mask_length]
    fixed = frozenset(n + 1 for n in range(FIXED_OUTPUTS) if output_mask[0] >> n & 1)
    terminals = frozenset(
        8 * k + b
        for k, byte in enumerate(output_mask[1:])
        for b in range(8)
        if byte >> b & 1 and 8 * k + b < model.terminals
    )
    type_offset = 50 + mask_length
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
