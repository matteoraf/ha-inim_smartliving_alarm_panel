"""The panel's configuration as one readable view.

Areas, zones, scenarios, outputs, codes and devices, each with a stable
number, its label and its decoded programming, under the same English keys
as the entity attributes. Built from the configuration stored in the entry,
so it also works while the panel cannot be reached. No Home Assistant
imports.
"""

from collections.abc import Callable, Mapping
from typing import Any

from .attribute_values import (
    area_settings_attributes,
    output_attributes,
    zone_programming_attributes,
)
from .peripherals import enabled_peripherals
from .selection import output_items, scenario_actions
from .stored_config import (
    area_names,
    area_records,
    keypad_names,
    name_table,
    refuses_not_ready,
    scenario_names,
    stored_model,
    stored_zones,
    terminal_records,
    zone_names,
    zone_records,
)


def _names(kind: str) -> Callable[[Mapping[str, Any]], list[str]]:
    """The names of a stored name table."""
    return lambda config: name_table(config, kind)


# Name tables of the other objects: view key, how to get the names
_NAME_TABLES: tuple[tuple[str, Callable[[Mapping[str, Any]], list[str]]], ...] = (
    ("codes", _names("codes")),
    ("keypads", keypad_names),
    ("readers", _names("readers")),
    ("expanders", _names("expanders")),
    ("keys", _names("keys")),
    ("timers", _names("timers")),
    ("sirens", _names("sirens")),
    ("phone_numbers", _names("phone_numbers")),
)


def configuration_view(config: Mapping[str, Any]) -> dict[str, Any]:
    """The stored configuration, decoded; labels in clear.

    Each list item has "number" (1-based, as the panel numbers it) and
    "label" (the panel's label, empty if none). Each zone number also has
    "is_zone": whether it is a zone on the panel, by its terminal's type
    (left out when the stored records cannot be decoded).
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

    # Whether each zone number is a zone on the panel; not known (left out)
    # without the zone and terminal records
    is_zone = {
        zone.number: zone.exists
        for zone in stored_zones(config)
        if zone.terminal is not None
    }

    def zone(number: int, label: str) -> dict[str, Any]:
        item: dict[str, Any] = {"number": number, "label": label}
        if number in is_zone:
            item["is_zone"] = is_zone[number]
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
        "refuses_arming_with_zones_not_ready": refuses_not_ready(config),
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
    for key, names in _NAME_TABLES:
        view[key] = [
            {"number": number, "label": label}
            for number, label in enumerate(names(config), start=1)
        ]
    return view
