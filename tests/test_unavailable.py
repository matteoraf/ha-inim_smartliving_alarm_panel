"""The panel out of reach: at setup, while polling, and back again."""

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.const import DATA_COORDINATOR, DOMAIN

from .fake_panel import FakePanel
from .test_init_baseline import ALARM_PANEL, refresh, state_of

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_setup_is_retried_while_the_panel_is_out_of_reach(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Setup fails for a retry, keeps nothing, and loads once the panel answers."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    panel.refuse_connections = True

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert panel.rejected_connections > 0

    panel.refuse_connections = False
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_entities_unavailable_while_polls_fail(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A failed poll makes the entities unavailable; the next good one restores them."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE

    panel.refuse_connections = True
    panel.drop_client()
    await refresh(hass, entry)
    assert coordinator.last_update_success is False
    assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE

    panel.refuse_connections = False
    await refresh(hass, entry)
    assert coordinator.last_update_success is True
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
