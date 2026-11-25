"""Decoding of the event log (raw 9-byte records in EEPROM)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .errors import ProtocolError
from .event_codes import FW51_FREE_EVENTS
from .model import PanelModel

EVENT_RECORD_LENGTH = 9
EPOCH = datetime(2000, 1, 1)  # timestamps count seconds from here, panel local time
NO_AGENT = 0xFF
NO_LOCATION = 0xFF
REMOTE_LOCATION = 0xFE  # app, cloud or installer software
# Written in place of a code that firmware 6 does not have (it uses 0-113)
NO_FW6_CODE = 0x7F
CODE_OFFSET = 8  # byte with the event code (bits 0-6) and activation (bit 7)
CODE_MASK = 0x7F
ACTIVATION_BIT = 0x80


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
    activation: bool  # False: the event ended

    @property
    def action(self) -> int:
        """The code byte in firmware 6 numbering, activation bit included.

        NO_FW6_CODE stands for a code firmware 6 does not have.
        """
        code = NO_FW6_CODE if self.code is None else self.code
        return (ACTIVATION_BIT if self.activation else 0) | code


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


def indexes_to_read(
    pointers: EventPointers,
    model: PanelModel,
    *,
    count: int | None = None,
    after: int | None = None,
    limit: int,
) -> list[int]:
    """Ring indexes of the events to read, newest first.

    With count, the newest count events (at least 1, at most limit); with
    after, those written since that index (none if it is not a ring index
    or the ring is empty), at most limit. The ring wraps around.
    """
    if count is not None:
        return newest_indexes(pointers, model, min(max(1, count), limit))
    if after is None:
        raise ValueError("either count or after is needed")
    size = model.event_ring_size
    newest = newest_indexes(pointers, model, 1)
    if not newest or not 0 <= after < size:
        return []
    return newest_indexes(pointers, model, min((newest[0] - after) % size, limit))


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


def raw_event_code(data: bytes) -> int:
    """A record's event code as the panel numbers it (0-127)."""
    return data[CODE_OFFSET] & CODE_MASK


def fw51_free_event(data: bytes, model: PanelModel) -> int | None:
    """The number (1-14) of a firmware 5.1 free event, None for other events.

    data is the raw record; on firmware 6 there are no free events.
    """
    code = raw_event_code(data)
    if model.event_codes is None or code not in FW51_FREE_EVENTS:
        return None
    return code - FW51_FREE_EVENTS.start + 1


def normalized_event_record(data: bytes, model: PanelModel) -> bytes:
    """The record with its event code in firmware 6 numbering.

    Unchanged on firmware 6; a code firmware 6 does not have becomes
    NO_FW6_CODE.
    """
    _check_record(data)
    if model.event_codes is None:
        return data
    code = fw6_event_code(raw_event_code(data), model)
    flags = data[CODE_OFFSET] & ACTIVATION_BIT
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
        code=fw6_event_code(raw_event_code(data), model),
        activation=bool(data[CODE_OFFSET] & ACTIVATION_BIT),
    )


def _check_record(data: bytes) -> None:
    if len(data) != EVENT_RECORD_LENGTH:
        raise ProtocolError(f"event record: expected 9 bytes, got {len(data)}")
