"""Commands that are not carried out raise clear errors in Home Assistant."""

import logging
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.inim_smartliving_alarm import utils
from custom_components.inim_smartliving_alarm.const import (
    CONF_SCENARIO_DISARM,
    DOMAIN,
    SERVICE_ALARM_RESET,
)
from custom_components.inim_smartliving_alarm.errors import (
    CommandFailedError,
    CommandRefusedError,
    ZonesNotReadyError,
    zone_text,
)
from custom_components.inim_smartliving_alarm.protocol.commands import (
    CommandKind,
    CommandResult,
    decode_result,
    encode_pin,
)
from custom_components.inim_smartliving_alarm.selection import (
    panel_config,
    zone_names,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import (
    ALARM_PANEL,
    AREA_3_SWITCH,
    SCENARIO_3_BUTTON,
    ZONE_36_SWITCH,
    call,
    refresh,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

FAILED_EVENT = f"{DOMAIN}_scenario_activation_failed"


def without_area(panel: FakePanel, code: int, area: int) -> None:
    """Take an area (1-based) out of a user code's area mask."""
    record = panel.state.code_records[code]
    mask = int.from_bytes(record[0:2], "little") & ~(1 << (area - 1))
    panel.state.code_records[code] = mask.to_bytes(2, "little") + record[2:]


def failed_events(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect the data of every scenario-activation-failed event."""
    events: list[dict[str, Any]] = []

    def listener(event: Event) -> None:
        events.append(dict(event.data))

    hass.bus.async_listen(FAILED_EVENT, listener)
    return events


# ------------------------------------------------------------ result codes


@pytest.mark.parametrize(
    ("kind", "code", "reason"),
    [
        (CommandKind.ARM, 0, None),
        (CommandKind.ARM, 1, "invalid_code"),
        (CommandKind.ARM, 0xFF, "invalid_code"),
        (CommandKind.ARM, 2, "open_zones"),
        (CommandKind.ARM, 3, "code_not_enabled"),
        (CommandKind.ARM, 9, "unknown"),
        (CommandKind.RESET, 2, "code_not_enabled"),
        (CommandKind.BYPASS, 2, "already_in_state"),
        (CommandKind.BYPASS, 3, "slot_out_of_range"),
        (CommandKind.BYPASS, 4, "slot_not_connected"),
        (CommandKind.BYPASS, 5, "null_terminal"),
        (CommandKind.BYPASS, 6, "cannot_be_bypassed"),
        (CommandKind.BYPASS, 7, "no_areas"),
        (CommandKind.BYPASS, 8, "double_zone_error"),
        (CommandKind.BYPASS, 9, "unknown"),
        (CommandKind.OUTPUT, 6, "output_not_enabled"),
        (CommandKind.OUTPUT, 7, "areas_armed"),
        (CommandKind.SCENARIO, 2, "refused"),
        (CommandKind.SCENARIO, 3, "refused"),
        (CommandKind.STOP, 2, "refused"),
    ],
)
def test_result_codes(kind: CommandKind, code: int, reason: str | None) -> None:
    """Each result code means something per command type."""
    result = decode_result(kind, bytes((code,)))
    assert result.reason == reason
    assert result.ok is (code == 0)


@pytest.mark.parametrize(
    ("result", "key"),
    [
        (CommandResult(CommandKind.ARM, 1, "invalid_code"), "refused_invalid_code"),
        (CommandResult(CommandKind.ARM, 2, "open_zones"), "refused_open_zones"),
        (CommandResult(CommandKind.BYPASS, 6, "cannot_be_bypassed"), None),
        (CommandResult(CommandKind.SCENARIO, 3, "refused"), "refused_refused"),
        (CommandResult(CommandKind.ARM, 9, "unknown"), "refused_unknown"),
        (CommandResult(CommandKind.ARM, 9, "something_new"), "refused_unknown"),
    ],
)
def test_refusal_translation_keys(result: CommandResult, key: str | None) -> None:
    """Every panel result maps to a message; unknown ones keep their code."""
    error = CommandRefusedError("Garage", result)
    assert error.translation_key == (key or f"refused_{result.reason}")
    assert error.translation_placeholders == {
        "target": "Garage",
        "code": str(result.code),
    }
    assert error.code == result.code


def test_zone_text() -> None:
    """Zones are named by label and number, or by number alone."""
    names = ["Front door", "", "Window"]
    assert zone_text(1, names) == "Front door (1)"
    assert zone_text(2, names) == "2"
    assert zone_text(7, names) == "7"


# ----------------------------------------------------------- area switches


async def test_area_switch_wrong_pin(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A PIN no longer in the panel gives a clear error and arms nothing."""
    panel.state.pins[0] = "9999"
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.translation_key == "refused_invalid_code"
    assert "did not accept the PIN for Area 3" in str(err.value)
    assert panel.state.armed_areas() == []


async def test_area_switch_pin_not_enabled(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Arming an area the PIN's code does not control is refused."""
    without_area(panel, 0, 3)
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.reason == "code_not_enabled"
    assert panel.state.armed_areas() == []


async def test_area_switch_open_zone_blocking(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Arming anyway, the panel's "open zones" refusal reaches the user."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "zones_not_ready": "warn"}
    )
    await hass.async_block_till_done()
    panel.state.zone_states[36] = c.ZONE_ALARM  # zone 37, area 3
    panel.state.block_arming_when_not_ready = True
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.reason == "open_zones"
    assert panel.state.armed_areas() == []


async def test_area_switch_open_zone_refused(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """By default the integration refuses first: nothing is sent."""
    panel.state.zone_states[36] = c.ZONE_ALARM  # zone 37, area 3
    with pytest.raises(ZonesNotReadyError):
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.armed_areas() == []


async def test_area_switch_disarm_refused(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A refused disarm raises too and leaves the area armed."""
    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    panel.state.pins[0] = "9999"
    with pytest.raises(CommandRefusedError):
        await call(hass, entry, "switch", "turn_off", AREA_3_SWITCH)
    assert panel.state.armed_areas() == [3]


async def test_area_switch_command_failed(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A command whose result cannot be read is a failure, not a refusal."""
    panel.corrupt_replies = 1
    with pytest.raises(CommandFailedError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.translation_key == "command_failed"
    assert str(err.value).startswith("The command for Area 3 could not be completed")


# ----------------------------------------------------------- zone switches


async def test_zone_switch_already_in_state(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Including a zone that is already included is reported by the panel."""
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "switch", "turn_on", ZONE_36_SWITCH)
    assert err.value.reason == "already_in_state"


async def test_zone_switch_not_connected(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A terminal that is no longer connected cannot be bypassed."""
    panel.state.physical_map[110] = 0xFF
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "switch", "turn_off", ZONE_36_SWITCH)
    assert err.value.reason == "slot_not_connected"
    assert panel.state.zone_included[35] is True


# --------------------------------------------------- scenarios and alarm panel


async def test_scenario_not_ready_is_not_sent(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The integration's own check names the zones and sends nothing."""
    events = failed_events(hass)
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    await refresh(hass, entry)
    writes = len([r for r in panel.requests if r.write])

    with pytest.raises(ZonesNotReadyError) as err:
        await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    assert err.value.zones == (1,)
    assert err.value.translation_placeholders["zones"].endswith("(1)")
    assert len([r for r in panel.requests if r.write]) == writes
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0]["reason"] == "zones_blocked"
    assert events[0]["panel_result"] is None
    zone = events[0]["blocking_zones"][0]
    assert zone["zone_id"] == 1
    assert zone["status"] == "not_ready"
    assert zone["areas"]
    # The plain label; the message has it with the number
    assert err.value.translation_placeholders["zones"] == zone_text(
        1, [zone["zone_name"]]
    )


async def test_scenario_not_ready_lists_the_panel_zones(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The notification and the event list the zones the panel reported.

    The panel also counts zones at rest as not ready (delayed zones, for
    example): they are listed as in the error message.
    """
    events = failed_events(hass)
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    panel.state.extra_not_ready = {2}  # zone 3, area 1, at rest
    await refresh(hass, entry)

    with (
        patch.object(utils.persistent_notification, "async_create") as note,
        pytest.raises(ZonesNotReadyError) as err,
    ):
        await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    await hass.async_block_till_done()
    assert err.value.zones == (1, 3)
    labels = err.value.translation_placeholders["zones"].split(", ")
    assert len(labels) == 2

    assert len(events) == 1
    event = events[0]
    assert event["reason"] == "zones_blocked"
    assert [z["zone_id"] for z in event["blocking_zones"]] == [1, 3]
    assert [z["status"] for z in event["blocking_zones"]] == ["not_ready"] * 2
    assert all(z["areas"] for z in event["blocking_zones"])
    # The plain labels; the message has them with their numbers
    names = zone_names(panel_config(entry.data))
    assert [z["zone_name"] for z in event["blocking_zones"]] == [names[0], names[2]]
    assert [zone_text(zone, names) for zone in (1, 3)] == labels

    message = note.call_args.args[1]
    assert message == event["message"]
    listed = [line.split(". ", 1)[1] for line in message.splitlines()[1:]]
    assert [line.split("' in ")[0].strip("'") for line in listed] == labels


async def test_scenario_refused_by_the_panel(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A wrong PIN on a scenario raises, notifies and fires the event."""
    events = failed_events(hass)
    panel.state.pins[0] = "9999"
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "button", "press", SCENARIO_3_BUTTON)
    assert err.value.reason == "invalid_code"
    assert panel.state.armed_areas() == []
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["panel_result"] == "invalid_code"
    assert events[0]["message"] == str(err.value)


async def test_scenario_refused_without_reason(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A scenario refusal carries the panel's result in the event."""
    events = failed_events(hass)
    without_area(panel, 0, 7)  # scenario 3 arms areas 1 and 7
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, "button", "press", SCENARIO_3_BUTTON)
    assert err.value.translation_key == "refused_refused"
    await hass.async_block_till_done()
    assert events[0]["panel_result"] == "refused"


async def test_disarm_without_mapped_scenario(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Disarming with no scenario mapped is a validation error."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SCENARIO_DISARM: None}
    )
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, entry, "alarm_control_panel", "alarm_disarm", ALARM_PANEL)
    assert err.value.translation_key == "scenario_not_mapped"
    assert not [r for r in panel.requests if r.write]


async def test_alarm_reset_refused_resets_the_other_areas(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Every area is tried; the first refusal is reported."""
    panel.state.area_alarm_memory = 1 << 3 | 1 << 9  # areas 4 and 10
    await refresh(hass, entry)
    without_area(panel, 0, 4)
    with pytest.raises(CommandRefusedError) as err:
        await call(hass, entry, DOMAIN, SERVICE_ALARM_RESET, ALARM_PANEL)
    assert err.value.reason == "code_not_enabled"
    assert "Area 4" in str(err.value)
    assert panel.state.area_alarm_memory == 1 << 3  # area 10 was reset


async def test_errors_are_home_assistant_errors() -> None:
    """Automations and the UI see every command error as a failed action."""
    for error in (CommandRefusedError, ZonesNotReadyError, CommandFailedError):
        assert issubclass(error, HomeAssistantError)


# --------------------------------------------------------------- PIN in logs


async def test_debug_logs_never_hold_the_pin(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """Setup and commands, every logger at debug: the PIN never shows."""
    pin = "918273"
    panel.state.pins[0] = pin
    caplog.set_level(logging.DEBUG)
    entry = await add_entry(hass, panel, pin=pin)
    await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    await call(hass, entry, "switch", "turn_off", ZONE_36_SWITCH)
    await hass.async_block_till_done()

    text = caplog.text
    assert "| ** ** ** ** ** **" in text  # command frames were logged, masked
    for secret in (pin, encode_pin(pin).hex(" "), encode_pin(pin).hex()):
        assert secret not in text, secret
    assert await hass.config_entries.async_unload(entry.entry_id)
