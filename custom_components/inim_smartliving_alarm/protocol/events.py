"""Decoding of the event log (raw 9-byte records in EEPROM)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .errors import ProtocolError
from .model import PanelModel

EVENT_RECORD_LENGTH = 9
EPOCH = datetime(2000, 1, 1)  # timestamps count seconds from here, panel local time
NO_AGENT = 0xFF
NO_LOCATION = 0xFF
REMOTE_LOCATION = 0xFE  # app, cloud or installer software
# The action code of a valid key on a reader (with the activation bit)
VALID_KEY_ACTION = 0x96
# Written in place of a code that firmware 6 does not have (it uses 0-113)
NO_FW6_CODE = 0x7F
CODE_OFFSET = 8  # byte with the event code (bits 0-6) and activation (bit 7)


def reader_index(location: int, keypads: int) -> int | None:
    """The reader (0-based) an event location is; None if it is not one.

    Locations count the keypads first, then the readers; locations from
    0xF0 up are not devices.
    """
    index = location - keypads
    return index if location < 0xF0 and index >= 0 else None


@dataclass(frozen=True, slots=True)
class EventPointers:
    """Ring pointers: head = next slot to write; tail > 0 once the ring wrapped."""

    head: int
    tail: int

    @property
    def wrapped(self) -> bool:
        """Whether the ring has been filled at least once."""
        return self.tail > 0


@dataclass(frozen=True, slots=True)
class PanelEvent:
    """One event-log record."""

    timestamp: datetime  # panel local time, no time zone
    areas: frozenset[int]
    agent: int | None  # user code, key, zone, ... depending on the event code
    location: int | None  # keypad, reader, REMOTE_LOCATION, ...
    code: int | None  # event type, firmware 6 numbering; None if it has none
    activation: bool  # False = restoral


def decode_event_pointers(data: bytes, model: PanelModel) -> EventPointers:
    """Decode the 4-byte event pointers (head, tail; u16 LE each)."""
    if len(data) != 4:
        raise ProtocolError(f"event pointers: expected 4 bytes, got {len(data)}")
    head = int.from_bytes(data[0:2], "little")
    tail = int.from_bytes(data[2:4], "little")
    if head >= model.event_ring_size or tail >= model.event_ring_size:
        raise ProtocolError(f"event pointers out of range: head {head}, tail {tail}")
    return EventPointers(head, tail)


def newest_indexes(pointers: EventPointers, model: PanelModel, count: int) -> list[int]:
    """Ring indexes of the newest events, newest first (at most count)."""
    size = model.event_ring_size
    available = size if pointers.wrapped else pointers.head
    return [(pointers.head - 1 - n) % size for n in range(min(count, available))]


def event_address(index: int, model: PanelModel) -> int:
    """EEPROM address of a ring entry."""
    if not 0 <= index < model.event_ring_size:
        raise ValueError(f"event index out of range: {index}")
    return model.event_log + EVENT_RECORD_LENGTH * index


def decode_timestamp(seconds: int) -> datetime:
    """Panel time (seconds since 2000-01-01, local time) as a naive datetime."""
    return EPOCH + timedelta(seconds=seconds)


def fw6_event_code(code: int, model: PanelModel) -> int | None:
    """The firmware 6 number of one of the panel's event codes (0-127)."""
    if model.event_codes is None:
        return code
    return model.event_codes[code]


def normalized_event_record(data: bytes, model: PanelModel) -> bytes:
    """The record with its event code in firmware 6 numbering.

    Unchanged on firmware 6; a code firmware 6 does not have becomes
    NO_FW6_CODE.
    """
    _check_record(data)
    if model.event_codes is None:
        return data
    code = fw6_event_code(data[CODE_OFFSET] & 0x7F, model)
    flags = data[CODE_OFFSET] & 0x80
    return data[:CODE_OFFSET] + bytes([flags | (NO_FW6_CODE if code is None else code)])


def decode_event_record(data: bytes, model: PanelModel) -> PanelEvent:
    """Decode a 9-byte event record as read from the panel (raw).

    The code is given in firmware 6 numbering. Pass the raw record, not one
    from normalized_event_record: on other firmware it would be translated
    twice.
    """
    _check_record(data)
    mask = int.from_bytes(data[4:6], "little")
    return PanelEvent(
        timestamp=decode_timestamp(int.from_bytes(data[0:4], "little")),
        areas=frozenset(a for a in range(1, model.areas + 1) if mask >> (a - 1) & 1),
        agent=None if data[6] == NO_AGENT else data[6],
        location=None if data[7] == NO_LOCATION else data[7],
        code=fw6_event_code(data[CODE_OFFSET] & 0x7F, model),
        activation=bool(data[CODE_OFFSET] & 0x80),
    )


def _check_record(data: bytes) -> None:
    if len(data) != EVENT_RECORD_LENGTH:
        raise ProtocolError(f"event record: expected 9 bytes, got {len(data)}")
