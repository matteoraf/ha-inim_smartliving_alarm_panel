"""What scenarios do to each area, and the Arm Away and Disarm proposed at setup."""

from collections.abc import Iterable
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.inim_smartliving_alarm import button
from custom_components.inim_smartliving_alarm.const import DOMAIN
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.selection import (
    is_disarm_scenario,
    scenario_actions,
    scenario_area_modes,
    scenario_armed_areas,
    suggested_mappings,
)

from .conftest import items_form
from .fake_panel import FakePanel, panel_10100
from .test_config_flow import form_defaults
from .test_init_baseline import entity_id_for

pytestmark = pytest.mark.usefixtures("socket_enabled")

AWAY, STAY, INSTANT, DISARM = (
    ArmMode.AWAY,
    ArmMode.STAY,
    ArmMode.INSTANT,
    ArmMode.DISARM,
)


def config(*scenarios: dict[int, int], areas: int = 10) -> dict[str, Any]:
    """A stored configuration with these scenarios (area -> mode), numbered 1-N."""

    def record(modes: dict[int, int]) -> str:
        data = bytearray(8)
        for area, mode in modes.items():
            data[(area - 1) // 2] |= mode << (4 * ((area - 1) % 2))
        return data.hex()

    return {
        "areas": {"names": [f"Area {n}" for n in range(1, areas + 1)]},
        "scenario_activations": [
            {"scenario_index": index, "raw_activation_hex": record(modes)}
            for index, modes in enumerate(scenarios)
        ],
    }


def presets(
    stored: dict[str, Any], areas: Iterable[int], scenarios: Iterable[int]
) -> tuple[int | None, int | None]:
    """(Arm Away, Disarm) proposed, 1-based."""
    found = suggested_mappings(
        stored, {"areas": list(areas), "scenarios": list(scenarios)}
    )

    def number(index: int | None) -> int | None:
        return None if index is None else index + 1

    return number(found["scenario_arm_away"]), number(found["scenario_disarm"])


# ------------------------------------------------------------------ rules


def test_modes_are_decoded_per_area() -> None:
    """One nibble per area, area 1 in the low nibble."""
    stored = config({1: AWAY, 2: STAY, 3: INSTANT, 10: DISARM})
    assert scenario_area_modes(stored)[1] == {
        1: 1,
        2: 2,
        3: 3,
        4: 0,
        5: 0,
        6: 0,
        7: 0,
        8: 0,
        9: 0,
        10: 4,
    }


def test_total_arming_is_preferred() -> None:
    """Scenario 1 arms partially, 3 in total: 3 is proposed."""
    stored = config(
        {1: STAY, 2: STAY},
        {1: DISARM, 2: DISARM},
        {1: AWAY, 2: AWAY},
    )
    assert presets(stored, [1, 2], [1, 2, 3]) == (3, 2)


def test_any_arming_mode_counts() -> None:
    """Without a total one, the lowest scenario arming them all in any mode."""
    stored = config({1: AWAY}, {1: INSTANT, 2: STAY}, {1: AWAY, 2: INSTANT})
    assert presets(stored, [1, 2], [1, 2, 3]) == (2, None)


def test_every_imported_area_is_needed() -> None:
    """A scenario that keeps one of the imported areas is not proposed."""
    stored = config({1: AWAY, 2: AWAY}, {1: DISARM})
    assert presets(stored, [1, 2, 3], [1, 2]) == (None, None)


def test_areas_not_imported_do_not_matter() -> None:
    """A scenario that also changes other areas still counts."""
    stored = config({1: AWAY, 2: AWAY, 9: DISARM}, {1: DISARM, 2: DISARM, 9: AWAY})
    assert presets(stored, [1, 2], [1, 2]) == (1, 2)


def test_lowest_disarming_scenario() -> None:
    """Of several scenarios disarming them all, the lowest number."""
    stored = config({1: AWAY}, {1: DISARM, 2: DISARM}, {1: DISARM})
    assert presets(stored, [1], [1, 2, 3]) == (1, 2)


def test_only_imported_scenarios() -> None:
    """A scenario that is not imported is not proposed."""
    stored = config({1: AWAY}, {1: DISARM}, {1: AWAY}, {1: DISARM})
    assert presets(stored, [1], [3, 4]) == (3, 4)


def test_nothing_to_propose() -> None:
    """No imported areas, or no scenario records stored: nothing."""
    stored = config({1: AWAY}, {1: DISARM})
    assert presets(stored, [], [1, 2]) == (None, None)
    assert presets({"areas": {"names": ["A"]}}, [1], [1, 2]) == (None, None)


# ------------------------------------------------------------------ setup


async def initial_options(
    hass: HomeAssistant, panel: FakePanel, areas: Iterable[int] | None
) -> dict:
    """The last setup step, after ticking these areas (None: as proposed)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": panel.host, "port": panel.port, "pin": "1234", "panel_name": "P"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        items_form(result, {"areas": areas, "zones": None, "scenarios": range(1, 31)}),
    )
    assert result["step_id"] == "initial_options"
    return result


async def test_setup_proposes_arm_away_and_disarm(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The fake 1050: scenario 1 arms areas 1-7, scenario 2 disarms 1-8.

    The setup proposes areas 1-7 (those with zones), so 1 and 2 are
    proposed; the other modes stay unmapped.
    """
    result = await initial_options(hass, panel, None)
    defaults = form_defaults(result)
    assert defaults["scenario_arm_away"] == "0"
    assert defaults["scenario_disarm"] == "1"
    for key in ("scenario_arm_home", "scenario_arm_night", "scenario_arm_vacation"):
        assert defaults[key] == "none"

    # Saved as proposed when left as it is
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["scenario_arm_away"] == 0
    assert entry.options["scenario_disarm"] == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_proposes_nothing_without_a_match(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """With all 10 areas no fake scenario arms or disarms them all."""
    result = await initial_options(hass, panel, range(1, 11))
    defaults = form_defaults(result)
    assert defaults["scenario_arm_away"] == "none"
    assert defaults["scenario_disarm"] == "none"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_setup_10100(hass: HomeAssistant) -> None:
    """The fake 10100: scenario 1 arms all 15 areas, 2 disarms them all."""
    fake = FakePanel(panel_10100())
    await fake.start()
    try:
        result = await initial_options(hass, fake, range(1, 16))
        defaults = form_defaults(result)
        assert defaults["scenario_arm_away"] == "0"
        assert defaults["scenario_disarm"] == "1"
        hass.config_entries.flow.async_abort(result["flow_id"])
    finally:
        await fake.stop()


# --------------------------------------------------------- scenario modes


def test_scenario_actions_name_every_mode() -> None:
    """Partial and instant arming are named, not "unknown"."""
    stored = config({1: AWAY, 2: STAY, 3: INSTANT, 4: DISARM}, areas=5)
    assert scenario_actions(stored, 1) == {
        "1": "total",
        "2": "partial",
        "3": "instant",
        "4": "disarm",
        "5": "keep",
    }
    assert scenario_actions(stored, 2) == {}  # no record stored
    assert scenario_armed_areas(stored, 1) == [1, 2, 3]


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ({1: DISARM, 2: DISARM}, True),
        ({1: DISARM, 2: STAY}, False),  # arms an area partially
        ({1: DISARM, 2: INSTANT}, False),
        ({1: AWAY}, False),
        ({}, False),  # keeps every area
    ],
)
def test_disarm_scenarios(modes: dict[int, int], expected: bool) -> None:
    """Only a scenario that disarms and arms nothing is a disarm scenario."""
    assert is_disarm_scenario(config(modes), 1) is expected


async def test_button_shows_the_area_actions(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The fake 1050's scenario 1 arms areas 1-7 in total mode."""
    state = hass.states.get(
        entity_id_for(hass, entry, ("button", "_scenario_button_0"))
    )
    assert state.attributes["area_actions_details"] == {
        **{str(area): "total" for area in range(1, 8)},
        **{str(area): "keep" for area in range(8, 11)},
    }


@pytest.mark.parametrize(("suffix", "disarm"), [("0", False), ("1", True)])
async def test_button_knows_disarm_scenarios(
    hass: HomeAssistant, entry: ConfigEntry, suffix: str, disarm: bool
) -> None:
    """Scenario 1 arms, scenario 2 disarms: refusals are explained so."""
    with patch.object(button, "async_activate_scenario") as activate:
        await hass.services.async_call(
            "button",
            "press",
            {
                "entity_id": entity_id_for(
                    hass, entry, ("button", f"_scenario_button_{suffix}")
                )
            },
            blocking=True,
        )
    assert activate.call_args.kwargs["is_disarm_scenario"] is disarm
