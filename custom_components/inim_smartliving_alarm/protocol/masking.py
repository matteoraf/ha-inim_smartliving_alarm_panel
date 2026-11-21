"""Which bytes of panel data are personal or secret, and masking them.

A system report shows memory blocks as hex rows; below the level that may
show them, personal bytes are replaced and secret blocks left out. Which
bytes of a record are personal (a label past its first characters, the
serial number) is panel knowledge, so it is described here; the fields of
a SmartLAN/SI board's discovery reply are in smartlan.py.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .model import LABEL_LENGTH

LABEL_SHOWN = 3  # characters of a label kept at the Redacted level
ROW_LENGTH = 16  # bytes per hex row


class Privacy(StrEnum):
    """How private a block or a field is."""

    PUBLIC = "public"
    PERSONAL = "personal"  # names, serial number, addresses
    SECRET = "secret"  # PINs, phone numbers, key serials, passwords


class Level(StrEnum):
    """What a report may show."""

    REDACTED = "redacted"  # public data only: safe to post
    FULL = "full"  # also names, serial number and addresses
    CONFIDENTIAL = "confidential"  # also PINs, phone numbers and passwords


_SHOWN = {
    Level.REDACTED: frozenset({Privacy.PUBLIC}),
    Level.FULL: frozenset({Privacy.PUBLIC, Privacy.PERSONAL}),
    Level.CONFIDENTIAL: frozenset(Privacy),
}


def shown(privacy: Privacy, level: Level) -> bool:
    """Whether data of that privacy is shown at that level."""
    return privacy in _SHOWN[level]


# (offset, length, privacy) of the private parts of a record; bytes not
# listed are public
Fields = tuple[tuple[int, int, Privacy], ...]


def label_fields(length: int) -> Fields:
    """A label table: each 16-byte label is personal past its first characters."""
    return tuple(
        (start + LABEL_SHOWN, LABEL_LENGTH - LABEL_SHOWN, Privacy.PERSONAL)
        for start in range(0, length, LABEL_LENGTH)
    )


# The production area (RAM 0xFEA0, 52 bytes): 16 ASCII serial digits first,
# of which the last 4 are public; the hardware signature and brand are public
SERIAL_DIGITS = 16
SERIAL_SHOWN = 4
HARDWARE_SIGNATURE_OFFSET = 44  # 2 bytes, B2 55
BRAND_OFFSET = 50  # 2 bytes LE, 0 = standard Inim
PRODUCTION_AREA_FIELDS: Fields = (
    (0, SERIAL_DIGITS - SERIAL_SHOWN, Privacy.PERSONAL),
    (  # meaning not known
        SERIAL_DIGITS,
        HARDWARE_SIGNATURE_OFFSET - SERIAL_DIGITS,
        Privacy.PERSONAL,
    ),
)


@dataclass(frozen=True, slots=True)
class ProductionArea:
    """The panel's production area: serial number, signature and brand."""

    serial: str
    hardware_signature: bytes
    brand: int

    @property
    def serial_shown(self) -> str:
        """The serial number as the Redacted level shows it."""
        return "…" + self.serial[-SERIAL_SHOWN:]


def decode_production_area(data: bytes) -> ProductionArea:
    """Decode the production area (RAM 0xFEA0, 52 bytes)."""
    return ProductionArea(
        serial=data[:SERIAL_DIGITS].decode("ascii", "replace"),
        hardware_signature=data[
            HARDWARE_SIGNATURE_OFFSET : HARDWARE_SIGNATURE_OFFSET + 2
        ],
        brand=int.from_bytes(data[BRAND_OFFSET : BRAND_OFFSET + 2], "little"),
    )


@dataclass(frozen=True, slots=True)
class MaskedRange:
    """Bytes replaced in a block: offset, length and why."""

    offset: int
    length: int
    privacy: Privacy

    def as_dict(self) -> dict[str, object]:
        """The range in a report."""
        return {
            "offset": self.offset,
            "length": self.length,
            "privacy": str(self.privacy),
        }


def mask(
    data: bytes, fields: Iterable[tuple[int, int, Privacy]], level: Level
) -> tuple[bytes, tuple[MaskedRange, ...]]:
    """Replace the bytes of the fields the level may not show.

    Returns the data with those bytes zeroed (they print as "xx" in hex
    rows) and the ranges replaced, clipped to the data.
    """
    out = bytearray(data)
    ranges = []
    for offset, length, privacy in fields:
        if shown(privacy, level):
            continue
        start, end = max(0, offset), min(len(data), offset + length)
        if end <= start:
            continue
        out[start:end] = bytes(end - start)
        ranges.append(MaskedRange(start, end - start, privacy))
    return bytes(out), tuple(ranges)


def whole(length: int, privacy: Privacy) -> Fields:
    """A record that is private as a whole."""
    return ((0, length, privacy),) if privacy is not Privacy.PUBLIC else ()


def hex_rows(
    address: int, data: bytes, masked: Iterable[MaskedRange] = ()
) -> list[str]:
    """Rows of 16 bytes with their address; masked bytes print as "xx"."""
    hidden = set()
    for item in masked:
        hidden.update(range(item.offset, item.offset + item.length))
    rows = []
    for start in range(0, len(data), ROW_LENGTH):
        cells = [
            "xx" if start + i in hidden else f"{byte:02x}"
            for i, byte in enumerate(data[start : start + ROW_LENGTH])
        ]
        rows.append(f"{address + start:05x}: {' '.join(cells)}")
    return rows
