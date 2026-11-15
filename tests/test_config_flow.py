"""Setup flow: board choice, panel checks and the config entry version."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry, discovery
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.config_flow import (
    InimAlarmConfigFlow,
)
from custom_components.inim_smartliving_alarm.const import DOMAIN
from custom_components.inim_smartliving_alarm.discovery import DiscoveryError
from custom_components.inim_smartliving_alarm.protocol.errors import (
    DecryptionError,
    ProtocolError,
)

from .fake_panel import FakePanel
from .fake_panel.discovery import DiscoveryResponder
from .fake_panel.server import TRANSPORT_G
from .test_discovery import free_udp_port

pytestmark = pytest.mark.usefixtures("socket_enabled")

USERNAME = "installer"
PASSWORD = "s3cret"
OPTIONS = {
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
}


@pytest.fixture
async def g_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake 1050 behind SmartLAN/G (one client at a time)."""
    fake = FakePanel(transport=TRANSPORT_G, username=USERNAME, password=PASSWORD)
    await fake.start()
    yield fake
    await fake.stop()


async def board_form(hass: HomeAssistant, board: str) -> str:
    """Start a setup, pick the board from the menu; return the flow ID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["discover", "smartlan_si", "smartlan_g"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": board}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == board
    return result["flow_id"]


async def submit(hass: HomeAssistant, flow_id: str, data: dict[str, Any]) -> dict:
    """Submit a step and wait for anything it triggers."""
    result = await hass.config_entries.flow.async_configure(flow_id, data)
    await hass.async_block_till_done()
    return result


def si_input(panel: FakePanel, pin: str = "1234") -> dict[str, Any]:
    """Connection form input for the SmartLAN/SI fake."""
    return {"host": panel.host, "port": panel.port, "pin": pin, "panel_name": "Panel"}


async def unload_all(hass: HomeAssistant) -> None:
    """Unload every entry (frees the fake panel's only connection)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------- full setups


async def test_setup_smartlan_si(hass: HomeAssistant, panel: FakePanel) -> None:
    """SmartLAN/SI: menu, connection, options; the entry is version 1.2."""
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    assert result["step_id"] == "initial_options"
    result = await submit(hass, flow_id, OPTIONS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry: ConfigEntry = result["result"]
    assert (entry.version, entry.minor_version) == (1, 3)
    assert entry.data["board"] == "smartlan_si"
    assert "username" not in entry.data
    assert entry.state is ConfigEntryState.LOADED
    await unload_all(hass)


async def test_setup_without_scenario_mappings(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Without a mapped Disarm scenario the alarm panel loads, state unknown."""
    flow_id = await board_form(hass, "smartlan_si")
    await submit(hass, flow_id, si_input(panel))
    unmapped = {
        key: "none" if key.startswith("scenario_") else value
        for key, value in OPTIONS.items()
    }
    result = await submit(hass, flow_id, unmapped)

    entry: ConfigEntry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get("alarm_control_panel.panel")
    assert state is not None
    assert state.state == "unknown"
    await unload_all(hass)


async def test_setup_smartlan_g(hass: HomeAssistant, g_panel: FakePanel) -> None:
    """SmartLAN/G: the credentials are stored and the entry loads encrypted."""
    flow_id = await board_form(hass, "smartlan_g")
    result = await submit(
        hass,
        flow_id,
        {
            "host": g_panel.host,
            "port": g_panel.port,
            "username": USERNAME,
            "password": PASSWORD,
            "pin": "1234",
            "panel_name": "Panel G",
        },
    )
    assert result["step_id"] == "initial_options"
    result = await submit(hass, flow_id, OPTIONS)

    entry: ConfigEntry = result["result"]
    assert entry.data["board"] == "smartlan_g"
    assert entry.data["username"] == USERNAME
    assert entry.data["password"] == PASSWORD
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("alarm_control_panel.panel_g") is not None
    assert g_panel.protocol_errors == []
    await unload_all(hass)


# ------------------------------------------------------------- panel checks


@pytest.mark.parametrize(
    ("pin", "error"), [("9999", "invalid_pin"), ("12a4", "invalid_pin_format")]
)
async def test_pin_is_checked(
    hass: HomeAssistant, panel: FakePanel, pin: str, error: str
) -> None:
    """A PIN matching no user code, or not 1-6 digits, is refused."""
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel, pin))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Corrected in the same flow
    result = await submit(hass, flow_id, si_input(panel))
    assert result["step_id"] == "initial_options"


async def test_firmware_below_5_is_refused(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Firmware 4.x cannot be set up."""
    panel.state.firmware = "4.20 01050  "
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    assert result["errors"] == {"base": "unsupported_firmware"}


@pytest.mark.parametrize(
    ("firmware", "warning"),
    [
        ("5.07 01050  ", "firmware 5.07 is not supported yet"),
        ("6.07 00515  ", "SmartLiving 515 is not supported yet"),
    ],
)
async def test_firmware_5_and_other_models_are_accepted(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    firmware: str,
    warning: str,
) -> None:
    """Firmware 5.x and other models (rebranded too) only get a warning."""
    panel.state.firmware = firmware
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    assert result["step_id"] == "initial_options"
    assert warning in caplog.text
    assert "handling the panel as a SmartLiving 1050" in caplog.text


async def test_undecryptable_reply_means_wrong_credentials(
    hass: HomeAssistant, g_panel: FakePanel
) -> None:
    """On SmartLAN/G a reply that does not decrypt points at the credentials."""
    flow_id = await board_form(hass, "smartlan_g")
    with patch.object(
        SmartLivingPanel,
        "identify",
        side_effect=DecryptionError("reply cannot be decrypted"),
    ):
        result = await submit(
            hass,
            flow_id,
            {
                "host": g_panel.host,
                "port": g_panel.port,
                "username": USERNAME,
                "password": "wrong",
                "pin": "1234",
            },
        )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_unexpected_smartlan_g_reply(
    hass: HomeAssistant, g_panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A /G reply that decrypts but has the wrong length asks for a report."""
    g_panel.g_reply_checksum = True  # a board adding the panel's checksum
    flow_id = await board_form(hass, "smartlan_g")
    data = {
        "host": g_panel.host,
        "port": g_panel.port,
        "username": USERNAME,
        "password": PASSWORD,
        "pin": "1234",
    }
    result = await submit(hass, flow_id, data)
    assert result["errors"] == {"base": "invalid_board_reply"}
    assert "expected 12 bytes, got 13" in caplog.text

    # The same flow goes on once the board answers as expected
    g_panel.g_reply_checksum = False
    result = await submit(hass, flow_id, data)
    assert result["step_id"] == "initial_options"


async def test_unexpected_smartlan_si_reply(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """On SmartLAN/SI a reply of the wrong form stays a generic panel error."""
    flow_id = await board_form(hass, "smartlan_si")
    with patch.object(
        SmartLivingPanel, "identify", side_effect=ProtocolError("expected 13 bytes")
    ):
        result = await submit(hass, flow_id, si_input(panel))
    assert result["errors"] == {"base": "invalid_panel_response"}


async def test_flows_do_not_share_data() -> None:
    """Each flow keeps its own data between the steps."""
    assert InimAlarmConfigFlow()._flow_data is not InimAlarmConfigFlow()._flow_data


# --------------------------------------------------------------- reconfigure


async def test_reconfigure_smartlan_g(hass: HomeAssistant, g_panel: FakePanel) -> None:
    """A SmartLAN/G entry is reconfigured with its credentials; PIN checked."""
    flow_id = await board_form(hass, "smartlan_g")
    g_input = {
        "host": g_panel.host,
        "port": g_panel.port,
        "username": USERNAME,
        "password": PASSWORD,
        "pin": "1234",
        "panel_name": "Panel G",
    }
    await submit(hass, flow_id, g_input)
    entry: ConfigEntry = (await submit(hass, flow_id, OPTIONS))["result"]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    fields = {str(key) for key in result["data_schema"].schema}
    assert fields == {"host", "port", "username", "password", "pin"}

    reconf = {key: g_input[key] for key in fields}
    result = await submit(hass, result["flow_id"], {**reconf, "pin": "9999"})
    assert result["errors"] == {"base": "invalid_pin"}
    result = await submit(hass, result["flow_id"], {**reconf, "pin": "4321"})
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["pin"] == "4321"
    assert entry.data["board"] == "smartlan_g"
    assert entry.state is ConfigEntryState.LOADED
    await unload_all(hass)


# ----------------------------------------------------------------- migration


async def test_migration_adds_the_board(hass: HomeAssistant) -> None:
    """Entries from before 1.2 are SmartLAN/SI entries (and then go to 1.3)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        data={"host": "192.0.2.10", "port": 5004, "pin": "1234"},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 3
    assert entry.data == {
        "host": "192.0.2.10",
        "port": 5004,
        "pin": "1234",
        "board": "smartlan_si",
    }


async def test_newer_major_version_is_not_loaded(hass: HomeAssistant) -> None:
    """An entry from a later major version is refused, not changed."""
    entry = MockConfigEntry(domain=DOMAIN, version=2, minor_version=1, data={})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 2


# ------------------------------------------------------------ network search


async def search(
    hass: HomeAssistant, responders: list[DiscoveryResponder]
) -> dict[str, Any]:
    """Choose "search" in the menu; the fake responders answer on 127.0.0.1."""
    si = next((r for r in responders if r.panel.transport != TRANSPORT_G), None)
    g = next((r for r in responders if r.panel.transport == TRANSPORT_G), None)
    si_ports = (si.port, si.reply_port) if si else (free_udp_port(), free_udp_port())
    g_ports = (g.port, g.reply_port) if g else (free_udp_port(), free_udp_port())

    async def fake_discover() -> list:
        return await discovery.discover(
            0.3, target="127.0.0.1", si_ports=si_ports, g_ports=g_ports
        )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(
        "custom_components.inim_smartliving_alarm.config_flow.discover",
        fake_discover,
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )


async def responder_for(panel: FakePanel, **kwargs) -> DiscoveryResponder:
    """A started discovery responder for a fake panel."""
    responder = DiscoveryResponder(panel, free_udp_port(), **kwargs)
    await responder.start()
    return responder


async def test_search_finds_and_prefills_smartlan_si(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """A found SmartLAN/SI board opens its form with host and port filled in."""
    responder = await responder_for(panel)
    try:
        result = await search(hass, [responder])
    finally:
        await responder.stop()

    assert result["step_id"] == "discover"
    assert result["description_placeholders"] == {"count": "1"}
    choices = result["data_schema"].schema["found_board"].container
    key, label = next(iter(choices.items()))
    assert label == f"127.0.0.1:{panel.port} - SmartLAN/SI, MAC 00:08:dc:12:34:56"

    result = await submit(hass, result["flow_id"], {"found_board": key})
    assert result["step_id"] == "smartlan_si"
    suggested = {
        str(field): field.description["suggested_value"]
        for field in result["data_schema"].schema
        if field.description and "suggested_value" in field.description
    }
    assert suggested["host"] == "127.0.0.1"
    assert suggested["port"] == panel.port

    result = await submit(
        hass, result["flow_id"], {**si_input(panel), "panel_name": "Found"}
    )
    assert result["step_id"] == "initial_options"
    entry = (await submit(hass, result["flow_id"], OPTIONS))["result"]
    assert entry.data["board"] == "smartlan_si"
    await unload_all(hass)


async def test_search_prefills_smartlan_g(
    hass: HomeAssistant, g_panel: FakePanel
) -> None:
    """A SmartLAN/G board also gives its panel's model, firmware and name."""
    responder = await responder_for(g_panel, panel_name="Casa al mare")
    try:
        result = await search(hass, [responder])
    finally:
        await responder.stop()

    choices = result["data_schema"].schema["found_board"].container
    key, label = next(iter(choices.items()))
    assert label == (
        f"127.0.0.1:{g_panel.port} - SmartLAN/G, SmartLiving 1050, "
        'firmware 6.07, "Casa al mare"'
    )
    result = await submit(hass, result["flow_id"], {"found_board": key})
    assert result["step_id"] == "smartlan_g"
    suggested = {
        str(field): field.description["suggested_value"]
        for field in result["data_schema"].schema
        if field.description and "suggested_value" in field.description
    }
    assert suggested["panel_name"] == "Casa al mare"
    assert suggested["port"] == g_panel.port


async def test_search_skips_configured_boards(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A board that is already set up is not offered again."""
    responder = await responder_for(panel)
    try:
        result = await search(hass, [responder])
    finally:
        await responder.stop()
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "discover_none"


async def test_search_finds_nothing(hass: HomeAssistant, panel: FakePanel) -> None:
    """Without answers the user can search again or enter the board by hand."""
    result = await search(hass, [])
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "discover_none"
    assert result["menu_options"] == ["discover", "smartlan_si", "smartlan_g"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await submit(hass, result["flow_id"], si_input(panel))
    assert result["step_id"] == "initial_options"


async def test_search_cannot_listen(hass: HomeAssistant) -> None:
    """Busy reply ports end like a search without answers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(
        "custom_components.inim_smartliving_alarm.config_flow.discover",
        side_effect=DiscoveryError("busy"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )
    assert result["step_id"] == "discover_none"
