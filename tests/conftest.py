"""Shared fixtures for the Inim SmartLiving Alarm tests."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Iterator
import socket
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.inim_smartliving_alarm import api
from custom_components.inim_smartliving_alarm.const import DOMAIN
from custom_components.inim_smartliving_alarm.coordinator import (
    InimDataUpdateCoordinator,
)
from custom_components.inim_smartliving_alarm.protocol.commands import CommandResult
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.transport import (
    base as transport_base,
)

from .fake_panel import FakePanel


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let the Home Assistant test instance load integrations from custom_components/."""
    return


@pytest.fixture
async def fake_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake SmartLiving 1050 on SmartLAN/SI, listening on a free local port.

    It accepts one client at a time, like the SmartLAN/SI board: the
    integration holds one connection per entry and closes it on unload; the
    config flow closes its own before the entry connects.
    """
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


@pytest.fixture(autouse=True)
def no_scheduled_polling(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries read the panel only when a test asks (a refresh, a command).

    Polling every second would add reads to any test that runs longer, so
    request counts and logs would depend on timing. Tests marked
    scheduled_polling keep the schedule and move its clock by hand.
    """
    if request.node.get_closest_marker("scheduled_polling") is None:
        monkeypatch.setattr(
            InimDataUpdateCoordinator, "async_start_polling", lambda self: None
        )


@pytest.fixture
def panel(fake_panel: FakePanel) -> FakePanel:
    """The fake panel (fake_panel), under the name the entry tests use."""
    return fake_panel


@pytest.fixture(autouse=True)
def fresh_reconnect_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts without the pauses earlier tests' connections left."""
    monkeypatch.setattr(transport_base, "_last_close", {})


def label_block(names: Iterable[str]) -> str:
    """A label table as stored: 16 bytes per name, space padded, as hex."""
    return b"".join(name.encode("ascii").ljust(16) for name in names).hex()


async def accepted(command: Awaitable[CommandResult]) -> bool:
    """Whether the panel accepted a command (False if it was not sent)."""
    try:
        return (await command).ok
    except (transport_base.TransportError, ProtocolError, api.NotReadyError):
        return False


async def wait_until(condition: Callable[[], bool], seconds: float = 2.0) -> None:
    """Wait until a condition holds; fail after that many seconds."""
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110 - the fake's state has no event
            await asyncio.sleep(0.005)


@pytest.fixture
def refused_port() -> Iterator[int]:
    """A local TCP port that refuses connections for the whole test.

    The socket stays bound without listening, so a connection is refused
    and no other socket can take the port meanwhile.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


ALL = range(1, 1000)  # every item the form offers


async def add_entry(
    hass: HomeAssistant,
    panel: FakePanel,
    *,
    areas: Iterable[int] | None = ALL,
    zones: Iterable[int] | None = range(1, 51),
    scenarios: Iterable[int] | None = ALL,
    name: str = "Test Panel",
    pin: str = "1234",
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
            "pin": pin,
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
