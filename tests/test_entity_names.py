"""Entity names: translated, without the panel name; entity IDs of existing
installations are kept as they are."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
import pytest

from custom_components.inim_smartliving_alarm.entity import name_item

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_config_flow import unload_all

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    ("label", "key", "placeholders"),
    [
        ("Kitchen window", "zone_tamper", {"number": "12", "label": "Kitchen window"}),
        ("", "zone_tamper_unnamed", {"number": "12"}),
        ("Zone 12", "zone_tamper_unnamed", {"number": "12"}),  # the default label
    ],
)
def test_names_with_and_without_a_label(
    label: str, key: str, placeholders: dict[str, str]
) -> None:
    """The panel's label goes in the name; without one, the number alone."""
    entity = Entity()
    name_item(entity, "zone_tamper", "Zone", 12, label)
    assert entity.has_entity_name
    assert entity.translation_key == key
    assert entity.translation_placeholders == placeholders


async def test_labels_in_the_names(hass: HomeAssistant, panel: FakePanel) -> None:
    """A labelled zone, area and scenario are named with their labels."""
    panel.state.labels[c.RAM_LABELS_ZONES][0] = "Front door"
    panel.state.labels[c.RAM_LABELS_AREAS][0] = "Ground floor"
    panel.state.labels[c.RAM_LABELS_SCENARIOS][0] = "Away"
    entry = await add_entry(hass, panel)
    registry = er.async_get(hass)
    names = {
        e.unique_id.removeprefix(entry.entry_id): e.original_name
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert names["_zone_1"] == "Zone 1 (Front door)"
    assert names["_zone_tamper_1"] == "Zone 1 tamper (Front door)"
    assert names["_zone_enabled_switch_1"] == "Zone 1 included (Front door)"
    assert names["_area_switch_1"] == "Area 1 (Ground floor)"
    assert names["_area_mode_select_1"] == "Area 1 arming mode (Ground floor)"
    assert names["_area_1_alarm_bs"] == "Area 1 alarm (Ground floor)"
    assert names["_scenario_button_0"] == "Activate scenario 1 (Away)"
    assert names["_scenario_0_active_bs"] == "Scenario 1 active (Away)"
    assert names["_zone_2"] == "Zone 2"
    assert names["_alarm_control_panel"] is None  # the device's name
    await unload_all(hass)


async def test_existing_entity_ids_are_kept(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """IDs with the panel name twice (recent Home Assistant) or once (earlier
    ones), and a name the user set, stay as they are."""
    entry = await add_entry(hass, panel)
    registry = er.async_get(hass)
    entries = sorted(
        er.async_entries_for_config_entry(registry, entry.entry_id),
        key=lambda e: e.unique_id,
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # As an earlier version left them: every other ID with the name twice
    kept: dict[str, str] = {}
    for index, item in enumerate(entries):
        entity_id = item.entity_id
        if index % 2 == 0:
            domain, object_id = entity_id.split(".", 1)
            entity_id = f"{domain}.test_panel_{object_id}"
            registry.async_update_entity(item.entity_id, new_entity_id=entity_id)
        kept[item.unique_id] = entity_id
    renamed = kept[f"{entry.entry_id}_zone_3"]
    registry.async_update_entity(renamed, name="Garage door")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert after == kept
    assert any(".test_panel_test_panel_" in entity_id for entity_id in after.values())
    assert registry.async_get(renamed).name == "Garage door"
    assert hass.states.get(renamed).attributes["friendly_name"] == "Garage door"
    await unload_all(hass)
