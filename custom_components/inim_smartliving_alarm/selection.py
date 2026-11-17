"""Which areas, zones and scenarios of the panel get entities.

The user picks them in the setup and the options: the selection is stored in
the entry options as lists of 1-based numbers. Only items that are on the
panel are offered: every area and scenario of the model, and the zone numbers
whose terminal is programmed as a zone.
"""

from collections.abc import Collection, Iterable, Mapping
import re
from typing import Any

from .const import (
    CONF_AREAS,
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
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
    KEY_INIT_AREAS,
    KEY_INIT_RAW_HEX,
    KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS,
    KEY_INIT_SCENARIO_ACTIVATION_INDEX,
    KEY_INIT_SCENARIO_ACTIVATION_RAW,
    KEY_INIT_SCENARIO_ACTIVATIONS,
    KEY_INIT_SCENARIO_NAMES,
    KEY_INIT_SCENARIOS,
    KEY_INIT_SYSTEM_INFO,
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
from .protocol.config import decode_zone_records
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .protocol.model import PanelModel, model_for, parse_model_string

SELECTION_KEYS = (CONF_AREAS, CONF_ZONES, CONF_SCENARIOS)

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
}


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


AVAILABLE = {
    CONF_AREAS: available_areas,
    CONF_ZONES: available_zones,
    CONF_SCENARIOS: available_scenarios,
}


# ------------------------------------------------------------- the selection


def selected(
    options: Mapping[str, Any], config: Mapping[str, Any], key: str
) -> list[int]:
    """The selected items of one kind that are on the panel, in order.

    An entry without a stored selection (it should not happen once the
    entry is migrated) gets every item.
    """
    available = AVAILABLE[key](config)
    stored = options.get(key)
    if stored is None:
        return available
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
    }


def mapped_scenarios(options: Mapping[str, Any]) -> dict[str, int]:
    """Alarm panel modes mapped to a scenario: option key -> 1-based number."""
    mapped = {}
    for key in SCENARIO_MAPPING_KEYS:
        value = options.get(key)
        if value is not None and str(value).isdigit():
            mapped[key] = int(value) + 1
    return mapped


def stored_model(config: Mapping[str, Any]) -> PanelModel | None:
    """The model profile of the stored model string; None if not known."""
    raw = (config.get(KEY_INIT_SYSTEM_INFO) or {}).get(KEY_INIT_RAW_HEX)
    try:
        return model_for(parse_model_string(bytes.fromhex(raw)))
    except (TypeError, ValueError, ProtocolError, UnsupportedPanelError):
        return None


def zone_option_flags(config: Mapping[str, Any]) -> dict[int, dict[str, bool]]:
    """Each zone's programming options: 1-based zone -> name -> on.

    Decoded from the stored zone records with the stored model's layout;
    empty when either is missing or they do not fit.
    """
    model = stored_model(config)
    raw = (config.get(KEY_INIT_ZONES_CONFIG) or {}).get(KEY_INIT_ZONE_RECORDS_RAW)
    if model is None:
        return {}
    try:
        zones = decode_zone_records(bytes.fromhex(raw), model)
    except (TypeError, ValueError, ProtocolError):
        return {}
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
    }


# ---------------------------------------------------------------- entities


def item_of_unique_id(entry_id: str, unique_id: str) -> tuple[str, int] | None:
    """(kind, 1-based number) of an area, zone or scenario entity, else None."""
    suffix = unique_id.removeprefix(entry_id)
    if suffix == unique_id:
        return None
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
