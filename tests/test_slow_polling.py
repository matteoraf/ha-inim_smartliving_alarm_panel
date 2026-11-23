"""Slow polling mode, the connect attempt while the board may be in use,
the long-outage log line and the outage statistic."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
import logging
import socket
import time
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.inim_smartliving_alarm import (
    async_unload_entry,
    coordinator as coordinator_module,
)
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    CONF_BOARD,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.coordinator import LONG_OUTAGE
from custom_components.inim_smartliving_alarm.polling import (
    GROUPS,
    SLOW_POLLING_DURATION,
    SLOW_POLLING_INTERVAL,
    PollGroup,
    slow_intervals,
    slow_polling_minutes,
)
from custom_components.inim_smartliving_alarm.transport import base as transport_base
from custom_components.inim_smartliving_alarm.transport.base import (
    BRIEF_CONNECT_TIMEOUT,
    SHARED_IDLE_CLOSE,
    ConnectError,
    TransportError,
    TransportStats,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import add_entry
from .fake_panel import FakePanel
from .fake_panel.server import TRANSPORT_G
from .test_config_flow import open_options, unload_all
from .test_init_baseline import ALARM_PANEL, ZONE_36_SWITCH, entity_id_for, state_of
from .test_polling import FakeTime, advance, coordinator_of, groups_read

pytestmark = pytest.mark.usefixtures("socket_enabled")

SWITCH = ("switch", "_slow_polling")
EVERY_GROUP = {
    PollGroup.ZONES: 2,
    PollGroup.AREAS: 2,
    PollGroup.INCLUSION: 1,
    PollGroup.EVENTS: 1,
    PollGroup.FAULTS: 1,
}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    """The schedule's clock and timers, moved by hand."""
    fake = FakeTime()
    monkeypatch.setattr(coordinator_module, "_now", lambda: fake.now)
    monkeypatch.setattr(coordinator_module, "async_call_later", fake.call_later)
    return fake


async def set_switch(hass: HomeAssistant, entry: ConfigEntry, on: bool) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id_for(hass, entry, SWITCH)},
        blocking=True,
    )
    await hass.async_block_till_done()


async def disconnected(panel: FakePanel) -> bool:
    """Whether the panel's client has gone (shared, the connection closes
    SHARED_IDLE_CLOSE after the last request; the fake notices it shortly)."""
    for _ in range(200):
        if not panel.has_client:
            return True
        await asyncio.sleep(0.01)
    return False


async def set_options(hass: HomeAssistant, entry: ConfigEntry, **options) -> None:
    """Change options; the entry reloads."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.async_block_till_done()


# ---------------------------------------------------------------- options


def test_slow_intervals_and_minutes() -> None:
    """Every group at the one interval, within its range; the minutes too."""
    assert slow_intervals({}) == dict.fromkeys(GROUPS, SLOW_POLLING_INTERVAL.default)
    assert set(slow_intervals({"slow_polling_interval": 45}).values()) == {45}
    assert set(slow_intervals({"slow_polling_interval": 1}).values()) == {10}
    assert set(slow_intervals({"slow_polling_interval": "x"}).values()) == {30}
    assert slow_polling_minutes({}) == SLOW_POLLING_DURATION.default
    assert slow_polling_minutes({"slow_polling_duration": 0}) == 0
    assert slow_polling_minutes({"slow_polling_duration": 5000}) == 1440


async def test_polling_step_slow_fields(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The two fields are offered; only values set otherwise are stored."""
    result = await open_options(hass, entry, "polling")
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["slow_polling_interval"] == 30
    assert defaults["slow_polling_duration"] == 60
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**defaults, "slow_polling_duration": 0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["slow_polling_duration"] == 0
    assert "slow_polling_interval" not in entry.options


@pytest.mark.parametrize(
    ("option", "value"),
    [("slow_polling_interval", 5), ("slow_polling_duration", 1441)],
)
async def test_polling_step_slow_bounds(
    hass: HomeAssistant, entry: ConfigEntry, option: str, value: float
) -> None:
    result = await open_options(hass, entry, "polling")
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {option: value}
        )
    hass.config_entries.options.async_abort(result["flow_id"])


# ------------------------------------------------------------ the switch


@pytest.mark.scheduled_polling
async def test_slow_polling_mode(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """On: the connection closes at once; every group is read together at
    the slow interval, and the connection closed after each read. Off:
    every group is read at once, then at the group intervals."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await advance(hass, clock, 1)
    assert panel.has_client

    before = dt_util.utcnow()
    await set_switch(hass, entry, True)
    assert coordinator.slow_polling
    assert coordinator.panel.transport.shared
    assert await disconnected(panel)
    state = hass.states.get(entity_id_for(hass, entry, SWITCH))
    assert state.state == STATE_ON
    until = dt_util.parse_datetime(state.attributes["until"])
    assert timedelta(minutes=59) < until - before < timedelta(minutes=61)

    start = len(panel.requests)
    await advance(hass, clock, 29)
    assert groups_read(panel, start) == {}
    connections = panel.connections
    await advance(hass, clock, 1)
    assert groups_read(panel, start) == EVERY_GROUP
    assert panel.connections == connections + 1  # one connection per read
    assert await disconnected(panel)

    await set_switch(hass, entry, False)
    assert not coordinator.slow_polling
    assert not coordinator.panel.transport.shared
    assert (
        hass.states.get(entity_id_for(hass, entry, SWITCH)).attributes["until"] is None
    )
    start = len(panel.requests)
    await advance(hass, clock, 0)
    assert groups_read(panel, start) == EVERY_GROUP
    start = len(panel.requests)
    await advance(hass, clock, 1)
    assert groups_read(panel, start) == {PollGroup.ZONES: 2}
    assert panel.has_client  # kept open again
    await unload_all(hass)


async def test_a_command_in_slow_polling_mode(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The command and the read after it, then the connection is closed."""
    entry = await add_entry(hass, panel)
    await set_switch(hass, entry, True)
    assert await disconnected(panel)
    start = len(panel.requests)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id_for(hass, entry, ZONE_36_SWITCH)},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert any(request.write for request in panel.requests[start:])
    assert groups_read(panel, start)[PollGroup.AREAS] == 2  # the full read
    assert await disconnected(panel)
    await unload_all(hass)


async def test_slow_polling_mode_ends_by_itself(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = await add_entry(hass, panel)
    await set_options(hass, entry, slow_polling_duration=1)
    await set_switch(hass, entry, True)
    coordinator = coordinator_of(hass, entry)
    assert coordinator.slow_polling
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert coordinator.slow_polling
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert not coordinator.slow_polling
    assert not coordinator.panel.transport.shared
    assert state_of(hass, entry, SWITCH) == STATE_OFF
    await unload_all(hass)


async def test_slow_polling_mode_until_turned_off(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = await add_entry(hass, panel)
    await set_options(hass, entry, slow_polling_duration=0)
    await set_switch(hass, entry, True)
    state = hass.states.get(entity_id_for(hass, entry, SWITCH))
    assert state.state == STATE_ON
    assert state.attributes["until"] is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=3))
    await hass.async_block_till_done()
    assert coordinator_of(hass, entry).slow_polling
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_board_in_use_in_slow_polling_mode(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another client holds the board for a minute, as the installer
    software does while it reads the programming: the entities are
    unavailable meanwhile, the switch stays available, and they come back
    at the first read after it leaves, with the long-outage line."""
    entry = await add_entry(hass, panel)
    await set_switch(hass, entry, True)
    caplog.clear()
    panel.refuse_connections = True  # held from now, for 60 s
    with caplog.at_level(logging.INFO):
        for _ in range(2):  # the reads at 30 s and 60 s fail
            await advance(hass, clock, 30)
            assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
            assert state_of(hass, entry, SWITCH) == STATE_ON
        panel.refuse_connections = False
        await advance(hass, clock, 30)  # the retry waits the slow interval
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
    assert await disconnected(panel)
    lines = [
        r.getMessage() for r in caplog.records if "did not answer" in r.getMessage()
    ]
    assert len(lines) == 1
    await unload_all(hass)


@pytest.mark.parametrize(
    ("restored", "until", "on"),
    [
        (STATE_ON, timedelta(minutes=10), True),
        (STATE_ON, timedelta(minutes=-1), False),
        (STATE_ON, None, True),
        (STATE_OFF, None, False),
    ],
)
async def test_slow_polling_mode_after_a_restart(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    restored: str,
    until: timedelta | None,
    on: bool,
) -> None:
    """On again if it was on and its time has not passed; an expired mode
    is not turned on at all."""
    end = None if until is None else dt_util.utcnow() + until
    mock_restore_cache(
        hass,
        [
            State(
                "switch.test_panel_slow_polling_mode",
                restored,
                {"until": None if end is None else end.isoformat()},
            )
        ],
    )
    with caplog.at_level(logging.INFO):
        entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    assert coordinator.slow_polling is on
    assert state_of(hass, entry, SWITCH) == (STATE_ON if on else STATE_OFF)
    started = any("slow polling mode on" in r.getMessage() for r in caplog.records)
    assert started is on
    if on:
        assert coordinator.slow_polling_until == end
    await unload_all(hass)


async def test_unload_in_slow_polling_mode(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await set_switch(hass, entry, True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._unsub_slow_polling_end is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
    await hass.async_block_till_done()


async def test_no_slow_polling_mode_for_smartlan_g(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The board without a client limit gets neither the switch nor the
    fields."""
    si_entry = await add_entry(hass, panel)
    data, options = dict(si_entry.data), dict(si_entry.options)
    await unload_all(hass)
    g_panel = FakePanel(transport=TRANSPORT_G, username="user", password="pass")
    await g_panel.start()
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=si_entry.version,
            minor_version=si_entry.minor_version,
            title="G Panel",
            data={
                **data,
                CONF_BOARD: "smartlan_g",
                CONF_HOST: g_panel.host,
                CONF_PORT: g_panel.port,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            options=options,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert entities
        assert not [e for e in entities if e.unique_id.endswith("_slow_polling")]
        result = await open_options(hass, entry, "polling")
        fields = {str(key) for key in result["data_schema"].schema}
        assert fields == {spec.option for spec in GROUPS.values()}
        hass.config_entries.options.async_abort(result["flow_id"])
        await unload_all(hass)
    finally:
        await g_panel.stop()


# ------------------------------------------------------ the long outage


@pytest.mark.scheduled_polling
@pytest.mark.parametrize(("seconds", "logged"), [(30, True), (5, False)])
async def test_long_outage_logged_once(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
    seconds: int,
    logged: bool,
) -> None:
    """Reads that fail for longer than LONG_OUTAGE give one line when they
    go through again, with how long and how to re-read the configuration."""
    assert seconds > LONG_OUTAGE if logged else seconds < LONG_OUTAGE
    entry = await add_entry(hass, panel)
    await advance(hass, clock, 1)
    caplog.clear()
    with (
        caplog.at_level(logging.INFO),
        patch.object(SmartLivingPanel, "read_zones", side_effect=TransportError("x")),
    ):
        for _ in range(seconds):
            await advance(hass, clock, 1)
    for _ in range(10):
        await advance(hass, clock, 1)
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
    lines = [
        r.getMessage() for r in caplog.records if "did not answer" in r.getMessage()
    ]
    assert len(lines) == (1 if logged else 0)
    if logged:
        assert "Re-read panel configuration" in lines[0]
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_cannot_connect_names_another_app(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = await add_entry(hass, panel)
    await advance(hass, clock, 1)
    with patch.object(SmartLivingPanel, "read_zones", side_effect=ConnectError("x")):
        await advance(hass, clock, 1)
    assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
    assert any("in use by another app" in r.getMessage() for r in caplog.records)
    await unload_all(hass)


# -------------------------------------------------------------- transport


@pytest.fixture
def ignoring_listener() -> Iterator[tuple[str, int]]:
    """A listener whose queue is full: new connections are ignored, as a
    SmartLAN/SI board ignores them while another client holds it."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(0)
    address = server.getsockname()
    held = []
    for _ in range(2):
        client = socket.socket()
        client.setblocking(False)
        client.connect_ex(address)
        held.append(client)
    time.sleep(0.05)
    yield address
    for client in held:
        client.close()
    server.close()


@pytest.mark.parametrize(("brief", "attempts"), [(True, 1), (False, 3)])
async def test_brief_connect(
    ignoring_listener: tuple[str, int], brief: bool, attempts: int
) -> None:
    """One attempt with a short timeout, else every attempt; the error says
    the board may be in use."""
    host, port = ignoring_listener
    transport = SmartLanSiTransport(
        host, port, connect_timeout=0.3, reconnect_pause=0.0
    )
    transport.shared = brief
    assert BRIEF_CONNECT_TIMEOUT > 0.3  # the shorter of the two applies
    with pytest.raises(ConnectError, match="in use by another app"):
        await transport.read(bytes(8), 1)
    assert transport.stats.counters["connect_failures"] == attempts
    assert transport.stats.as_dict()["longest_outage_s"] is None  # not over yet


async def test_only_smartlan_si_may_be_in_use(
    ignoring_listener: tuple[str, int],
) -> None:
    """The SmartLAN/G board has no client limit: its failed connection
    only says it can't be reached."""
    host, port = ignoring_listener
    transport = SmartLanGTransport(
        host,
        port,
        username="user",
        password="pass",
        connect_timeout=0.3,
        connect_attempts=1,
    )
    with pytest.raises(ConnectError) as err:
        await transport.read(bytes(8), 1)
    assert "can't be reached" in str(err.value)
    assert "in use" not in str(err.value)


async def test_shared_connection_closes_when_idle(panel: FakePanel) -> None:
    """Shared, the connection closes shortly after the last request, for
    any caller; requests that follow each other share it."""
    transport = SmartLanSiTransport(panel.host, panel.port, reconnect_pause=0.0)
    transport.shared = True
    model = SmartLivingPanel(transport, "1234")
    await model.read_zones()
    await model.read_areas()  # right after: the same connection
    assert transport.connected
    assert panel.connections == 1
    await asyncio.sleep(SHARED_IDLE_CLOSE + 0.2)
    assert not transport.connected
    await model.read_zones()
    assert panel.connections == 2
    transport.shared = False
    await asyncio.sleep(SHARED_IDLE_CLOSE + 0.2)
    assert transport.connected  # not shared: kept open
    await transport.close()


def test_longest_outage() -> None:
    """From the first failure to the next answer; a cancelled request is
    not an outage."""
    stats = TransportStats()
    stats.exchange(b"\0" * 8, None, 0.1, keep_reply=False, error="cancelled")
    stats.exchange(b"\0" * 8, b"\0", 0.1, keep_reply=True)
    assert stats.as_dict()["longest_outage_s"] is None
    with patch("time.monotonic", return_value=100.0):
        stats.exchange(b"\0" * 8, None, 0.1, keep_reply=False, error="timeout")
    with patch("time.monotonic", return_value=103.0):
        stats.failing()  # still failing: the start stays
    with patch("time.monotonic", return_value=112.5):
        stats.answered()
    assert stats.as_dict()["longest_outage_s"] == 12.5
    with patch("time.monotonic", return_value=200.0):
        stats.failing()
    with patch("time.monotonic", return_value=201.0):
        stats.answered()
    assert stats.longest_outage == 12.5


def test_panel_shared() -> None:
    """The panel shares its transport's board, or holds it."""
    panel = SmartLivingPanel(SmartLanSiTransport("192.0.2.1", 5004), "1234")
    panel.set_shared(True)
    assert panel.transport.shared
    panel.set_shared(False)
    assert not panel.transport.shared


# ------------------------------------------------------- review follow-ups


@pytest.mark.scheduled_polling
async def test_one_connection_per_slow_read(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads take time: every group of a slow read still counts from the
    read's start, so the groups stay together, one wake-up and one
    connection per interval."""
    for name in (
        "read_zones",
        "read_areas",
        "read_zone_inclusion",
        "read_panel_state",
        "read_events",
    ):
        read = getattr(SmartLivingPanel, name)

        async def slow(self, *args, _read=read, **kwargs):
            clock.now += 0.05  # each read takes 50 ms
            return await _read(self, *args, **kwargs)

        monkeypatch.setattr(SmartLivingPanel, name, slow)
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await set_switch(hass, entry, True)
    assert await disconnected(panel)
    with patch.object(
        coordinator, "_read_due_groups", wraps=coordinator._read_due_groups
    ) as wake_ups:
        for cycle in range(1, 5):
            connections, start = panel.connections, len(panel.requests)
            await advance(hass, clock, 30)
            assert await disconnected(panel)
            assert wake_ups.call_count == cycle, cycle
            assert panel.connections == connections + 1, cycle
            assert groups_read(panel, start) == EVERY_GROUP, cycle
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_one_failed_slow_read_is_not_a_long_outage(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another app takes the board during one read: the next read, an
    interval later, goes through, and no long outage is logged."""
    entry = await add_entry(hass, panel)
    await set_switch(hass, entry, True)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        panel.refuse_connections = True
        await advance(hass, clock, 30)
        assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
        panel.refuse_connections = False
        await advance(hass, clock, 30)
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
    assert not [r for r in caplog.records if "did not answer" in r.getMessage()]
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_board_ignoring_connections(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board in use ignores the connection: in slow polling mode one
    attempt, cut at the brief timeout; the entities are unavailable until
    the board answers again."""
    monkeypatch.setattr(transport_base, "BRIEF_CONNECT_TIMEOUT", 0.2)
    entry = await add_entry(hass, panel)
    await set_switch(hass, entry, True)
    assert await disconnected(panel)
    attempts = 0

    async def ignored(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(3600)

    with patch.object(transport_base.asyncio, "open_connection", ignored):
        started = time.monotonic()
        await advance(hass, clock, 30)
        waited = time.monotonic() - started
    assert attempts == 1
    assert 0.2 <= waited < 1.5
    assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
    assert state_of(hass, entry, SWITCH) == STATE_ON
    await advance(hass, clock, 30)
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
    await unload_all(hass)


async def test_any_request_closes_in_slow_polling_mode(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """A request with no read after it (a refused command, a system
    report) also leaves the board free."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await set_switch(hass, entry, True)
    assert await disconnected(panel)
    await coordinator.panel.read_zones()
    assert panel.has_client
    assert await disconnected(panel)
    await unload_all(hass)


async def test_a_queued_end_after_turning_on_again(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """An end armed for an earlier time does not end the mode turned on
    again since."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await set_switch(hass, entry, True)
    earlier = coordinator.slow_polling_until
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await set_switch(hass, entry, True)  # turned on again: a later end
    assert coordinator.slow_polling_until != earlier
    await coordinator._async_end_slow_polling_at(earlier)
    assert coordinator.slow_polling
    await coordinator._async_end_slow_polling_at(coordinator.slow_polling_until)
    assert not coordinator.slow_polling
    await unload_all(hass)


async def test_not_turned_on_once_stopped(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Once the entry is unloading, neither a turn-on nor a restore turns
    the mode on."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await coordinator.async_stop_polling()
    await coordinator.async_set_slow_polling(True)
    await coordinator.async_restore_slow_polling(None)
    assert not coordinator.slow_polling
    assert not coordinator.panel.transport.shared
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_end_set_again_when_unloading_fails(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """The stop cancels the end; when the platforms stay, it is set again,
    and the mode still ends on time."""
    entry = await add_entry(hass, panel)
    await set_options(hass, entry, slow_polling_duration=1)
    coordinator = coordinator_of(hass, entry)
    await set_switch(hass, entry, True)
    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert not await async_unload_entry(hass, entry)
    assert coordinator.slow_polling
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert not coordinator.slow_polling
    await unload_all(hass)
