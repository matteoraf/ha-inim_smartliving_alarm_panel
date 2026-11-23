"""The polling groups: their schedule, the reads on the panel, failures,
the refresh after commands, the Polling options and the migration."""

import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
import logging
import math
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HassJob, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.inim_smartliving_alarm import (
    async_migrate_entry,
    async_unload_entry,
    coordinator as coordinator_module,
)
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    DATA_COORDINATOR,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.coordinator import (
    REFRESH_COOLDOWN,
    InimDataUpdateCoordinator,
)
from custom_components.inim_smartliving_alarm.polling import (
    FAILURES_BEFORE_RETRY_INTERVAL,
    GROUPS,
    LATE_READS_REPORTED,
    RETRY_INTERVAL,
    SLOW_POLLING_DURATION,
    SLOW_POLLING_INTERVAL,
    PollGroup,
    PollSchedule,
    intervals,
)
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.model import SMARTLIVING_1050_FW6
from custom_components.inim_smartliving_alarm.transport.base import TransportError
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import add_entry
from .fake_panel import FakePanel, PanelState, const as c, panel_1050
from .fake_panel.state import panel_1050_fw51, panel_10100
from .test_config_flow import open_options, unload_all
from .test_init_baseline import ALARM_PANEL, ZONE_36_SWITCH, entity_id_for, state_of

pytestmark = pytest.mark.usefixtures("socket_enabled")

DEFAULTS = {group: spec.default for group, spec in GROUPS.items()}
ZONE_1 = ("binary_sensor", "_zone_1")
EVENT_LOG = ("sensor", "_event_log")

# The group each status read belongs to, by register
GROUP_OF = {
    c.RAM_ZONE_STATE: PollGroup.ZONES,
    c.RAM_ZONE_MEMORY: PollGroup.ZONES,
    c.RAM_AREA_STATUS: PollGroup.AREAS,
    c.RAM_ACTIVE_SCENARIO: PollGroup.AREAS,
    c.RAM_ZONE_INCLUSION: PollGroup.INCLUSION,
    c.RAM_EVENT_POINTERS: PollGroup.EVENTS,
    c.RAM_FAULTS: PollGroup.FAULTS,
}


# ------------------------------------------------------------ the schedule


def test_intervals_default_and_bounds() -> None:
    """The defaults, the options within their range, and nonsense ignored."""
    assert intervals({}) == {
        PollGroup.ZONES: 1.0,
        PollGroup.AREAS: 2.0,
        PollGroup.INCLUSION: 30.0,
        PollGroup.EVENTS: 10.0,
        PollGroup.FAULTS: 60.0,
    }
    chosen = intervals(
        {
            "poll_interval_zones": 0.1,  # below 0.5
            "poll_interval_areas": "5",
            "poll_interval_inclusion": 1000,  # above 300
            "poll_interval_events": "soon",
            "poll_interval_faults": math.nan,
        }
    )
    assert chosen == {
        PollGroup.ZONES: 0.5,
        PollGroup.AREAS: 5.0,
        PollGroup.INCLUSION: 300.0,
        PollGroup.EVENTS: 10.0,
        PollGroup.FAULTS: 60.0,
    }


def run(schedule: PollSchedule, until: float, step: float = 0.5) -> dict:
    """Wake up every step and read what is due; the reads at each time."""
    reads = {}
    now = 0.0
    while now <= until:
        due = schedule.due(now)
        for group in due:
            schedule.read(group, now)
        if due:
            reads[now] = due
        now += step
    return reads


def test_each_group_at_its_interval() -> None:
    """Read from 0 to 60 s: every group as often as its interval says."""
    schedule = PollSchedule(DEFAULTS)
    schedule.read_all(0.0)
    reads = run(schedule, 60)
    counts = Counter(group for groups in reads.values() for group in groups)
    assert counts == {
        PollGroup.ZONES: 60,
        PollGroup.AREAS: 30,
        PollGroup.EVENTS: 6,
        PollGroup.INCLUSION: 2,
        PollGroup.FAULTS: 1,
    }
    assert reads[60.0] == list(GROUPS)  # in reading order
    assert reads[2.0] == [PollGroup.ZONES, PollGroup.AREAS]


def test_next_wake() -> None:
    """The time until the next group is due, never negative."""
    schedule = PollSchedule(DEFAULTS)
    assert schedule.next_wake(0.0) == 0  # nothing read yet
    schedule.read_all(10.0)
    assert schedule.next_wake(10.0) == 1.0
    assert schedule.next_wake(10.4) == pytest.approx(0.6)
    assert schedule.next_wake(15.0) == 0


def test_slow_reads_stretch_the_intervals() -> None:
    """Reads slower than the intervals: every wake-up reads something, each
    group at most once, nothing queued; the zones wait at most for the
    reads of one wake-up."""
    schedule = PollSchedule(DEFAULTS)
    now = 0.0
    wakes = []
    zone_reads = []
    for _ in range(40):
        due = schedule.due(now)
        wakes.append(due)
        for group in due:
            if group is PollGroup.ZONES:
                zone_reads.append(now)
            schedule.read(group, now)
            now += 0.75  # each read takes 0.75 s; the zones' interval is 1 s
        now += schedule.next_wake(now)
    assert all(due and len(set(due)) == len(due) for due in wakes)
    gaps = [b - a for a, b in zip(zone_reads, zone_reads[1:], strict=False)]
    assert max(gaps) <= 0.75 * len(GROUPS)
    assert len(zone_reads) > len(wakes) / 2


def test_retry_after_failures() -> None:
    """A failed read waits the shortest interval, then RETRY_INTERVAL; a
    good read goes back to the intervals."""
    schedule = PollSchedule(DEFAULTS)
    schedule.read_all(0.0)
    now = 1.0
    for failure in range(1, FAILURES_BEFORE_RETRY_INTERVAL + 1):
        schedule.failed(now)
        assert schedule.failures == failure
        wait = 1.0 if failure < FAILURES_BEFORE_RETRY_INTERVAL else RETRY_INTERVAL
        assert schedule.due(now + wait - 0.1) == []
        assert schedule.next_wake(now) == wait
        now += wait
        assert PollGroup.ZONES in schedule.due(now)
    schedule.succeeded()
    assert schedule.failures == 0
    assert schedule.next_wake(now) == 0


def test_retry_with_long_intervals() -> None:
    """With every interval above RETRY_INTERVAL, a failed read is tried
    again after the shortest interval, never sooner after more failures."""
    schedule = PollSchedule({group: 10.0 for group in GROUPS})
    schedule.read_all(0.0)
    waits = []
    for _ in range(FAILURES_BEFORE_RETRY_INTERVAL + 2):
        schedule.failed(100.0)
        waits.append(schedule.next_wake(100.0))
    assert waits == [10.0] * len(waits)


def test_late_reads_per_group() -> None:
    """Each group's late reads are counted on their own."""
    schedule = PollSchedule(DEFAULTS)
    now = 0.0
    zones, areas = [], []
    for _ in range(LATE_READS_REPORTED):
        schedule.read(PollGroup.ZONES, now)
        schedule.read(PollGroup.AREAS, now)
        now += 2.5  # zones 1.5 s late; areas due after 2 s, 0.5 s late
        zones.append(schedule.pace(PollGroup.ZONES, now))
        areas.append(schedule.pace(PollGroup.AREAS, now))
    assert zones[-1] is True
    assert set(areas) == {None}


def test_late_reads_are_reported_once() -> None:
    """Reads starting more than an interval late, LATE_READS_REPORTED times
    in a row: reported once; the first on time after it: reported too."""
    schedule = PollSchedule(DEFAULTS)
    assert schedule.pace(PollGroup.ZONES, 0.0) is None  # never read
    now = 0.0
    results = []
    for _ in range(LATE_READS_REPORTED + 2):
        schedule.read(PollGroup.ZONES, now)
        now += 2.5  # due after 1 s: 1.5 s late
        results.append(schedule.pace(PollGroup.ZONES, now))
    assert results.count(True) == 1
    assert results[LATE_READS_REPORTED - 1] is True
    schedule.read(PollGroup.ZONES, now)
    assert schedule.pace(PollGroup.ZONES, now + 1.0) is False
    schedule.read(PollGroup.ZONES, now + 1.0)
    assert schedule.pace(PollGroup.ZONES, now + 2.0) is None


# ---------------------------------------------------------- the API reads


@pytest.mark.parametrize("profile", [panel_1050, panel_10100, panel_1050_fw51])
async def test_group_reads_match_the_full_read(
    profile: Callable[[], PanelState],
) -> None:
    """Each group's reading, applied to a status read before the panel
    changed, gives the status read after it, field by field."""
    fake = FakePanel(profile())
    await fake.start()
    panel = SmartLivingPanel(
        SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01), "1234"
    )
    try:
        before = await panel.read_status()
        state = fake.state
        state.area_modes[2] = c.MODE_AWAY  # area status, active scenario
        state.area_alarm = 0b100
        state.zone_states[3] = c.ZONE_ALARM  # zone state
        state.zone_memory[3] = True  # zone memory
        state.zone_included[5] = False  # zone inclusion
        state.in_programming = True  # panel state flags
        after = await panel.read_status()
        for field in (
            "areas",
            "zone_states",
            "zone_inclusion",
            "zone_memory",
            "active_scenario",
            "panel_state",
            "raw",
            "raw_active_scenario",
        ):
            assert getattr(before, field) != getattr(after, field), field
        status = before
        for reading in (
            await panel.read_zones(),
            await panel.read_areas(),
            await panel.read_zone_inclusion(),
            await panel.read_panel_state(),
        ):
            status = reading.apply(status)
        assert status == after
    finally:
        await panel.close()
        await fake.stop()


async def test_no_panel_state_without_a_profile() -> None:
    """A panel handled as another model: its faults register is not read."""
    state = panel_1050()
    state.firmware = "5.05 01050  "
    fake = FakePanel(state)
    await fake.start()
    panel = SmartLivingPanel(
        SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01),
        "1234",
        fallback_model=SMARTLIVING_1050_FW6,
    )
    try:
        await panel.identify()
        start = len(fake.requests)
        reading = await panel.read_panel_state()
        assert reading.panel_state is None
        assert reading.raw == {}
        assert fake.requests[start:] == []
    finally:
        await panel.close()
        await fake.stop()


# ------------------------------------------------- scheduled reads (panel)


class FakeTime:
    """The schedule's clock and timers, moved by hand."""

    def __init__(self) -> None:
        """Start at an arbitrary time with no timer."""
        self.now = 1000.0
        self.timers: list[list] = []  # [when, job, cancelled]

    def call_later(self, hass: HomeAssistant, delay: float, job: HassJob):
        timer = [self.now + delay, job, False]
        self.timers.append(timer)

        def cancel() -> None:
            timer[2] = True

        return cancel


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    """The schedule's clock and timers, moved by hand."""
    fake = FakeTime()
    monkeypatch.setattr(coordinator_module, "_now", lambda: fake.now)
    monkeypatch.setattr(coordinator_module, "async_call_later", fake.call_later)
    return fake


async def advance(
    hass: HomeAssistant, clock: FakeTime, seconds: float, *, stalled: bool = False
) -> None:
    """Move the clock, running each timer at its time on the way.

    stalled: as if the event loop had been busy, the timers due run only
    at the end, late.
    """
    target = clock.now + seconds
    if stalled:
        clock.now = target
    while True:
        due = [t for t in clock.timers if not t[2] and t[0] <= target]
        if not due:
            break
        timer = min(due, key=lambda t: t[0])
        clock.timers.remove(timer)
        clock.now = max(clock.now, timer[0])
        await timer[1].target(dt_util.utcnow())
        await hass.async_block_till_done()
    clock.now = target
    await hass.async_block_till_done()


def groups_read(panel: FakePanel, start: int) -> Counter:
    """Status registers read since start, counted by group."""
    return Counter(
        GROUP_OF[request.address]
        for request in panel.requests[start:]
        if request.space == "ram" and not request.write and request.address in GROUP_OF
    )


def coordinator_of(
    hass: HomeAssistant, entry: ConfigEntry
) -> InimDataUpdateCoordinator:
    return hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]


@pytest.mark.scheduled_polling
async def test_groups_read_on_the_panel(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """Each second only what is due; every register once per read."""
    await add_entry(hass, panel)
    seen = {}
    for second in range(1, 61):
        start = len(panel.requests)
        await advance(hass, clock, 1)
        seen[second] = groups_read(panel, start)
    for second, counts in seen.items():
        expected = {PollGroup.ZONES: 2}
        if second % 2 == 0:
            expected[PollGroup.AREAS] = 2
        if second % 10 == 0:
            expected[PollGroup.EVENTS] = 1
        if second % 30 == 0:
            expected[PollGroup.INCLUSION] = 1
        if second == 60:
            expected[PollGroup.FAULTS] = 1
        assert counts == expected, second
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_only_changes_reach_the_entities(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """A read that finds nothing new calls no entity; a change shows at the
    next read of its group."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    with patch.object(
        coordinator, "async_update_listeners", wraps=coordinator.async_update_listeners
    ) as listeners:
        await advance(hass, clock, 1)
        await advance(hass, clock, 1)
        assert listeners.call_count == 0
        panel.state.zone_states[0] = c.ZONE_ALARM
        await advance(hass, clock, 1)
        assert listeners.call_count == 1
    assert state_of(hass, entry, ZONE_1) == STATE_ON
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_dimmer_levels_with_the_inclusion(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """The dimmer levels are read with the zone inclusion, not every second."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    with patch.object(
        coordinator, "_read_dimmer_levels", AsyncMock(return_value={})
    ) as levels:
        for _ in range(29):
            await advance(hass, clock, 1)
        assert levels.call_count == 0
        await advance(hass, clock, 1)
        assert levels.call_count == 1
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_a_failed_group_read(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The entities become unavailable and the groups after it are not read;
    tries at the zones' interval, then every RETRY_INTERVAL; once back,
    the entities are available again. Logged once each way."""
    entry = await add_entry(hass, panel)
    for _ in range(9):
        await advance(hass, clock, 1)
    caplog.clear()
    with patch.object(
        SmartLivingPanel, "read_areas", side_effect=TransportError("gone")
    ):
        start = len(panel.requests)
        await advance(hass, clock, 1)  # zones, areas (fails), not events
        assert groups_read(panel, start) == {PollGroup.ZONES: 2}
        assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
        for _ in range(FAILURES_BEFORE_RETRY_INTERVAL - 1):
            start = len(panel.requests)
            await advance(hass, clock, 1)
            assert groups_read(panel, start)[PollGroup.ZONES] == 2
        start = len(panel.requests)
        for _ in range(int(RETRY_INTERVAL) - 1):
            await advance(hass, clock, 1)
        assert groups_read(panel, start) == {}
    start = len(panel.requests)
    await advance(hass, clock, 1)
    assert groups_read(panel, start)[PollGroup.EVENTS] == 1  # still due
    assert state_of(hass, entry, ALARM_PANEL) != STATE_UNAVAILABLE
    messages = [record.getMessage() for record in caplog.records]
    assert sum("Error requesting" in m for m in messages) == 1
    assert sum("data recovered" in m for m in messages) == 1
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_reads_that_keep_starting_late(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning once when reads keep starting late, a line when they keep up."""
    await add_entry(hass, panel)
    caplog.clear()
    for _ in range(LATE_READS_REPORTED + 3):
        await advance(hass, clock, 2.5, stalled=True)
    await advance(hass, clock, 1)
    messages = [record.getMessage() for record in caplog.records]
    warnings = [m for m in messages if "cannot be read every 1 s" in m]
    assert len(warnings) == 1
    assert (
        sum("keep up with the 1 s interval (zones) again" in m for m in messages) == 1
    )
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_a_command_goes_between_two_groups(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """A command waits only for the group being read, not the whole read."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    await advance(hass, clock, 1)
    panel.reply_delay = 0.2
    start = len(panel.requests)
    reads = hass.async_create_task(advance(hass, clock, 1))  # zones and areas
    await asyncio.sleep(0.05)  # the zones' first request is on its way
    await coordinator.panel.arm_areas({3: ArmMode.AWAY})
    await reads
    order = [
        "command" if request.write else GROUP_OF.get(request.address)
        for request in panel.requests[start:]
        if request.write or request.address in GROUP_OF
    ]
    first_area = order.index(PollGroup.AREAS)
    assert order.index("command") < first_area
    assert order.index(PollGroup.ZONES) < order.index("command")
    panel.reply_delay = 0
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_polling_turned_off_for_the_entry(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """With "Enable polling for updates" off, nothing is read on a schedule;
    homeassistant.update_entity still reads every group."""
    entry = await add_entry(hass, panel)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    start = len(panel.requests)
    await advance(hass, clock, 60)
    assert groups_read(panel, start) == {}
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": entity_id_for(hass, entry, ALARM_PANEL)},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert set(groups_read(panel, start)) == set(GROUPS)
    await unload_all(hass)


# -------------------------------------------------- refresh after commands


async def test_a_command_reads_every_group_at_once(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Arming from an area switch: every group is read right after."""
    entry = await add_entry(hass, panel)
    start = len(panel.requests)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id_for(hass, entry, ("switch", "_area_switch_3"))},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert set(groups_read(panel, start)) == set(GROUPS)
    await unload_all(hass)


async def test_refreshes_asked_for_together(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Two refreshes asked for at once: one now, one REFRESH_COOLDOWN later
    (not 10 s later)."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    with patch.object(
        SmartLivingPanel, "read_status", wraps=coordinator.panel.read_status
    ) as reads:
        await coordinator.async_request_refresh()
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()
        assert reads.call_count == 1
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=REFRESH_COOLDOWN)
        )
        await hass.async_block_till_done()
        assert reads.call_count == 2
    await unload_all(hass)


# ---------------------------------------------------------------- options


async def test_setup_and_general_settings_have_no_interval(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The intervals are only in the Polling step."""
    result = await open_options(hass, entry)
    assert "polling_interval" not in {str(key) for key in result["data_schema"].schema}
    hass.config_entries.options.async_abort(result["flow_id"])
    assert not {spec.option for spec in GROUPS.values()} & set(entry.options)


async def test_polling_step(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The defaults are shown; only intervals set otherwise are stored, and
    the entry reads at the new ones."""
    result = await open_options(hass, entry, "polling")
    assert result["step_id"] == "polling"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {
        spec.option: spec.default
        for spec in (*GROUPS.values(), SLOW_POLLING_INTERVAL, SLOW_POLLING_DURATION)
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**defaults, "poll_interval_zones": 0.5, "poll_interval_events": 30},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["poll_interval_zones"] == 0.5
    assert entry.options["poll_interval_events"] == 30
    assert "poll_interval_areas" not in entry.options
    assert entry.options["event_log_size"] == 5  # the rest is kept
    coordinator = coordinator_of(hass, entry)
    assert coordinator.intervals[PollGroup.ZONES] == 0.5
    assert coordinator.intervals[PollGroup.EVENTS] == 30

    # Back to the default: no longer stored
    result = await open_options(hass, entry, "polling")
    assert {str(k): k.default() for k in result["data_schema"].schema}[
        "poll_interval_zones"
    ] == 0.5
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**defaults, "poll_interval_events": 30}
    )
    await hass.async_block_till_done()
    assert "poll_interval_zones" not in entry.options


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("poll_interval_zones", 0.25),
        ("poll_interval_faults", 5),
        ("poll_interval_inclusion", 301),
    ],
)
async def test_polling_step_bounds(
    hass: HomeAssistant, entry: ConfigEntry, option: str, value: float
) -> None:
    """Values out of a group's range are refused."""
    result = await open_options(hass, entry, "polling")
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {option: value}
        )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert option not in entry.options


# -------------------------------------------------------------- migration


async def test_migration_drops_the_single_interval(hass: HomeAssistant) -> None:
    """2.2: the single interval goes, from the options and the data; the
    groups start on their defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=1,
        data={"host": "192.0.2.10", "polling_interval": 15},
        options={"polling_interval": 45, "event_log_size": 20},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.data) == {"host": "192.0.2.10"}
    assert dict(entry.options) == {"event_log_size": 20}
    assert intervals(entry.options) == DEFAULTS


async def test_setup_logs_the_intervals(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """One line at setup says how often each group is read."""
    with caplog.at_level(logging.INFO):
        await add_entry(hass, panel)
    assert (
        "every 1 s (zones), 2 s (areas), 30 s (inclusion), 10 s (events), "
        "60 s (faults)" in caplog.text
    )
    await unload_all(hass)


# --------------------------------------------- failures within a tick


@pytest.mark.scheduled_polling
async def test_events_survive_a_later_failure(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """Events read before a later group fails reach the event log once,
    with the next good read."""
    entry = await add_entry(hass, panel)
    hass.config_entries.async_update_entry(  # reloads; room for every event
        entry, options={**entry.options, "event_log_size": 100}
    )
    await hass.async_block_till_done()
    sensor = entity_id_for(hass, entry, EVENT_LOG)
    for _ in range(59):
        await advance(hass, clock, 1)

    def shown() -> int:
        return hass.states.get(sensor).attributes["event_log_count_current"]

    count = shown()
    panel.state._log(1, 1, c.EVENT_AREA_RESET | c.EVENT_ACTIVATION, "Area reset")
    with patch.object(
        SmartLivingPanel, "read_panel_state", side_effect=TransportError("gone")
    ):
        await advance(hass, clock, 1)  # every group; the events, then faults fail
    assert state_of(hass, entry, ALARM_PANEL) == STATE_UNAVAILABLE
    assert shown() == count
    await advance(hass, clock, 1)  # the retry: faults read again
    assert shown() == count + 1
    for _ in range(10):
        await advance(hass, clock, 1)
    assert shown() == count + 1
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_readings_kept_when_a_later_group_fails(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """What the groups before a failure read is shown at the next good
    read, not one interval later."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    for _ in range(59):
        await advance(hass, clock, 1)
    assert state_of(hass, entry, ZONE_36_SWITCH) == STATE_ON
    panel.state.zone_included[35] = False  # zone 36 bypassed at a keypad
    with patch.object(
        SmartLivingPanel, "read_panel_state", side_effect=TransportError("gone")
    ):
        await advance(hass, clock, 1)  # inclusion read, then faults fail
    assert not coordinator.data.status.zone_inclusion.included[35]
    assert state_of(hass, entry, ZONE_36_SWITCH) == STATE_UNAVAILABLE
    start = len(panel.requests)
    await advance(hass, clock, 1)  # the retry
    assert PollGroup.INCLUSION not in groups_read(panel, start)
    assert state_of(hass, entry, ZONE_36_SWITCH) == STATE_OFF
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_unexpected_errors_logged_once(
    hass: HomeAssistant,
    panel: FakePanel,
    clock: FakeTime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected error: its traceback once, then at debug, until a read
    goes through."""

    def tracebacks() -> int:
        return sum(
            1
            for record in caplog.records
            if record.levelno == logging.ERROR
            and "Unexpected error fetching data" in record.getMessage()
            and record.exc_info
        )

    entry = await add_entry(hass, panel)
    assert entry
    with caplog.at_level(logging.DEBUG, "custom_components.inim_smartliving_alarm"):
        with patch.object(
            SmartLivingPanel, "read_zones", side_effect=RuntimeError("boom")
        ):
            for _ in range(4):
                await advance(hass, clock, RETRY_INTERVAL)
        assert tracebacks() == 1
        debug = [
            record
            for record in caplog.records
            if record.levelno == logging.DEBUG
            and "Unexpected error fetching data: boom" in record.getMessage()
        ]
        assert len(debug) >= 3
        await advance(hass, clock, RETRY_INTERVAL)  # a good read
        with patch.object(
            SmartLivingPanel, "read_zones", side_effect=RuntimeError("boom")
        ):
            await advance(hass, clock, RETRY_INTERVAL)
        assert tracebacks() == 2
    await unload_all(hass)


# ------------------------------------------------- unload and shutdown


@pytest.mark.scheduled_polling
async def test_unload_during_a_read(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """Unloading while a group is read waits for it; nothing opens the
    connection again once it is closed."""
    entry = await add_entry(hass, panel)
    old = coordinator_of(hass, entry).panel
    await advance(hass, clock, 1)
    opened = old.transport.connections_opened
    panel.reply_delay = 0.2
    reads = hass.async_create_task(advance(hass, clock, 1))  # zones, then areas
    await asyncio.sleep(0.05)  # the zones' first request is on its way
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not old.transport.connected
    await reads
    await asyncio.sleep(0.3)
    assert not old.transport.connected
    assert old.transport.connections_opened == opened
    panel.reply_delay = 0
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator_of(hass, entry).last_update_success
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_polling_goes_on_when_unloading_fails(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """If the platforms cannot be unloaded, the entry keeps reading."""
    entry = await add_entry(hass, panel)
    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert not await async_unload_entry(hass, entry)
    start = len(panel.requests)
    await advance(hass, clock, 1)
    assert groups_read(panel, start)[PollGroup.ZONES] == 2
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_no_reads_while_shutting_down(
    hass: HomeAssistant, panel: FakePanel, clock: FakeTime
) -> None:
    """Home Assistant stopping, or the coordinator shut down: no more
    scheduled reads."""
    entry = await add_entry(hass, panel)
    coordinator = coordinator_of(hass, entry)
    hass.set_state(CoreState.stopping)
    try:
        start = len(panel.requests)
        await advance(hass, clock, 5)
        assert groups_read(panel, start) == {}
    finally:
        hass.set_state(CoreState.running)
    coordinator.async_start_polling()
    await advance(hass, clock, 1)
    await coordinator.async_shutdown()
    start = len(panel.requests)
    await advance(hass, clock, 5)
    assert groups_read(panel, start) == {}
    await unload_all(hass)


# ------------------------------------------------------- more migration


async def test_migration_runs_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A 2.0 entry goes to 2.2; migrating again changes nothing. An interval
    other than the old default is logged as replaced, once."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=0,
        title="Old",
        data={"host": "192.0.2.10"},
        options={"polling_interval": 45, "event_log_size": 20},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await async_migrate_entry(hass, entry)
        assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.options) == {"event_log_size": 20}
    assert caplog.text.count("polling interval of 45 s is replaced") == 1


async def test_old_default_interval_dropped_quietly(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The old default (30 s) needs no line in the log."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=1,
        data={},
        options={"polling_interval": 30},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await async_migrate_entry(hass, entry)
    assert dict(entry.options) == {}
    assert "is replaced" not in caplog.text
