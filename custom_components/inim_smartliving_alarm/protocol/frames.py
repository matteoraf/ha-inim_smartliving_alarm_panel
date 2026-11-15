"""Request frames and reply checks.

A request is an 8-byte header: op, num_dev, device, address (2 bytes BE),
length (2 bytes BE), checksum. A read reply is the data followed by its
checksum; a write is the header followed by the payload, acknowledged with
one byte, the checksum of the payload.

The two SmartLAN boards use different device codes for EEPROM and allow
different request sizes (a Dialect); RAM requests are the same on both.
Builders default to the SmartLAN/SI dialect.
"""

from dataclasses import dataclass

from . import registers as reg
from .errors import ChecksumError, ProtocolError
from .model import PanelModel

OP_READ = 0x00
OP_WRITE = 0x01
OP_PERIPHERAL_READ = 0x10
OP_PERIPHERAL_WRITE = 0x11

DEVICE_RAM = 0x00
DEVICE_EEPROM = 0x01  # SmartLAN/SI
DEVICE_FLASH = 0x02  # SmartLAN/SI
DEVICE_EEPROM_G = 0xFF  # SmartLAN/G

HEADER_LENGTH = 8


@dataclass(frozen=True, slots=True)
class Dialect:
    """How one board type encodes requests."""

    name: str
    eeprom_device: int  # device byte of EEPROM requests
    max_transfer: int  # longest read in one request


SMARTLAN_SI = Dialect("SmartLAN/SI", DEVICE_EEPROM, 250)
SMARTLAN_G = Dialect("SmartLAN/G", DEVICE_EEPROM_G, 2000)

# Longest read on SmartLAN/SI, and longest write payload on both boards
# (commands are at most 14 bytes)
MAX_TRANSFER = SMARTLAN_SI.max_transfer


def checksum(data: bytes) -> int:
    """Sum of the bytes, modulo 256."""
    return sum(data) & 0xFF


def header(op: int, num_dev: int, device: int, address: int, length: int) -> bytes:
    """Build an 8-byte request header."""
    for name, value, limit in (
        ("op", op, 0xFF),
        ("num_dev", num_dev, 0xFF),
        ("device", device, 0xFF),
        ("address", address, 0xFFFF),
        ("length", length, 0xFFFF),
    ):
        if not 0 <= value <= limit:
            raise ValueError(f"{name} out of range: {value}")
    data = bytes(
        (op, num_dev, device, address >> 8, address & 0xFF, length >> 8, length & 0xFF)
    )
    return data + bytes((checksum(data),))


def read_ram(address: int, length: int, dialect: Dialect = SMARTLAN_SI) -> bytes:
    """Request to read RAM (plain RAM may be read at any address)."""
    _check_length(length, dialect.max_transfer)
    return header(OP_READ, 0, DEVICE_RAM, address, length)


def read_eeprom(address: int, length: int, dialect: Dialect = SMARTLAN_SI) -> bytes:
    """Request to read EEPROM at a 24-bit address."""
    _check_length(length, dialect.max_transfer)
    if not 0 <= address <= 0xFFFFFF:
        raise ValueError(f"address out of range: {address:#x}")
    return header(
        OP_READ, address >> 16, dialect.eeprom_device, address & 0xFFFF, length
    )


def write_ram(address: int, payload: bytes) -> bytes:
    """Write to a RAM register: header followed by the payload."""
    _check_length(len(payload), MAX_TRANSFER)
    return header(OP_WRITE, 0, DEVICE_RAM, address, len(payload)) + payload


def split(
    address: int, length: int, dialect: Dialect = SMARTLAN_SI
) -> list[tuple[int, int]]:
    """Split a long read into (address, length) parts one request allows."""
    parts = []
    while length > 0:
        part = min(length, dialect.max_transfer)
        parts.append((address, part))
        address += part
        length -= part
    return parts


def active_scenario_request(area_mask: int) -> bytes:
    """Ask which scenario matches the areas of a user code.

    The area mask (bit n-1 = area n) must be the user code's own mask; the
    panel only considers those areas. Its high byte goes in num_dev and its
    low byte in the high length byte; the reply is 1 byte.
    """
    if not 0 <= area_mask <= 0xFFFF:
        raise ValueError(f"area mask out of range: {area_mask:#x}")
    return header(
        OP_READ,
        area_mask >> 8,
        DEVICE_RAM,
        reg.ACTIVE_SCENARIO,
        (area_mask & 0xFF) << 8 | 1,
    )


def not_ready_request(scenario: int, model: PanelModel) -> bytes:
    """Ask which zones would prevent activating a scenario (0-based).

    The selector (0x80 | scenario) goes in the high length byte and the reply
    length in the low one.
    """
    if not 0 <= scenario < model.scenarios:
        raise ValueError(f"scenario out of range: {scenario}")
    return header(
        OP_READ,
        0,
        DEVICE_RAM,
        reg.NOT_READY_ZONES,
        (0x80 | scenario) << 8 | model.zone_bitmap_length,
    )


def parse_read_reply(reply: bytes, length: int) -> bytes:
    """Check a read reply (data + checksum) and return the data.

    The form of a SmartLAN/SI reply: the panel's reply as it is.
    """
    if len(reply) != length + 1:
        raise ProtocolError(f"expected {length + 1} bytes, got {len(reply)}")
    data = reply[:-1]
    if checksum(data) != reply[-1]:
        raise ChecksumError(
            f"checksum {reply[-1]:#04x} does not match {checksum(data):#04x}"
        )
    return data


def parse_plain_read_reply(reply: bytes, length: int) -> bytes:
    """Check a read reply without checksum (data only) and return the data.

    The form of a SmartLAN/G reply: the board checks the panel's checksum
    itself and sends the data alone.
    """
    if len(reply) != length:
        raise ProtocolError(f"expected {length} bytes, got {len(reply)}")
    return reply


PIN_LENGTH = 6
_COMMAND_REGISTERS = range(reg.ARM_AREAS, reg.STOP_SIRENS_OR_CALLS + 1)


def carries_pin(frame: bytes) -> bool:
    """Whether a request is a command write, whose payload starts with the PIN."""
    address = frame[3] << 8 | frame[4]
    return (
        len(frame) > HEADER_LENGTH
        and frame[0] == OP_WRITE
        and frame[2] == DEVICE_RAM
        and address in _COMMAND_REGISTERS
    )


def describe(frame: bytes) -> str:
    """A request in hex for logs; the PIN of a command is masked."""
    text = frame[:HEADER_LENGTH].hex(" ")
    payload = frame[HEADER_LENGTH:]
    if carries_pin(frame):
        text += " | " + " ".join(["**"] * PIN_LENGTH)
        payload = payload[PIN_LENGTH:]
    if payload:
        text += " | " + payload.hex(" ")
    return text


def check_write_ack(ack: bytes, payload: bytes) -> None:
    """Check the 1-byte acknowledgement of a write.

    The error does not show the byte: for a command it is a checksum over
    the PIN.
    """
    if len(ack) != 1 or ack[0] != checksum(payload):
        raise ProtocolError("unexpected write acknowledgement")


def _check_length(length: int, limit: int) -> None:
    if not 1 <= length <= limit:
        raise ValueError(f"length must be 1-{limit}, not {length}")
