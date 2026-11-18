"""The asynchronous panel API (api.py) against the fake SmartLiving 1050."""

import asyncio
from collections.abc import AsyncGenerator
import logging
import time

import pytest

from custom_components.inim_smartliving_alarm.api import (
    NotReadyError,
    SmartLivingPanel,
)
from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.errors import (
    UnsupportedPanelError,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    AreaMode,
    ZoneState,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import FakePanel, const as c
from .fake_panel.server import TRANSPORT_G

pytestmark = pytest.mark.usefixtures("socket_enabled")

ALL_AREAS = frozenset(range(1, 11))


@pytest.fixture
async def make_panel(
    fake_panel: FakePanel,
) -> AsyncGenerator:
    """API objects for the single-client fake panel, closed afterwards."""
    panels: list[SmartLivingPanel] = []

    def make(pin: str = "1234", **kwargs) -> SmartLivingPanel:
        transport = SmartLanSiTransport(
            fake_panel.host, fake_panel.port, reconnect_pause=0.01
        )
        panel = SmartLivingPanel(transport, pin, **kwargs)
        panels.append(panel)
        return panel

    yield make
    for panel in panels:
        await panel.close()


def written(fake: FakePanel) -> list[int]:
    """Registers written on the fake panel."""
    return [request.address for request in fake.requests if request.write]


# ---------------------------------------------------------- identification


async def test_identify_finds_the_user_code(make_panel) -> None:
    """Model string and the areas of the PIN's user code."""
    panel = make_panel("1234")
    firmware = await panel.identify()
    assert firmware.version == "6.07"
    assert firmware.model == "1050"
    assert panel.model is SL1050
    assert panel.user_code_found
    assert panel.user_areas == ALL_AREAS

    limited = make_panel("5678")
    await panel.close()  # the fake takes one client
    await limited.identify()
    assert limited.user_areas == {1}


async def test_unknown_pin_uses_every_area(
    make_panel, caplog: pytest.LogCaptureFixture
) -> None:
    """A PIN matching no code is not refused; the queries cover all areas."""
    panel = make_panel("9999")
    await panel.identify()
    assert not panel.user_code_found
    assert panel.user_areas == ALL_AREAS
    assert "does not match any user code" in caplog.text


async def test_unsupported_panel(make_panel, fake_panel: FakePanel) -> None:
    """Other models are refused, or handled as the fallback model."""
    fake_panel.state.firmware = "6.07 00999  "
    refused = make_panel()
    with pytest.raises(UnsupportedPanelError):
        await refused.identify()
    await refused.close()  # the fake takes one client

    fallback = make_panel(fallback_model=SL1050)
    firmware = await fallback.identify()
    assert firmware.model == "999"
    assert fallback.model is SL1050


async def test_methods_identify_on_first_use(make_panel) -> None:
    """No explicit identify() is needed."""
    panel = make_panel()
    status = await panel.read_status()
    assert panel.model is SL1050
    assert status.active_scenario is not None


# ----------------------------------------------------------- configuration


async def test_configuration(make_panel) -> None:
    """Labels through the RAM pointers, terminals, zones and scenarios."""
    config = await make_panel().read_configuration()

    assert config.model_string == b"6.07 01050  "
    assert config.area_labels.names == [f"Area {n}" for n in range(1, 11)]
    assert config.zone_labels.names[:50] == [f"Zone {n}" for n in range(1, 51)]
    assert len(config.zone_labels.names) == 100
    assert config.scenario_labels.names[0] == "Scenario 1"
    assert config.keypad_labels.names[:2] == ["Tast Zona Giorno", "Tast Garage"]
    assert config.physical_map.slot_of(35) == 110
    assert config.zones[19].areas == {1}
    assert config.scenarios[0].area_modes == dict.fromkeys(range(1, 8), ArmMode.AWAY)
    assert len(config.terminals) == 50


# ------------------------------------------------------------------ status


async def test_status_is_fully_decoded(make_panel, fake_panel: FakePanel) -> None:
    """Every arming mode, zone state and memory bit is decoded."""
    state = fake_panel.state
    state.area_modes[2] = c.MODE_STAY
    state.area_modes[3] = c.MODE_INSTANT
    state.zone_states[19] = c.ZONE_SHORT
    state.zone_states[20] = c.ZONE_TAMPER
    state.zone_included[23] = False
    state.area_alarm_memory = 0b11 << 8

    status = await make_panel().read_status()

    assert status.areas.modes[3] == AreaMode.STAY
    assert status.areas.modes[4] == AreaMode.INSTANT
    assert status.areas.alarm_memory == {9, 10}
    assert status.zone_states[19] == ZoneState.SHORT
    assert status.zone_states[20] == ZoneState.TAMPER
    assert not status.zone_inclusion.included[23]
    assert set(status.raw) == {
        reg.AREA_STATUS,
        reg.ZONE_STATE,
        reg.ZONE_INCLUSION,
        reg.ZONE_MEMORY,
        reg.ACTIVE_SCENARIO,
        reg.FAULTS,
    }
    assert status.panel_state is not None
    assert not status.panel_state.programming


async def test_active_scenario_covers_the_user_code_areas(
    make_panel, fake_panel: FakePanel
) -> None:
    """Scenarios that differ only in areas 9-10 are told apart (issue #15)."""
    state = fake_panel.state
    state.scenario_records[0] = bytes.fromhex("4444444444000000")  # all disarmed
    state.scenario_records[1] = bytes.fromhex("4444444411000000")  # 9-10 armed
    state.area_modes[8] = state.area_modes[9] = c.MODE_AWAY

    status = await make_panel("1234").read_status()

    assert status.active_scenario == 1
    query = next(r for r in fake_panel.requests if r.address == reg.ACTIVE_SCENARIO)
    assert query.instance == 0x03  # area mask high byte: areas 9-10
    assert query.length >> 8 == 0xFF


# ---------------------------------------------------------------- commands


async def test_commands(make_panel, fake_panel: FakePanel) -> None:
    """All arming modes, reset and bypass go through one connection."""
    panel = make_panel()
    state = fake_panel.state

    assert (await panel.arm_areas({3: ArmMode.STAY})).ok
    assert state.area_modes[2] == c.MODE_STAY
    assert (await panel.arm_areas({3: ArmMode.DISARM})).ok

    state.area_alarm_memory = 1 << 6
    assert (await panel.reset_alarm_memory(frozenset({7}))).ok
    assert state.area_alarm_memory == 0

    assert (await panel.bypass_zone(23, bypass=True)).ok
    assert state.zone_included[23] is False

    assert panel.transport.connections_opened == 1
    await panel.close()  # the fake takes one client

    refused = await make_panel("5678").arm_areas({3: ArmMode.AWAY})
    assert not refused.ok  # code 2 may only operate area 1


async def test_connection_held_after_an_accepted_command(
    make_panel, fake_panel: FakePanel
) -> None:
    """A poll waiting behind an accepted command reads after the settle time."""
    panel = make_panel(command_settle=0.3)
    await panel.identify()
    fake_panel.reply_delay = 0.05  # the poll queues while the command runs

    command = asyncio.create_task(panel.arm_areas({3: ArmMode.AWAY}))
    await asyncio.sleep(0.01)
    poll = asyncio.create_task(panel.read_status())
    result = await command
    accepted = time.monotonic()
    assert not poll.done()
    status = await poll
    assert result.ok
    assert status.areas.modes[3] == AreaMode.AWAY
    assert time.monotonic() - accepted >= 0.3 - 0.05  # settle, then the reads

    # A refused command does not hold the connection
    fake_panel.reply_delay = 0
    fake_panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    fake_panel.state.block_arming_when_not_ready = True
    started = time.monotonic()
    refused = await panel.arm_areas({1: ArmMode.AWAY})
    assert not refused.ok
    assert time.monotonic() - started < 0.3


async def test_scenario_not_ready(make_panel, fake_panel: FakePanel) -> None:
    """An open zone stops the scenario before anything is written."""
    fake_panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    panel = make_panel()
    with pytest.raises(NotReadyError) as err:
        await panel.activate_scenario(0)
    assert err.value.zones == {0}
    assert written(fake_panel) == []

    fake_panel.state.zone_states[0] = c.ZONE_REST
    assert (await panel.activate_scenario(0)).ok
    assert fake_panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]


async def test_pin_table_is_not_logged(
    make_panel, caplog: pytest.LogCaptureFixture
) -> None:
    """The PIN table read at start-up appears in the log by size only."""
    caplog.set_level(logging.DEBUG, logger="custom_components.inim_smartliving_alarm")
    await make_panel().identify()
    assert "<- 251 bytes, not logged" in caplog.text
    assert "<- 51 bytes, not logged" in caplog.text
    assert "01 02 03 04" not in caplog.text  # PIN 1234 as stored in the table


# ------------------------------------------------------------- SmartLAN/G


async def test_over_smartlan_g(socket_enabled: None) -> None:
    """The same API works over the encrypted SmartLAN/G transport."""
    fake = FakePanel(transport=TRANSPORT_G, username="user", password="pass")
    await fake.start()
    transport = SmartLanGTransport(
        fake.host, fake.port, username="user", password="pass"
    )
    panel = SmartLivingPanel(transport, "1234")
    try:
        config = await panel.read_configuration()
        assert config.area_labels.names[0] == "Area 1"
        assert panel.user_areas == ALL_AREAS
        assert (await panel.arm_areas({3: ArmMode.AWAY})).ok
        assert fake.state.armed_areas() == [3]
    finally:
        await panel.close()
        await fake.stop()
    assert fake.protocol_errors == []
    assert transport.connections_opened == 1
