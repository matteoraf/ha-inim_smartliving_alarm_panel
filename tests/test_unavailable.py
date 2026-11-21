"""The panel out of reach: at setup, while polling, and back again."""

import logging
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm import (
    _ACTION_DESCRIPTIONS as ACTION_DESCRIPTIONS,
)
from custom_components.inim_smartliving_alarm.const import DATA_COORDINATOR, DOMAIN

from .conftest import wait_until
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
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed poll makes the entities unavailable; the next good one restores them."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE

    panel.refuse_connections = True
    panel.drop_client()
    caplog.clear()
    for _ in range(3):
        await refresh(hass, entry)
    assert coordinator.last_update_success is False
    assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
    # Home Assistant reports the first failure once; nothing more per poll
    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name.endswith(".coordinator") and record.levelno >= logging.WARNING
    ]
    assert len(reported) == 1
    assert reported[0].startswith("Error fetching")

    panel.refuse_connections = False
    await refresh(hass, entry)
    assert coordinator.last_update_success is True
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE


async def test_failed_platform_setup_keeps_nothing(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """If the platforms fail to set up, the client is closed and nothing stays."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform failed"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert DOMAIN not in hass.data
    await wait_until(lambda: not panel.has_client)  # the connection was closed

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_last_unload_leaves_no_action_descriptions(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The action descriptions kept for the loaded panels go with the last one."""
    assert ACTION_DESCRIPTIONS in hass.data
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data
    assert ACTION_DESCRIPTIONS not in hass.data
