"""Each area switch's own arm mode, set in the options."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.inim_smartliving_alarm.attribute_values import (
    ARM_MODE_TEXT,
    SWITCH_ARM_MODE_OPTIONS,
    switch_arm_mode,
)
from custom_components.inim_smartliving_alarm.const import (
    DEFAULT_AREA_SWITCHES_ARM_MODE,
)
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode

from .fake_panel import FakePanel, const as c
from .test_config_flow import form_defaults, open_options
from .test_init_baseline import entity_id_for

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def save_area_modes(
    hass: HomeAssistant, entry: ConfigEntry, user_input: dict[str, Any]
) -> dict:
    """Save the "Area switches default arm mode" step with this input."""
    result = await open_options(hass, entry, "area_switches")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


async def turn_on(hass: HomeAssistant, entry: ConfigEntry, area: int) -> None:
    """Turn on an area's switch."""
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id_for(hass, entry, ("switch", f"_area_switch_{area}"))},
        blocking=True,
    )


async def test_one_select_per_imported_area(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Named by number and label; every area starts at "Use the default"."""
    result = await open_options(hass, entry, "area_switches")
    assert result["step_id"] == "area_switches"
    defaults = form_defaults(result)
    assert list(defaults) == [f"{n} - Area {n}" for n in range(1, 11)]
    assert set(defaults.values()) == {"default"}
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == ["default", "total", "partial", "instant"]
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_only_areas_set_otherwise_are_stored(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The others follow the default; the rest of the options is kept."""
    options = dict(entry.options)
    result = await save_area_modes(
        hass, entry, {"3 - Area 3": "partial", "4 - Area 4": "total"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["area_switch_arm_mode_by_area"] == {
        "3": "partial",
        "4": "total",
    }
    for key, value in options.items():
        assert entry.options[key] == value

    # Shown again as stored
    result = await open_options(hass, entry, "area_switches")
    defaults = form_defaults(result)
    assert defaults["3 - Area 3"] == "partial"
    assert defaults["4 - Area 4"] == "total"
    assert defaults["5 - Area 5"] == "default"
    hass.config_entries.options.async_abort(result["flow_id"])


@pytest.mark.parametrize(
    ("area_modes", "default", "area", "expected"),
    [
        ({}, "total", 3, c.MODE_AWAY),  # nothing set: the default
        ({}, "partial", 3, c.MODE_STAY),
        ({"3": "partial"}, "total", 3, c.MODE_STAY),  # the area's own mode
        ({"3": "instant"}, "total", 3, c.MODE_INSTANT),
        ({"3": "total"}, "partial", 3, c.MODE_AWAY),
        ({"3": "partial"}, "total", 4, c.MODE_AWAY),  # another area: default
    ],
)
async def test_area_switch_arms_in_its_mode(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    area_modes: dict[str, str],
    default: str,
    area: int,
    expected: int,
) -> None:
    """Turning a switch on arms its area in its own mode, else the default."""
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "area_switches_default_arm_mode": default,
            "area_switch_arm_mode_by_area": area_modes,
        },
    )
    await hass.async_block_till_done()
    await turn_on(hass, entry, area)
    assert panel.state.armed_areas() == [area]
    assert panel.state.area_modes[area - 1] == expected


async def test_areas_not_imported_are_dropped(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """A mode stored for an area that is no longer imported goes on saving."""
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "areas": [1, 2, 3],
            "area_switch_arm_mode_by_area": {"3": "partial", "9": "instant"},
        },
    )
    await hass.async_block_till_done()
    result = await open_options(hass, entry, "area_switches")
    assert list(form_defaults(result)) == ["1 - Area 1", "2 - Area 2", "3 - Area 3"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"3 - Area 3": "partial"}
    )
    await hass.async_block_till_done()
    assert entry.options["area_switch_arm_mode_by_area"] == {"3": "partial"}


def test_switch_options_are_the_arm_mode_words() -> None:
    """The stored option values stay "total", "partial" and "instant"."""
    assert SWITCH_ARM_MODE_OPTIONS == ("total", "partial", "instant")
    assert DEFAULT_AREA_SWITCHES_ARM_MODE == ARM_MODE_TEXT[ArmMode.AWAY]
    assert [switch_arm_mode(option) for option in SWITCH_ARM_MODE_OPTIONS] == [
        ArmMode.AWAY,
        ArmMode.STAY,
        ArmMode.INSTANT,
    ]
    assert switch_arm_mode("disarm") is ArmMode.AWAY  # not an option: the default
    assert switch_arm_mode(None) is ArmMode.AWAY
