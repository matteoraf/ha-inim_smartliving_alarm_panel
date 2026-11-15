"""Setting up and using the integration against the fake SmartLiving 1050.

The entities created by a fresh setup are compared with
tests/baselines/entities_1050.json. After an intended change to the
entities, regenerate the file with:

    INIM_UPDATE_BASELINE=1 pytest tests/test_init_baseline.py
"""

import json
import os
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.inim_smartliving_alarm.const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ALARM_RESET,
)

from .fake_panel import FakePanel, const as c

pytestmark = pytest.mark.usefixtures("socket_enabled")

BASELINE = Path(__file__).parent / "baselines" / "entities_1050.json"

# Entities used below, by platform and unique ID (without the entry ID prefix).
# Entity IDs are not used directly: Home Assistant derives them differently
# across releases.
ALARM_PANEL = ("alarm_control_panel", "_alarm_control_panel")
AREA_3_SWITCH = ("switch", "_area_switch_3")
ZONE_36_SWITCH = ("switch", "_zone_enabled_switch_36")
SCENARIO_3_BUTTON = ("button", "_scenario_button_2")
ZONE_20_SENSOR = ("binary_sensor", "_zone_20")
AREA_4_ALARM_MEMORY = ("binary_sensor", "_area_4_triggered_bs")
AREA_4_ALARM = ("binary_sensor", "_area_4_alarm_bs")
AREA_4_TAMPER = ("binary_sensor", "_area_4_tamper_bs")
AREA_10_ALARM_MEMORY = ("binary_sensor", "_area_10_triggered_bs")


async def refresh(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Poll the panel now instead of waiting for the update interval."""
    await hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR].async_refresh()
    await hass.async_block_till_done()


def entity_id_for(
    hass: HomeAssistant, entry: ConfigEntry, entity: tuple[str, str]
) -> str:
    """Entity ID of an entity given by platform and unique ID suffix."""
    platform, suffix = entity
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}{suffix}"
    )
    assert entity_id is not None, entity
    return entity_id


def state_of(hass: HomeAssistant, entry: ConfigEntry, entity: tuple[str, str]) -> str:
    """Current state of an entity given by platform and unique ID suffix."""
    return hass.states.get(entity_id_for(hass, entry, entity)).state


def entity_summary(hass: HomeAssistant, entry: ConfigEntry) -> list[dict[str, Any]]:
    """Registry details and state of every entity of the entry, sorted."""
    registry = er.async_get(hass)
    summary = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(entity.entity_id)
        device_class = entity.original_device_class
        summary.append(
            {
                "platform": entity.domain,
                "unique_id": entity.unique_id.removeprefix(entry.entry_id),
                "original_name": entity.original_name,
                "device_class": str(device_class) if device_class else None,
                "state": state.state if state else None,
            }
        )
    return sorted(summary, key=lambda item: (item["platform"], item["unique_id"]))


async def test_entities_match_baseline(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A fresh setup creates exactly the entities in the baseline file."""
    summary = entity_summary(hass, entry)

    if os.environ.get("INIM_UPDATE_BASELINE"):
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps(summary, indent=2) + "\n")

    assert summary == json.loads(BASELINE.read_text())


async def call(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    service: str,
    entity: tuple[str, str],
) -> None:
    """Call a service on one entity and wait for it to finish."""
    await hass.services.async_call(
        domain,
        service,
        {"entity_id": entity_id_for(hass, entry, entity)},
        blocking=True,
    )


async def test_area_switch_arms_the_area(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Turning on an area switch arms only that area."""
    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.armed_areas() == [3]

    await refresh(hass, entry)
    assert state_of(hass, entry, AREA_3_SWITCH) == "on"
    # No mapped scenario matches a single armed area
    assert state_of(hass, entry, ALARM_PANEL) == "armed_custom_bypass"


@pytest.mark.parametrize(
    ("mode", "arming_mode"),
    [(c.MODE_AWAY, "total"), (c.MODE_STAY, "partial"), (c.MODE_INSTANT, "instant")],
)
async def test_area_switch_is_on_in_every_armed_mode(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    mode: int,
    arming_mode: str,
) -> None:
    """An area armed in any mode turns its switch on; the mode is an attribute."""
    panel.state.area_modes[2] = mode
    await refresh(hass, entry)
    state = hass.states.get(entity_id_for(hass, entry, AREA_3_SWITCH))
    assert state.state == "on"
    assert state.attributes["status_text"] == "armed"
    assert state.attributes["arming_mode"] == arming_mode

    await call(hass, entry, "switch", "turn_off", AREA_3_SWITCH)
    assert panel.state.armed_areas() == []
    await refresh(hass, entry)
    state = hass.states.get(entity_id_for(hass, entry, AREA_3_SWITCH))
    assert state.state == "off"
    assert state.attributes["arming_mode"] == "disarmed"


async def test_area_binary_sensors(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Live alarm, live tamper and alarm memory each have their own sensor."""
    for entity in (AREA_4_ALARM, AREA_4_TAMPER, AREA_4_ALARM_MEMORY):
        assert state_of(hass, entry, entity) == "off"

    panel.state.area_alarm = 1 << 3
    panel.state.area_tamper = 1 << 3
    panel.state.area_alarm_memory = 1 << 3 | 1 << 9
    panel.state.area_tamper_memory = 1 << 3
    await refresh(hass, entry)
    for entity in (AREA_4_ALARM, AREA_4_TAMPER, AREA_4_ALARM_MEMORY):
        assert state_of(hass, entry, entity) == "on"
    assert state_of(hass, entry, AREA_10_ALARM_MEMORY) == "on"
    memory = hass.states.get(entity_id_for(hass, entry, AREA_4_ALARM_MEMORY))
    assert memory.attributes["tamper_memory"] is True

    # The alarm ends: the memory stays until it is reset
    panel.state.area_alarm = 0
    panel.state.area_tamper = 0
    await refresh(hass, entry)
    assert state_of(hass, entry, AREA_4_ALARM) == "off"
    assert state_of(hass, entry, AREA_4_TAMPER) == "off"
    assert state_of(hass, entry, AREA_4_ALARM_MEMORY) == "on"


async def test_alarm_panel_is_triggered_only_during_an_alarm(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The live alarm triggers the panel; memory or tamper alone do not."""
    panel.state.area_alarm_memory = 1 << 9
    panel.state.area_tamper = 1 << 9
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == "disarmed"

    panel.state.area_alarm = 1 << 9
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == "triggered"

    panel.state.area_alarm = 0
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == "disarmed"


async def test_zone_switch_bypasses_the_zone(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Turning off a zone switch bypasses the zone through its physical slot."""
    await call(hass, entry, "switch", "turn_off", ZONE_36_SWITCH)
    assert panel.state.zone_included[35] is False  # logical 35 = slot 110

    await refresh(hass, entry)
    assert state_of(hass, entry, ZONE_36_SWITCH) == "off"


async def test_scenario_button(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Pressing a scenario button activates that scenario."""
    await call(hass, entry, "button", "press", SCENARIO_3_BUTTON)
    assert panel.state.armed_areas() == [1, 7]


async def test_alarm_panel_arm_away_and_disarm(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Arm away and disarm activate the mapped scenarios."""
    await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == "armed_away"

    await call(hass, entry, "alarm_control_panel", "alarm_disarm", ALARM_PANEL)
    assert panel.state.armed_areas() == []
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == "disarmed"


async def test_alarm_reset_service(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The reset service clears every area with an alarm or tamper memory."""
    panel.state.area_alarm_memory = 1 << 3 | 1 << 9
    panel.state.area_tamper_memory = 1 << 2
    panel.state.zone_memory[23] = True
    await refresh(hass, entry)
    assert state_of(hass, entry, AREA_4_ALARM_MEMORY) == "on"
    assert state_of(hass, entry, AREA_10_ALARM_MEMORY) == "on"

    await call(hass, entry, DOMAIN, SERVICE_ALARM_RESET, ALARM_PANEL)
    assert panel.state.area_alarm_memory == 0
    assert panel.state.area_tamper_memory == 0
    assert panel.state.zone_memory[23] is False
    # one reset per area: 3, 4 and 10
    assert [r.address for r in panel.requests if r.write] == [c.RAM_CMD_RESET] * 3

    await refresh(hass, entry)
    assert state_of(hass, entry, AREA_4_ALARM_MEMORY) == "off"
    assert state_of(hass, entry, AREA_10_ALARM_MEMORY) == "off"


async def test_zone_alarm_reaches_binary_sensor(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A zone in alarm turns its binary sensor on after the next poll."""
    assert state_of(hass, entry, ZONE_20_SENSOR) == "off"

    panel.state.zone_states[19] = c.ZONE_ALARM
    await refresh(hass, entry)
    assert state_of(hass, entry, ZONE_20_SENSOR) == "on"
