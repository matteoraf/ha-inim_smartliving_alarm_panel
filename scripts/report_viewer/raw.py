"""The raw block reader: each block's bytes and the ways to read them.

Part of the view: the bytes come from the report's hex rows ("xx" for a
masked byte, kept as None and shown as "·", never guessed). Each block has
a default way to read it; blocks with records the integration can decode
also offer their fields, decoded here with the integration's own decoders.
The page's script offers the same formats; these are used for the page as
it loads and when scripts are off.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from typing import Any

from . import loader

ROW_DEFAULT = 16
ROW_MIN, ROW_MAX = 2, 32
MASK = "·"


class Format(StrEnum):
    """Ways to read a block."""

    TEXT = "text"
    DECIMAL = "decimal"
    BITS = "bits"
    NIBBLES = "nibbles"
    DIGITS = "digits"
    U16 = "u16"
    FIELDS = "fields"


FORMAT_NAMES = {
    Format.TEXT: "Text",
    Format.DECIMAL: "Decimal",
    Format.BITS: "Bits",
    Format.NIBBLES: "Nibbles",
    Format.DIGITS: "Digits",
    Format.U16: "16-bit numbers",
    Format.FIELDS: "Fields",
}

# Default ways to read blocks, by name (anything else: text)
_BITS = {
    "general_options",
    "programming_00000",
    "zone_inclusion",
    "zone_memory",
    "faults",
    "faults_present",
    "zone_fault_memory",
    "zones_not_ready",
    "walk_test_hits",
    "keypads_present",
    "peripherals_lost_tampered",
    "not_ready_zones",
    "extra_output_options",
    "zone_options",
    "cloud_enabled",
}
_U16 = {"label_pointers", "event_pointers", "event_log_pointers"}
_DIGITS = {"pin_table"}


@dataclass(slots=True)
class RawBlock:
    """One block as the reader shows it."""

    name: str
    header: str  # space · address · length · privacy · source
    start: int  # address of the first byte
    data: list[int | None]  # None: masked
    default: Format = Format.TEXT
    record: int | None = None  # record size, for Fields
    fields: list[str] = field(default_factory=list)  # one line per record
    badge: str | None = None  # withheld / not read, and why

    @property
    def formats(self) -> list[Format]:
        """The ways this block can be read."""
        offered = [f for f in Format if f is not Format.FIELDS]
        return offered + ([Format.FIELDS] if self.fields else [])

    @property
    def row_length(self) -> int:
        """The default row length."""
        return (
            self.record
            if self.default is Format.FIELDS and self.record
            else ROW_DEFAULT
        )

    def hex_text(self) -> str:
        """The bytes as hex, "xx" for masked ones, without separators."""
        return "".join("xx" if b is None else f"{b:02x}" for b in self.data)


# ------------------------------------------------------------------ formats


def _printable(b: int | None) -> str:
    return chr(b) if b is not None and 0x20 <= b < 0x7F else MASK


def _bits(row: list[int | None]) -> str:
    cells = ["········" if b is None else f"{b:08b}" for b in row]
    set_bits = [
        str(i * 8 + bit + 1)
        for i, b in enumerate(row)
        if b is not None
        for bit in range(8)
        if b >> bit & 1
    ]
    return " ".join(cells) + "  set: " + (", ".join(set_bits) or "none")


def _digit(b: int | None) -> str:
    if b is None:
        return MASK
    if b == 0xFF:
        return "_"
    return str(b) if b < 10 else "?"


def _u16(row: list[int | None], big: bool) -> str:
    words = []
    for i in range(0, len(row) - 1, 2):
        a, b = row[i], row[i + 1]
        if a is None or b is None:
            words.append(MASK)
            continue
        value = (a << 8 | b) if big else (b << 8 | a)
        words.append(f"{value} (0x{value:04x})")
    if len(row) % 2:
        words.append(MASK)
    return "  ".join(words)


ROW_FORMATTERS: dict[Format, Callable[[list[int | None]], str]] = {
    Format.TEXT: lambda row: "".join(_printable(b) for b in row),
    Format.DECIMAL: lambda row: " ".join(
        MASK.rjust(3) if b is None else f"{b:3d}" for b in row
    ),
    Format.BITS: _bits,
    Format.NIBBLES: lambda row: " ".join(
        MASK if b is None else f"{b & 0xF}/{b >> 4}" for b in row
    ),
    Format.DIGITS: lambda row: "".join(_digit(b) for b in row),
    Format.U16: lambda row: _u16(row, big=False),
}


def lines(
    block: RawBlock, fmt: Format | None = None, length: int | None = None
) -> list[tuple[str, str]]:
    """(hex row, decoded row) pairs, as the page shows them as it loads."""
    fmt = fmt or block.default
    length = length or block.row_length
    out = []
    for index, start in enumerate(range(0, len(block.data), length)):
        row = block.data[start : start + length]
        hex_row = f"{block.start + start:05x}: " + " ".join(
            "xx" if b is None else f"{b:02x}" for b in row
        )
        if fmt is Format.FIELDS:
            decoded = block.fields[index] if index < len(block.fields) else ""
        else:
            decoded = ROW_FORMATTERS[fmt](row)
        out.append((hex_row, decoded))
    return out


# ------------------------------------------------------------------ fields


def _plain(value: Any) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "{"
            + ", ".join(
                f"{f.name}={_plain(getattr(value, f.name))}" for f in fields(value)
            )
            + "}"
        )
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, (set, frozenset)):
        return "[" + ", ".join(_plain(v) for v in sorted(value)) + "]"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_plain(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {_plain(v)}" for k, v in value.items()) + "}"
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _record_line(record: Any) -> str:
    if is_dataclass(record) and not isinstance(record, type):
        return ", ".join(
            f"{f.name}={_plain(getattr(record, f.name))}" for f in fields(record)
        )
    return _plain(record)


def panel_model(report: Mapping[str, Any]) -> Any | None:
    """The integration's profile of the report's panel; None if not known."""
    panel = report.get("panel") or {}
    try:
        model_module = loader.module("protocol.model")
        firmware = model_module.parse_model_string(bytes.fromhex(panel["model_string"]))
        model, _reason = model_module.model_for_or_fallback(firmware)
    except Exception:  # noqa: BLE001 - no model, no fields
        return None
    return model


def _decoders(model: Any) -> dict[str, tuple[int, Callable[[bytes], list[Any]]]]:
    """Record size and a whole-block decoder, by block name."""
    config = loader.module("protocol.config")
    events = loader.module("protocol.events")

    def each(size: int, decode: Callable[[bytes], Any]) -> Callable[[bytes], list[Any]]:
        return lambda data: [
            decode(data[i : i + size]) for i in range(0, len(data) - size + 1, size)
        ]

    def options(data: bytes) -> list[str]:
        refuses = config.refuses_arming_when_not_ready(data[:6])
        return [f"refuses arming with zones not ready={'yes' if refuses else 'no'}"]

    def peripherals(data: bytes) -> list[Any]:
        return [config.decode_peripherals(data[: model.peripherals_length], model)]

    def whole(count: int, size: int, decode: Callable[[bytes, Any], Any]):
        """A whole-table decoder, the block padded or cut to the table's size."""
        length = count * size
        return lambda data: decode((data + bytes(length))[:length], model)

    return {
        "zone_records": (
            model.zone_record_length,
            whole(model.zones, model.zone_record_length, config.decode_zone_records),
        ),
        "terminal_records": (
            model.terminal_record_length,
            whole(
                model.terminals, model.terminal_record_length, config.decode_terminals
            ),
        ),
        "area_records": (
            model.area_record_length,
            whole(model.areas, model.area_record_length, config.decode_area_records),
        ),
        "scenario_records": (
            model.scenario_record_length,
            whole(
                model.scenarios,
                model.scenario_record_length,
                config.decode_scenario_records,
            ),
        ),
        "code_records": (
            model.code_record_length,
            each(
                model.code_record_length, lambda r: config.decode_code_record(r, model)
            ),
        ),
        "general_options": (10, options),
        "programming_00000": (model.peripherals_length, peripherals),
        "event_log": (
            events.EVENT_RECORD_LENGTH,
            each(
                events.EVENT_RECORD_LENGTH,
                lambda r: events.decode_event_record(
                    events.normalized_event_record(r, model), model
                ),
            ),
        ),
    }


def _fields(
    name: str, data: list[int | None], model: Any
) -> tuple[int | None, list[str]]:
    """Record size and one decoded line per record; none if no decoder."""
    if model is None or not data:
        return None, []
    try:
        decoders = _decoders(model)
    except Exception:  # noqa: BLE001 - the integration's modules are missing
        return None, []
    if name not in decoders:
        return None, []
    size, decode = decoders[name]
    try:
        records = decode(bytes(0 if b is None else b for b in data))
    except Exception as err:  # noqa: BLE001 - shown, not raised
        return size, [f"cannot decode ({type(err).__name__})"]
    out = []
    for index in range(0, len(data), size):
        chunk = data[index : index + size]
        record = index // size
        if None in chunk:
            out.append("[masked]")
        elif record < len(records):
            out.append(_record_line(records[record]))
        else:
            out.append("")
    return size, out


# ------------------------------------------------------------------ blocks


def _parse_rows(rows: list[str]) -> tuple[int, list[int | None]]:
    start, data = None, []
    for row in rows:
        address, _, cells = row.partition(": ")
        if start is None:
            start = int(address, 16)
        data += [None if c == "xx" else int(c, 16) for c in cells.split()]
    return start or 0, data


def default_format(name: str, space: str, has_fields: bool) -> Format:
    """How a block opens."""
    if has_fields:
        return Format.FIELDS
    if space == "labels":
        return Format.TEXT
    if name in _BITS:
        return Format.BITS
    if name in _U16:
        return Format.U16
    if name in _DIGITS:
        return Format.DIGITS
    if name == "scenario_records":
        return Format.NIBBLES
    return Format.TEXT


def raw_blocks(report: Mapping[str, Any]) -> list[RawBlock]:
    """Every block of the report, ready for the reader."""
    blocks = report.get("blocks")
    if not isinstance(blocks, list):
        return []
    model = panel_model(report)
    out = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        name = str(block.get("name"))
        parts = [
            str(block.get("space")),
            str(block.get("read_at") or block.get("address")),
            f"{block.get('length')} bytes",
            str(block.get("privacy")),
        ]
        if block.get("source"):
            parts.append(str(block["source"]))
        start, data = _parse_rows(block.get("rows") or [])
        why = block.get("withheld") or block.get("not_read") or block.get("error")
        badge = None
        if not data:
            label = "withheld" if block.get("withheld") else "not read"
            badge = f"{label}: {why}" if why else "no data"
        record, decoded = _fields(name, data, model)
        out.append(
            RawBlock(
                name=name,
                header=" · ".join(parts),
                start=start,
                data=data,
                default=default_format(name, str(block.get("space")), bool(decoded)),
                record=record if decoded else None,
                fields=decoded,
                badge=badge,
            )
        )
    return out
