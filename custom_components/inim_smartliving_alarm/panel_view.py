"""The panel's configuration as one readable view.

Areas, zones, scenarios, outputs, codes and devices, each with a stable
number, its label and its decoded programming, under the same English keys
as the entity attributes. Built from the configuration stored in the entry,
so it also works while the panel cannot be reached. No Home Assistant
imports.
"""

from collections.abc import Mapping
from typing import Any

from .attribute_values import (
    area_settings_attributes,
    output_attributes,
    zone_programming_attributes,
)
from .const import (
    KEY_INIT_CODES,
    KEY_INIT_EXPANDERS,
    KEY_INIT_GENERAL_OPTIONS,
    KEY_INIT_KEYBOARD_NAMES,
    KEY_INIT_KEYBOARDS,
    KEY_INIT_KEYS,
    KEY_INIT_NAMES,
    KEY_INIT_RAW_HEX,
    KEY_INIT_READERS,
    KEY_INIT_SIRENS,
    KEY_INIT_TIMERS,
)
from .peripherals import enabled_peripherals
from .protocol.config import refuses_arming_when_not_ready
from .protocol.errors import ProtocolError
from .selection import (
    area_names,
    area_records,
    output_items,
    scenario_actions,
    scenario_names,
    stored_model,
    terminal_records,
    zone_names,
    zone_records,
)

# Name tables of the other objects: view key, stored table, its names key
_NAME_TABLES = (
    ("codes", KEY_INIT_CODES, KEY_INIT_NAMES),
    ("keypads", KEY_INIT_KEYBOARDS, KEY_INIT_KEYBOARD_NAMES),
    ("readers", KEY_INIT_READERS, KEY_INIT_NAMES),
    ("expanders", KEY_INIT_EXPANDERS, KEY_INIT_NAMES),
    ("keys", KEY_INIT_KEYS, KEY_INIT_NAMES),
    ("timers", KEY_INIT_TIMERS, KEY_INIT_NAMES),
    ("sirens", KEY_INIT_SIRENS, KEY_INIT_NAMES),
)


def _names(config: Mapping[str, Any], table: str, key: str) -> list[str]:
    return list((config.get(table) or {}).get(key) or [])


def _refuses_not_ready(config: Mapping[str, Any]) -> bool | None:
    """The panel's own refuse-arming option; None if not stored or unreadable."""
    if stored_model(config) is None:
        return None
    raw = (config.get(KEY_INIT_GENERAL_OPTIONS) or {}).get(KEY_INIT_RAW_HEX)
    try:
        return refuses_arming_when_not_ready(bytes.fromhex(raw))
    except (TypeError, ValueError, ProtocolError):
        return None


def configuration_view(config: Mapping[str, Any]) -> dict[str, Any]:
    """The stored configuration, decoded; labels in clear.

    Each list item has "number" (1-based, as the panel numbers it) and
    "label" (the panel's label, empty if none).
    """
    model = stored_model(config)
    areas = area_records(config) or []
    zones = zone_records(config) or []
    terminals = terminal_records(config) or []

    def area(number: int, label: str) -> dict[str, Any]:
        item: dict[str, Any] = {"number": number, "label": label}
        if number <= len(areas):
            item |= area_settings_attributes(areas[number - 1])
        return item

    def zone(number: int, label: str) -> dict[str, Any]:
        item: dict[str, Any] = {"number": number, "label": label}
        index = number - 1
        if terminals and index < len(zones):
            terminal_index = index % len(terminals)
            item |= zone_programming_attributes(
                zones[index],
                terminals[terminal_index],
                terminal_index,
                second_zone=index >= len(terminals),
            )
        return item

    view: dict[str, Any] = {
        "model": None if model is None else model.name,
        "refuses_arming_with_zones_not_ready": _refuses_not_ready(config),
        "areas": [
            area(number, label)
            for number, label in enumerate(area_names(config), start=1)
        ],
        "zones": [
            zone(number, label)
            for number, label in enumerate(zone_names(config), start=1)
        ],
        "scenarios": [
            {
                "number": number,
                "label": label,
                "areas": scenario_actions(config, number),
            }
            for number, label in enumerate(scenario_names(config), start=1)
        ],
        "outputs": [
            {"number": item.number, "label": item.name, **output_attributes(item)}
            for item in output_items(config)
        ],
        "enabled_devices": enabled_peripherals(config),
    }
    for key, table, names_key in _NAME_TABLES:
        view[key] = [
            {"number": number, "label": label}
            for number, label in enumerate(_names(config, table, names_key), start=1)
        ]
    return view
