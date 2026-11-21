"""The alarm panel's modes, alarm_reset with several panels, removing an entry."""

from collections.abc import AsyncGenerator

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.inim_smartliving_alarm.const import (
    DOMAIN,
    SERVICE_ALARM_RESET,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import ALARM_PANEL, call, refresh, state_of

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    ("service", "option", "scenario", "state"),
    [
        # 0-based; scenarios 0 and 1 are mapped to arm away and disarm
        ("alarm_arm_home", "scenario_arm_home", 2, "armed_home"),
        ("alarm_arm_night", "scenario_arm_night", 3, "armed_night"),
        ("alarm_arm_vacation", "scenario_arm_vacation", 4, "armed_vacation"),
    ],
)
async def test_mapped_modes_activate_their_scenario(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    service: str,
    option: str,
    scenario: int,
    state: str,
) -> None:
    """Arm home, night and vacation activate the scenario mapped to them."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, option: scenario}
    )
    await hass.async_block_till_done()  # the entry reloads with the mapping

    await call(hass, entry, "alarm_control_panel", service, ALARM_PANEL)
    assert panel.state.active_scenario == scenario
    await refresh(hass, entry)
    assert state_of(hass, entry, ALARM_PANEL) == state


@pytest.fixture
async def second_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Another fake panel, for a second entry."""
    fake = FakePanel()
    await fake.start()
    yield fake
    await fake.stop()


async def test_alarm_reset_with_two_panels(
    hass: HomeAssistant, panel: FakePanel, second_panel: FakePanel
) -> None:
    """alarm_reset resets the targeted panel only, also after the other unloads."""
    first = await add_entry(hass, panel, name="First")
    second = await add_entry(hass, second_panel, name="Second")
    panel.state.area_alarm_memory = 1 << 3
    second_panel.state.area_alarm_memory = 1 << 3
    # The reset clears the areas the last poll showed with a memory
    await refresh(hass, first)
    await refresh(hass, second)

    await call(hass, second, DOMAIN, SERVICE_ALARM_RESET, ALARM_PANEL)
    assert second_panel.state.area_alarm_memory == 0
    assert panel.state.area_alarm_memory == 1 << 3
    assert not [r for r in panel.requests if r.write]

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    second_panel.state.area_alarm_memory = 1 << 3
    await refresh(hass, second)
    await call(hass, second, DOMAIN, SERVICE_ALARM_RESET, ALARM_PANEL)
    assert second_panel.state.area_alarm_memory == 0
    writes = [r.address for r in second_panel.requests if r.write]
    assert writes == [c.RAM_CMD_RESET] * 2  # one per call, never twice
    assert await hass.config_entries.async_unload(second.entry_id)


async def test_removing_an_entry_removes_its_repair_issue(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The unsupported-panel issue goes with the entry it belongs to."""
    entry = await add_entry(hass, panel)
    issue_id = f"unsupported_panel_{entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unsupported_panel",
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
