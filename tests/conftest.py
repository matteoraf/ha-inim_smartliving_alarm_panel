"""Shared fixtures for the Inim SmartLiving Alarm tests."""

from collections.abc import AsyncGenerator

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.inim_smartliving_alarm import api
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


@pytest.fixture(autouse=True)
def no_command_settle(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake panel applies commands at once: no settle time after them.

    Real-panel tests keep it, as the real panel needs it.
    """
    if request.node.get_closest_marker("real_panel") is None:
        monkeypatch.setattr(api, "COMMAND_SETTLE", 0)


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Fake panel accepting one client at a time, like the SmartLAN/SI board.

    The integration holds one connection per entry and closes it on unload;
    the config flow closes its own before the entry connects.
    """
    fake = FakePanel()
    await fake.start()
    yield fake
    await fake.stop()


async def add_entry(
    hass: HomeAssistant,
    panel: FakePanel,
    *,
    limit_areas: int | None = 10,
    limit_zones: int | None = 50,
) -> ConfigEntry:
    """Add the integration through the config flow and wait until it is loaded.

    A limit of None is left out of the form, so the proposed default applies.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "smartlan_si"

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

    limits = {"limit_areas": limit_areas, "limit_zones": limit_zones}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "polling_interval": 30,
            **{key: value for key, value in limits.items() if value is not None},
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
    return config_entry


@pytest.fixture
async def entry(hass: HomeAssistant, panel: FakePanel) -> AsyncGenerator[ConfigEntry]:
    """The integration added through the config flow, unloaded afterwards."""
    config_entry = await add_entry(hass, panel)
    yield config_entry

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
