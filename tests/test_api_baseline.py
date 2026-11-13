"""Behaviour of InimAlarmAPI against the fake SmartLiving 1050.

These tests describe what the API does today. Tests marked xfail describe
known limitations; they are strict, so fixing one makes the test pass and
pytest reports it until the marker is removed.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.inim_api import InimAlarmAPI

from .fake_panel import FakePanel, captures, const as c

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Fake panel accepting a new connection while the previous one closes.

    The API opens a new connection for every operation; with a single client
    allowed, the reconnect would race the server noticing the disconnect.
    """
    fake = FakePanel(single_client=False)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def api(panel: FakePanel) -> InimAlarmAPI:
    """API client for the fake panel, using PIN 1234 (user code 0)."""
    return InimAlarmAPI(host=panel.host, port=panel.port, pin_code_str="1234")


async def run(
    hass: HomeAssistant, func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Run a blocking API call in the Home Assistant executor."""
    return await hass.async_add_executor_job(lambda: func(*args, **kwargs))


def writes(panel: FakePanel) -> list[bytes]:
    """Command frames received by the panel (header + payload)."""
    return [
        request.raw + request.payload for request in panel.requests if request.write
    ]


# ------------------------------------------------------------ configuration


async def test_initial_configuration(hass: HomeAssistant, api: InimAlarmAPI) -> None:
    """Names, scenario actions and zone configuration are read and decoded."""
    config = await run(hass, api.get_initial_panel_configuration)

    assert config["errors"] == []
    assert config["system_info"]["version"] == "6.07"
    assert config["system_info"]["type"] == "01050"
    assert config["areas"]["names"] == [f"Area {n}" for n in range(1, 11)]
    assert config["zones"]["zone_names"] == [f"Zone {n}" for n in range(1, 51)]
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
    assert len(zones) == 50
    assert zones[19]["assigned_areas"] == [1]
    assert zones[23]["assigned_areas"] == [4]
    assert zones[35]["assigned_areas"] == [7]
    # internal_index is the physical slot: keypad zones 36 and 37 sit at 110 and 112
    assert zones[0]["internal_index"] == 0
    assert zones[35]["internal_index"] == 110
    assert zones[36]["internal_index"] == 112
    assert "internal_index" not in zones[37]


# ------------------------------------------------------------ live status


async def test_live_status_defaults(hass: HomeAssistant, api: InimAlarmAPI) -> None:
    """Everything disarmed and at rest; scenario 1 matches."""
    status = await run(hass, api.get_live_status_snapshot)

    assert status["errors"] == []
    assert status["areas_status"]["area_statuses"] == dict.fromkeys(
        range(1, 11), "disarmed"
    )
    assert status["areas_status"]["triggered_areas"] == []
    assert status["zones_status"]["zone_statuses"] == dict.fromkeys(
        range(1, 51), "clear"
    )
    assert status["zones_excluded_status"]["zone_excluded_statuses"] == dict.fromkeys(
        range(1, 51), "enabled"
    )
    assert status["zones_triggered_status"]["zone_triggered_statuses"] == dict.fromkeys(
        range(1, 51), "clear"
    )
    assert status["active_scenario"]["active_scenario_number"] == 1


async def test_live_status_follows_panel(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
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
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Arming sends the captured frame and the area is armed; disarming reverts it."""
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3]) is True
    assert writes(panel) == [captures.ARM_AREA_3]
    assert panel.state.armed_areas() == [3]

    assert await run(hass, api.execute_arm_disarm_areas, areas_to_disarm=[3]) is True
    assert panel.state.armed_areas() == []


async def test_arm_with_wrong_pin(hass: HomeAssistant, panel: FakePanel) -> None:
    """A PIN not in the panel is refused and nothing changes."""
    api = InimAlarmAPI(host=panel.host, port=panel.port, pin_code_str="9999")
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3]) is False
    assert panel.state.armed_areas() == []


async def test_arm_with_open_zone(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Arming an area with an open zone is refused."""
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[1]) is False
    assert panel.state.armed_areas() == []


async def test_reset_area_alarm(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Reset sends the captured frame and clears the area and zone memories."""
    panel.state.area_alarm_memory = 1 << 6
    panel.state.zone_memory[35] = True
    assert await run(hass, api.execute_reset_area_alarm, 7) is True
    assert writes(panel) == [captures.RESET_AREA_7]
    assert panel.state.area_alarm_memory == 0
    assert panel.state.zone_memory[35] is False


async def test_bypass_and_include_zone(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Bypass by physical slot sends the captured frame; including reverts it."""
    assert await run(hass, api.execute_set_zone_excluded_status, 23, True) is True
    assert writes(panel) == [captures.BYPASS_SLOT_23]
    assert panel.state.zone_included[23] is False

    assert await run(hass, api.execute_set_zone_excluded_status, 23, False) is True
    assert panel.state.zone_included[23] is True


async def test_bypass_keypad_zone_by_physical_slot(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Zone 36 (logical 35) is wired to a keypad and is addressed by its slot 110."""
    assert await run(hass, api.execute_set_zone_excluded_status, 110, True) is True
    assert panel.state.zone_included[35] is False


async def test_activate_scenario(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Scenario activation checks the zones first, then sends the captured frame."""
    assert await run(hass, api.execute_activate_scenario, 0) is True
    assert writes(panel) == [captures.ACTIVATE_SCENARIO_0]
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]


async def test_activate_scenario_blocked_by_open_zone(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """With an open zone the scenario is not sent at all."""
    panel.state.zone_states[0] = c.ZONE_ALARM
    assert await run(hass, api.execute_check_scenario_activation_allowed, 0) is False
    assert await run(hass, api.execute_activate_scenario, 0) is False
    assert writes(panel) == []
    assert panel.state.armed_areas() == []


# ---------------------------------------------------------------- events


async def test_recent_events(hass: HomeAssistant, api: InimAlarmAPI) -> None:
    """The newest events are read from the end of the log and decoded."""
    result = await run(hass, api.execute_get_compact_events, count=3)

    assert result["latest_event_index_val"] == 0xA26F
    events = result["events"]
    assert [event["query_index_val"] for event in events] == [0xA26F, 0xA266, 0xA25D]
    assert [event["action_code"] for event in events] == [0x05, 0x85, 0x00]
    assert [event["action_description"] for event in events] == [
        "End of Zone Excluded",
        "Zone Excluded",
        "Area/Zone Alarm End",
    ]
    assert all(event["affected_areas"] == [7] for event in events)
    assert all(event["zone_number_0_indexed"] == 35 for event in events)
    assert [event["timestamp_iso"] for event in events] == [
        "2025-06-15T12:09:32+01:00",
        "2025-06-15T12:09:12+01:00",
        "2025-03-28T07:35:37+01:00",
    ]


async def test_new_event_after_command(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """An incremental read returns only the event added after the last index."""
    await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[3])
    result = await run(
        hass,
        api.execute_get_compact_events,
        last_processed_compact_event_index_val=0xA26F,
    )

    assert result["latest_event_index_val"] == 0xA278
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["action_code"] == 0x8A
    assert event["affected_areas"] == [3]
    assert event["authorized_id_hex"] == "00"
    assert event["device_id_hex"] == "fe"
    assert event["timestamp_iso"] == "2026-11-12T08:30:15+01:00"


# ------------------------------------------------------- known limitations


@pytest.mark.xfail(strict=True, reason="replies are read with a single recv() call")
async def test_fragmented_replies(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Replies split across TCP segments are read completely."""
    panel.fragment_replies = True
    status = await run(hass, api.get_live_status_snapshot)
    assert status["areas_status"]["area_statuses"] == dict.fromkeys(
        range(1, 11), "disarmed"
    )


@pytest.mark.xfail(strict=True, reason="only total arming and disarmed are decoded")
async def test_partial_arming_is_reported(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """An area armed in partial (stay) or instant mode is not reported as unknown."""
    panel.state.area_modes[2] = c.MODE_STAY
    panel.state.area_modes[3] = c.MODE_INSTANT
    status = await run(hass, api.get_live_status_snapshot)
    statuses = status["areas_status"]["area_statuses"]
    assert statuses[3] != "unknown"
    assert statuses[4] != "unknown"


@pytest.mark.xfail(
    strict=True, reason="zone values 0 (short) and 3 (tamper) are not decoded"
)
async def test_short_and_tamper_zones_are_reported(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Short-circuited and tampered zones get a known status."""
    panel.state.zone_states[19] = c.ZONE_SHORT
    panel.state.zone_states[20] = c.ZONE_TAMPER
    status = await run(hass, api.get_live_status_snapshot)
    statuses = status["zones_status"]["zone_statuses"]
    assert statuses[20] != "unknown"
    assert statuses[21] != "unknown"


@pytest.mark.xfail(
    strict=True, reason="only the low byte of the alarm memory mask is read"
)
async def test_alarm_memory_of_areas_9_and_10(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """Alarm memory on areas 9 and 10 is reported."""
    panel.state.area_alarm_memory = 0b11 << 8
    status = await run(hass, api.get_live_status_snapshot)
    assert status["areas_status"]["triggered_areas"] == [9, 10]


@pytest.mark.xfail(
    strict=True, reason="zones 51-100 (second zone of double terminals) are not read"
)
async def test_double_terminal_zones_are_reported(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
) -> None:
    """The second zone of a double terminal has its own status."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel.state.zone_states[c.NUM_TERMINALS] = c.ZONE_ALARM  # zone 51
    status = await run(hass, api.get_live_status_snapshot)
    assert status["zones_status"]["zone_statuses"].get(51) == "alarmed"


@pytest.mark.xfail(
    strict=True, reason="event indexes do not wrap around the 500-entry log"
)
async def test_events_across_the_end_of_the_log(
    hass: HomeAssistant, api: InimAlarmAPI, panel: FakePanel
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
