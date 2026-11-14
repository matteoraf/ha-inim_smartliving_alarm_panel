"""Decoding of the live status registers.

Zones are numbered 0-based over the zone index space of 2L zones: 0..L-1
are the primary zones of the logical terminals, L..2L-1 the second zones of
double terminals. Areas are numbered 1-based.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from .errors import ProtocolError
from .model import FIXED_OUTPUTS, PanelModel

VOLTS_PER_UNIT = 0.01516  # supply voltage scale in the faults register


class AreaMode(IntEnum):
    """Arming state of an area."""

    NONE = 0  # not valid / not used
    AWAY = 1  # armed total
    STAY = 2  # armed partial
    INSTANT = 3  # armed partial, no entry delay
    DISARMED = 4

    @property
    def armed(self) -> bool:
        """Whether the area is armed in any mode."""
        return self in (AreaMode.AWAY, AreaMode.STAY, AreaMode.INSTANT)


class ZoneState(IntEnum):
    """Real-time state of a zone (2 bits per zone)."""

    SHORT = 0
    STANDBY = 1  # at rest
    ALARM = 2  # open
    TAMPER = 3


@dataclass(frozen=True, slots=True)
class AreaStatus:
    """Area modes and the per-area alarm, tamper and auto-arm masks."""

    modes: dict[int, AreaMode]
    alarm: frozenset[int]
    tamper: frozenset[int]
    alarm_memory: frozenset[int]
    tamper_memory: frozenset[int]
    auto_arm: frozenset[int]

    @property
    def armed(self) -> frozenset[int]:
        """Areas armed in any mode."""
        return frozenset(area for area, mode in self.modes.items() if mode.armed)


@dataclass(frozen=True, slots=True)
class ZoneInclusion:
    """Per zone: included (not bypassed) and in test mode."""

    included: tuple[bool, ...]
    test: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class ZoneMemory:
    """Alarm memory per zone, tamper memory per terminal and the output image."""

    alarm_memory: tuple[bool, ...]  # per zone (2L)
    terminal_tamper_memory: tuple[bool, ...]  # per logical terminal (L)
    outputs: tuple[bool, ...]  # per logical terminal (L)
    fixed_outputs: tuple[bool, ...]  # O1-O3


@dataclass(frozen=True, slots=True)
class PanelState:
    """Panel state flags (stato_centrale)."""

    no_area_armed: bool  # byte 1 bit 3
    programming: bool  # byte 1 bit 6
    voice_board_present: bool  # byte 1 bit 7 is set when absent
    raw: bytes


class Fault(StrEnum):
    """Faults reported in the faults register."""

    ZONE_FUSE = "zone_fuse"
    IBUS_FUSE = "ibus_fuse"
    BATTERY = "battery"
    MAINS = "mains"
    TELEPHONE_LINE = "telephone_line"
    JAMMING = "jamming"
    WIRELESS_LOW_BATTERY = "wireless_low_battery"
    WIRELESS_ZONE_LOSS = "wireless_zone_loss"
    GSM = "gsm"
    DUSTY_DETECTOR = "dusty_detector"
    ZONE_FAULTS = "zone_faults"
    SOUNDER_FAULTS = "sounder_faults"
    PERIPHERAL_LOSS_OR_TAMPER = "peripheral_loss_or_tamper"
    POWER_SUPPLY = "power_supply"
    INTERNET_CONNECTION_LOSS = "internet_connection_loss"
    PANEL_DISLODGED = "panel_dislodged"
    PANEL_OPEN = "panel_open"
    READER_TAMPER = "reader_tamper"
    KEYPAD_TAMPER = "keypad_tamper"
    EXPANSION_TAMPER = "expansion_tamper"


# (byte, bit) of each fault in the faults register
_FAULT_BITS: dict[Fault, tuple[int, int]] = {
    Fault.ZONE_FUSE: (0, 0),
    Fault.IBUS_FUSE: (0, 1),
    Fault.BATTERY: (0, 2),
    Fault.MAINS: (0, 3),
    Fault.TELEPHONE_LINE: (0, 4),
    Fault.JAMMING: (0, 5),
    Fault.WIRELESS_LOW_BATTERY: (0, 6),
    Fault.WIRELESS_ZONE_LOSS: (0, 7),
    Fault.GSM: (1, 0),
    Fault.DUSTY_DETECTOR: (1, 1),
    Fault.ZONE_FAULTS: (1, 2),
    Fault.SOUNDER_FAULTS: (1, 3),
    Fault.PERIPHERAL_LOSS_OR_TAMPER: (1, 4),
    Fault.POWER_SUPPLY: (1, 5),
    Fault.INTERNET_CONNECTION_LOSS: (1, 6),
    Fault.PANEL_DISLODGED: (4, 4),
    Fault.PANEL_OPEN: (4, 5),
    Fault.READER_TAMPER: (4, 6),
    Fault.KEYPAD_TAMPER: (4, 7),
    Fault.EXPANSION_TAMPER: (5, 0),
}


@dataclass(frozen=True, slots=True)
class Faults:
    """Active faults and the panel supply voltage."""

    active: frozenset[Fault]
    supply_voltage: float


def decode_area_status(data: bytes, model: PanelModel) -> AreaStatus:
    """Decode the area status register."""
    _check_length(data, model.area_status_length, "area status")
    modes = {}
    for area in range(1, model.areas + 1):
        byte = data[(area - 1) // 2]
        value = byte >> 4 if area % 2 == 0 else byte & 0x0F
        modes[area] = AreaMode(value) if value in AreaMode else AreaMode.NONE
    offset = model.area_mode_bytes

    def mask(index: int) -> frozenset[int]:
        value = int.from_bytes(
            data[offset + 2 * index : offset + 2 * index + 2], "little"
        )
        return frozenset(a for a in range(1, model.areas + 1) if value >> (a - 1) & 1)

    return AreaStatus(modes, mask(0), mask(1), mask(2), mask(3), mask(4))


def decode_zone_states(data: bytes, model: PanelModel) -> tuple[ZoneState, ...]:
    """Decode the zone state register: 2 bits per zone, 4 zones per byte."""
    _check_length(data, model.zone_state_length, "zone state")
    return tuple(
        ZoneState(data[zone // 4] >> (2 * (zone % 4)) & 0b11)
        for zone in range(model.zones)
    )


def decode_zone_inclusion(data: bytes, model: PanelModel) -> ZoneInclusion:
    """Decode the zone inclusion register (inclusion, then test flags)."""
    _check_length(data, model.zone_inclusion_length, "zone inclusion")
    half = model.zone_bitmap_length
    return ZoneInclusion(
        included=_bits(data[:half], model.zones),
        test=_bits(data[half:], model.zones),
    )


def decode_zone_memory(data: bytes, model: PanelModel) -> ZoneMemory:
    """Decode the zone memory register (alarm memory, tamper memory, outputs)."""
    _check_length(data, model.zone_memory_length, "zone memory")
    tamper_start = model.zone_bitmap_length
    outputs_start = tamper_start + model.terminal_bitmap_length
    outputs = _bits(data[outputs_start:], model.terminals + FIXED_OUTPUTS)
    return ZoneMemory(
        alarm_memory=_bits(data[:tamper_start], model.zones),
        terminal_tamper_memory=_bits(data[tamper_start:outputs_start], model.terminals),
        outputs=outputs[: model.terminals],
        fixed_outputs=outputs[model.terminals :],
    )


def decode_active_scenario(data: bytes, model: PanelModel) -> int | None:
    """The active scenario (0-based), or None when no scenario matches."""
    _check_length(data, 1, "active scenario")
    return data[0] if data[0] < model.scenarios else None


def decode_not_ready_zones(data: bytes, model: PanelModel) -> frozenset[int]:
    """Zones that would prevent activating the requested scenario."""
    _check_length(data, model.zone_bitmap_length, "not-ready zones")
    bits = _bits(data, model.zones)
    return frozenset(zone for zone, set_ in enumerate(bits) if set_)


def decode_panel_state(data: bytes) -> PanelState:
    """Decode the panel state flags (2 bytes)."""
    _check_length(data, 2, "panel state")
    flags = data[1]
    return PanelState(
        no_area_armed=bool(flags & 0x08),
        programming=bool(flags & 0x40),
        voice_board_present=not flags & 0x80,
        raw=bytes(data),
    )


def decode_faults(data: bytes) -> Faults:
    """Decode the faults register (7 bytes; bytes 2-3 are the supply voltage)."""
    _check_length(data, 7, "faults")
    active = frozenset(
        fault for fault, (byte, bit) in _FAULT_BITS.items() if data[byte] >> bit & 1
    )
    raw_voltage = int.from_bytes(data[2:4], "little")
    return Faults(active, round(raw_voltage * VOLTS_PER_UNIT, 2))


def _bits(data: bytes, count: int) -> tuple[bool, ...]:
    """The first count bits, least significant bit of each byte first."""
    return tuple(bool(data[i // 8] >> (i % 8) & 1) for i in range(count))


def _check_length(data: bytes, length: int, what: str) -> None:
    if len(data) != length:
        raise ProtocolError(f"{what}: expected {length} bytes, got {len(data)}")
