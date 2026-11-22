"""The system report: what the integration knows about the panel, in one file.

Built from the blocks just read from the panel, the configuration stored in
the entry and the transport's statistics. Every field is placed by level:
public data always, names, the serial number and addresses from Full on,
PINs, phone numbers and passwords only at Full confidential. A last check
searches the finished text for the PIN, the board's credentials, the host
and (at Redacted) the labels, and drops any section where one turns up.
No Home Assistant imports.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import ipaddress
import json
import logging
import re
from typing import Any

from . import stored_config
from .api import BlockRead, ReportReads
from .const import (
    BOARD_SMARTLAN_G,
)
from .panel_view import configuration_view
from .protocol import registers as reg
from .protocol.commands import encode_pin
from .protocol.events import (
    EVENT_RECORD_LENGTH,
    decode_event_pointers,
    decode_event_record,
    normalized_event_record,
)
from .protocol.masking import (
    LABEL_SHOWN,
    PRODUCTION_AREA_FIELDS,
    Fields,
    Level,
    Privacy,
    decode_production_area,
    hex_rows,
    label_fields,
    mask,
    shown,
    whole,
)
from .protocol.report_layout import Space
from .protocol.smartlan import (
    G_REPLY_FIELDS,
    MAC_HIDDEN,
    SI_REPLY_FIELDS,
    decode_si_reply,
    split_g_reply,
)
from .protocol.status import (
    decode_active_scenario,
    decode_area_status,
    decode_cloud_status,
    decode_faults,
    decode_not_ready_zones,
    decode_panel_state,
    decode_zone_inclusion,
    decode_zone_memory,
    decode_zone_states,
)

_LOGGER = logging.getLogger(__name__)

REPORT_VERSION = 1
SELF_CHECK_ERROR = {"error": "self-check"}

# Blocks also stored in the entry, under the same name. The stored physical
# map covers the three map blocks: each one's part starts at its distance
# from the first.
_CACHED: dict[str, str] = {
    **{name: name for name in stored_config.BLOCKS},
    "physical_map_expanders": stored_config.PHYSICAL_MAP,
    "physical_map_keypads": stored_config.PHYSICAL_MAP,
}
_PHYSICAL_MAP = "physical_map"


@dataclass(frozen=True, slots=True)
class EntryFacts:
    """What the report needs from the config entry and Home Assistant."""

    title: str
    entry_version: str
    integration_version: str
    board: str
    host: str
    port: int
    options: Mapping[str, Any]
    stored_config: Mapping[str, Any]
    # One per entity: kind, unique suffix, enabled; entity_id and name too
    entities: list[dict[str, Any]] = field(default_factory=list)
    # Secrets the finished text must not contain below Full confidential
    pin: str | None = None
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True, slots=True)
class ReportInput:
    """Everything a report is built from."""

    report_id: str
    created: str  # ISO 8601 with the time zone
    level: Level
    entry: EntryFacts
    reads: ReportReads | None  # None when the panel could not be read
    read_error: str | None = None
    board_reply: bytes | None = None
    transport_stats: Mapping[str, Any] | None = None
    trace: Iterable[Mapping[str, Any]] = ()
    # The PIN's user code: found, its 1-based number and its areas
    user_code: Mapping[str, Any] | None = None


# ------------------------------------------------------------------ helpers


def plain(value: Any) -> Any:
    """A decoded value as JSON: dataclasses, enums, sets, bytes, tuples."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.name.lower() if isinstance(value.value, int) else value.value
    if isinstance(value, (frozenset, set)):
        return sorted(plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _label_text(text: str, level: Level) -> str:
    """A label as a level shows it: Redacted keeps its first characters."""
    if shown(Privacy.PERSONAL, level) or not text:
        return text
    return text[:LABEL_SHOWN] + "…" if len(text) > LABEL_SHOWN else text


def _host_text(host: str, level: Level) -> str:
    """The panel's address as a level shows it."""
    if shown(Privacy.PERSONAL, level):
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "host name (hidden)"
    return (
        "private address (hidden)" if address.is_private else "public address (hidden)"
    )


def _fields(read: BlockRead, length: int) -> Fields:
    block = read.block
    if block.space is Space.LABELS:
        return label_fields(length)
    if block.name == "production_area":
        return PRODUCTION_AREA_FIELDS
    return whole(length, block.privacy)


# ------------------------------------------------------------------ sections


def _cached(
    read: BlockRead, stored: Mapping[str, Any], physical_map: int | None
) -> bytes | None:
    """The stored copy of a block, if the entry keeps one."""
    block = read.block
    if (name := _CACHED.get(block.name)) is None:
        return None
    raw = stored_config.raw_block(stored, name)
    if raw is None:
        return None
    offset = 0
    if name == _PHYSICAL_MAP:
        if physical_map is None:
            return None
        offset = block.address - physical_map
    return raw[offset : offset + block.length] or None


def _block(
    read: BlockRead,
    stored: Mapping[str, Any],
    level: Level,
    physical_map: int | None = None,
) -> dict[str, Any]:
    """One block: hex rows masked by level; secret ones withheld.

    physical_map is the address of the table's first map block.
    """
    block = read.block
    item: dict[str, Any] = {
        "name": block.name,
        "space": str(block.space),
        "address": f"0x{block.address:05X}",
        "length": block.length,
        "privacy": str(block.privacy),
        "source": None,
        "sha256": None,
        "rows": [],
        "masked": [],
        "error": read.error,
        "not_read": read.not_read,
        "withheld": None,
    }
    if block.space is Space.LABELS and read.address is not None:
        item["read_at"] = f"0x{read.address:05X}"
    cached = _cached(read, stored, physical_map)
    data = read.data if read.data is not None else cached
    if data is None:
        return item
    item["source"] = "fresh" if read.data is not None else "cached"
    if block.privacy is Privacy.SECRET and not shown(Privacy.SECRET, level):
        item["withheld"] = "secret"
        return item
    # A hash of private data could be matched against guesses: only shown
    # where the data itself is
    if shown(block.privacy, level):
        compared = min(len(data), len(cached)) if cached and read.data else None
        item["sha256"] = {
            "fresh": _sha256(read.data[:compared] if compared else read.data)
            if read.data is not None
            else None,
            "cached": _sha256(cached[:compared] if compared else cached)
            if cached is not None
            else None,
        }
        if compared is not None and compared != len(read.data):
            item["sha256"]["compared_bytes"] = compared
    masked_data, ranges = mask(data, _fields(read, len(data)), level)
    start = read.address if read.address is not None else block.address
    item["rows"] = hex_rows(start, masked_data, ranges)
    item["masked"] = [r.as_dict() for r in ranges]
    return item


def _by_name(reads: ReportReads | None) -> dict[str, BlockRead]:
    return {} if reads is None else {r.block.name: r for r in reads.reads}


def _panel(data: ReportInput) -> dict[str, Any]:
    reads, level = data.reads, data.level
    stored = data.entry.stored_config
    section: dict[str, Any] = {}
    if reads is not None:
        firmware = reads.firmware
        section |= {
            "model_string": reads.model_string.hex(),
            "firmware": firmware.version,
            "family": reads.table.family,
            "model": firmware.model,
            "profile": reads.model.name,
            "fallback": reads.fallback,
            "layout_verified": reads.table.layout_verified,
            "experimental": reads.model.experimental,
        }
    else:
        raw = stored_config.raw_block(stored, stored_config.MODEL_STRING)
        section |= {
            "model_string": None if raw is None else raw.hex(),
            "source": "cached",
        }
    production = _by_name(reads).get("production_area")
    if production is not None and production.data:
        area = decode_production_area(production.data)
        section["serial"] = (
            area.serial if shown(Privacy.PERSONAL, level) else area.serial_shown
        )
        section["hardware_signature"] = area.hardware_signature.hex()
        section["brand"] = area.brand
    if data.user_code is not None:
        section["user_code"] = dict(data.user_code)
    return section


def _si_reply_fields(raw: bytes, level: Level) -> dict[str, Any] | None:
    reply = decode_si_reply(raw)
    if reply is None:
        return None
    personal = shown(Privacy.PERSONAL, level)
    result: dict[str, Any] = {
        "mac": reply.mac.hex(":")
        if personal
        else "xx:" * MAC_HIDDEN.length + reply.mac_shown.hex(":"),
        "type": reply.config_type,
        "port": reply.port,
        "board_firmware": reply.firmware,
        "lan_password_enabled": reply.lan_password_enabled,
        "dynamic_dns_host": reply.ddns_host
        if personal
        else ("set" if reply.ddns_host else "empty"),
    }
    if personal:
        result |= {
            "ip": reply.ip,
            "subnet_mask": reply.subnet_mask,
            "gateway": reply.gateway,
            "dns": reply.dns,
            "dynamic_dns_user": reply.ddns_user,
        }
    if shown(Privacy.SECRET, level):
        result |= {
            "dynamic_dns_password": reply.ddns_password,
            "lan_password": reply.lan_password,
        }
    return result


def _board(data: ReportInput) -> dict[str, Any]:
    entry, level, reply = data.entry, data.level, data.board_reply
    section: dict[str, Any] = {
        "type": entry.board,
        "host": _host_text(entry.host, level),
        "port": entry.port,
        "reply": None,
    }
    if reply is None:
        # The board answers with a broadcast on its own network only
        section["reply_note"] = (
            "no reply (Home Assistant may not be on the board's network)"
        )
        return section
    if entry.board == BOARD_SMARTLAN_G:
        parts = split_g_reply(reply)
        for index, (_name, privacy) in enumerate(G_REPLY_FIELDS[: len(parts)]):
            if privacy is Privacy.PERSONAL:
                parts[index] = _label_text(parts[index], level)
        section["reply"] = {"fields": parts}
        return section
    masked, ranges = mask(reply, SI_REPLY_FIELDS, level)
    section["reply"] = {
        "rows": hex_rows(0, masked, ranges),
        "masked": [r.as_dict() for r in ranges],
        "fields": _si_reply_fields(reply, level),
    }
    return section


def _mask_labels(value: Any, level: Level) -> Any:
    """Every "label" (and device name list) in a view, as the level shows it."""
    if isinstance(value, Mapping):
        return {
            k: _label_text(v, level)
            if k == "label" and isinstance(v, str)
            else _mask_labels(v, level)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _label_text(v, level) if isinstance(v, str) else _mask_labels(v, level)
            for v in value
        ]
    return value


def _status(reads: ReportReads | None) -> dict[str, Any] | None:
    if reads is None:
        return None
    model, by = reads.model, _by_name(reads)

    def decoded(name: str, decode: Callable[[bytes], Any]) -> Any:
        read = by.get(name)
        if read is None or read.data is None:
            return None
        try:
            return plain(decode(read.data))
        except Exception as err:  # noqa: BLE001 - shown as an error, not raised
            return {"error": type(err).__name__}

    section = {
        "areas": decoded("area_status", lambda d: decode_area_status(d, model)),
        "zone_states": decoded("zone_state", lambda d: decode_zone_states(d, model)),
        "zone_inclusion": decoded(
            "zone_inclusion", lambda d: decode_zone_inclusion(d, model)
        ),
        "zone_memory": decoded("zone_memory", lambda d: decode_zone_memory(d, model)),
        "faults": decoded("faults", lambda d: decode_faults(d)),
        "panel_state": decoded(
            "faults",
            lambda d: decode_panel_state(
                d[
                    reg.PANEL_STATE_OFFSET : reg.PANEL_STATE_OFFSET
                    + reg.PANEL_STATE_LENGTH
                ]
            ),
        ),
        "active_scenario": decoded(
            "active_scenario", lambda d: decode_active_scenario(d, model)
        ),
    }
    cloud = by.get("cloud_enabled")
    if cloud is not None and cloud.data is not None:
        flags = decode_cloud_status(cloud.data)
        result = by.get("cloud_last_result")
        section["cloud"] = {
            "configured": "yes" if flags.configured else "no",
            "bit_0": flags.bit_0,
            "bit_1": flags.bit_1,
            "raw": cloud.data.hex(),
            "last_result": None
            if result is None or result.data is None
            else result.data.hex(),
        }
        if flags.other_bits:
            section["cloud"]["other_bits"] = f"0x{flags.other_bits:02x}"
    not_ready = by.get("not_ready_zones")
    if not_ready is not None and not_ready.data:
        size = not_ready.block.length
        section["not_ready_by_scenario"] = {
            str(s + 1): plain(
                decode_not_ready_zones(not_ready.data[s * size : (s + 1) * size], model)
            )
            for s in range(len(not_ready.data) // size)
        }
    return section


def _events(reads: ReportReads | None) -> dict[str, Any] | None:
    if reads is None:
        return None
    model, by = reads.model, _by_name(reads)
    section: dict[str, Any] = {"ring_size": reads.table.event_ring_size}
    pointers = by.get("event_pointers")
    if pointers is not None and pointers.data:
        decoded = decode_event_pointers(pointers.data, model)
        section |= {"head": decoded.head, "tail": decoded.tail}
    log = by.get("event_log")
    if log is None or log.data is None:
        return section
    records = []
    for index in range(len(log.data) // EVENT_RECORD_LENGTH):
        raw = log.data[index * EVENT_RECORD_LENGTH : (index + 1) * EVENT_RECORD_LENGTH]
        if raw in (b"\xff" * EVENT_RECORD_LENGTH, bytes(EVENT_RECORD_LENGTH)):
            continue
        record: dict[str, Any] = {"index": index, "raw": raw.hex()}
        try:
            record["decoded"] = plain(
                decode_event_record(normalized_event_record(raw, model), model)
            )
        except Exception as err:  # noqa: BLE001 - shown as an error, not raised
            record["decoded"] = {"error": type(err).__name__}
        records.append(record)
    section["records"] = records
    return section


def _integration(data: ReportInput) -> dict[str, Any]:
    entry, level = data.entry, data.level
    personal = shown(Privacy.PERSONAL, level)
    options = {
        k: v
        for k, v in entry.options.items()
        if k not in ("pin", "password", "username", "host")
    }
    entities = [
        e if personal else {k: e.get(k) for k in ("kind", "unique_suffix", "enabled")}
        for e in entry.entities
    ]
    return {
        "title": _label_text(entry.title, level),
        "entry_version": entry.entry_version,
        "options": options,
        "entities": entities,
    }


def _transport(data: ReportInput) -> dict[str, Any]:
    reads, level = data.reads, data.level
    trace = []
    for entry in data.trace:
        item = dict(entry)
        reply = item.get("reply")
        # Replies of plain reads may hold labels: only their length below Full
        if (
            isinstance(reply, str)
            and not shown(Privacy.PERSONAL, level)
            and re.fullmatch(r"[0-9a-f]{2}( [0-9a-f]{2})*", reply)
        ):
            item["reply"] = f"{len(reply.split())} bytes, not kept"
        trace.append(item)
    section: dict[str, Any] = {
        "stats": dict(data.transport_stats or {}),
        "trace": trace,
    }
    if reads is not None:
        section["report_reads"] = {
            "requests": reads.requests,
            "seconds": reads.seconds,
            "programming_mode": reads.programming_mode,
        }
    return section


# ---------------------------------------------------------------- self-check


@dataclass(frozen=True, slots=True)
class Forbidden:
    """What the string values of a finished report must not contain.

    Only string values are searched, never keys or numbers: a short PIN or
    a label such as "test" would otherwise match the year, a ring size or a
    key, and drop a whole section for nothing.
    """

    plain_pin: re.Pattern[str] | None = None  # not in times or the report's ID
    # (kind, pattern): what is found is logged by its kind only
    anywhere: tuple[tuple[str, re.Pattern[str]], ...] = ()
    labels: tuple[str, ...] = ()  # not in identifier-like values
    # (kind, pattern) of digits-only credentials: found only as a number of
    # their own, and not in times, the report's ID or hex data, as the PIN
    standalone: tuple[tuple[str, re.Pattern[str]], ...] = ()
    # (kind, text) of credentials too short to search inside text: found
    # only as a whole value
    whole: tuple[tuple[str, str], ...] = ()

    def match_kind(self, value: Any, key: str | None = None) -> str | None:
        """The kind of forbidden text a value holds anywhere inside; None if none.

        Kinds: pin_digits, pin_encoded, pin_ascii, credentials, host, label.
        """
        if isinstance(value, Mapping):
            items = [(v, str(k)) for k, v in value.items()]
        elif isinstance(value, list):
            items = [(v, key) for v in value]
        else:
            return self._match_text(value, key)
        return next(
            (kind for v, k in items if (kind := self.match_kind(v, k)) is not None),
            None,
        )

    def found_in(self, value: Any, key: str | None = None) -> bool:
        """Whether a value, or anything inside it, holds a forbidden text."""
        return self.match_kind(value, key) is not None

    def _match_text(self, value: Any, key: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        for kind, text in self.whole:
            if stripped == text:
                return kind
        for kind, pattern in self.anywhere:
            if pattern.search(value):
                return kind
        exempt = (
            key in _NOT_PIN_CHECKED
            or _TIMESTAMP.match(value)
            or _HEX_DATA.fullmatch(value)
        )
        if not exempt:
            if self.plain_pin is not None and self.plain_pin.search(value):
                return "pin_digits"
            for kind, pattern in self.standalone:
                if pattern.search(value):
                    return kind
        if self.labels and not _IDENTIFIER.fullmatch(value):
            if any(label in value for label in self.labels):
                return "label"
        return None


_NOT_PIN_CHECKED = frozenset({"created", "report_id"})
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_IDENTIFIER = re.compile(r"[a-z0-9_.:-]+")
# Hex rows, hashes, raw records and addresses: a PIN in them shows as bytes
# (checked as such), and its digits match there by chance (row addresses).
# Bare hex counts only with a letter or when long, so a value that is just
# digits (the PIN itself) is still checked.
_HEX_DATA = re.compile(
    r"[0-9a-f]{5}: [0-9a-fx]{2}( [0-9a-fx]{2})*"
    r"|0x[0-9A-Fa-f]+"
    r"|(?=[0-9]*[a-f])[0-9a-f]+"
    r"|[0-9a-f]{16,}"
)


class CredentialRule(Enum):
    """How the report is searched for a credential."""

    VALUE = "value"  # 1-3 characters: a value equal to it
    DIGITS = "digits"  # digits only: a number of its own, as the PIN
    ANYWHERE = "anywhere"  # inside any text, even inside a word


def credential_rule(text: str) -> CredentialRule:
    """How a credential is searched for.

    A short one would turn up in ordinary text (a hex row, a word, a
    version), and so would a number inside other numbers.
    """
    if len(text) <= LABEL_SHOWN:
        return CredentialRule.VALUE
    if text.isascii() and text.isdigit():
        return CredentialRule.DIGITS
    return CredentialRule.ANYWHERE


def _forbidden(data: ReportInput) -> Forbidden:
    """What the finished report must not contain at its level."""
    level, entry = data.level, data.entry
    if shown(Privacy.SECRET, level):
        return Forbidden()
    plain_pin = None
    anywhere: list[tuple[str, re.Pattern[str]]] = []
    if entry.pin:
        plain_pin = re.compile(rf"(?<![0-9]){re.escape(entry.pin)}(?![0-9])")
        encoded = encode_pin(entry.pin)
        for text in (encoded.hex(), encoded.hex(" ")):
            anywhere.append(("pin_encoded", re.compile(re.escape(text), re.IGNORECASE)))
        # The PIN kept as text (ASCII digits) in a block
        ascii_pin = entry.pin.encode().hex(" ")
        anywhere.append(("pin_ascii", re.compile(re.escape(ascii_pin), re.IGNORECASE)))
    standalone: list[tuple[str, re.Pattern[str]]] = []
    whole: list[tuple[str, str]] = []

    def credential(text: str) -> None:
        rule = credential_rule(text)
        escaped = re.escape(text)
        if rule is CredentialRule.VALUE:
            whole.append(("credentials", text))
        elif rule is CredentialRule.DIGITS:
            pattern = re.compile(rf"(?<![0-9]){escaped}(?![0-9])")
            standalone.append(("credentials", pattern))
        else:
            anywhere.append(("credentials", re.compile(escaped)))

    if entry.password:
        credential(entry.password)
    labels: tuple[str, ...] = ()
    if not shown(Privacy.PERSONAL, level):
        if entry.username:
            credential(entry.username)
        if entry.host:
            anywhere.append(("host", re.compile(re.escape(entry.host))))
        labels = tuple(
            label
            for label in _all_labels(entry.stored_config)
            if len(label) > LABEL_SHOWN
        )
    return Forbidden(
        plain_pin, tuple(anywhere), labels, tuple(standalone), tuple(whole)
    )


def _all_labels(config: Mapping[str, Any]) -> set[str]:
    view = configuration_view(config)
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                if k == "label" and isinstance(v, str):
                    found.add(v.strip())
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str):
                    found.add(v.strip())
                else:
                    walk(v)

    walk(view)
    found.add(str(config.get("panel_name") or "").strip())
    return {label for label in found if label}


def self_check(report: dict[str, Any], forbidden: Forbidden) -> list[str]:
    """Withhold what holds a forbidden text; where it was found.

    A block is withheld on its own (its rows and hashes removed), so one
    block does not take the others with it; any other section is replaced
    whole. Each hit is logged with the kind of text found, never the text.
    """
    hits = []
    blocks = report.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if (kind := forbidden.match_kind(block)) is None:
                continue
            block |= {
                "rows": [],
                "masked": [],
                "sha256": None,
                "withheld": "self-check",
            }
            where = f"blocks/{block.get('name')}"
            _log_hit(where, kind)
            hits.append(where)
    for key, value in report.items():
        if key in _NOT_PIN_CHECKED:
            continue
        if (kind := forbidden.match_kind(value, key)) is not None:
            report[key] = dict(SELF_CHECK_ERROR)
            _log_hit(key, kind)
            hits.append(key)
    return hits


def _log_hit(where: str, kind: str) -> None:
    _LOGGER.warning(
        "System report: %s was left out, it failed the privacy check (%s)",
        where,
        kind,
    )


# -------------------------------------------------------------------- build


def build_report(data: ReportInput) -> dict[str, Any]:
    """The report as JSON-ready data, masked for its level."""
    level, entry = data.level, data.entry
    stored = entry.stored_config
    errors = []
    if data.read_error:
        errors.append({"where": "panel", "error": data.read_error})
    if data.reads is not None and data.reads.programming_mode:
        errors.append(
            {
                "where": "panel",
                "error": f"programming mode ({data.reads.programming_mode})",
            }
        )
    blocks = []
    if data.reads is not None:
        first_map = data.reads.table.block(_PHYSICAL_MAP)
        blocks = [
            _block(read, stored, level, first_map and first_map.address)
            for read in data.reads.reads
        ]
    fallback = data.reads is not None and data.reads.fallback
    if fallback and not shown(Privacy.SECRET, level):
        # Read with another model's layout: its bytes may be anything
        configuration = {
            "source": "stored",
            "read_with": data.reads.model.name,
            "withheld": "fallback layout",
        }
    else:
        configuration = _mask_labels(configuration_view(stored), level)
        configuration["source"] = "stored"
        if fallback:
            configuration["read_with"] = data.reads.model.name
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "report_id": data.report_id,
        "level": str(level),
        "created": data.created,
        "integration_version": entry.integration_version,
        "panel": _panel(data),
        "board": _board(data),
        "configuration": configuration,
        "status": _status(data.reads),
        "events": _events(data.reads),
        "blocks": blocks,
        "integration": _integration(data),
        "transport": _transport(data),
        "errors": errors,
    }
    hits = self_check(report, _forbidden(data))
    for section in hits:
        report["errors"] = [
            *report.get("errors", []),
            {"where": section, "error": "self-check"},
        ]
    return report


def report_text(report: Mapping[str, Any]) -> bytes:
    """The report as compact JSON text."""
    return json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
