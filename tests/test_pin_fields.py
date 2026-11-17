"""The PIN and the board password in the setup, Reconfigure and the options.

They are password fields, never filled in with the stored values. In
Reconfigure and the options an empty field keeps the stored value. The user
code the PIN belongs to is shown by name, never the PIN.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import TextSelector
import pytest

import custom_components.inim_smartliving_alarm as integration
from custom_components.inim_smartliving_alarm import config_flow
from custom_components.inim_smartliving_alarm.const import (
    DATA_COORDINATOR,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.user_code import (
    user_code_info,
    user_code_placeholders,
)

from .fake_panel import FakePanel
from .test_config_flow import (
    OPTIONS,
    PASSWORD,
    USERNAME,
    board_form,
    form_defaults,
    g_panel,  # noqa: F401 (fixture)
    open_options,
    pick_items,
    submit,
    unload_all,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
def setups(entry: ConfigEntry) -> Iterator[AsyncMock]:
    """Count the setups of the entry after it was added (one per reload)."""
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as mock:
        yield mock


def field(result: dict, name: str) -> tuple[Any, Any]:
    """(key, validator) of a form field."""
    for key, validator in result["data_schema"].schema.items():
        if str(key) == name:
            return key, validator
    raise KeyError(name)


def assert_secret_field(result: dict, name: str) -> None:
    """A password field with nothing filled in."""
    key, validator = field(result, name)
    assert isinstance(validator, TextSelector)
    assert validator.config["type"] == "password"
    assert "suggested_value" not in (key.description or {})


# ------------------------------------------------------------------ setup


async def test_setup_pin_is_a_password_field(hass: HomeAssistant) -> None:
    """Typed as a password at setup, for both boards."""
    for board in ("smartlan_si", "smartlan_g"):
        flow_id = await board_form(hass, board)
        result = await hass.config_entries.flow.async_configure(flow_id)
        assert_secret_field(result, "pin")
        if board == "smartlan_g":
            assert_secret_field(result, "password")
        hass.config_entries.flow.async_abort(flow_id)


# ---------------------------------------------------------------- options


async def test_options_pin_field_is_empty(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The current PIN is not sent to the form."""
    result = await open_options(hass, entry)
    assert_secret_field(result, "pin")
    assert "pin" not in form_defaults(result)
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_options_change_the_pin(
    hass: HomeAssistant, entry: ConfigEntry, setups: AsyncMock
) -> None:
    """A PIN of another user code is checked, stored and used; one reload."""
    options = dict(entry.options)
    result = await open_options(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"pin": "4321"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["pin"] == "4321"
    for key in ("areas", "zones", "scenarios", "polling_interval"):
        assert entry.options[key] == options[key]
    assert setups.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_refresh()
    assert coordinator.last_update_success


@pytest.mark.parametrize(
    ("pin", "error"), [("9999", "invalid_pin"), ("12a4", "invalid_pin_format")]
)
async def test_options_refuse_a_wrong_pin(
    hass: HomeAssistant, entry: ConfigEntry, setups: AsyncMock, pin: str, error: str
) -> None:
    """The setup's message; nothing is saved, the other values are kept."""
    data, options = dict(entry.data), dict(entry.options)
    result = await open_options(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"pin": pin, "polling_interval": 45}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"pin": error}
    assert dict(entry.data) == data
    assert dict(entry.options) == options
    assert setups.call_count == 0
    assert form_defaults(result)["polling_interval"] == 45
    assert_secret_field(result, "pin")

    # The running entry was paused for the check, and goes on
    assert entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_options_pin_check_cannot_connect(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """An unreachable panel: the connection message on the PIN field."""
    result = await open_options(hass, entry)
    with patch.object(
        config_flow,
        "_validate_connection_and_fetch_initial_config",
        side_effect=ConnectionError("refused"),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"pin": "4321"}
        )
    assert result["errors"] == {"pin": "cannot_connect"}
    assert entry.data["pin"] == "1234"
    hass.config_entries.options.async_abort(result["flow_id"])


@pytest.mark.parametrize("pin", [None, "", "1234"])
async def test_options_keep_the_pin(
    hass: HomeAssistant, entry: ConfigEntry, setups: AsyncMock, pin: str | None
) -> None:
    """Empty, or the same PIN: not checked, not changed; options saved."""
    user_input: dict[str, Any] = {"polling_interval": 45}
    if pin is not None:
        user_input["pin"] = pin
    result = await open_options(hass, entry)
    with patch.object(
        config_flow, "_validate_connection_and_fetch_initial_config"
    ) as check:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    check.assert_not_called()
    assert entry.data["pin"] == "1234"
    assert entry.options["polling_interval"] == 45
    assert setups.call_count == 1


# ------------------------------------------------------------ reconfigure


async def open_reconfigure(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Start Reconfigure for an entry."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )


async def test_reconfigure_keeps_an_empty_pin(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Not filled in; left empty, the stored PIN is used and kept."""
    result = await open_reconfigure(hass, entry)
    assert_secret_field(result, "pin")
    assert field(result, "host")[0].description == {"suggested_value": panel.host}

    result = await submit(
        hass, result["flow_id"], {"host": panel.host, "port": panel.port}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["pin"] == "1234"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_smartlan_g_keeps_the_secrets(
    hass: HomeAssistant,
    g_panel: FakePanel,  # noqa: F811
) -> None:
    """SmartLAN/G: an empty board password and PIN keep the stored ones."""
    flow_id = await board_form(hass, "smartlan_g")
    g_input = {
        "host": g_panel.host,
        "port": g_panel.port,
        "username": USERNAME,
        "password": PASSWORD,
        "pin": "1234",
        "panel_name": "Panel G",
    }
    await pick_items(hass, flow_id, await submit(hass, flow_id, g_input))
    entry: ConfigEntry = (await submit(hass, flow_id, OPTIONS))["result"]

    result = await open_reconfigure(hass, entry)
    assert_secret_field(result, "password")
    assert_secret_field(result, "pin")
    assert field(result, "username")[0].description == {"suggested_value": USERNAME}

    result = await submit(
        hass,
        result["flow_id"],
        {
            "host": g_panel.host,
            "port": g_panel.port,
            "username": USERNAME,
            "password": "",
            "pin": "",
        },
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["password"] == PASSWORD
    assert entry.data["pin"] == "1234"
    assert entry.state is ConfigEntryState.LOADED
    await unload_all(hass)


async def test_reconfigure_error_does_not_return_the_pin(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Shown again after a refused PIN: the PIN typed is not sent back."""
    result = await open_reconfigure(hass, entry)
    result = await submit(
        hass,
        result["flow_id"],
        {"host": panel.host, "port": panel.port, "pin": "9999"},
    )
    assert result["errors"] == {"base": "invalid_pin"}
    assert_secret_field(result, "pin")
    assert entry.data["pin"] == "1234"
    hass.config_entries.flow.async_abort(result["flow_id"])


# -------------------------------------------------------------- user code


def user_code_state(hass: HomeAssistant, entry: ConfigEntry):
    """State of the User code sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_user_code"
    )
    assert entity_id is not None
    return hass.states.get(entity_id)


async def test_user_code_sensor(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The fake's PIN 1234 is code 1, "Master", a master code of all areas."""
    registry_entry = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_user_code"
        )
    )
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    state = user_code_state(hass, entry)
    assert state.state == "Master"
    assert state.attributes["number"] == 1
    assert state.attributes["areas"] == [f"Area {n}" for n in range(1, 11)]
    assert state.attributes["type"] == "master"
    assert "1234" not in str(state.attributes)


async def test_general_settings_name_the_user_code(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """General settings say which user code commands are sent with."""
    result = await open_options(hass, entry)
    assert result["description_placeholders"] == {
        "user_code": "1 - Master",
        "user_areas": ", ".join(f"Area {n}" for n in range(1, 11)),
    }
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_user_code_follows_a_pin_change(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """After the change to 4321 (code 2, areas 1-7) both show the new code."""
    result = await open_options(hass, entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"pin": "4321"}
    )
    await hass.async_block_till_done()

    state = user_code_state(hass, entry)
    assert state.state == "User 2"
    assert state.attributes["number"] == 2
    assert state.attributes["areas"] == [f"Area {n}" for n in range(1, 8)]
    assert state.attributes["type"] == "standard"
    result = await open_options(hass, entry)
    assert result["description_placeholders"]["user_code"] == "2 - User 2"
    hass.config_entries.options.async_abort(result["flow_id"])


def test_pin_matching_no_user_code() -> None:
    """Nothing to name: the sensor is unknown, the form shows "-"."""
    panel = SimpleNamespace(user_code_index=None, user_code=None)
    assert user_code_info(panel, {}) is None
    assert user_code_placeholders(panel, {}) == {
        "user_code": "-",
        "user_areas": "-",
    }
    assert user_code_placeholders(None, {})["user_code"] == "-"


def test_user_code_without_a_name() -> None:
    """A code without a name on the panel is "Code N"."""
    code = SimpleNamespace(areas=frozenset({2}), code_type=None)
    panel = SimpleNamespace(user_code_index=4, user_code=code)
    info = user_code_info(panel, {"code_names": {"names": ["Master"]}})
    assert (info.name, info.number, info.areas, info.code_type) == (
        "Code 5",
        5,
        ["Area 2"],
        None,
    )
