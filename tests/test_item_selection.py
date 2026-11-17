"""The areas, zones and scenarios to import: setup, options, upgrade, arm_area."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)

from .conftest import add_entry, items_field, items_form, offered, proposed
from .fake_panel import FakePanel, const as c
from .test_area_modes import arm_area, arm_writes
from .test_config_flow import board_form, open_options, si_input, submit, unload_all
from .test_reconfigure_refresh import reread
from .test_zones import has_entity, stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")


def items_input(result: dict, **chosen) -> dict:
    """Input of an items form: the given numbers (the rest as proposed)."""
    kinds = ("areas", "zones", "scenarios")
    return items_form(result, {key: chosen.get(key) for key in kinds})


async def change_items(hass: HomeAssistant, entry: ConfigEntry, **chosen) -> dict:
    """Save the options' items step with these numbers (the rest as they are)."""
    result = await open_options(hass, entry, "items")
    assert result["step_id"] == "items"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], items_input(result, **chosen)
    )
    await hass.async_block_till_done()
    return result


def area_choices(hass: HomeAssistant) -> list[dict[str, str]]:
    """The areas the arm_area action offers."""
    return hass.data["inim_test_descriptions"][DOMAIN]["arm_area"]["fields"]["areas"][
        "selector"
    ]["select"]["options"]


async def load_descriptions(hass: HomeAssistant) -> None:
    hass.data["inim_test_descriptions"] = await async_get_all_descriptions(hass)


# ------------------------------------------------------------------ setup


async def test_setup_offers_and_proposes(hass: HomeAssistant, panel: FakePanel) -> None:
    """Every area and scenario is offered, and the zones; what is in use is ticked.

    The fake's zones 1-37 belong to areas 1-7; its scenarios 1-3 change
    areas, 4-30 change none.
    """
    panel.state.zone_areas[4] = 0  # zone 5 belongs to no area
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    assert result["step_id"] == "items"

    assert offered(result, "areas") == list(range(1, 11))
    assert offered(result, "zones") == list(range(1, 38))
    assert offered(result, "scenarios") == list(range(1, 31))
    assert proposed(result, "areas") == list(range(1, 8))
    assert proposed(result, "zones") == [z for z in range(1, 38) if z != 5]
    assert proposed(result, "scenarios") == [1, 2, 3]
    hass.config_entries.flow.async_abort(flow_id)


async def test_lists_show_the_panel_labels(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Each item is listed with its number and label; unnamed ones by number."""
    labels = panel.state.labels[c.RAM_LABELS_AREAS]
    labels[0], labels[1] = "Ingresso", ""
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    options = items_field(result, "areas")[1].config["options"][:3]
    assert options == [
        {"value": "1", "label": "1 - Ingresso"},
        {"value": "2", "label": "Area 2"},
        {"value": "3", "label": "3 - Area 3"},
    ]
    hass.config_entries.flow.async_abort(flow_id)


async def test_only_the_ticked_items_get_entities(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Areas, zones and scenarios not ticked get no entities."""
    # scenarios 1 and 2 are the arm away and disarm scenarios of add_entry
    entry = await add_entry(hass, panel, areas=[1, 3], zones=[2, 20], scenarios=[1, 2])
    assert entry.options["areas"] == [1, 3]
    assert entry.options["zones"] == [2, 20]
    assert entry.options["scenarios"] == [1, 2]
    for entity, expected in (
        (("switch", "_area_switch_3"), True),
        (("switch", "_area_switch_2"), False),
        (("select", "_area_mode_select_1"), True),
        (("binary_sensor", "_area_2_alarm_bs"), False),
        (("binary_sensor", "_zone_20"), True),
        (("switch", "_zone_enabled_switch_2"), True),
        (("binary_sensor", "_zone_1"), False),
        (("button", "_scenario_button_1"), True),  # scenario 2 (0-based IDs)
        (("button", "_scenario_button_2"), False),
        (("binary_sensor", "_scenario_2_active_bs"), False),
    ):
        assert has_entity(hass, entry, entity) is expected, entity
    await unload_all(hass)


async def test_mappings_offer_the_imported_scenarios(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Setup and options map modes only to imported scenarios."""
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    result = await submit(hass, flow_id, items_input(result, scenarios=[1, 3]))
    assert result["step_id"] == "initial_options"
    choices = result["data_schema"].schema
    for marker, validator in choices.items():
        if str(marker) == "scenario_arm_away":
            assert list(validator.container) == ["none", "0", "2"]
    hass.config_entries.flow.async_abort(flow_id)

    entry = await add_entry(hass, panel, scenarios=[1, 2, 4])
    result = await open_options(hass, entry)
    for marker, validator in result["data_schema"].schema.items():
        if str(marker) == "scenario_disarm":
            assert list(validator.container) == ["none", "0", "1", "3"]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload_all(hass)


# ---------------------------------------------------------------- options


async def test_untick_and_tick_again(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unticked items lose their entities; ticked again, they come back."""
    registry = er.async_get(hass)
    area_switch = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_area_switch_3"
    )
    zone_sensor = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_zone_20"
    )
    others = len(er.async_entries_for_config_entry(registry, entry.entry_id))

    areas = [a for a in entry.options["areas"] if a != 3]
    zones = [z for z in entry.options["zones"] if z != 20]
    scenarios = [s for s in entry.options["scenarios"] if s != 5]
    result = await change_items(
        hass, entry, areas=areas, zones=zones, scenarios=scenarios
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["areas"] == areas
    assert entry.options["polling_interval"] == 30  # the rest is kept
    assert registry.async_get(area_switch) is None
    assert registry.async_get(zone_sensor) is None
    for entity in (
        ("select", "_area_mode_select_3"),
        ("binary_sensor", "_area_3_tamper_bs"),
        ("switch", "_zone_enabled_switch_20"),
        ("button", "_scenario_button_4"),
    ):
        assert not has_entity(hass, entry, entity), entity
    # 5 area, 4 zone and 2 scenario entities fewer
    assert (
        len(er.async_entries_for_config_entry(registry, entry.entry_id)) == others - 11
    )

    await change_items(
        hass, entry, areas=[*areas, 3], zones=[*zones, 20], scenarios=[*scenarios, 5]
    )
    assert registry.async_get(area_switch) is not None
    assert registry.async_get(zone_sensor) is not None
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == others


async def test_a_mapped_scenario_cannot_be_unticked(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Scenario 2 is the disarm scenario: the mapping is changed first."""
    options = dict(entry.options)
    result = await change_items(hass, entry, scenarios=[1, 3])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "mapped_scenario_not_imported"}
    assert result["description_placeholders"]["scenarios"] == "2 - Scenario 2"
    assert dict(entry.options) == options
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_reread_keeps_the_selection(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """A zone wired later appears in the options, not ticked by itself."""
    entry = await add_entry(hass, panel, zones=[1, 2])
    panel.state.terminal_types[37] = c.TERMINAL_INPUT  # terminal 38 now a zone
    panel.state.zone_areas[37] = 1
    await reread(hass, entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["zones"] == [1, 2]
    result = await open_options(hass, entry, "items")
    assert 38 in offered(result, "zones")
    assert proposed(result, "zones") == [1, 2]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload_all(hass)


# ---------------------------------------------------------------- upgrade


async def test_upgrade_keeps_the_entities(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Counts become the first N items; a mapped scenario beyond them is kept."""
    stored = await stored_configuration(panel)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=3,
        title="Old",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Old",
            DATA_INITIAL_PANEL_CONFIG: stored,
        },
        options={
            "polling_interval": 30,
            "limit_areas": 4,
            "limit_zones": 20,
            "limit_scenarios": 5,
            "scenario_arm_away": 9,  # scenario 10
            "scenario_disarm": 1,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    assert not {"limit_areas", "limit_zones", "limit_scenarios"} & set(entry.options)
    assert entry.options["areas"] == [1, 2, 3, 4]
    assert entry.options["zones"] == list(range(1, 21))
    assert entry.options["scenarios"] == [1, 2, 3, 4, 5, 10]
    assert entry.options["polling_interval"] == 30
    for entity, expected in (
        (("switch", "_area_switch_4"), True),
        (("switch", "_area_switch_5"), False),
        (("binary_sensor", "_zone_20"), True),
        (("binary_sensor", "_zone_21"), False),
        (("button", "_scenario_button_9"), True),
        (("button", "_scenario_button_5"), False),
    ):
        assert has_entity(hass, entry, entity) is expected, entity
    await unload_all(hass)


@pytest.mark.parametrize(
    ("options", "areas", "zones", "scenarios"),
    [
        ({}, 15, 200, 30),  # no counts stored: everything
        ({"limit_areas": 16, "limit_zones": 250}, 15, 200, 30),  # above today's
    ],
)
async def test_upgrade_without_or_with_large_counts(
    hass: HomeAssistant, options: dict, areas: int, zones: int, scenarios: int
) -> None:
    """Missing counts mean everything; counts above the maximum are capped."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, minor_version=3, data={}, options=options
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.options["areas"] == list(range(1, areas + 1))
    assert entry.options["zones"] == list(range(1, zones + 1))
    assert entry.options["scenarios"] == list(range(1, scenarios + 1))


# --------------------------------------------------------------- arm_area


async def test_arm_area_lists_the_imported_areas(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The action offers the imported areas by name; none once unloaded."""
    panel.state.labels[c.RAM_LABELS_AREAS][0] = "Ingresso"
    entry = await add_entry(hass, panel, areas=[1, 3])
    await load_descriptions(hass)
    assert area_choices(hass) == [
        {"value": "1", "label": "Ingresso"},
        {"value": "3", "label": "Area 3"},
    ]

    await arm_area(hass, entry.entry_id, ["3"], "partial")  # as the form sends
    assert panel.state.armed_areas() == [3]
    assert arm_writes(panel) == 1

    # Changing the options updates the list
    await change_items(hass, entry, areas=[2])
    await load_descriptions(hass)
    assert area_choices(hass) == [{"value": "2", "label": "Area 2"}]

    await unload_all(hass)
    await load_descriptions(hass)
    assert area_choices(hass) == []


async def test_arm_area_lists_the_areas_of_each_panel(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """With two panels each choice names the area of every panel importing it."""
    other = FakePanel()
    other.state.labels[c.RAM_LABELS_AREAS][1] = "Office"
    await other.start()
    try:
        await add_entry(hass, panel, areas=[1, 2], name="Home")
        await add_entry(hass, other, areas=[2], name="Work")
        await load_descriptions(hass)
        assert area_choices(hass) == [
            {"value": "1", "label": "Area 1 (Home)"},
            {"value": "2", "label": "Area 2 (Home), Office (Work)"},
        ]
        await unload_all(hass)
    finally:
        await other.stop()
