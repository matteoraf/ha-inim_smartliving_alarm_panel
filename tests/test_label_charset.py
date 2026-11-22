"""Area, zone, scenario and keypad names in the panel's own character set."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.const import DATA_INITIAL_PANEL_CONFIG
from custom_components.inim_smartliving_alarm.peripherals import enabled_peripherals
from custom_components.inim_smartliving_alarm.selection import (
    panel_config,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    area_label,
    area_names,
    keypad_names,
    migrate_v1,
    scenario_label,
    scenario_names,
    zone_label,
    zone_names,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import entity_id_for

pytestmark = pytest.mark.usefixtures("socket_enabled")

ENTRIES = Path(__file__).parent / "fixtures" / "entries"

# The first label of each table, with letters outside ASCII
ACCENTED = {
    c.RAM_LABELS_AREAS: "Ingresso Città",
    c.RAM_LABELS_ZONES: "Porta Perù",
    c.RAM_LABELS_SCENARIOS: "Caffè sera",
    c.RAM_LABELS_KEYPADS: "Tast Entrée",
}
# Entities named after those labels, and the label each name shows
ENTITIES = {
    ("switch", "_area_switch_1"): "Ingresso Città",
    ("binary_sensor", "_zone_1"): "Porta Perù",
    ("button", "_scenario_button_0"): "Caffè sera",
}


def with_accents(panel: FakePanel) -> None:
    """Give the first label of the four tables accented letters."""
    for register, text in ACCENTED.items():
        names = list(panel.state.labels[register])
        names[0] = text
        panel.state.labels[register] = names


def friendly_names(hass: HomeAssistant, entry) -> dict[tuple[str, str], str]:
    """The name each entity shows."""
    return {
        entity: hass.states.get(entity_id_for(hass, entry, entity)).attributes[
            "friendly_name"
        ]
        for entity in ENTITIES
    }


async def test_names_in_the_panel_charset(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Accented letters are shown in all four tables and in entity names."""
    with_accents(panel)
    entry = await add_entry(hass, panel)
    config = panel_config(entry.data)

    assert area_names(config)[0] == "Ingresso Città"
    assert zone_names(config)[0] == "Porta Perù"
    assert scenario_names(config)[0] == "Caffè sera"
    assert keypad_names(config)[0] == "Tast Entrée"
    assert enabled_peripherals(config)["keypads"][0] == "Tast Entrée"
    # One name per zone number
    assert len(zone_names(config)) == 100
    for entity, name in friendly_names(hass, entry).items():
        assert ENTITIES[entity] in name

    assert await hass.config_entries.async_unload(entry.entry_id)


def test_an_entry_stored_by_0_4_shows_the_accents() -> None:
    """Version 1 stored the names without accented letters next to the bytes
    as read: after the update the names come from the bytes."""
    fixture = json.loads((ENTRIES / "0.4.0b2.json").read_text(encoding="utf-8"))
    old = fixture["data"][DATA_INITIAL_PANEL_CONFIG]
    raw = bytearray(bytes.fromhex(old["areas"]["raw_hex"]))
    raw[:16] = b"Ingresso Citt\x94".ljust(16)
    old["areas"] = {
        "raw_hex": raw.hex(),
        "names": ["Ingresso Citt", *old["areas"]["names"][1:]],
    }

    assert area_names(migrate_v1(old))[0] == "Ingresso Città"


def test_names_without_the_label_bytes() -> None:
    """A table not stored (until it is read again) has no names: items get
    their default names."""
    assert area_names({}) == []
    assert area_label({}, 1) == "Area 1"
    assert zone_label({}, 3) == "Zone 3"
    assert scenario_label({}, 2) == "Scenario 2"


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        (None, []),  # not stored
        ("zz", []),  # not hex
        (12, []),  # not text
        ("41" * 16, ["A" * 16]),  # one label
    ],
)
def test_unreadable_label_bytes(raw: object, names: list[str]) -> None:
    """Label bytes that cannot be read give no names."""
    assert area_names({} if raw is None else {"area_labels": raw}) == names


def test_label_bytes_decoded() -> None:
    """Charset bytes decode; unmapped ones show as "?"; trailing spaces go."""
    raw = (b"Citt\x94".ljust(16, b" ") + b"A\xfe".ljust(16, b"\x00")).hex()
    assert scenario_names({"scenario_labels": raw}) == ["Città", "A?"]
