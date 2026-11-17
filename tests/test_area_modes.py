"""Area arming modes: the per-area select, the arm_area action and the switch option."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.inim_smartliving_alarm.const import DOMAIN, SERVICE_ARM_AREA
from custom_components.inim_smartliving_alarm.errors import CommandRefusedError

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_config_flow import form_defaults, open_options
from .test_init_baseline import AREA_3_SWITCH, call, entity_id_for, refresh

pytestmark = pytest.mark.usefixtures("socket_enabled")

AREA_3_SELECT = ("select", "_area_mode_select_3")


def arm_writes(panel: FakePanel) -> int:
    """How many arm/disarm commands the panel received."""
    return len([r for r in panel.requests if r.write and r.address == c.RAM_CMD_ARM])


async def select(hass: HomeAssistant, entry: ConfigEntry, option: str) -> None:
    """Choose an option of the area 3 select."""
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id_for(hass, entry, AREA_3_SELECT), "option": option},
        blocking=True,
    )


async def arm_area(hass: HomeAssistant, entry_id: str, areas, mode: str) -> None:
    """Call the arm_area action."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ARM_AREA,
        {"config_entry_id": entry_id, "areas": areas, "mode": mode},
        blocking=True,
    )


# ----------------------------------------------------------------- select


async def test_select_entity(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """One select per area, named "Area N arming mode", with four options."""
    entity_id = entity_id_for(hass, entry, AREA_3_SELECT)
    assert entity_id.endswith("area_3_arming_mode")
    state = hass.states.get(entity_id)
    assert state.state == "disarmed"
    assert state.attributes["options"] == [
        "disarmed",
        "armed_total",
        "armed_partial",
        "armed_instant",
    ]
    assert state.attributes["area_id"] == 3
    assert state.attributes["area_name"] == "Area 3"


@pytest.mark.parametrize(
    ("mode", "option"),
    [
        (c.MODE_AWAY, "armed_total"),
        (c.MODE_STAY, "armed_partial"),
        (c.MODE_INSTANT, "armed_instant"),
        (c.MODE_DISARMED, "disarmed"),
        (c.MODE_NONE, "unknown"),
    ],
)
async def test_select_follows_the_panel(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, mode: int, option: str
) -> None:
    """The select shows the area's mode, or unknown."""
    panel.state.area_modes[2] = mode
    await refresh(hass, entry)
    assert hass.states.get(entity_id_for(hass, entry, AREA_3_SELECT)).state == option


@pytest.mark.parametrize(
    ("option", "mode"),
    [
        ("armed_total", c.MODE_AWAY),
        ("armed_partial", c.MODE_STAY),
        ("armed_instant", c.MODE_INSTANT),
    ],
)
async def test_select_arms_only_that_area(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, option: str, mode: int
) -> None:
    """Choosing a mode arms that area only, in that mode, then disarms it."""
    await select(hass, entry, option)
    assert panel.state.armed_areas() == [3]
    assert panel.state.area_modes[2] == mode
    assert hass.states.get(entity_id_for(hass, entry, AREA_3_SELECT)).state == option

    await select(hass, entry, "disarmed")
    assert panel.state.armed_areas() == []
    assert arm_writes(panel) == 2


async def test_select_refused(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A refusal raises and the select keeps the panel's mode."""
    panel.state.pins[0] = "9999"
    with pytest.raises(CommandRefusedError):
        await select(hass, entry, "armed_partial")
    assert panel.state.armed_areas() == []
    assert hass.states.get(entity_id_for(hass, entry, AREA_3_SELECT)).state == (
        "disarmed"
    )


async def test_selects_of_imported_areas_only(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Selects follow the imported areas, like the area switches."""
    entry = await add_entry(hass, panel, areas=[1, 2])
    states = [
        s for s in hass.states.async_all("select") if "arming_mode" in s.entity_id
    ]
    assert len(states) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


# -------------------------------------------------------------- arm_area


async def test_arm_area_several_areas_in_one_command(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Several areas change together, with one command."""
    await arm_area(hass, entry.entry_id, [1, 3], "partial")
    assert panel.state.armed_areas() == [1, 3]
    assert panel.state.area_modes[0] == panel.state.area_modes[2] == c.MODE_STAY
    assert arm_writes(panel) == 1

    await arm_area(hass, entry.entry_id, ["3", "1"], "disarm")  # as the UI sends
    assert panel.state.armed_areas() == []
    assert arm_writes(panel) == 2


@pytest.mark.parametrize(
    ("mode", "panel_mode"),
    [("total", c.MODE_AWAY), ("partial", c.MODE_STAY), ("instant", c.MODE_INSTANT)],
)
async def test_arm_area_modes(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    mode: str,
    panel_mode: int,
) -> None:
    """Each mode arms in that mode."""
    await arm_area(hass, entry.entry_id, 4, mode)
    assert panel.state.armed_areas() == [4]
    assert panel.state.area_modes[3] == panel_mode


async def test_arm_area_does_not_check_zones(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """With a zone open the command is still sent; the panel decides."""
    panel.state.zone_states[36] = c.ZONE_ALARM  # zone 37, area 3
    await arm_area(hass, entry.entry_id, [3], "total")
    assert panel.state.armed_areas() == [3]


async def test_arm_area_not_imported(hass: HomeAssistant, panel: FakePanel) -> None:
    """Areas not imported (or not on the panel) are named and nothing is sent."""
    entry = await add_entry(hass, panel, areas=[1, 2, 3])
    with pytest.raises(ServiceValidationError) as err:
        await arm_area(hass, entry.entry_id, [2, 4, 11], "total")
    assert err.value.translation_key == "area_not_imported"
    assert err.value.translation_placeholders == {
        "areas": "4, 11",
        "title": "Test Panel",
    }
    assert arm_writes(panel) == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_arm_area_refused(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A refusal names the areas."""
    panel.state.pins[0] = "9999"
    with pytest.raises(CommandRefusedError) as err:
        await arm_area(hass, entry.entry_id, [1, 3], "total")
    assert err.value.translation_placeholders["target"] == "Area 1, Area 3"


async def test_arm_area_unknown_or_unloaded_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The panel must exist and be loaded."""
    with pytest.raises(ServiceValidationError) as err:
        await arm_area(hass, "not-an-entry", [1], "total")
    assert err.value.translation_key == "entry_not_found"

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await arm_area(hass, entry.entry_id, [1], "total")
    assert err.value.translation_key == "entry_not_loaded_command"
    assert await hass.config_entries.async_setup(entry.entry_id)


# ------------------------------------------------- area switch arm mode option


async def test_switch_arms_total_by_default(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Without the option the switch arms total, as before."""
    result = await open_options(hass, entry)
    assert form_defaults(result)["area_switches_default_arm_mode"] == "total"
    hass.config_entries.options.async_abort(result["flow_id"])

    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.area_modes[2] == c.MODE_AWAY


async def test_entry_saved_before_the_option(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Options saved by an earlier version, without the option, mean total."""
    old_options = {
        key: value
        for key, value in entry.options.items()
        if key != "area_switches_default_arm_mode"
    }
    hass.config_entries.async_update_entry(entry, options=old_options)  # reloads
    await hass.async_block_till_done()
    assert "area_switches_default_arm_mode" not in entry.options

    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.area_modes[2] == c.MODE_AWAY

    # The options form offers total and saves it unless another is chosen
    result = await open_options(hass, entry)
    assert form_defaults(result)["area_switches_default_arm_mode"] == "total"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["area_switches_default_arm_mode"] == "total"


@pytest.mark.parametrize(
    ("option", "mode"), [("partial", c.MODE_STAY), ("instant", c.MODE_INSTANT)]
)
async def test_switch_arms_in_the_chosen_mode(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, option: str, mode: int
) -> None:
    """The option chosen in the options form sets the switch's arming mode."""
    result = await open_options(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"area_switches_default_arm_mode": option}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["area_switches_default_arm_mode"] == option

    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.area_modes[2] == mode
    await refresh(hass, entry)
    assert hass.states.get(entity_id_for(hass, entry, AREA_3_SWITCH)).state == "on"
