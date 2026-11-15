"""Behaviour of the integration's panel client against the fake SmartLiving 1050.

These tests describe the dictionaries the entities receive (legacy.py, on
top of the asynchronous API). Tests marked xfail describe known
limitations; they are strict, so fixing one makes the test pass and pytest
reports it until the marker is removed.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.legacy import (
    LegacyPanelClient,
    create_client,
    zone_exists,
)

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
async def api(panel: FakePanel) -> AsyncGenerator[LegacyPanelClient]:
    """Client for the fake panel, using PIN 1234 (user code 0)."""
    client = create_client(panel.host, panel.port, "1234")
    yield client
    await client.close()


async def run(
    hass: HomeAssistant,
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call a client method (hass kept so the tests read as before)."""
    return await func(*args, **kwargs)


def writes(panel: FakePanel) -> list[bytes]:
    """Command frames received by the panel (header + payload)."""
    return [
        request.raw + request.payload for request in panel.requests if request.write
    ]


# ------------------------------------------------------------ configuration


async def test_initial_configuration(
    hass: HomeAssistant, api: LegacyPanelClient
) -> None:
    """Names, scenario actions and zone configuration are read and decoded."""
    config = await run(hass, api.get_initial_panel_configuration)

    assert config["errors"] == []
    assert config["system_info"]["version"] == "6.07"
    assert config["system_info"]["type"] == "01050"
    assert config["areas"]["names"] == [f"Area {n}" for n in range(1, 11)]
    assert config["zones"]["zone_names"] == [f"Zone {n}" for n in range(1, 101)]
    assert config["scenarios"]["names"] == [f"Scenario {n}" for n in range(1, 31)]
    assert config["keyboard_names"]["names"][:2] == ["Tast Zona Giorno", "Tast Garage"]

    activations = config["scenario_activations"]
    assert len(activations) == 30
    assert activations[0]["area_actions"] == {
        **dict.fromkeys(range(1, 8), "arm"),
        **dict.fromkeys(range(8, 11), "keep"),
    }
    assert activations[1]["area_actions"] == {
        **dict.fromkeys(range(1, 9), "disarm"),
        9: "keep",
        10: "keep",
    }
    assert activations[2]["area_actions"][1] == "arm"
    assert activations[2]["area_actions"][7] == "arm"

    zones = config["zones_config"]["zones_config_detailed"]
    assert len(zones) == 100  # every zone number; 51-100 are second zones
    assert zones[19]["assigned_areas"] == [1]
    assert zones[23]["assigned_areas"] == [4]
    assert zones[35]["assigned_areas"] == [7]
    # internal_index is the physical slot: keypad zones 36 and 37 sit at 110 and 112
    assert zones[0]["internal_index"] == 0
    assert zones[35]["internal_index"] == 110
    assert zones[36]["internal_index"] == 112
    assert "internal_index" not in zones[37]
    # terminal types: 37 inputs, the rest unused
    assert zones[0]["terminal_type_desc"] == "Input"
    assert zones[37]["terminal_type_desc"] == "Not used"
    assert zones[50]["terminal_index"] == 0
    assert zones[50]["second_zone"] is True
    assert [zone_exists(zone) for zone in zones].count(True) == 37


# ------------------------------------------------------------ live status


async def test_live_status_defaults(
    hass: HomeAssistant, api: LegacyPanelClient
) -> None:
    """Everything disarmed and at rest; scenario 1 matches."""
    status = await run(hass, api.get_live_status_snapshot)

    assert status["errors"] == []
    assert status["areas_status"]["area_statuses"] == dict.fromkeys(
        range(1, 11), "disarmed"
    )
    assert status["areas_status"]["area_modes"] == dict.fromkeys(
        range(1, 11), "disarmed"
    )
    assert status["areas_status"]["triggered_areas"] == []
    assert status["areas_status"]["alarm_areas"] == []
    assert status["areas_status"]["tamper_areas"] == []
    assert status["areas_status"]["tamper_memory_areas"] == []
    assert status["areas_status"]["auto_arm_areas"] == list(range(1, 11))
    assert status["zones_status"]["zone_statuses"] == dict.fromkeys(
        range(1, 101), "clear"
    )
    assert status["zones_excluded_status"]["zone_excluded_statuses"] == dict.fromkeys(
        range(1, 101), "enabled"
    )
    assert status["zones_excluded_status"]["zone_test_statuses"] == dict.fromkeys(
        range(1, 101), False
    )
    assert status["zones_triggered_status"]["zone_triggered_statuses"] == dict.fromkeys(
        range(1, 101), "clear"
    )
    assert status["zones_triggered_status"][
        "zone_tamper_memory_statuses"
    ] == dict.fromkeys(range(1, 101), False)
    assert status["active_scenario"]["active_scenario_number"] == 1


async def test_live_status_follows_panel(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Armed area, zone alarm, bypass and memories show up in the snapshot."""
    state = panel.state
    state.area_modes[2] = c.MODE_AWAY
    state.zone_states[19] = c.ZONE_ALARM
    state.zone_included[23] = False
    state.zone_memory[35] = True
    state.area_alarm_memory = 1 << 3

    status = await run(hass, api.get_live_status_snapshot)

    assert status["areas_status"]["area_statuses"][3] == "armed"
    assert status["areas_status"]["triggered_areas"] == [4]
    assert status["zones_status"]["zone_statuses"][20] == "alarmed"
    assert status["zones_excluded_status"]["zone_excluded_statuses"][24] == "disabled"
    assert (
        status["zones_triggered_status"]["zone_triggered_statuses"][36] == "triggered"
    )
    assert status["active_scenario"]["active_scenario_number"] == c.NO_SCENARIO


# ---------------------------------------------------------------- commands


async def test_arm_and_disarm(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Arming sends the captured frame and the area is armed; disarming reverts it."""
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3]) is True
    assert writes(panel) == [captures.ARM_AREA_3]
    assert panel.state.armed_areas() == [3]

    assert await run(hass, api.execute_arm_disarm_areas, areas_to_disarm=[3]) is True
    assert panel.state.armed_areas() == []


async def test_arm_with_wrong_pin(hass: HomeAssistant, panel: FakePanel) -> None:
    """A PIN not in the panel is refused and nothing changes."""
    api = create_client(panel.host, panel.port, "9999")
    try:
        assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3]) is False
    finally:
        await api.close()
    assert panel.state.armed_areas() == []


async def test_arm_with_open_zone(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Arming an area with an open zone is refused."""
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[1]) is False
    assert panel.state.armed_areas() == []


async def test_reset_area_alarm(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Reset sends the captured frame and clears the area and zone memories."""
    panel.state.area_alarm_memory = 1 << 6
    panel.state.zone_memory[35] = True
    assert await run(hass, api.execute_reset_area_alarm, 7) is True
    assert writes(panel) == [captures.RESET_AREA_7]
    assert panel.state.area_alarm_memory == 0
    assert panel.state.zone_memory[35] is False


async def test_bypass_and_include_zone(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Bypass by physical slot sends the captured frame; including reverts it."""
    assert await run(hass, api.execute_set_zone_excluded_status, 23, True) is True
    assert writes(panel) == [captures.BYPASS_SLOT_23]
    assert panel.state.zone_included[23] is False

    assert await run(hass, api.execute_set_zone_excluded_status, 23, False) is True
    assert panel.state.zone_included[23] is True


async def test_bypass_keypad_zone_by_physical_slot(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Zone 36 (logical 35) is wired to a keypad and is addressed by its slot 110."""
    assert await run(hass, api.execute_set_zone_excluded_status, 110, True) is True
    assert panel.state.zone_included[35] is False


async def test_activate_scenario(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Scenario activation checks the zones first, then sends the captured frame."""
    assert await run(hass, api.execute_activate_scenario, 0) is True
    assert writes(panel) == [captures.ACTIVATE_SCENARIO_0]
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]


async def test_activate_scenario_blocked_by_open_zone(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """With an open zone the scenario is not sent at all."""
    panel.state.zone_states[0] = c.ZONE_ALARM
    assert await run(hass, api.execute_check_scenario_activation_allowed, 0) is False
    assert await run(hass, api.execute_activate_scenario, 0) is False
    assert writes(panel) == []
    assert panel.state.armed_areas() == []


# ---------------------------------------------------------------- events


async def test_recent_events(hass: HomeAssistant, api: LegacyPanelClient) -> None:
    """The newest events are read from the end of the log and decoded."""
    result = await run(hass, api.execute_get_compact_events, count=3, time_zone=ROME)

    # positions in the event ring: the head (next free entry) is 468
    assert result["latest_event_index_val"] == 467
    events = result["events"]
    assert [event["query_index_val"] for event in events] == [467, 466, 465]
    assert [event["action_code"] for event in events] == [0x05, 0x85, 0x00]
    assert [event["action_description"] for event in events] == [
        "End of Zone Excluded",
        "Zone Excluded",
        "Area/Zone Alarm End",
    ]
    assert all(event["affected_areas"] == [7] for event in events)
    assert all(event["zone_number_0_indexed"] == 35 for event in events)
    # panel local time in the given zone: summer time in June, not in March
    assert [event["timestamp_iso"] for event in events] == [
        "2025-06-15T12:09:32+02:00",
        "2025-06-15T12:09:12+02:00",
        "2025-03-28T07:35:37+01:00",
    ]


async def test_event_times_without_a_time_zone(
    hass: HomeAssistant, api: LegacyPanelClient
) -> None:
    """Without a time zone the panel's local time carries no offset."""
    result = await run(hass, api.execute_get_compact_events, count=1)
    assert result["events"][0]["timestamp_iso"] == "2025-06-15T12:09:32"


async def test_new_event_after_command(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """An incremental read returns only the event added after the last index."""
    await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3])
    result = await run(
        hass,
        api.execute_get_compact_events,
        last_processed_compact_event_index_val=467,
        time_zone=ROME,
    )

    assert result["latest_event_index_val"] == 468
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["action_code"] == 0x8A
    assert event["affected_areas"] == [3]
    assert event["authorized_id_hex"] == "00"
    assert event["device_id_hex"] == "fe"
    assert event["timestamp_iso"] == "2026-11-12T08:30:15+01:00"


# ------------------------------------------------------- known limitations


async def test_fragmented_replies(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Replies split across TCP segments are read completely."""
    panel.fragment_replies = True
    panel.fragment_delay = 0.001  # one byte at a time, without hitting the timeout
    status = await run(hass, api.get_live_status_snapshot)
    assert status["areas_status"]["area_statuses"] == dict.fromkeys(
        range(1, 11), "disarmed"
    )


async def test_partial_arming_is_reported(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Areas armed in partial (stay) or instant mode are armed, with their mode."""
    panel.state.area_modes[1] = c.MODE_AWAY
    panel.state.area_modes[2] = c.MODE_STAY
    panel.state.area_modes[3] = c.MODE_INSTANT
    status = await run(hass, api.get_live_status_snapshot)
    areas = status["areas_status"]
    assert [areas["area_statuses"][area] for area in (2, 3, 4, 5)] == [
        "armed",
        "armed",
        "armed",
        "disarmed",
    ]
    assert [areas["area_modes"][area] for area in (2, 3, 4, 5)] == [
        "total",
        "partial",
        "instant",
        "disarmed",
    ]


async def test_invalid_area_mode_is_unknown(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """A mode value outside 1-4 (0 = not valid) is reported as unknown."""
    panel.state.area_modes[9] = c.MODE_NONE
    status = await run(hass, api.get_live_status_snapshot)
    assert status["areas_status"]["area_statuses"][10] == "unknown"
    assert status["areas_status"]["area_modes"][10] == "unknown"


async def test_area_alarm_and_tamper_masks(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Live alarm and tamper, tamper memory and auto-arm cover all ten areas."""
    state = panel.state
    state.area_alarm = 1 << 1 | 1 << 9
    state.area_tamper = 1 << 8
    state.area_tamper_memory = 1 << 2 | 1 << 9
    state.area_auto_arm = 0b1000000001
    status = await run(hass, api.get_live_status_snapshot)
    areas = status["areas_status"]
    assert areas["alarm_areas"] == [2, 10]
    assert areas["tamper_areas"] == [9]
    assert areas["tamper_memory_areas"] == [3, 10]
    assert areas["auto_arm_areas"] == [1, 10]
    assert areas["triggered_areas"] == []


async def test_short_and_tamper_zones_are_reported(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Short-circuited and tampered zones get their own status; neighbours keep theirs."""
    panel.state.zone_states[19] = c.ZONE_SHORT
    panel.state.zone_states[20] = c.ZONE_TAMPER
    panel.state.zone_states[21] = c.ZONE_ALARM
    status = await run(hass, api.get_live_status_snapshot)
    statuses = status["zones_status"]["zone_statuses"]
    assert [statuses[zone] for zone in (19, 20, 21, 22, 23)] == [
        "clear",
        "short",
        "tamper",
        "alarmed",
        "clear",
    ]


async def test_zone_test_flags_and_terminal_tamper_memory(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Test flags per zone; tamper memory per terminal, shared by its two zones."""
    panel.state.zone_inclusion_tail = bytes((0x10,)) + bytes(12)  # zone 5 in test
    panel.state.zone_memory_middle = bytes((0x04,)) + bytes(6)  # terminal 3
    status = await run(hass, api.get_live_status_snapshot)
    test = status["zones_excluded_status"]["zone_test_statuses"]
    assert [zone for zone, flag in test.items() if flag] == [5]
    memory = status["zones_triggered_status"]["zone_tamper_memory_statuses"]
    assert [zone for zone, flag in memory.items() if flag] == [3, 53]


async def test_alarm_memory_of_areas_9_and_10(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """Alarm memory on areas 9 and 10 is reported."""
    panel.state.area_alarm_memory = 0b11 << 8
    status = await run(hass, api.get_live_status_snapshot)
    assert status["areas_status"]["triggered_areas"] == [9, 10]


async def test_double_terminal_zones_are_reported(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """The second zone of a double terminal has its own status."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel.state.zone_states[c.NUM_TERMINALS] = c.ZONE_ALARM  # zone 51
    status = await run(hass, api.get_live_status_snapshot)
    assert status["zones_status"]["zone_statuses"].get(51) == "alarmed"


async def test_no_new_events(hass: HomeAssistant, api: LegacyPanelClient) -> None:
    """Nothing is read again when the newest event was already processed."""
    result = await run(
        hass, api.execute_get_compact_events, last_processed_compact_event_index_val=467
    )
    assert result == {"events": [], "latest_event_index_val": 467}


async def test_new_events_across_the_end_of_the_log(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
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

    result = await run(
        hass,
        api.execute_get_compact_events,
        last_processed_compact_event_index_val=c.EVENT_RING_SIZE - 1,
    )
    assert result["latest_event_index_val"] == 1
    assert [event["query_index_val"] for event in result["events"]] == [1, 0]
    assert [event["action_code"] for event in result["events"]] == [
        samples[3].code,
        samples[2].code,
    ]


async def test_events_across_the_end_of_the_log(
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """After the log wraps, the newest events continue from the end of the ring."""
    state = panel.state
    newest = [event for event in state.events if event is not None][-3:]
    state.events = [None] * c.EVENT_RING_SIZE
    state.events[0] = newest[2]
    state.events[c.EVENT_RING_SIZE - 1] = newest[1]
    state.events[c.EVENT_RING_SIZE - 2] = newest[0]
    state.event_head = 1

    result = await run(hass, api.execute_get_compact_events, count=3)
    codes = [event["action_code"] for event in result["events"]]
    assert codes == [newest[2].code, newest[1].code, newest[0].code]
