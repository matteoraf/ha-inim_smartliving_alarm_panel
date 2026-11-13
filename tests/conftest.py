"""Shared fixtures for the Inim SmartLiving Alarm tests."""

from collections.abc import AsyncGenerator

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.inim_smartliving_alarm.const import DOMAIN

from .fake_panel import FakePanel


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let the Home Assistant test instance load integrations from custom_components/."""
    return


@pytest.fixture
async def fake_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake SmartLiving 1050 on SmartLAN/SI, listening on a free local port."""
    panel = FakePanel()
    await panel.start()
    yield panel
    await panel.stop()


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Fake panel accepting a new connection while the previous one closes."""
    fake = FakePanel(single_client=False)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def entry(hass: HomeAssistant, panel: FakePanel) -> AsyncGenerator[ConfigEntry]:
    """The integration added through the config flow, unloaded afterwards."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "panel_name": "Test Panel",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "initial_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "polling_interval": 30,
            "limit_areas": 10,
            "limit_zones": 50,
            "limit_scenarios": 30,
            "event_log_size": 5,
            "reader_names": "",
            "scenario_arm_away": "0",
            "scenario_arm_home": "none",
            "scenario_arm_night": "none",
            "scenario_arm_vacation": "none",
            "scenario_disarm": "1",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    config_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert config_entry.state is ConfigEntryState.LOADED
    yield config_entry

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
