"""Behaviour of the integration against the fake SmartLiving 1050.

These tests describe what is stored from the panel configuration, the live
status and the events as the API decodes them (and as the event log shows
them) and the commands.
"""

from collections.abc import AsyncGenerator
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.api import (
    SmartLivingPanel,
    create_panel,
)
from custom_components.inim_smartliving_alarm.attribute_values import (
    area_mode_text,
    area_status_text,
    zone_state_text,
)
from custom_components.inim_smartliving_alarm.event_log import event_attributes
from custom_components.inim_smartliving_alarm.protocol.commands import (
    ArmMode,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    area_names,
    keypad_names,
    scenario_area_modes,
    scenario_names,
    store,
    stored_zones,
    system_info,
    zone_names,
)

from .conftest import accepted
from .fake_panel import FakePanel, captures, const as c

pytestmark = pytest.mark.usefixtures("socket_enabled")

ROME = ZoneInfo("Europe/Rome")


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Fake panel accepting one client, like the SmartLAN/SI board."""
    fake = FakePanel()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def api(panel: FakePanel) -> AsyncGenerator[SmartLivingPanel]:
    """The fake panel through the API, using PIN 1234 (user code 0)."""
    connection = create_panel(panel.host, panel.port, "1234")
    yield connection
    await connection.close()


def writes(panel: FakePanel) -> list[bytes]:
    """Command frames received by the panel (header + payload)."""
    return [
        request.raw + request.payload for request in panel.requests if request.write
    ]


# ------------------------------------------------------------ configuration


async def test_initial_configuration(
    hass: HomeAssistant, api: SmartLivingPanel
) -> None:
    """Names, scenario actions and zone configuration are read and decoded."""
    config = store(await api.read_configuration())

    info = system_info(config)
    assert info is not None
    assert (info.version, info.type) == ("6.07", "01050")
    assert area_names(config) == [f"Area {n}" for n in range(1, 11)]
    assert zone_names(config) == [f"Zone {n}" for n in range(1, 101)]
    assert scenario_names(config) == [f"Scenario {n}" for n in range(1, 31)]
    assert keypad_names(config)[:2] == ["Tast Zona Giorno", "Tast Garage"]

    modes = scenario_area_modes(config)
    assert len(modes) == 30
    assert modes[1] == {
        **dict.fromkeys(range(1, 8), ArmMode.AWAY),
        **dict.fromkeys(range(8, 11), ArmMode.KEEP),
    }
    assert modes[2] == {
        **dict.fromkeys(range(1, 9), ArmMode.DISARM),
        9: ArmMode.KEEP,
        10: ArmMode.KEEP,
    }
    assert modes[3][1] == ArmMode.AWAY
    assert modes[3][7] == ArmMode.AWAY

    zones = stored_zones(config)
    assert len(zones) == 100  # every zone number; 51-100 are second zones
    assert zones[19].areas == (1,)
    assert zones[23].areas == (4,)
    assert zones[35].areas == (7,)
    # the slot is the first physical slot: keypad zones 36 and 37 at 110 and 112
    assert zones[0].slot == 0
    assert zones[35].slot == 110
    assert zones[36].slot == 112
    assert zones[37].slot is None
    # terminal types: 37 inputs, the rest unused
    assert zones[0].terminal_type == 0
    assert zones[37].terminal_type == 4
    assert zones[50].terminal == 0
    assert zones[50].second_zone is True
    assert [zone.exists for zone in zones].count(True) == 37


# ------------------------------------------------------------ live status


def area_statuses(status) -> dict[int, str]:
    """Each area's state as the area switches show it."""
    return {area: area_status_text(mode) for area, mode in status.areas.modes.items()}


def area_modes(status) -> dict[int, str]:
    """Each area's arming mode as the attributes show it."""
    return {area: area_mode_text(mode) for area, mode in status.areas.modes.items()}


def zone_statuses(status) -> dict[int, str]:
    """Each zone's state as the zone sensors show it (1-based)."""
    return {
        zone: zone_state_text(state)
        for zone, state in enumerate(status.zone_states, start=1)
    }


async def test_live_status_defaults(hass: HomeAssistant, api: SmartLivingPanel) -> None:
    """Everything disarmed and at rest; scenario 1 matches."""
    status = await api.read_status()

    assert area_statuses(status) == dict.fromkeys(range(1, 11), "disarmed")
    assert area_modes(status) == dict.fromkeys(range(1, 11), "disarmed")
    assert status.areas.alarm_memory == frozenset()
    assert status.areas.alarm == frozenset()
    assert status.areas.tamper == frozenset()
    assert status.areas.tamper_memory == frozenset()
    assert status.areas.auto_arm == frozenset(range(1, 11))
    assert zone_statuses(status) == dict.fromkeys(range(1, 101), "clear")
    assert status.zone_inclusion.included == (True,) * 100
    assert status.zone_inclusion.test == (False,) * 100
    assert status.zone_memory.alarm_memory == (False,) * 100
    assert [status.zone_memory.zone_tamper_memory(z) for z in range(100)] == [
        False
    ] * 100
    assert status.active_scenario == 1
    assert status.scenario_matches
    assert status.raw_active_scenario == bytes((1,))


async def test_live_status_follows_panel(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Armed area, zone alarm, bypass and memories show up in the status."""
    state = panel.state
    state.area_modes[2] = c.MODE_AWAY
    state.zone_states[19] = c.ZONE_ALARM
    state.zone_included[23] = False
    state.zone_memory[35] = True
    state.area_alarm_memory = 1 << 3

    status = await api.read_status()

    assert area_statuses(status)[3] == "armed"
    assert status.areas.alarm_memory == frozenset({4})
    assert zone_statuses(status)[20] == "alarmed"
    assert status.zone_inclusion.included[23] is False
    assert status.zone_memory.alarm_memory[35] is True
    # no scenario matches the areas' state: none, the reported byte kept
    assert status.active_scenario is None
    assert not status.scenario_matches
    assert status.raw_active_scenario == bytes((c.NO_SCENARIO,))


# ---------------------------------------------------------------- commands


async def test_arm_and_disarm(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Arming sends the captured frame and the area is armed; disarming reverts it."""
    assert await accepted(api.arm_areas({3: ArmMode.AWAY})) is True
    assert writes(panel) == [captures.ARM_AREA_3]
    assert panel.state.armed_areas() == [3]

    assert await accepted(api.arm_areas({3: ArmMode.DISARM})) is True
    assert panel.state.armed_areas() == []


async def test_arm_with_wrong_pin(hass: HomeAssistant, panel: FakePanel) -> None:
    """A PIN not in the panel is refused and nothing changes."""
    connection = create_panel(panel.host, panel.port, "9999")
    try:
        assert await accepted(connection.arm_areas({3: ArmMode.AWAY})) is False
    finally:
        await connection.close()
    assert panel.state.armed_areas() == []


async def test_arm_with_open_zone(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """A panel that does not allow arming with zones not ready refuses it."""
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    panel.state.block_arming_when_not_ready = True
    assert await accepted(api.arm_areas({1: ArmMode.AWAY})) is False
    assert panel.state.armed_areas() == []


async def test_arm_with_open_zone_accepted(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Otherwise the panel arms with a zone open."""
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    assert await accepted(api.arm_areas({1: ArmMode.AWAY})) is True
    assert panel.state.armed_areas() == [1]


async def test_reset_area_alarm(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Reset sends the captured frame and clears the area and zone memories."""
    panel.state.area_alarm_memory = 1 << 6
    panel.state.zone_memory[35] = True
    assert await accepted(api.reset_alarm_memory(frozenset({7}))) is True
    assert writes(panel) == [captures.RESET_AREA_7]
    assert panel.state.area_alarm_memory == 0
    assert panel.state.zone_memory[35] is False


async def test_bypass_and_include_zone(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Bypass by physical slot sends the captured frame; including reverts it."""
    assert await accepted(api.bypass_zone(23, bypass=True)) is True
    assert writes(panel) == [captures.BYPASS_SLOT_23]
    assert panel.state.zone_included[23] is False

    assert await accepted(api.bypass_zone(23, bypass=False)) is True
    assert panel.state.zone_included[23] is True


async def test_bypass_keypad_zone_by_physical_slot(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Zone 36 (logical 35) is wired to a keypad and is addressed by its slot 110."""
    assert await accepted(api.bypass_zone(110, bypass=True)) is True
    assert panel.state.zone_included[35] is False


async def test_activate_scenario(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Scenario activation checks the zones first, then sends the captured frame."""
    assert await accepted(api.activate_scenario(0)) is True
    assert writes(panel) == [captures.ACTIVATE_SCENARIO_0]
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]


async def test_activate_scenario_blocked_by_open_zone(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """With an open zone the scenario is not sent at all."""
    panel.state.zone_states[0] = c.ZONE_ALARM
    assert await api.not_ready_zones(0)
    assert await accepted(api.activate_scenario(0)) is False
    assert writes(panel) == []
    assert panel.state.armed_areas() == []


# ---------------------------------------------------------------- events


async def test_recent_events(hass: HomeAssistant, api: SmartLivingPanel) -> None:
    """The newest events are read from the end of the log and decoded."""
    batch = await api.read_events(count=3)

    # positions in the event ring: the head (next free entry) is 468
    assert batch.latest_index == 467
    events = batch.events
    assert [logged.index for logged in events] == [467, 466, 465]
    assert [logged.event.action for logged in events] == [0x05, 0x85, 0x00]
    shown = [event_attributes(logged, {}, ROME) for logged in events]
    assert [item["description"] for item in shown] == [
        "Zone bypassed (ended)",
        "Zone bypassed",
        "Zone alarm (ended)",
    ]
    assert all(logged.event.areas == {7} for logged in events)
    assert all(logged.event.agent == 35 for logged in events)  # zone 36
    # panel local time in the given zone: summer time in June, not in March
    assert [item["timestamp"] for item in shown] == [
        "2025-06-15T12:09:32+02:00",
        "2025-06-15T12:09:12+02:00",
        "2025-03-28T07:35:37+01:00",
    ]


async def test_event_times_without_a_time_zone(
    hass: HomeAssistant, api: SmartLivingPanel
) -> None:
    """Without a time zone the panel's local time carries no offset."""
    batch = await api.read_events(count=1)
    item = event_attributes(batch.events[0], {}, None)
    assert item["timestamp"] == "2025-06-15T12:09:32"


async def test_new_event_after_command(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """An incremental read returns only the event added after the last index."""
    await accepted(api.arm_areas({3: ArmMode.AWAY}))
    batch = await api.read_events(after=467)

    assert batch.latest_index == 468
    assert len(batch.events) == 1
    event = batch.events[0].event
    assert event.action == 0x8A
    assert event.areas == {3}
    item = event_attributes(batch.events[0], {}, ROME)
    assert item["authorized_id"] == "00"
    assert item["device_id"] == "fe"
    assert item["timestamp"] == "2026-11-12T08:30:15+01:00"


# ------------------------------------------------------------- edge cases


async def test_fragmented_replies(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Replies split across TCP segments are read completely."""
    panel.fragment_replies = True
    panel.fragment_delay = 0.001  # one byte at a time, without hitting the timeout
    status = await api.read_status()
    assert area_statuses(status) == dict.fromkeys(range(1, 11), "disarmed")


async def test_partial_arming_is_reported(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Areas armed in partial (stay) or instant mode are armed, with their mode."""
    panel.state.area_modes[1] = c.MODE_AWAY
    panel.state.area_modes[2] = c.MODE_STAY
    panel.state.area_modes[3] = c.MODE_INSTANT
    status = await api.read_status()
    assert [area_statuses(status)[area] for area in (2, 3, 4, 5)] == [
        "armed",
        "armed",
        "armed",
        "disarmed",
    ]
    assert [area_modes(status)[area] for area in (2, 3, 4, 5)] == [
        "total",
        "partial",
        "instant",
        "disarmed",
    ]


async def test_invalid_area_mode_is_unknown(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """A mode value outside 1-4 (0 = not valid) is reported as unknown."""
    panel.state.area_modes[9] = c.MODE_NONE
    status = await api.read_status()
    assert area_statuses(status)[10] == "unknown"
    assert area_modes(status)[10] == "unknown"


async def test_area_alarm_and_tamper_masks(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Live alarm and tamper, tamper memory and auto-arm cover all ten areas."""
    state = panel.state
    state.area_alarm = 1 << 1 | 1 << 9
    state.area_tamper = 1 << 8
    state.area_tamper_memory = 1 << 2 | 1 << 9
    state.area_auto_arm = 0b1000000001
    areas = (await api.read_status()).areas
    assert areas.alarm == {2, 10}
    assert areas.tamper == {9}
    assert areas.tamper_memory == {3, 10}
    assert areas.auto_arm == {1, 10}
    assert areas.alarm_memory == frozenset()


async def test_short_and_tamper_zones_are_reported(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Short-circuited and tampered zones get their own status; neighbours keep theirs."""
    panel.state.zone_states[19] = c.ZONE_SHORT
    panel.state.zone_states[20] = c.ZONE_TAMPER
    panel.state.zone_states[21] = c.ZONE_ALARM
    statuses = zone_statuses(await api.read_status())
    assert [statuses[zone] for zone in (19, 20, 21, 22, 23)] == [
        "clear",
        "short",
        "tamper",
        "alarmed",
        "clear",
    ]


async def test_zone_test_flags_and_terminal_tamper_memory(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Test flags per zone; tamper memory per terminal, shared by its two zones."""
    panel.state.zone_inclusion_tail = bytes((0x10,)) + bytes(12)  # zone 5 in test
    panel.state.zone_memory_middle = bytes((0x04,)) + bytes(6)  # terminal 3
    status = await api.read_status()
    test = status.zone_inclusion.test
    assert [zone for zone, flag in enumerate(test, start=1) if flag] == [5]
    memory = [status.zone_memory.zone_tamper_memory(z) for z in range(100)]
    assert [zone for zone, flag in enumerate(memory, start=1) if flag] == [3, 53]


async def test_alarm_memory_of_areas_9_and_10(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Alarm memory on areas 9 and 10 is reported."""
    panel.state.area_alarm_memory = 0b11 << 8
    status = await api.read_status()
    assert status.areas.alarm_memory == {9, 10}


async def test_double_terminal_zones_are_reported(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """The second zone of a double terminal has its own status."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel.state.zone_states[c.NUM_TERMINALS] = c.ZONE_ALARM  # zone 51
    status = await api.read_status()
    assert zone_statuses(status)[51] == "alarmed"


async def test_no_new_events(hass: HomeAssistant, api: SmartLivingPanel) -> None:
    """Nothing is read again when the newest event was already processed."""
    batch = await api.read_events(after=467)
    assert batch.events == ()
    assert batch.latest_index == 467


async def test_new_events_across_the_end_of_the_log(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """Events written past the last ring entry are found after the wrap."""
    state = panel.state
    samples = [event for event in state.events if event is not None][-4:]
    state.events = [None] * c.EVENT_RING_SIZE
    for index, event in zip(
        (c.EVENT_RING_SIZE - 2, c.EVENT_RING_SIZE - 1, 0, 1), samples, strict=True
    ):
        state.events[index] = event
    state.event_head = 2  # entries 0 and 1 written after the wrap

    batch = await api.read_events(after=c.EVENT_RING_SIZE - 1)
    assert batch.latest_index == 1
    assert [logged.index for logged in batch.events] == [1, 0]
    assert [logged.event.action for logged in batch.events] == [
        samples[3].code,
        samples[2].code,
    ]


async def test_events_across_the_end_of_the_log(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """After the log wraps, the newest events continue from the end of the ring."""
    state = panel.state
    newest = [event for event in state.events if event is not None][-3:]
    state.events = [None] * c.EVENT_RING_SIZE
    state.events[0] = newest[2]
    state.events[c.EVENT_RING_SIZE - 1] = newest[1]
    state.events[c.EVENT_RING_SIZE - 2] = newest[0]
    state.event_head = 1

    batch = await api.read_events(count=3)
    codes = [logged.event.action for logged in batch.events]
    assert codes == [newest[2].code, newest[1].code, newest[0].code]
