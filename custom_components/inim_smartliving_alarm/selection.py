"""Which areas, zones and scenarios of the panel get entities.

The user picks them in the setup and the options: the selection is stored in
the entry options as lists of 1-based numbers. Only items that are on the
panel are offered: every area and scenario of the model, and the zone numbers
whose terminal is programmed as a zone.
"""

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from .attribute_values import time_text
from .const import (
    CONF_AREAS,
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
    CONF_OUTPUTS,
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
    CONF_SCENARIOS,
    CONF_ZONES,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_LIMIT_SCENARIOS,
    DEFAULT_LIMIT_ZONES,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREA_RECORDS,
    KEY_INIT_AREAS,
    KEY_INIT_EXTRA_OUTPUT_OPTIONS,
    KEY_INIT_FIXED_OUTPUT_LABELS,
    KEY_INIT_FIXED_OUTPUT_RECORDS,
    KEY_INIT_PERIPHERALS,
    KEY_INIT_PHYSICAL_MAP,
    KEY_INIT_RAW_HEX,
    KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS,
    KEY_INIT_SCENARIO_ACTIVATION_INDEX,
    KEY_INIT_SCENARIO_ACTIVATION_RAW,
    KEY_INIT_SCENARIO_ACTIVATIONS,
    KEY_INIT_SCENARIO_NAMES,
    KEY_INIT_SCENARIOS,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_TERMINAL_RECORDS_RAW,
    KEY_INIT_USER_CODE_OUTPUTS,
    KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS,
    KEY_INIT_ZONE_CONFIG_INDEX,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONE_RECORDS_RAW,
    KEY_INIT_ZONES,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    MAX_LIMIT_AREAS,
    MAX_LIMIT_ZONES,
)
from .legacy import SCENARIO_ACTIONS, zone_exists
from .protocol.commands import ArmMode
from .protocol.config import (
    AreaConfig,
    OutputConfig,
    Terminal,
    ZoneConfig,
    decode_area_records,
    decode_fixed_outputs,
    decode_home_automation,
    decode_labels,
    decode_output_mask,
    decode_physical_map,
    decode_terminals,
    decode_zone_records,
    fixed_output_number,
    output_address,
    terminal_output_number,
)
from .protocol.errors import ProtocolError
from .protocol.model import (
    FIXED_OUTPUTS,
    PanelModel,
    model_for_or_fallback,
    parse_model_string,
)

SELECTION_KEYS = (CONF_AREAS, CONF_ZONES, CONF_SCENARIOS, CONF_OUTPUTS)

# Alarm panel modes mapped to a scenario (option key -> stored 0-based index)
SCENARIO_MAPPING_KEYS = (
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
)

# Unique ID suffixes (after the entry ID) of the entities of one item, and
# whether the number in them is 0-based
_ITEM_UNIQUE_IDS: dict[str, tuple[tuple[re.Pattern[str], bool], ...]] = {
    CONF_AREAS: (
        (re.compile(r"_area_(?:switch_|mode_select_)?(\d+)(?:_[a-z_]+)?"), False),
    ),
    CONF_ZONES: (
        (re.compile(r"_zone(?:_triggered|_tamper|_enabled_switch)?_(\d+)"), False),
    ),
    CONF_SCENARIOS: (
        (re.compile(r"_scenario_button_(\d+)"), True),
        (re.compile(r"_scenario_(\d+)_active_bs"), True),
    ),
    # Terminal outputs by 1-based terminal = output number; fixed outputs
    # are handled in item_of_unique_id (their number depends on the model)
    CONF_OUTPUTS: ((re.compile(r"_terminal_output_(\d+)"), False),),
}
_FIXED_OUTPUT_UNIQUE_ID = re.compile(r"_fixed_output_(\d)")


# ------------------------------------------------------------ panel config


def panel_config(entry_data: Mapping[str, Any]) -> Mapping[str, Any]:
    """The panel configuration stored in an entry's data."""
    return entry_data.get(DATA_INITIAL_PANEL_CONFIG) or {}


def area_names(config: Mapping[str, Any]) -> list[str]:
    """The area labels, one per area of the model."""
    return (config.get(KEY_INIT_AREAS) or {}).get(KEY_INIT_AREA_NAMES) or []


def zone_names(config: Mapping[str, Any]) -> list[str]:
    """The zone labels, one per zone number."""
    return (config.get(KEY_INIT_ZONES) or {}).get(KEY_INIT_ZONE_NAMES) or []


def scenario_names(config: Mapping[str, Any]) -> list[str]:
    """The scenario labels, one per scenario of the model."""
    return (config.get(KEY_INIT_SCENARIOS) or {}).get(KEY_INIT_SCENARIO_NAMES) or []


def zone_details(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored setup of every zone number."""
    return (config.get(KEY_INIT_ZONES_CONFIG) or {}).get(
        KEY_INIT_ZONES_CONFIG_DETAILED
    ) or []


def _label(names: list[str], number: int, kind: str) -> str:
    name = names[number - 1] if number <= len(names) else ""
    return name or f"{kind} {number}"


def area_label(config: Mapping[str, Any], number: int) -> str:
    """The panel's label of an area, or "Area N" if it has none."""
    return _label(area_names(config), number, "Area")


def zone_label(config: Mapping[str, Any], number: int) -> str:
    """The panel's label of a zone, or "Zone N" if it has none."""
    return _label(zone_names(config), number, "Zone")


def scenario_label(config: Mapping[str, Any], number: int) -> str:
    """The panel's label of a scenario, or "Scenario N" if it has none."""
    return _label(scenario_names(config), number, "Scenario")


# ------------------------------------------------------------- what is offered


def available_areas(config: Mapping[str, Any]) -> list[int]:
    """Every area of the model."""
    return list(range(1, len(area_names(config)) + 1))


def available_zones(config: Mapping[str, Any]) -> list[int]:
    """The zone numbers that are zones on the panel.

    Without stored zone details (a configuration from an early version)
    every zone number is offered, as such entries always worked.
    """
    details = zone_details(config)
    if not details:
        return list(range(1, len(zone_names(config)) + 1))
    return sorted(
        detail[KEY_INIT_ZONE_CONFIG_INDEX] + 1
        for detail in details
        if KEY_INIT_ZONE_CONFIG_INDEX in detail and zone_exists(detail)
    )


def available_scenarios(config: Mapping[str, Any]) -> list[int]:
    """Every scenario of the model."""
    return list(range(1, len(scenario_names(config)) + 1))


def available_outputs(config: Mapping[str, Any]) -> list[int]:
    """The outputs that can be switched with the user code: output numbers.

    Programmed as a switched output, in a physical slot the output command
    can address, and in the user code's output mask. None without the
    stored mask (a configuration from an earlier version).
    """
    allowed = stored_output_mask(config)
    if allowed is None:
        return []
    terminals, fixed = allowed
    return [
        item.number
        for item in output_items(config)
        if item.address is not None
        and (item.terminal in terminals or item.fixed in fixed)
    ]


AVAILABLE = {
    CONF_AREAS: available_areas,
    CONF_ZONES: available_zones,
    CONF_SCENARIOS: available_scenarios,
    CONF_OUTPUTS: available_outputs,
}


# ------------------------------------------------------------- the selection


def selected(
    options: Mapping[str, Any], config: Mapping[str, Any], key: str
) -> list[int]:
    """The selected items of one kind that are on the panel, in order.

    An entry without a stored selection (it should not happen once the
    entry is migrated) gets every item, except outputs: none of them until
    the user ticks them.
    """
    available = AVAILABLE[key](config)
    stored = options.get(key)
    if stored is None:
        return [] if key == CONF_OUTPUTS else available
    chosen = {int(number) for number in stored}
    return [number for number in available if number in chosen]


def suggested_selection(config: Mapping[str, Any]) -> dict[str, list[int]]:
    """What a new setup proposes: what the panel shows in use.

    Zones that belong to at least one area, the areas those zones belong to,
    and the scenarios that change at least one area.
    """
    zones: list[int] = []
    areas: set[int] = set()
    for detail in zone_details(config):
        if KEY_INIT_ZONE_CONFIG_INDEX not in detail or not zone_exists(detail):
            continue
        assigned = detail.get(KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS) or []
        if assigned:
            zones.append(detail[KEY_INIT_ZONE_CONFIG_INDEX] + 1)
            areas.update(int(area) for area in assigned)
    scenarios = [
        activation[KEY_INIT_SCENARIO_ACTIVATION_INDEX] + 1
        for activation in config.get(KEY_INIT_SCENARIO_ACTIVATIONS) or []
        if any(
            action != "keep"
            for action in (
                activation.get(KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS) or {}
            ).values()
        )
    ]
    return {
        CONF_AREAS: [area for area in available_areas(config) if area in areas],
        CONF_ZONES: sorted(zones),
        CONF_SCENARIOS: [
            number for number in available_scenarios(config) if number in scenarios
        ],
        # The panel cannot tell what an output drives (a siren, a lock, ...):
        # only those the installer marked as home automation are proposed
        CONF_OUTPUTS: home_automation_outputs(config),
    }


def mapped_scenarios(options: Mapping[str, Any]) -> dict[str, int]:
    """Alarm panel modes mapped to a scenario: option key -> 1-based number."""
    mapped = {}
    for key in SCENARIO_MAPPING_KEYS:
        value = options.get(key)
        if value is not None and str(value).isdigit():
            mapped[key] = int(value) + 1
    return mapped


def stored_layout_fits(config: Mapping[str, Any], model: PanelModel) -> bool:
    """Whether the stored configuration was read with this model's layout.

    Told by the length of two stored raw blocks: the logical terminal
    records (L x 12 bytes, which differs between models) and the enabled
    peripherals (which differs between firmware families). A block that is
    not stored does not count against it.
    """
    terminals = (config.get(KEY_INIT_ZONES_CONFIG) or {}).get(
        KEY_INIT_TERMINAL_RECORDS_RAW
    )
    peripherals = (config.get(KEY_INIT_PERIPHERALS) or {}).get(KEY_INIT_RAW_HEX)
    return all(
        not isinstance(raw, str) or len(raw) // 2 == expected
        for raw, expected in (
            (terminals, model.terminals * model.terminal_record_length),
            (peripherals, model.peripherals_length),
        )
    )


def _stored_model_and_fallback(
    config: Mapping[str, Any],
) -> tuple[PanelModel, bool] | None:
    """(layout, whether it is the fallback) of the stored model string.

    None when the stored blocks were read with another layout.
    """
    raw = (config.get(KEY_INIT_SYSTEM_INFO) or {}).get(KEY_INIT_RAW_HEX)
    try:
        model, unsupported = model_for_or_fallback(
            parse_model_string(bytes.fromhex(raw))
        )
    except (TypeError, ValueError, ProtocolError):
        return None
    if not stored_layout_fits(config, model):
        return None
    return model, unsupported is not None


def stored_model(config: Mapping[str, Any]) -> PanelModel | None:
    """The layout the stored configuration was read with.

    The model profile of the stored model string, or the fallback profile
    for a panel without its own (as the panel connection uses); None when
    no valid model string is stored, or the stored blocks were read with
    another layout (e.g. before the panel's firmware had its own profile,
    until they are read again).
    """
    found = _stored_model_and_fallback(config)
    return found[0] if found else None


def area_records(config: Mapping[str, Any]) -> list[AreaConfig] | None:
    """The stored area records (A, by 0-based area), decoded.

    None when they are not stored or do not fit, and for a panel handled
    with the fallback layout: on another model the fallback's address is not
    the area record, so its times would be wrong.
    """
    found = _stored_model_and_fallback(config)
    raw = (config.get(KEY_INIT_AREA_RECORDS) or {}).get(KEY_INIT_RAW_HEX)
    if found is None or found[1]:
        return None
    try:
        return decode_area_records(bytes.fromhex(raw), found[0])
    except (TypeError, ValueError, ProtocolError):
        return None


def _stored_records[T](
    config: Mapping[str, Any],
    key: str,
    decode: Callable[[bytes, PanelModel], list[T]],
) -> list[T] | None:
    model = stored_model(config)
    raw = (config.get(KEY_INIT_ZONES_CONFIG) or {}).get(key)
    if model is None:
        return None
    try:
        return decode(bytes.fromhex(raw), model)
    except (TypeError, ValueError, ProtocolError):
        return None


def zone_records(config: Mapping[str, Any]) -> list[ZoneConfig] | None:
    """The stored zone records (2L, by 0-based zone), decoded.

    None when they or the model string are not stored, or do not fit.
    """
    return _stored_records(config, KEY_INIT_ZONE_RECORDS_RAW, decode_zone_records)


def terminal_records(config: Mapping[str, Any]) -> list[Terminal] | None:
    """The stored logical terminal records (L, by 0-based terminal), decoded.

    None when they or the model string are not stored, or do not fit.
    """
    return _stored_records(config, KEY_INIT_TERMINAL_RECORDS_RAW, decode_terminals)


# ----------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True)
class OutputItem:
    """An output of the panel, from the stored configuration."""

    number: int  # the panel's output numbering: terminals 1-L, then O1-O3
    terminal: int | None  # logical terminal (0-based) of a terminal output
    fixed: int | None  # 1-3 for O1-O3
    name: str  # the panel's label; empty if it has none
    config: OutputConfig
    address: int | None  # the output command's OUTPUT byte; None: cannot
    # Marked as a home automation output by the installer; None: not known
    home_automation: bool | None = None

    @property
    def unique_suffix(self) -> str:
        """The end of the unique ID of the output's entity."""
        if self.fixed is not None:
            return f"fixed_output_{self.fixed}"
        return f"terminal_output_{self.number}"


def _raw(config: Mapping[str, Any], key: str) -> bytes | None:
    try:
        return bytes.fromhex((config.get(key) or {})[KEY_INIT_RAW_HEX])
    except (KeyError, TypeError, ValueError):
        return None


def output_items(config: Mapping[str, Any]) -> list[OutputItem]:
    """Every switched output programmed on the panel, terminals then O1-O3.

    Empty when the stored configuration lacks the outputs' data or was read
    with another layout.
    """
    model = stored_model(config)
    terminals = terminal_records(config)
    physical = _raw(config, KEY_INIT_PHYSICAL_MAP)
    fixed_raw = _raw(config, KEY_INIT_FIXED_OUTPUT_RECORDS)
    labels_raw = _raw(config, KEY_INIT_FIXED_OUTPUT_LABELS)
    if None in (model, terminals, physical, fixed_raw, labels_raw):
        return []
    try:
        physical_map = decode_physical_map(physical, model)
        fixed_configs = decode_fixed_outputs(fixed_raw)
        fixed_names = decode_labels(labels_raw, FIXED_OUTPUTS)
    except ProtocolError:
        return []
    flags: tuple[bool, ...] | None = None
    if extra := _raw(config, KEY_INIT_EXTRA_OUTPUT_OPTIONS):  # empty: unknown
        try:
            flags = decode_home_automation(extra, model)
        except ProtocolError:
            flags = None

    def home_automation(index: int) -> bool | None:
        return None if flags is None else flags[index]

    def address(number: int) -> int | None:
        try:
            return output_address(number, physical_map, model)
        except ValueError:
            return None

    names = zone_names(config)
    items = []
    for index, terminal in enumerate(terminals):
        output = terminal.output
        if output is None:
            continue
        number = terminal_output_number(index, model)
        items.append(
            OutputItem(
                number=number,
                terminal=index,
                fixed=None,
                name=names[index] if index < len(names) else "",
                config=output,
                address=address(number),
                home_automation=home_automation(index),
            )
        )
    for fixed, output in enumerate(fixed_configs, start=1):
        number = fixed_output_number(fixed, model)
        items.append(
            OutputItem(
                number=number,
                terminal=None,
                fixed=fixed,
                name=fixed_names[fixed - 1],
                config=output,
                address=address(number),
                home_automation=home_automation(model.terminals + fixed - 1),
            )
        )
    return items


def stored_output_mask(
    config: Mapping[str, Any],
) -> tuple[frozenset[int], frozenset[int]] | None:
    """(terminals 0-based, fixed outputs 1-3) the stored user code may drive.

    None when the mask is not stored; empty sets when the PIN matched no
    user code.
    """
    model = stored_model(config)
    raw = _raw(config, KEY_INIT_USER_CODE_OUTPUTS)
    if model is None or raw is None:
        return None
    if not raw:
        return frozenset(), frozenset()
    try:
        return decode_output_mask(raw, model)
    except ProtocolError:
        return None


def imported_outputs(
    options: Mapping[str, Any], config: Mapping[str, Any]
) -> list[OutputItem]:
    """The outputs the user imported."""
    chosen = set(selected(options, config, CONF_OUTPUTS))
    return [item for item in output_items(config) if item.number in chosen]


def home_automation_outputs(config: Mapping[str, Any]) -> list[int]:
    """The offered outputs the installer marked as home automation outputs."""
    offered = set(available_outputs(config))
    return [
        item.number
        for item in output_items(config)
        if item.home_automation and item.number in offered
    ]


def outputs_shown(options: Mapping[str, Any], config: Mapping[str, Any]) -> list[int]:
    """The outputs ticked in the options form.

    What the user saved; the first time (no outputs saved yet), the ones
    the installer marked as home automation outputs.
    """
    if CONF_OUTPUTS not in options:
        return home_automation_outputs(config)
    return selected(options, config, CONF_OUTPUTS)


def output_label(config: Mapping[str, Any], number: int) -> str:
    """How an output is listed: "O2 - Garage (monostable 30 s)"."""
    for item in output_items(config):
        if item.number == number:
            break
    else:
        return f"Output {number}"
    title = f"O{item.fixed}" if item.fixed is not None else str(item.number)
    name = f"{title} - {item.name}" if item.name else title
    detail = item.config.kind.value.replace("_", "/")
    if (time := item.config.monostable_time) is not None:
        detail += f" {time_text(time)}"
    if item.home_automation:
        detail += ", home automation"
    return f"{name} ({detail})"


def zone_option_flags(config: Mapping[str, Any]) -> dict[int, dict[str, bool]]:
    """Each zone's programming options: 1-based zone -> name -> on.

    Empty when the stored zone records cannot be decoded.
    """
    zones = zone_records(config) or []
    return {number: zone.flags for number, zone in enumerate(zones, start=1)}


def scenario_area_modes(config: Mapping[str, Any]) -> dict[int, dict[int, int]]:
    """What each scenario does to each area: number -> area -> mode.

    Decoded from the stored scenario records (one nibble per area, area 1
    in the low nibble of the first byte; see ArmMode).
    """
    areas = len(area_names(config))
    modes: dict[int, dict[int, int]] = {}
    for activation in config.get(KEY_INIT_SCENARIO_ACTIVATIONS) or []:
        raw = activation.get(KEY_INIT_SCENARIO_ACTIVATION_RAW)
        if KEY_INIT_SCENARIO_ACTIVATION_INDEX not in activation or not raw:
            continue
        record = bytes.fromhex(raw)
        modes[activation[KEY_INIT_SCENARIO_ACTIVATION_INDEX] + 1] = {
            area: record[(area - 1) // 2] >> (4 * ((area - 1) % 2)) & 0x0F
            for area in range(1, areas + 1)
            if (area - 1) // 2 < len(record)
        }
    return modes


_ARMED = frozenset((ArmMode.AWAY, ArmMode.STAY, ArmMode.INSTANT))


def scenario_actions(config: Mapping[str, Any], number: int) -> dict[str, str]:
    """What a scenario does to each area: "keep", "total", "partial", ...

    Keyed by area number as text; empty if its record is not stored.
    """
    return {
        str(area): SCENARIO_ACTIONS.get(mode, "unknown")
        for area, mode in scenario_area_modes(config).get(number, {}).items()
    }


def scenario_armed_areas(config: Mapping[str, Any], number: int) -> list[int]:
    """The areas a scenario arms, in any mode."""
    return [
        area
        for area, mode in scenario_area_modes(config).get(number, {}).items()
        if mode in _ARMED
    ]


def is_disarm_scenario(config: Mapping[str, Any], number: int) -> bool:
    """Whether a scenario disarms areas and arms none."""
    modes = scenario_area_modes(config).get(number, {}).values()
    return ArmMode.DISARM in modes and not any(mode in _ARMED for mode in modes)


def suggested_mappings(
    config: Mapping[str, Any], selection: Mapping[str, list[int]]
) -> dict[str, int | None]:
    """Arm Away and Disarm scenarios a new setup proposes (0-based, or None).

    Among the imported scenarios: Arm Away is one that arms every imported
    area, in any mode, preferring one that arms them all in total mode, then
    the lowest number; Disarm is the lowest-numbered one that disarms every
    imported area. What a scenario does to areas not imported does not
    matter. Without imported areas nothing is proposed.
    """
    areas = selection.get(CONF_AREAS) or []
    modes = scenario_area_modes(config)
    arming: list[tuple[bool, int]] = []
    disarming: list[int] = []
    for number in sorted(selection.get(CONF_SCENARIOS) or []):
        record = modes.get(number)
        if not areas or record is None:
            continue
        chosen = [record.get(area) for area in areas]
        if all(mode in _ARMED for mode in chosen):
            arming.append((not all(mode == ArmMode.AWAY for mode in chosen), number))
        elif all(mode == ArmMode.DISARM for mode in chosen):
            disarming.append(number)
    return {
        CONF_SCENARIO_ARM_AWAY: min(arming)[1] - 1 if arming else None,
        CONF_SCENARIO_DISARM: disarming[0] - 1 if disarming else None,
    }


def migrated_selection(settings: Mapping[str, Any]) -> dict[str, list[int]]:
    """The selection that keeps an entry's entities as the counts gave them.

    The first N areas, zone numbers and scenarios; zone numbers that are not
    zones were never given entities and stay without. Scenarios mapped to an
    alarm panel mode beyond the count are added, so that the mapping stays
    valid.
    """

    def count(key: str, default: int, maximum: int) -> int:
        value = settings.get(key)
        return min(int(value) if value else default, maximum)

    scenarios = set(
        range(1, count(CONF_LIMIT_SCENARIOS, DEFAULT_LIMIT_SCENARIOS, 30) + 1)
    )
    scenarios.update(mapped_scenarios(settings).values())
    return {
        CONF_AREAS: list(
            range(1, count(CONF_LIMIT_AREAS, DEFAULT_LIMIT_AREAS, MAX_LIMIT_AREAS) + 1)
        ),
        CONF_ZONES: list(
            range(1, count(CONF_LIMIT_ZONES, DEFAULT_LIMIT_ZONES, MAX_LIMIT_ZONES) + 1)
        ),
        CONF_SCENARIOS: sorted(scenarios),
        CONF_OUTPUTS: [],
    }


# ---------------------------------------------------------------- entities


def item_of_unique_id(
    entry_id: str, unique_id: str, terminals: int | None = None
) -> tuple[str, int] | None:
    """(kind, number) of an area, zone, scenario or output entity, else None.

    A fixed output's number depends on the model's terminals (L): without
    them, it is not recognised.
    """
    suffix = unique_id.removeprefix(entry_id)
    if suffix == unique_id:
        return None
    if match := _FIXED_OUTPUT_UNIQUE_ID.fullmatch(suffix):
        if terminals is None:
            return None
        return CONF_OUTPUTS, terminals + int(match.group(1))
    for key, patterns in _ITEM_UNIQUE_IDS.items():
        for pattern, zero_based in patterns:
            if match := pattern.fullmatch(suffix):
                number = int(match.group(1))
                return key, number + 1 if zero_based else number
    return None


def unselected_items(
    options: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, set[int]]:
    """Items on the panel that the user did not select."""
    return {
        key: set(AVAILABLE[key](config)) - set(selected(options, config, key))
        for key in SELECTION_KEYS
    }


def numbers(values: Iterable[Any]) -> list[int]:
    """Form values (strings) as sorted 1-based numbers."""
    return sorted({int(value) for value in values})


def as_form_values(values: Collection[int]) -> list[str]:
    """Numbers as the values of a form's select."""
    return [str(value) for value in sorted(values)]
