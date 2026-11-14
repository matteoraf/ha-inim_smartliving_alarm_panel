"""Commands: writes to the command registers, and their results.

Every command payload starts with the 6-byte PIN; the panel checks it on
each command. After the write is acknowledged, the command result register
(0x2004) tells whether the command was carried out.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from . import registers as reg
from .errors import InvalidPinError
from .frames import checksum, write_ram
from .model import PanelModel

PIN_LENGTH = 6


class ArmMode(IntEnum):
    """Requested arming per area (also used in scenario records)."""

    KEEP = 0
    AWAY = 1  # total
    STAY = 2  # partial
    INSTANT = 3  # partial, no entry delay
    DISARM = 4


class CommandKind(StrEnum):
    """Command types; the meaning of a result code depends on them."""

    ARM = "arm"
    OUTPUT = "output"
    RESET = "reset"
    BYPASS = "bypass"
    SCENARIO = "scenario"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class Command:
    """A command ready to send."""

    kind: CommandKind
    register: int
    payload: bytes

    @property
    def frame(self) -> bytes:
        """Header followed by the payload."""
        return write_ram(self.register, self.payload)

    @property
    def expected_ack(self) -> int:
        """The acknowledgement byte the panel sends back."""
        return checksum(self.payload)


def encode_pin(pin: str) -> bytes:
    """Six bytes: one digit per byte, unused digits 0xFF."""
    if not pin.isdigit() or not 1 <= len(pin) <= PIN_LENGTH or not pin.isascii():
        raise InvalidPinError("the PIN must be 1 to 6 digits")
    return bytes(int(digit) for digit in pin) + b"\xff" * (PIN_LENGTH - len(pin))


def arm_areas(pin: str, modes: Mapping[int, ArmMode], model: PanelModel) -> Command:
    """Arm or disarm areas; areas not listed keep their state.

    One nibble per area: area n is in byte (n-1)//2, low nibble for odd n,
    high nibble for even n.
    """
    nibbles = bytearray(8)
    for area, mode in modes.items():
        _check_area(area, model)
        mode = ArmMode(mode)
        index = (area - 1) // 2
        nibbles[index] |= mode << 4 if area % 2 == 0 else mode
    return Command(CommandKind.ARM, reg.ARM_AREAS, encode_pin(pin) + bytes(nibbles))


def reset_alarm_memory(pin: str, areas: Iterable[int], model: PanelModel) -> Command:
    """Reset the alarm memory of some areas."""
    mask = 0
    for area in areas:
        _check_area(area, model)
        mask |= 1 << (area - 1)
    return Command(
        CommandKind.RESET,
        reg.RESET_ALARM_MEMORY,
        encode_pin(pin) + mask.to_bytes(2, "little"),
    )


def bypass_zone(
    pin: str, slot: int, model: PanelModel, *, bypass: bool, second_zone: bool = False
) -> Command:
    """Bypass (exclude) or include a zone.

    The zone is given by its physical terminal slot (from the physical map),
    not by its logical number; second_zone selects the second zone of a
    double terminal.
    """
    if not 0 <= slot < model.physical_slots:
        raise ValueError(f"physical slot out of range: {slot}")
    return Command(
        CommandKind.BYPASS,
        reg.BYPASS_ZONE,
        encode_pin(pin) + bytes((slot, int(second_zone), int(bypass))),
    )


def set_output(pin: str, output: int, value: int, model: PanelModel) -> Command:
    """Switch an output: 0/1 off/on, or 0-100 for a dimmer.

    Outputs 0, 1 and 2 are the fixed outputs O1-O3; a terminal output is
    given by its physical slot (3 or more).
    """
    if not 0 <= output < model.physical_slots:
        raise ValueError(f"output out of range: {output}")
    if not 0 <= value <= 100:
        raise ValueError(f"value out of range: {value}")
    return Command(
        CommandKind.OUTPUT, reg.SET_OUTPUT, encode_pin(pin) + bytes((output, value))
    )


def activate_scenario(pin: str, scenario: int, model: PanelModel) -> Command:
    """Activate a scenario (0-based)."""
    if not 0 <= scenario < model.scenarios:
        raise ValueError(f"scenario out of range: {scenario}")
    return Command(
        CommandKind.SCENARIO,
        reg.ACTIVATE_SCENARIO,
        encode_pin(pin) + bytes((scenario,)),
    )


def stop_sirens(pin: str) -> Command:
    """Stop the sirens."""
    return Command(
        CommandKind.STOP, reg.STOP_SIRENS_OR_CALLS, encode_pin(pin) + b"\x00"
    )


def clear_call_queue(pin: str) -> Command:
    """Clear the queue of telephone calls."""
    return Command(
        CommandKind.STOP, reg.STOP_SIRENS_OR_CALLS, encode_pin(pin) + b"\x01"
    )


# Result codes per command type, as stable keys
_RESULTS: dict[CommandKind, dict[int, str]] = {
    CommandKind.ARM: {2: "open_zones", 3: "code_not_enabled"},
    CommandKind.RESET: {2: "code_not_enabled"},
    CommandKind.BYPASS: {
        2: "already_in_state",
        3: "slot_out_of_range",
        4: "slot_not_connected",
        5: "null_terminal",
        6: "cannot_be_bypassed",
        7: "no_areas",
        8: "double_zone_error",
    },
    CommandKind.OUTPUT: {
        2: "already_in_state",
        3: "slot_out_of_range",
        4: "slot_not_connected",
        5: "null_terminal",
        6: "output_not_enabled",
        7: "areas_armed",
    },
    CommandKind.SCENARIO: {2: "refused", 3: "refused"},
    CommandKind.STOP: {2: "refused", 3: "refused"},
}


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The panel's verdict on a command."""

    kind: CommandKind
    code: int
    reason: str | None  # None when the command succeeded

    @property
    def ok(self) -> bool:
        """Whether the command was carried out."""
        return self.code == 0


def decode_result(kind: CommandKind, data: bytes) -> CommandResult:
    """Decode the 1-byte command result register."""
    code = data[0]
    if code == 0:
        reason = None
    elif code in (1, 0xFF):
        reason = "invalid_code"
    else:
        reason = _RESULTS[kind].get(code, "unknown")
    return CommandResult(kind, code, reason)


def _check_area(area: int, model: PanelModel) -> None:
    if not 1 <= area <= model.areas:
        raise ValueError(f"area out of range: {area}")
