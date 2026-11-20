"""Area, zone, scenario and keypad names in the panel's own character set."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm import selection
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import DATA_INITIAL_PANEL_CONFIG
from custom_components.inim_smartliving_alarm.peripherals import enabled_peripherals
from custom_components.inim_smartliving_alarm.selection import (
    area_names,
    keypad_names,
    panel_config,
    scenario_names,
    zone_names,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import entity_id_for

pytestmark = pytest.mark.usefixtures("socket_enabled")

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
# Where each table keeps its bytes in the stored configuration
RAW_KEYS = {
    "areas": "raw_hex",
    "zones": "raw_hex_full",
    "scenarios": "raw_hex",
    "keyboard_names": "raw_hex",
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
    # As many names as before, so no number moves
    assert len(zone_names(config)) == len(config["zones"]["zone_names"])
    for entity, name in friendly_names(hass, entry).items():
        assert ENTITIES[entity] in name

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_existing_entry_shows_the_accents(
    hass: HomeAssistant, panel: FakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set up as earlier versions did, then started again: no read, same IDs."""
    with_accents(panel)
    with monkeypatch.context() as earlier:
        # Earlier versions used the stored names, without accented letters
        earlier.setattr(
            selection, "_decoded_labels", lambda raw_hex, count: None, raising=False
        )
        entry = await add_entry(hass, panel)
        entity_ids = {entity: entity_id_for(hass, entry, entity) for entity in ENTITIES}
        assert all("Città" not in name for name in friendly_names(hass, entry).values())
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    with patch.object(
        SmartLivingPanel,
        "read_configuration",
        side_effect=AssertionError("the configuration was read again"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert {entity: entity_id_for(hass, entry, entity) for entity in ENTITIES} == (
        entity_ids
    )
    for entity, name in friendly_names(hass, entry).items():
        assert ENTITIES[entity] in name
    # The stored data is unchanged
    assert panel_config(entry.data)["areas"]["names"][0] == "Ingresso Citt"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entry_without_the_label_bytes(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Without the stored bytes the stored names are used, with no error."""
    with_accents(panel)
    entry = await add_entry(hass, panel)
    config = {
        table: (
            {k: v for k, v in value.items() if k != RAW_KEYS[table]}
            if table in RAW_KEYS
            else value
        )
        for table, value in panel_config(entry.data).items()
    }
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, DATA_INITIAL_PANEL_CONFIG: config}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    stored = panel_config(entry.data)
    assert area_names(stored)[0] == "Ingresso Citt"
    assert zone_names(stored)[0] == "Porta Per"
    assert scenario_names(stored)[0] == "Caff sera"
    assert keypad_names(stored)[0] == "Tast Entre"
    assert "Ingresso Citt" in friendly_names(hass, entry)[("switch", "_area_switch_1")]

    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "raw",
    [
        None,  # not stored
        "zz",  # not hex
        "41" * 16,  # shorter than the names stored
        12,  # not text
    ],
)
def test_unreadable_label_bytes(raw: object) -> None:
    """The stored names are used when the bytes cannot be read."""
    table = {"names": ["First", "Second"]}
    if raw is not None:
        table["raw_hex"] = raw
    assert area_names({"areas": table}) == ["First", "Second"]


def test_label_bytes_decoded() -> None:
    """Charset bytes decode; unmapped ones show as "?"; trailing spaces go."""
    raw = (b"Citt\x94".ljust(16, b" ") + b"A\xfe".ljust(16, b"\x00")).hex()
    config = {"scenarios": {"names": ["Citt", "A"], "raw_hex": raw}}
    assert scenario_names(config) == ["Città", "A?"]
