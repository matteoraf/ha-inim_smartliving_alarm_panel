"""A report as plain data to lay out: sections of rows of cells.

Nothing here prints: the terminal and HTML renderers lay the view out, and
another front end can use it too. Values the report hides are never guessed:
a cut label stays cut, a withheld part says why.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from . import loader
from .files import Kind
from .raw import RawBlock, panel_model, raw_blocks


class State(StrEnum):
    """How a value is shown in the report."""

    SHOWN = "shown"
    MASKED = "masked"  # cut or partly hidden
    WITHHELD = "withheld"  # left out, with a reason
    MISSING = "missing"  # not in the report


@dataclass(frozen=True, slots=True)
class Cell:
    """One value; a badge names a state to show as such (e.g. "alarm")."""

    text: str
    state: State = State.SHOWN
    badge: str | None = None
    mono: bool = False  # hex rows and other fixed-width text


@dataclass(slots=True)
class Section:
    """A titled table: column names and rows of cells.

    Each row may carry tags (what it can be filtered by, e.g. "alarm" or
    "area-3") and a group (the summary's cards).
    """

    title: str
    columns: list[str]
    rows: list[list[Cell]] = field(default_factory=list)
    note: str | None = None
    tags: list[frozenset[str]] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    # Per row: every attribute of the item, for its details panel
    details: list[list[tuple[str, Cell]]] = field(default_factory=list)
    # Per row: the item's record in the raw blocks (block, offset, length)
    links: list[tuple[str, int, int] | None] = field(default_factory=list)

    @property
    def key(self) -> str:
        """A stable anchor name made of the title."""
        return "".join(c if c.isalnum() else "-" for c in self.title.lower()).strip("-")

    def tags_of(self, index: int) -> frozenset[str]:
        """The tags of a row (none if not given)."""
        return self.tags[index] if index < len(self.tags) else frozenset()

    def group_of(self, index: int) -> str:
        """The group of a row ("" if not given)."""
        return self.groups[index] if index < len(self.groups) else ""

    def details_of(self, index: int) -> list[tuple[str, Cell]]:
        """The details of a row (none if not given)."""
        return self.details[index] if index < len(self.details) else []

    def link_of(self, index: int) -> tuple[str, int, int] | None:
        """The raw record of a row, if it has one."""
        return self.links[index] if index < len(self.links) else None


MASK_MARK = "…"
WITHHELD_KEYS = ("withheld", "error")


def cell(value: Any) -> Cell:
    """A report value as a cell, its state recognised."""
    if value is None:
        return Cell("—", State.MISSING)
    if isinstance(value, Mapping):
        if value.get("error") == "self-check":
            return Cell("[withheld: self-check]", State.WITHHELD)
        if "withheld" in value:
            return Cell(f"[withheld: {value['withheld']}]", State.WITHHELD)
        return Cell(", ".join(f"{k}: {_text(v)}" for k, v in value.items()))
    if isinstance(value, bool):
        return Cell("yes" if value else "no")
    if isinstance(value, list):
        return Cell(", ".join(_text(v) for v in value) or "—")
    text = str(value)
    if text.endswith(MASK_MARK) or text.startswith(MASK_MARK) or "(hidden)" in text:
        return Cell(text, State.MASKED)
    if text.startswith("xx:"):
        return Cell(text, State.MASKED)
    return Cell(text)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "[" + ", ".join(_text(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {_text(v)}" for k, v in value.items()) + "}"
    return "—" if value is None else str(value)


def _withheld(value: Any) -> str | None:
    """Why a section was left out, or None."""
    if isinstance(value, Mapping):
        if value.get("error") == "self-check":
            return "self-check"
        if "withheld" in value:
            return str(value["withheld"])
    return None


def _pairs(
    title: str, items: Iterable[tuple[Any, ...]], note: str | None = None
) -> Section:
    """Name and value rows; a third item is the row's group."""
    section = Section(title, ["", ""], note=note)
    for name, value, *group in items:
        section.rows.append([Cell(name), cell(value)])
        section.groups.append(group[0] if group else "")
    return section


# ----------------------------------------------------------------- sections


def summary(report: Mapping[str, Any], kind: Kind) -> Section:
    """Who, what and how: the first thing shown."""
    panel = report.get("panel") or {}
    board = report.get("board") or {}
    status = report.get("status") or {}
    cloud = status.get("cloud") if isinstance(status, Mapping) else None
    reads = (report.get("transport") or {}).get("report_reads") or {}
    reply = board.get("reply")
    board_text: Any = board.get("reply_note") if reply is None else "reply received"
    reads_text = (
        f"{reads.get('requests')} requests, {reads.get('seconds')} s" if reads else None
    )
    return _pairs(
        "Summary",
        [
            ("Panel", f"{panel.get('profile') or panel.get('model') or '?'}", "Panel"),
            ("Firmware", panel.get("firmware"), "Panel"),
            ("Layout", panel.get("family"), "Panel"),
            ("Layout verified on hardware", panel.get("layout_verified"), "Panel"),
            ("Fallback profile", panel.get("fallback"), "Panel"),
            ("Experimental", panel.get("experimental"), "Panel"),
            ("Serial number", panel.get("serial"), "Panel"),
            ("User code", panel.get("user_code"), "Panel"),
            ("Board", f"{board.get('type') or '?'}, port {board.get('port')}", "Board"),
            ("Board address", board.get("host"), "Board"),
            ("Board reply", board_text, "Board"),
            *_cloud_rows(cloud),
            ("File", str(kind), "Report"),
            ("Report", report.get("report_id"), "Report"),
            ("Level", report.get("level"), "Report"),
            ("Created", report.get("created"), "Report"),
            ("Integration", report.get("integration_version"), "Report"),
            ("Report reads", reads_text, "Report"),
        ],
    )


def _cloud_rows(cloud: Any) -> list[tuple[str, Any, str]]:
    """The cloud flag as rows of the summary's Cloud card."""
    if not isinstance(cloud, Mapping):
        return [("Cloud", cloud, "Cloud")]
    rows = [
        ("Configured", cloud.get("configured"), "Cloud"),
        (
            "Flag bits",
            f"bit 0: {_text(cloud.get('bit_0'))}, bit 1: {_text(cloud.get('bit_1'))}",
            "Cloud",
        )
        if "bit_0" in cloud
        else None,
        ("Raw byte", cloud.get("raw"), "Cloud") if "raw" in cloud else None,
        ("Other bits", cloud.get("other_bits"), "Cloud")
        if cloud.get("other_bits")
        else None,
        ("Last result", cloud.get("last_result"), "Cloud"),
    ]
    return [row for row in rows if row is not None]


def header(report: Mapping[str, Any]) -> tuple[str, str]:
    """The page's title line and the line under it."""
    panel = report.get("panel") or {}
    board = report.get("board") or {}
    model = panel.get("model") or panel.get("profile") or "?"
    firmware = panel.get("firmware") or "?"
    report_id = str(report.get("report_id") or "")
    short = report_id[:8] + ("…" if len(report_id) > 8 else "")
    second = " · ".join(
        part
        for part in (
            str(board.get("type") or ""),
            f"report {short}" if short else "",
            str(report.get("level") or ""),
            str(report.get("created") or ""),
        )
        if part
    )
    return f"SmartLiving {model} · firmware {firmware}", second


def not_zones(report: Mapping[str, Any]) -> set[int] | None:
    """0-based indexes of the zone numbers the report marks as not zones.

    None when the report does not mark them (made before the marker, or
    without the zone records): counts then take every zone number.
    """
    config = report.get("configuration")
    if _withheld(config) or not isinstance(config, Mapping):
        return None
    zones = config.get("zones")
    if not isinstance(zones, list):
        return None
    marked = [z for z in zones if isinstance(z, Mapping) and "is_zone" in z]
    if not marked:
        return None
    return {
        z["number"] - 1
        for z in marked
        if z.get("is_zone") is False and isinstance(z.get("number"), int)
    }


def _zone_states(report: Mapping[str, Any], states: list[Any]) -> list[Any]:
    """The states of the zone numbers that are zones (all, if not marked)."""
    skip = not_zones(report) or set()
    return [state for index, state in enumerate(states) if index not in skip]


def headline(report: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Four numbers at a glance: (label, value, tone: ok / warn / bad / dim)."""
    status = report.get("status")
    data = status if isinstance(status, Mapping) and not _withheld(status) else {}
    areas = data.get("areas") or {}
    modes = areas.get("modes") or {} if isinstance(areas, Mapping) else {}
    armed = sum(1 for m in modes.values() if str(m) != "disarmed")
    states = data.get("zone_states") or []
    alarm = sum(1 for z in _zone_states(report, states) if str(z) == "alarm")
    faults = data.get("faults") or {}
    active = faults.get("active") if isinstance(faults, Mapping) else None
    cloud = data.get("cloud") if isinstance(data.get("cloud"), Mapping) else None
    configured = (cloud or {}).get("configured")
    return [
        (
            "Areas armed",
            f"{armed} / {len(modes)}" if modes else "—",
            "warn" if armed else "ok",
        ),
        ("Zones in alarm", str(alarm) if states else "—", "bad" if alarm else "ok"),
        (
            "Faults",
            str(len(active)) if isinstance(active, list) else "—",
            "warn" if active else "ok",
        ),
        (
            "Cloud",
            {"yes": "On", "no": "Off"}.get(str(configured), "not read"),
            "dim" if configured is None else "ok",
        ),
    ]


def findings(report: Mapping[str, Any]) -> Section:
    """What the reader should know first: errors, parts left out."""
    section = Section("Findings", ["Where", "What"])
    for error in report.get("errors") or []:
        if isinstance(error, Mapping):
            section.rows.append(
                [Cell(str(error.get("where"))), Cell(str(error.get("error")))]
            )
    for name in ("configuration", "status", "events", "blocks", "panel", "board"):
        reason = _withheld(report.get(name))
        if reason:
            section.rows.append(
                [Cell(name), Cell(f"[withheld: {reason}]", State.WITHHELD)]
            )
    blocks = report.get("blocks")
    for block in blocks if isinstance(blocks, list) else []:
        why = block.get("withheld") or block.get("not_read") or block.get("error")
        if block.get("withheld") == "secret":
            continue  # expected below Full confidential: counted below
        if why:
            state = State.WITHHELD if block.get("withheld") else State.MISSING
            section.rows.append(
                [Cell(f"block {block.get('name')}"), Cell(str(why), state)]
            )
    secret = [
        b.get("name")
        for b in (blocks if isinstance(blocks, list) else [])
        if b.get("withheld") == "secret"
    ]
    if secret:
        section.rows.append(
            [
                Cell(f"{len(secret)} secret blocks"),
                Cell("[withheld: secret] at this level", State.WITHHELD),
            ]
        )
    configuration = report.get("configuration") or {}
    if isinstance(configuration, Mapping) and configuration.get("read_with"):
        section.rows.append(
            [Cell("configuration"), Cell(f"read with {configuration['read_with']}")]
        )
    if not section.rows:
        section.note = "nothing to report"
    return section


def _items(
    title: str,
    items: Any,
    columns: list[tuple[str, str]],
    *,
    details: bool = False,
) -> Section:
    """A list of numbered items as a table; with details, every attribute."""
    reason = _withheld(items)
    if reason:
        return Section(title, [], note=f"[withheld: {reason}]")
    if not isinstance(items, list):
        return Section(title, [], note="not in the report")
    keys = [key for key, _ in columns]
    if details:
        extra = sorted({k for item in items for k in item if k not in keys})
        keys += extra
        columns = columns + [(k, k) for k in extra]
    section = Section(title, [name for _, name in columns])
    for item in items:
        section.rows.append([cell(item.get(key)) for key in keys])
        section.details.append([(str(k), cell(v)) for k, v in item.items()])
    if not items:
        section.note = "none"
    return section


def configuration(report: Mapping[str, Any], *, details: bool = False) -> list[Section]:
    """Areas, zones, scenarios, codes, devices and outputs."""
    config = report.get("configuration")
    reason = _withheld(config)
    if reason or not isinstance(config, Mapping):
        note = f"[withheld: {reason}]" if reason else "not in the report"
        return [Section("Configuration", [], note=note)]
    named = [("number", "#"), ("label", "Name")]
    zones = _items(
        "Zones",
        config.get("zones"),
        named
        + [
            ("zone_type", "Type"),
            ("assigned_areas", "Areas"),
            ("balancing", "Balancing"),
            ("terminal_index", "Terminal"),
        ],
        details=details,
    )
    _add_zone_state(zones, config.get("zones"), report.get("status"))
    _add_zone_marker(zones, config.get("zones"))
    areas = _items(
        "Areas",
        config.get("areas"),
        named + [("exit_time", "Exit"), ("entry_time", "Entry")],
        details=details,
    )
    scenarios = _items(
        "Scenarios", config.get("scenarios"), named + [("areas", "Areas")]
    )
    codes = _items("Codes", config.get("codes"), named)
    model = panel_model(report)
    for section, kind, items in (
        (zones, "zone", config.get("zones")),
        (areas, "area", config.get("areas")),
        (scenarios, "scenario", config.get("scenarios")),
        (codes, "code", config.get("codes")),
    ):
        section.links = [record_link(kind, item, model) for item in items or []]
    sections = [areas, zones, scenarios, codes]
    devices = Section("Devices", ["Kind", "Names"])
    enabled = config.get("enabled_devices")
    if isinstance(enabled, Mapping):
        for kind, names in enabled.items():
            devices.rows.append([Cell(f"{kind} enabled"), cell(names)])
    for kind in ("keypads", "readers", "expanders", "keys", "timers", "sirens"):
        labels = [
            item.get("label") for item in config.get(kind) or [] if item.get("label")
        ]
        devices.rows.append([Cell(f"{kind} named"), cell(labels)])
    sections.append(devices)
    outputs = _items(
        "Outputs",
        config.get("outputs"),
        named + [("kind", "Kind"), ("home_automation", "Home automation")],
        details=details,
    )
    outputs.links = [
        record_link("output", item, model) for item in config.get("outputs") or []
    ]
    sections.append(outputs)
    return sections


def record_link(
    kind: str, item: Mapping[str, Any], model: Any
) -> tuple[str, int, int] | None:
    """Where an item's record is in the raw blocks: (block, offset, length).

    The record sizes are the panel model's, as the integration's decoders
    use them; None when the model or the item's index is not known.
    """
    if model is None or not isinstance(item, Mapping):
        return None
    if kind == "output":
        if isinstance(item.get("fixed_output"), int):
            size = loader.module("protocol.config").FIXED_OUTPUT_RECORD_LENGTH
            return "fixed_output_records", (item["fixed_output"] - 1) * size, size
        if isinstance(item.get("terminal_index"), int):
            size = model.terminal_record_length
            return "terminal_records", item["terminal_index"] * size, size
        return None
    sizes = {
        "zone": ("zone_records", model.zone_record_length),
        "area": ("area_records", model.area_record_length),
        "scenario": ("scenario_records", model.scenario_record_length),
        "code": ("code_records", model.code_record_length),
    }
    if kind not in sizes or not isinstance(item.get("number"), int):
        return None
    block, size = sizes[kind]
    return block, (item["number"] - 1) * size, size


def _add_zone_marker(section: Section, zones: Any) -> None:
    """Whether each zone number is a zone on the panel, where the report
    says so: a column and the "not-zone" tag."""
    if not isinstance(zones, list) or not any(
        isinstance(zone, Mapping) and "is_zone" in zone for zone in zones
    ):
        return
    section.columns.append("Zone")
    tags = list(section.tags) + [frozenset()] * (len(section.rows) - len(section.tags))
    for index, (row, zone) in enumerate(zip(section.rows, zones, strict=False)):
        value = zone.get("is_zone") if isinstance(zone, Mapping) else None
        if value is False:
            row.append(Cell("no", badge="not-zone"))
            tags[index] = tags[index] | {"not-zone"}
        else:
            row.append(cell(value if value is None else "yes"))
    section.tags = tags


def _add_zone_state(section: Section, zones: Any, data: Any) -> None:
    """The zones' live state from the status, as columns and filter tags."""
    if not isinstance(zones, list) or not isinstance(data, Mapping) or _withheld(data):
        return
    states = data.get("zone_states") or []
    inclusion = data.get("zone_inclusion") or {}
    included = inclusion.get("included") or [] if isinstance(inclusion, Mapping) else []
    memory = data.get("zone_memory") or {}
    alarm_memory = (
        memory.get("alarm_memory") or [] if isinstance(memory, Mapping) else []
    )
    not_ready: set[int] = set()
    for zone_list in (data.get("not_ready_by_scenario") or {}).values():
        not_ready.update(z for z in zone_list or [] if isinstance(z, int))
    section.columns += ["State", "Bypassed", "Not ready", "Alarm memory"]
    for row, zone in zip(section.rows, zones, strict=False):
        index = int(zone.get("number", 0)) - 1
        state = states[index] if 0 <= index < len(states) else None
        bypassed = not included[index] if 0 <= index < len(included) else None
        memory_on = alarm_memory[index] if 0 <= index < len(alarm_memory) else None
        tags = set()
        if state in ("alarm", "tamper", "short"):
            tags.add(str(state))
        if bypassed:
            tags.add("bypassed")
        if index in not_ready:
            tags.add("not-ready")
        if memory_on:
            tags.add("alarm")
        live = [
            Cell(str(state), badge=str(state)) if state else cell(None),
            Cell("yes", badge="bypassed") if bypassed else cell(bypassed),
            Cell("yes", badge="not-ready") if index in not_ready else Cell("no"),
            Cell("yes", badge="alarm") if memory_on else cell(memory_on),
        ]
        row += live
        if index < len(section.details):
            section.details[index] = (
                list(
                    zip(
                        ["state", "bypassed", "not ready", "alarm memory"],
                        live,
                        strict=True,
                    )
                )
                + section.details[index]
            )
        section.tags.append(frozenset(tags))


def areas_now(report: Mapping[str, Any]) -> Section:
    """Each area's mode and alarms when the report was made."""
    data = report.get("status")
    section = Section("Areas now", ["#", "Mode", "Alarm", "Tamper", "Alarm memory"])
    areas = data.get("areas") if isinstance(data, Mapping) else None
    if _withheld(data) or not isinstance(areas, Mapping):
        section.note = "not in the report"
        return section
    for area, mode in (areas.get("modes") or {}).items():
        number = int(area)
        flags = [
            number in (areas.get(key) or [])
            for key in ("alarm", "tamper", "alarm_memory")
        ]
        section.rows.append(
            [Cell(str(area)), Cell(str(mode), badge=str(mode))]
            + [Cell("yes", badge="alarm") if on else Cell("no") for on in flags]
        )
        section.tags.append(
            frozenset({str(mode)} | ({"alarm"} if any(flags) else set()))
        )
    return section


def status(report: Mapping[str, Any]) -> Section:
    """The panel's state when the report was made."""
    data = report.get("status")
    reason = _withheld(data)
    if reason or not isinstance(data, Mapping):
        return Section(
            "Status",
            [],
            note=f"[withheld: {reason}]" if reason else "not in the report",
        )
    rows: list[tuple[str, Any]] = []
    areas = data.get("areas") or {}
    if isinstance(areas, Mapping):
        modes = areas.get("modes") or {}
        rows.append(("Area modes", [f"{a}: {m}" for a, m in modes.items()]))
        for key in ("alarm", "tamper", "alarm_memory", "tamper_memory"):
            rows.append((f"Areas in {key.replace('_', ' ')}", areas.get(key)))
    states = data.get("zone_states")
    if isinstance(states, list):
        counts: dict[str, int] = {}
        for state in _zone_states(report, states):
            counts[str(state)] = counts.get(str(state), 0) + 1
        title = "Zone states"
        if skipped := len([i for i in not_zones(report) or () if 0 <= i < len(states)]):
            title += f" ({skipped} not zones left out)"
        rows.append((title, [f"{k}: {v}" for k, v in sorted(counts.items())]))
    rows += [
        ("Faults", data.get("faults")),
        ("Panel state", data.get("panel_state")),
        ("Active scenario", data.get("active_scenario")),
        ("Cloud", data.get("cloud")),
    ]
    not_ready = data.get("not_ready_by_scenario")
    if isinstance(not_ready, Mapping):
        rows.append(
            (
                "Zones not ready, by scenario",
                [f"{s}: {_text(z)}" for s, z in not_ready.items() if z],
            )
        )
    return _pairs("Status", rows)


def events(report: Mapping[str, Any], limit: int | None = 50) -> Section:
    """The event log, newest first."""
    data = report.get("events")
    reason = _withheld(data)
    if reason or not isinstance(data, Mapping):
        return Section(
            "Events",
            [],
            note=f"[withheld: {reason}]" if reason else "not in the report",
        )
    ring = data.get("ring_size") or 0
    head = data.get("head")
    records = list(data.get("records") or [])
    if ring and head is not None:
        records.sort(key=lambda r: (head - 1 - r.get("index", 0)) % ring)
    shown = records if limit is None else records[:limit]
    section = Section(
        "Events",
        ["#", "Time", "Code", "On", "Agent", "Location", "Areas"],
        note=f"{len(shown)} of {len(records)} events, newest first",
    )
    for record in shown:
        decoded = record.get("decoded") or {}
        if "error" in decoded:
            section.tags.append(frozenset())
            section.rows.append(
                [cell(record.get("index")), Cell(f"cannot decode {record.get('raw')}")]
                + [Cell("")] * 5
            )
            continue
        areas = decoded.get("areas") or []
        section.tags.append(
            frozenset({f"code-{decoded.get('code')}"} | {f"area-{a}" for a in areas})
        )
        section.rows.append(
            [
                cell(record.get("index")),
                cell(decoded.get("timestamp")),
                cell(decoded.get("code")),
                cell(decoded.get("activation")),
                cell(decoded.get("agent")),
                cell(decoded.get("location")),
                cell(decoded.get("areas")),
            ]
        )
    return section


def transport(report: Mapping[str, Any]) -> list[Section]:
    """Connection statistics and the last exchanges."""
    data = report.get("transport")
    reason = _withheld(data)
    if reason or not isinstance(data, Mapping):
        note = f"[withheld: {reason}]" if reason else "not in the report"
        return [Section("Connection", [], note=note)]
    stats = data.get("stats") or {}
    sections = [
        _pairs(
            "Connection",
            [
                ("Counters", stats.get("counters")),
                ("Command results", stats.get("command_results")),
                ("Round trip", stats.get("round_trip")),
                ("Connect", stats.get("connect")),
                ("Last errors", [_text(e) for e in stats.get("last_errors") or []]),
            ],
        )
    ]
    trace = Section("Last exchanges", ["Request", "Reply", "ms", "Error"])
    for entry in data.get("trace") or []:
        trace.rows.append(
            [
                cell(entry.get("request")),
                cell(entry.get("reply")),
                cell(entry.get("ms")),
                cell(entry.get("error")),
            ]
        )
    sections.append(trace)
    return sections


def integration(report: Mapping[str, Any]) -> Section:
    """The integration's entry: options and entities."""
    data = report.get("integration")
    reason = _withheld(data)
    if reason or not isinstance(data, Mapping):
        return Section(
            "Integration",
            [],
            note=f"[withheld: {reason}]" if reason else "not in the report",
        )
    entities = data.get("entities") or []
    enabled = sum(1 for e in entities if e.get("enabled"))
    return _pairs(
        "Integration",
        [
            ("Entry", data.get("title")),
            ("Entry version", data.get("entry_version")),
            ("Options", data.get("options")),
            ("Entities", f"{len(entities)} ({enabled} enabled)"),
        ],
    )


def report_view(
    report: Mapping[str, Any],
    kind: Kind,
    *,
    details: bool = False,
    events_limit: int | None = 50,
) -> list[Section]:
    """Every section of `show`, in order."""
    sections = [
        summary(report, kind),
        findings(report),
        *configuration(report, details=details),
        areas_now(report),
        status(report),
        events(report, events_limit),
        integration(report),
        *transport(report),
    ]
    return sections


@dataclass(slots=True)
class Page:
    """Everything the HTML page shows."""

    title: str
    subtitle: str
    findings: int  # how many findings
    headline: list[tuple[str, str, str]]
    sections: list[Section]
    blocks: list[RawBlock]


def page(
    report: Mapping[str, Any],
    kind: Kind,
    *,
    details: bool = False,
    events_limit: int | None = 50,
    note: str | None = None,
) -> Page:
    """The report as one page: header, numbers, sections and raw blocks."""
    sections = report_view(report, kind, details=details, events_limit=events_limit)
    if note:
        sections[0].note = note
    title, subtitle = header(report)
    found = next((s for s in sections if s.title == "Findings"), None)
    return Page(
        title=title,
        subtitle=subtitle,
        findings=len(found.rows) if found else 0,
        headline=headline(report),
        sections=sections,
        blocks=raw_blocks(report),
    )
