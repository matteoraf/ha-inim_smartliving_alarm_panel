"""Shared fixtures for the Inim SmartLiving Alarm tests."""

from collections.abc import AsyncGenerator, Iterable
from typing import Any

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


ALL = range(1, 1000)  # every item the form offers


async def add_entry(
    hass: HomeAssistant,
    panel: FakePanel,
    *,
    areas: Iterable[int] | None = ALL,
    zones: Iterable[int] | None = range(1, 51),
    scenarios: Iterable[int] | None = ALL,
    name: str = "Test Panel",
) -> ConfigEntry:
    """Add the integration through the config flow and wait until it is loaded.

    The areas, zones and scenarios ticked are those of the form among these
    numbers; None keeps what the form proposes.
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
            "panel_name": name,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "items"

    chosen = {"areas": areas, "zones": zones, "scenarios": scenarios}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        items_form(result, chosen),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "initial_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "polling_interval": 30,
            "event_log_size": 5,
            "scenario_arm_away": "0",
            "scenario_arm_home": "none",
            "scenario_arm_night": "none",
            "scenario_arm_vacation": "none",
            "scenario_disarm": "1",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    config_entry = result["result"]
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry


def items_field(result: dict, key: str) -> tuple[Any, Any]:
    """(field, selector) of the areas, zones or scenarios list, in its section."""
    for marker, value in result["data_schema"].schema.items():
        if str(marker) == key:
            for field, selector in value.schema.schema.items():
                if str(field) == key:
                    return field, selector
    raise KeyError(key)


def offered(result: dict, key: str) -> list[int]:
    """The numbers a form's areas, zones or scenarios list offers."""
    options = items_field(result, key)[1].config["options"]
    return [int(option["value"]) for option in options]


def proposed(result: dict, key: str) -> list[int]:
    """The numbers a form's areas, zones or scenarios list has ticked."""
    field = items_field(result, key)[0]
    return [int(value) for value in field.description["suggested_value"]]


def tick(result: dict, key: str, numbers: Iterable[int] | None) -> list[str]:
    """Form values ticking these numbers among those offered (None: proposed)."""
    if numbers is None:
        return [str(number) for number in proposed(result, key)]
    wanted = set(numbers)
    return [str(number) for number in offered(result, key) if number in wanted]


def items_form(result: dict, chosen: dict[str, Iterable[int] | None]) -> dict:
    """Input of an items form: one section per kind (None: as proposed)."""
    return {key: {key: tick(result, key, numbers)} for key, numbers in chosen.items()}


@pytest.fixture
async def entry(hass: HomeAssistant, panel: FakePanel) -> AsyncGenerator[ConfigEntry]:
    """The integration added through the config flow, unloaded afterwards."""
    config_entry = await add_entry(hass, panel)
    yield config_entry

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
