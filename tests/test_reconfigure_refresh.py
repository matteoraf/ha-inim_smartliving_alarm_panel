"""Reconfiguring an entry and reading the panel configuration again.

Both keep the entry, its options and the unique IDs of its entities, and
end with exactly one reload of the entry.
"""

from collections.abc import AsyncGenerator, Iterator
import socket
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.inim_smartliving_alarm as integration
from custom_components.inim_smartliving_alarm.const import (
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
    SERVICE_REREAD_PANEL_CONFIGURATION,
)

from .fake_panel import FakePanel, const as c

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def second_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Another fake panel, standing for the same panel at a new address."""
    fake = FakePanel(single_client=False)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def setups(entry: ConfigEntry) -> Iterator[AsyncMock]:
    """Count the setups of the entry after it was added (one per reload)."""
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as mock:
        yield mock


def closed_port() -> int:
    """A local TCP port nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Entity ID by unique ID of every entity of the entry."""
    registry = er.async_get(hass)
    return {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def registry_entry(
    hass: HomeAssistant, entry: ConfigEntry, platform: str, suffix: str
) -> er.RegistryEntry:
    """Registry entry of an entity given by platform and unique ID suffix."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}{suffix}"
    )
    assert entity_id is not None
    return registry.async_get(entity_id)


def panel_config(entry: ConfigEntry) -> dict[str, Any]:
    """The panel configuration stored in the entry."""
    return entry.data[DATA_INITIAL_PANEL_CONFIG]


async def reconfigure(
    hass: HomeAssistant, entry: ConfigEntry, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Run the reconfigure flow of the entry with the given input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"] == {"panel_name": "Test Panel"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


async def reread(hass: HomeAssistant, entry_id: str) -> None:
    """Call the re-read service for one entry and wait for the reload."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_REREAD_PANEL_CONFIGURATION,
        {"config_entry_id": entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_reconfigure_moves_entry_to_new_address(
    hass: HomeAssistant,
    entry: ConfigEntry,
    second_panel: FakePanel,
    setups: AsyncMock,
) -> None:
    """New host, port and PIN are stored and used; entities and options stay."""
    before = entity_ids(hass, entry)
    options = dict(entry.options)

    result = await reconfigure(
        hass,
        entry,
        {"host": second_panel.host, "port": second_panel.port, "pin": "4321"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setups.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == f"{second_panel.host}:{second_panel.port}"
    assert entry.data["port"] == second_panel.port
    assert entry.data["pin"] == "4321"
    assert entry.data["panel_name"] == "Test Panel"
    assert dict(entry.options) == options
    assert entity_ids(hass, entry) == before

    # Polls now reach the panel at the new address
    assert hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT].port == (
        second_panel.port
    )
    second_panel.state.zone_states[19] = c.ZONE_ALARM
    await hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR].async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(before[f"{entry.entry_id}_zone_20"]).state == "on"


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, setups: AsyncMock
) -> None:
    """An unreachable address shows an error and changes nothing."""
    data = dict(entry.data)

    result = await reconfigure(
        hass, entry, {"host": "127.0.0.1", "port": closed_port(), "pin": "1234"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert dict(entry.data) == data
    assert setups.call_count == 0

    # The user can correct the details in the same flow
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": panel.host, "port": panel.port, "pin": "1234"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Unchanged details still reload the entry once
    assert setups.call_count == 1
    assert dict(entry.data) == data
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_rejects_address_of_another_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The address of another configured panel is refused."""
    MockConfigEntry(domain=DOMAIN, unique_id="192.0.2.10:5004").add_to_hass(hass)
    data = dict(entry.data)

    result = await reconfigure(
        hass, entry, {"host": "192.0.2.10", "port": 5004, "pin": "1234"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert dict(entry.data) == data


async def test_reread_service_updates_configuration(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, setups: AsyncMock
) -> None:
    """Names changed on the panel reach the entry and its entities."""
    panel.state.labels[c.RAM_LABELS_SCENARIOS][2] = "Night"
    panel.state.labels[c.RAM_LABELS_ZONES][19] = "Kitchen window"
    before = entity_ids(hass, entry)
    options = dict(entry.options)

    await reread(hass, entry.entry_id)

    assert setups.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert panel_config(entry)["scenarios"]["names"][2] == "Night"
    assert panel_config(entry)["zones"]["zone_names"][19] == "Kitchen window"
    button = registry_entry(hass, entry, "button", "_scenario_button_2")
    assert button.original_name == "Activate 'Night'"
    assert entity_ids(hass, entry) == before
    assert dict(entry.options) == options


async def test_reread_unchanged_configuration_reloads_once(
    hass: HomeAssistant, entry: ConfigEntry, setups: AsyncMock
) -> None:
    """Without changes on the panel the entry is still reloaded once."""
    data = dict(entry.data)

    await reread(hass, entry.entry_id)

    assert setups.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert dict(entry.data) == data


async def test_reread_button(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, setups: AsyncMock
) -> None:
    """The button re-reads the configuration of its own entry."""
    panel.state.labels[c.RAM_LABELS_AREAS][0] = "Ground floor"
    button = registry_entry(hass, entry, "button", "_reread_panel_configuration")
    assert button.entity_category == "config"

    await hass.services.async_call(
        "button", "press", {"entity_id": button.entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert setups.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert panel_config(entry)["areas"]["names"][0] == "Ground floor"


async def test_reread_unknown_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """An entry ID that is not an entry of this integration is refused."""
    with pytest.raises(ServiceValidationError) as err:
        await reread(hass, "not-an-entry")
    assert err.value.translation_key == "entry_not_found"


async def test_reread_entry_not_loaded(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """An entry that is not loaded is refused."""
    other = MockConfigEntry(domain=DOMAIN, title="Other panel")
    other.add_to_hass(hass)

    with pytest.raises(ServiceValidationError) as err:
        await reread(hass, other.entry_id)
    assert err.value.translation_key == "entry_not_loaded"


async def test_reread_panel_unreachable(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel, setups: AsyncMock
) -> None:
    """If the panel cannot be reached the entry is left as it was."""
    data = dict(entry.data)
    await panel.stop()

    with pytest.raises(HomeAssistantError) as err:
        await reread(hass, entry.entry_id)

    assert err.value.translation_key == "cannot_connect"
    assert dict(entry.data) == data
    assert setups.call_count == 0
    assert entry.state is ConfigEntryState.LOADED


async def test_reread_incomplete_configuration(
    hass: HomeAssistant, entry: ConfigEntry, setups: AsyncMock
) -> None:
    """An incomplete configuration from the panel is not stored."""
    data = dict(entry.data)
    api = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    with (
        patch.object(
            api,
            "get_initial_panel_configuration",
            return_value={"errors": ["Failed zones."], "system_info": {}},
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await reread(hass, entry.entry_id)

    assert err.value.translation_key == "invalid_panel_configuration"
    assert dict(entry.data) == data
    assert setups.call_count == 0
