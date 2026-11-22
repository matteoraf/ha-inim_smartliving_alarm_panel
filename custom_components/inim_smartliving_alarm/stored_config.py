"""The panel configuration stored in the config entry.

The entry keeps the panel's configuration blocks as read, one hex string
per block name (the names of PanelConfiguration and of the system report).
Nothing derived is stored: names, zones, scenarios and outputs are decoded
from the blocks when they are needed, with the layout of the stored model
string. This module is the only one that knows the stored shape. No Home
Assistant imports.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .api import PanelConfiguration
from .protocol.config import (
    FIXED_OUTPUT_RECORD_LENGTH,
    AreaConfig,
    OutputConfig,
    Peripherals,
    PhysicalMap,
    Terminal,
    TerminalType,
    ZoneConfig,
    decode_area_records,
    decode_fixed_outputs,
    decode_home_automation,
    decode_labels,
    decode_output_mask,
    decode_peripherals,
    decode_physical_map,
    decode_terminals,
    decode_zone_records,
    is_zone_terminal,
    refuses_arming_when_not_ready,
    scenario_record_modes,
)
from .protocol.errors import ProtocolError
from .protocol.model import (
    FIXED_OUTPUTS,
    GENERAL_OPTIONS_READ_LENGTH,
    LABEL_LENGTH,
    PanelModel,
    model_for_or_fallback,
    parse_model_string,
)

MODEL_STRING = "model_string"
AREA_LABELS = "area_labels"
ZONE_LABELS = "zone_labels"
SCENARIO_LABELS = "scenario_labels"
KEYPAD_LABELS = "keypad_labels"
CODE_LABELS = "code_labels"
KEY_LABELS = "key_labels"
READER_LABELS = "reader_labels"
EXPANDER_LABELS = "expander_labels"
TIMER_LABELS = "timer_labels"
SIREN_LABELS = "siren_labels"
ZONE_RECORDS = "zone_records"
TERMINAL_RECORDS = "terminal_records"
SCENARIO_RECORDS = "scenario_records"
AREA_RECORDS = "area_records"
GENERAL_OPTIONS = "general_options"
PERIPHERALS = "peripherals"
PHYSICAL_MAP = "physical_map"
FIXED_OUTPUT_RECORDS = "fixed_output_records"
FIXED_OUTPUT_LABELS = "fixed_output_labels"
USER_CODE_OUTPUTS = "user_code_outputs"
EXTRA_OUTPUT_OPTIONS = "extra_output_options"

# Every block a stored configuration holds
BLOCKS = (
    MODEL_STRING,
    AREA_LABELS,
    ZONE_LABELS,
    SCENARIO_LABELS,
    KEYPAD_LABELS,
    CODE_LABELS,
    KEY_LABELS,
    READER_LABELS,
    EXPANDER_LABELS,
    TIMER_LABELS,
    SIREN_LABELS,
    ZONE_RECORDS,
    TERMINAL_RECORDS,
    SCENARIO_RECORDS,
    AREA_RECORDS,
    GENERAL_OPTIONS,
    PERIPHERALS,
    PHYSICAL_MAP,
    FIXED_OUTPUT_RECORDS,
    FIXED_OUTPUT_LABELS,
    USER_CODE_OUTPUTS,
    EXTRA_OUTPUT_OPTIONS,
)

# Name tables of the objects events refer to: kind -> block
NAME_TABLES = {
    "codes": CODE_LABELS,
    "keys": KEY_LABELS,
    "readers": READER_LABELS,
    "expanders": EXPANDER_LABELS,
    "timers": TIMER_LABELS,
    "sirens": SIREN_LABELS,
}


# ----------------------------------------------------------------- writing


def store(config: PanelConfiguration) -> dict[str, str]:
    """The configuration as stored in the entry: every block, as read."""
    blocks = {
        MODEL_STRING: config.model_string,
        AREA_LABELS: config.area_labels.raw,
        ZONE_LABELS: config.zone_labels.raw,
        SCENARIO_LABELS: config.scenario_labels.raw,
        KEYPAD_LABELS: config.keypad_labels.raw,
        CODE_LABELS: config.code_labels.raw,
        KEY_LABELS: config.key_labels.raw,
        READER_LABELS: config.reader_labels.raw,
        EXPANDER_LABELS: config.expander_labels.raw,
        TIMER_LABELS: config.timer_labels.raw,
        SIREN_LABELS: config.siren_labels.raw,
        ZONE_RECORDS: config.zone_records,
        TERMINAL_RECORDS: config.terminal_records,
        SCENARIO_RECORDS: config.scenario_records,
        AREA_RECORDS: config.area_records_raw,
        GENERAL_OPTIONS: config.general_options_raw,
        PERIPHERALS: config.peripherals_raw,
        PHYSICAL_MAP: config.physical_map_raw,
        FIXED_OUTPUT_RECORDS: config.fixed_output_records,
        FIXED_OUTPUT_LABELS: config.fixed_output_labels.raw,
        USER_CODE_OUTPUTS: config.user_output_mask,
        EXTRA_OUTPUT_OPTIONS: config.extra_output_options,
    }
    return {name: blocks[name].hex() for name in BLOCKS}


def _block_lengths(model: PanelModel) -> dict[str, frozenset[int]]:
    """The lengths each block is read with on a model (as the API reads it)."""

    def labels(count: int) -> frozenset[int]:
        return frozenset({count * LABEL_LENGTH})

    return {
        AREA_LABELS: labels(model.areas),
        ZONE_LABELS: labels(model.zones),
        SCENARIO_LABELS: labels(model.scenarios),
        KEYPAD_LABELS: labels(model.keypads),
        CODE_LABELS: labels(model.codes),
        KEY_LABELS: labels(model.keys),
        READER_LABELS: labels(model.readers),
        EXPANDER_LABELS: labels(model.expanders),
        TIMER_LABELS: labels(model.timers),
        SIREN_LABELS: labels(model.sirens),
        FIXED_OUTPUT_LABELS: labels(FIXED_OUTPUTS),
        ZONE_RECORDS: frozenset({model.zones * model.zone_record_length}),
        TERMINAL_RECORDS: frozenset({model.terminals * model.terminal_record_length}),
        SCENARIO_RECORDS: frozenset({model.scenarios * model.scenario_record_length}),
        AREA_RECORDS: frozenset({model.areas * model.area_record_length}),
        GENERAL_OPTIONS: frozenset({GENERAL_OPTIONS_READ_LENGTH}),
        PERIPHERALS: frozenset({model.peripherals_length}),
        PHYSICAL_MAP: frozenset({model.physical_slots}),
        FIXED_OUTPUT_RECORDS: frozenset({FIXED_OUTPUTS * FIXED_OUTPUT_RECORD_LENGTH}),
        # Empty when the PIN matches no user code
        USER_CODE_OUTPUTS: frozenset({0, model.code_output_mask_length}),
        # Empty where the model's address is not known
        EXTRA_OUTPUT_OPTIONS: frozenset(
            {0}
            if model.extra_output_options is None
            else {model.terminals + FIXED_OUTPUTS}
        ),
    }


def missing_blocks(stored: Mapping[str, Any]) -> set[str]:
    """The blocks a stored configuration lacks (read again at setup).

    A block counts as missing when it is not stored, is not valid hex, or
    (once the stored model string gives the layout) does not have the
    length it is read with; a model string that is not one counts as
    missing too.
    """
    missing = {name for name in BLOCKS if raw_block(stored, name) is None}
    model_string = raw_block(stored, MODEL_STRING)
    if model_string is not None:
        try:
            parse_model_string(model_string)
        except ProtocolError:
            missing.add(MODEL_STRING)
    if (model := stored_model(stored)) is not None:
        for name, lengths in _block_lengths(model).items():
            raw = raw_block(stored, name)
            if raw is not None and len(raw) not in lengths:
                missing.add(name)
    return missing


# --------------------------------------------------------------- migration


def migrate_v1(old: Mapping[str, Any]) -> dict[str, str]:
    """A configuration stored by entry version 1, in the current shape.

    Every block the old shape holds as read is kept under its name; what was
    derived from the blocks is dropped. A configuration already in the
    current shape is returned as it is. Blocks the old shape did not keep
    (the names of codes, keys, readers, expanders, timers and sirens) are
    left out, to be read again from the panel.
    """

    # Already in the current shape: blocks are hex strings (version 1 kept
    # dictionaries under some of the same names, and no model_string)
    if MODEL_STRING in old or any(isinstance(old.get(name), str) for name in BLOCKS):
        return dict(old)

    def raw(*path: str) -> str | None:
        value: Any = old
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        return value if isinstance(value, str) else None

    found = {
        MODEL_STRING: raw("system_info", "raw_hex"),
        AREA_LABELS: raw("areas", "raw_hex"),
        ZONE_LABELS: raw("zones", "raw_hex_full"),
        SCENARIO_LABELS: raw("scenarios", "raw_hex"),
        KEYPAD_LABELS: raw("keyboard_names", "raw_hex"),
        ZONE_RECORDS: raw("zones_config", "raw_hex_part1_full_response"),
        TERMINAL_RECORDS: raw("zones_config", "raw_hex_part2_full_response"),
        SCENARIO_RECORDS: _scenario_records_v1(old.get("scenario_activations")),
        AREA_RECORDS: raw("area_records", "raw_hex"),
        GENERAL_OPTIONS: raw("general_options", "raw_hex"),
        PERIPHERALS: raw("peripherals", "raw_hex"),
        PHYSICAL_MAP: raw("physical_map", "raw_hex"),
        FIXED_OUTPUT_RECORDS: raw("fixed_output_records", "raw_hex"),
        FIXED_OUTPUT_LABELS: raw("fixed_output_labels", "raw_hex"),
        USER_CODE_OUTPUTS: raw("user_code_outputs", "raw_hex"),
        EXTRA_OUTPUT_OPTIONS: raw("extra_output_options", "raw_hex"),
    }
    return {name: value for name, value in found.items() if value is not None}


def _scenario_records_v1(activations: Any) -> str | None:
    """The scenario records block from version 1's per-scenario records.

    Their concatenation in scenario order; None unless every scenario from
    the first on has its record.
    """
    if not isinstance(activations, list) or not activations:
        return None
    records: dict[int, str] = {}
    for activation in activations:
        if not isinstance(activation, Mapping):
            return None
        index, record = (
            activation.get("scenario_index"),
            activation.get("raw_activation_hex"),
        )
        if not isinstance(index, int) or not isinstance(record, str) or not record:
            return None
        records[index] = record
    if sorted(records) != list(range(len(records))):
        return None
    return "".join(records[index] for index in range(len(records)))


# ----------------------------------------------------------------- blocks


def raw_block(stored: Mapping[str, Any], name: str) -> bytes | None:
    """A stored block as bytes; None if it is not stored or not valid hex."""
    value = stored.get(name)
    if not isinstance(value, str):
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


# ------------------------------------------------------------------ model


def stored_layout_fits(stored: Mapping[str, Any], model: PanelModel) -> bool:
    """Whether the stored configuration was read with this model's layout.

    Told by the length of two stored blocks: the logical terminal records
    (L x 12 bytes, which differs between models) and the enabled
    peripherals (which differs between firmware families). A block that is
    not stored does not count against it.
    """
    return all(
        raw is None or len(raw) == expected
        for raw, expected in (
            (
                raw_block(stored, TERMINAL_RECORDS),
                model.terminals * model.terminal_record_length,
            ),
            (raw_block(stored, PERIPHERALS), model.peripherals_length),
        )
    )


def _stored_model_and_fallback(
    stored: Mapping[str, Any],
) -> tuple[PanelModel, bool] | None:
    """(layout, whether it is the fallback) of the stored model string.

    None when the stored blocks were read with another layout.
    """
    raw = raw_block(stored, MODEL_STRING)
    if raw is None:
        return None
    try:
        model, unsupported = model_for_or_fallback(parse_model_string(raw))
    except ProtocolError:
        return None
    if not stored_layout_fits(stored, model):
        return None
    return model, unsupported is not None


def stored_model(stored: Mapping[str, Any]) -> PanelModel | None:
    """The layout the stored configuration was read with.

    The model profile of the stored model string, or the fallback profile
    for a panel without its own (as the panel connection uses); None when
    no valid model string is stored, or the stored blocks were read with
    another layout (e.g. before the panel's firmware had its own profile,
    until they are read again).
    """
    found = _stored_model_and_fallback(stored)
    return found[0] if found else None


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """The panel's firmware version and type, from the stored model string."""

    version: str
    type: str


def system_info(stored: Mapping[str, Any]) -> SystemInfo | None:
    """Firmware version and panel type ("6.07", "01050"); None if not stored."""
    raw = raw_block(stored, MODEL_STRING)
    if raw is None:
        return None
    parts = raw.decode("ascii", errors="ignore").strip().split(" ")
    return SystemInfo(
        version=parts[0] if parts else "Unknown",
        type=parts[1] if len(parts) > 1 else "Unknown",
    )


def device_model(info: SystemInfo | None) -> str:
    """The panel type for the device entry ("SmartLiving" if not known)."""
    return info.type if info is not None else "SmartLiving"


def device_sw_version(info: SystemInfo | None) -> str:
    """The firmware version for the device entry ("Unknown" if not known)."""
    return info.version if info is not None else "Unknown"


# ----------------------------------------------------------------- labels


@lru_cache(maxsize=64)
def _decoded_labels(raw_hex: str, count: int | None) -> tuple[str, ...]:
    """The labels of a stored table, at most count of them."""
    try:
        data = bytes.fromhex(raw_hex)
    except ValueError:
        return ()
    found = len(data) // LABEL_LENGTH
    if count is not None:
        found = min(found, count)
    return tuple(decode_labels(data[: found * LABEL_LENGTH], found))


def _labels(stored: Mapping[str, Any], name: str, count: int | None) -> list[str]:
    value = stored.get(name)
    if not isinstance(value, str):
        return []
    return list(_decoded_labels(value, count))


def area_names(stored: Mapping[str, Any]) -> list[str]:
    """The area labels, one per area of the model; empty if not stored."""
    model = stored_model(stored)
    return _labels(stored, AREA_LABELS, model.areas if model else None)


def zone_names(stored: Mapping[str, Any]) -> list[str]:
    """The zone labels, one per zone number; empty if not stored."""
    model = stored_model(stored)
    return _labels(stored, ZONE_LABELS, model.zones if model else None)


def scenario_names(stored: Mapping[str, Any]) -> list[str]:
    """The scenario labels, one per scenario of the model; empty if not stored."""
    model = stored_model(stored)
    return _labels(stored, SCENARIO_LABELS, model.scenarios if model else None)


def keypad_names(stored: Mapping[str, Any]) -> list[str]:
    """The keypad labels, one per keypad of the model; empty if not stored."""
    return _labels(stored, KEYPAD_LABELS, None)


def name_table(stored: Mapping[str, Any], kind: str) -> list[str]:
    """The labels of codes, keys, readers, expanders, timers or sirens.

    Empty when the table is not stored (until the configuration is read
    again from the panel).
    """
    return _labels(stored, NAME_TABLES[kind], None)


def _label(names: list[str], number: int, kind: str) -> str:
    name = names[number - 1] if 0 < number <= len(names) else ""
    return name or f"{kind} {number}"


def area_label(stored: Mapping[str, Any], number: int) -> str:
    """The panel's label of an area, or "Area N" if it has none."""
    return _label(area_names(stored), number, "Area")


def zone_label(stored: Mapping[str, Any], number: int) -> str:
    """The panel's label of a zone, or "Zone N" if it has none."""
    return _label(zone_names(stored), number, "Zone")


def scenario_label(stored: Mapping[str, Any], number: int) -> str:
    """The panel's label of a scenario, or "Scenario N" if it has none."""
    return _label(scenario_names(stored), number, "Scenario")


def area_count(stored: Mapping[str, Any]) -> int:
    """How many areas the panel has: the model's, else the stored labels'."""
    model = stored_model(stored)
    return model.areas if model else len(area_names(stored))


def scenario_count(stored: Mapping[str, Any]) -> int:
    """How many scenarios the panel has: the model's, else the stored labels'."""
    model = stored_model(stored)
    return model.scenarios if model else len(scenario_names(stored))


# ---------------------------------------------------------------- records


def _decoded[T](stored: Mapping[str, Any], name: str, decode) -> T | None:
    model = stored_model(stored)
    raw = raw_block(stored, name)
    if model is None or raw is None:
        return None
    try:
        return decode(raw, model)
    except ProtocolError:
        return None


def zone_records(stored: Mapping[str, Any]) -> list[ZoneConfig] | None:
    """The stored zone records (2L, by 0-based zone), decoded.

    None when they or the model string are not stored, or do not fit.
    """
    return _decoded(stored, ZONE_RECORDS, decode_zone_records)


def terminal_records(stored: Mapping[str, Any]) -> list[Terminal] | None:
    """The stored logical terminal records (L, by 0-based terminal), decoded.

    None when they or the model string are not stored, or do not fit.
    """
    return _decoded(stored, TERMINAL_RECORDS, decode_terminals)


def area_records(stored: Mapping[str, Any]) -> list[AreaConfig] | None:
    """The stored area records (A, by 0-based area), decoded.

    None when they are not stored or do not fit, and for a panel handled
    with the fallback layout: on another model the fallback's address is not
    the area record, so its times would be wrong.
    """
    found = _stored_model_and_fallback(stored)
    if found is None or found[1]:
        return None
    return _decoded(stored, AREA_RECORDS, decode_area_records)


def physical_map(stored: Mapping[str, Any]) -> PhysicalMap | None:
    """The stored physical map; None if not stored or it does not fit."""
    return _decoded(stored, PHYSICAL_MAP, decode_physical_map)


def peripherals(stored: Mapping[str, Any]) -> Peripherals | None:
    """The enabled keypads, readers, expanders and sirens; None if not stored."""
    return _decoded(stored, PERIPHERALS, decode_peripherals)


def refuses_not_ready(stored: Mapping[str, Any]) -> bool | None:
    """Whether the panel itself refuses arming with zones not ready.

    Decoded from the stored general options; None when they are not stored,
    were read with another layout, or cannot be decoded.
    """
    raw = raw_block(stored, GENERAL_OPTIONS)
    if raw is None or stored_model(stored) is None:
        return None
    try:
        return refuses_arming_when_not_ready(raw)
    except ProtocolError:
        return None


def scenario_area_modes(stored: Mapping[str, Any]) -> dict[int, dict[int, int]]:
    """What each scenario does to each area: number -> area -> mode.

    Decoded from the stored scenario records (see ArmMode); empty when they
    are not stored or do not fit.
    """
    model = stored_model(stored)
    raw = raw_block(stored, SCENARIO_RECORDS)
    if model is None or raw is None:
        return {}
    size = model.scenario_record_length
    if len(raw) != model.scenarios * size:
        return {}
    return {
        index + 1: scenario_record_modes(
            raw[index * size : (index + 1) * size], model.areas
        )
        for index in range(model.scenarios)
    }


def stored_output_mask(
    stored: Mapping[str, Any],
) -> tuple[frozenset[int], frozenset[int]] | None:
    """(terminals 0-based, fixed outputs 1-3) the stored user code may drive.

    None when the mask is not stored; empty sets when the PIN matched no
    user code.
    """
    model = stored_model(stored)
    raw = raw_block(stored, USER_CODE_OUTPUTS)
    if model is None or raw is None:
        return None
    if not raw:
        return frozenset(), frozenset()
    try:
        return decode_output_mask(raw, model)
    except ProtocolError:
        return None


def fixed_outputs(stored: Mapping[str, Any]) -> list[OutputConfig] | None:
    """The programming of O1-O3; None if not stored or unreadable."""
    raw = raw_block(stored, FIXED_OUTPUT_RECORDS)
    if raw is None:
        return None
    try:
        return decode_fixed_outputs(raw)
    except ProtocolError:
        return None


def fixed_output_names(stored: Mapping[str, Any]) -> list[str] | None:
    """The labels of O1-O3; None if not stored or unreadable."""
    raw = raw_block(stored, FIXED_OUTPUT_LABELS)
    if raw is None:
        return None
    try:
        return decode_labels(raw, FIXED_OUTPUTS)
    except ProtocolError:
        return None


def home_automation_flags(stored: Mapping[str, Any]) -> tuple[bool, ...] | None:
    """Which outputs are marked as home automation outputs: terminals, then
    O1-O3. None when not known (not stored, empty or another layout).
    """
    return (
        _decoded(stored, EXTRA_OUTPUT_OPTIONS, decode_home_automation)
        if raw_block(stored, EXTRA_OUTPUT_OPTIONS)
        else None
    )


# ------------------------------------------------------------------ zones


@dataclass(frozen=True, slots=True)
class StoredZone:
    """A zone number of the panel, from the stored configuration.

    Without the zone and terminal records (not stored or another layout)
    only the number is known: the zone is then taken as a zone on the panel,
    with no terminal and no physical slot.
    """

    number: int  # 1-based
    terminal: int | None = None  # 0-based logical terminal
    second_zone: bool = False  # zone L+n: the second zone of terminal n
    terminal_type: TerminalType | int | None = None
    areas: tuple[int, ...] = ()
    slot: int | None = None  # first physical slot of the terminal
    exists: bool = True  # a zone on the panel (by its terminal's type)


def stored_zones(stored: Mapping[str, Any]) -> list[StoredZone]:
    """Every zone number (2L) with its terminal, areas and physical slot.

    Without the zone and terminal records, one bare entry per zone label.
    """
    zones = zone_records(stored)
    terminals = terminal_records(stored)
    if zones is None or terminals is None:
        return [StoredZone(number) for number in range(1, len(zone_names(stored)) + 1)]
    slots = physical_map(stored)
    found = []
    for index, zone in enumerate(zones):
        terminal = index % len(terminals)
        second_zone = index >= len(terminals)
        terminal_type = terminals[terminal].terminal_type
        found.append(
            StoredZone(
                number=index + 1,
                terminal=terminal,
                second_zone=second_zone,
                terminal_type=terminal_type,
                areas=tuple(sorted(zone.areas)),
                slot=slots.slot_of(terminal) if slots is not None else None,
                exists=is_zone_terminal(terminal_type, second_zone),
            )
        )
    return found


def stored_zone(stored: Mapping[str, Any], number: int) -> StoredZone | None:
    """One zone number (1-based); None if there is no such zone number."""
    for zone in stored_zones(stored):
        if zone.number == number:
            return zone
    return None


def zones_to_create(
    stored: Mapping[str, Any], selected: Collection[int]
) -> list[StoredZone]:
    """The selected zone numbers (1-based) that are zones on the panel."""
    return [
        zone for zone in stored_zones(stored) if zone.number in selected and zone.exists
    ]
