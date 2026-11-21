"""The integration's own texts: English as before, Italian when HA is in Italian."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm import readiness, utils
from custom_components.inim_smartliving_alarm.errors import (
    CommandRefusedError,
    ZonesNotReadyError,
)
from custom_components.inim_smartliving_alarm.notifications import Texts, async_texts

from .fake_panel import FakePanel, const as c
from .test_command_errors import failed_events
from .test_config_flow import open_options
from .test_init_baseline import ALARM_PANEL, SCENARIO_3_BUTTON, call
from .test_prearm_check import arm_area

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_english_texts_are_unchanged(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Each English text reads exactly as the code wrote it before."""
    t = await async_texts(hass, "en")
    assert t("notification_scenario_blocked_title", scenario="'Night'") == (
        "Inim Alarm - Scenario 'Night' Blocked"
    )
    assert t(
        "notification_scenario_blocked_message", scenario="Night", zones="1. x"
    ) == ("Cannot activate Night. The following zones are preventing activation:\n1. x")
    assert t("notification_blocking_zone_line", zone="'Door (3)'", areas="Home") == (
        "'Door (3)' in Home"
    )
    assert t("notification_relevant_areas") == "relevant area(s)"
    assert t("notification_scenario_failed_title", scenario="'Night'") == (
        "Inim Alarm - 'Night' Failed"
    )
    assert t(
        "notification_scenario_failed_message", action="arm away", entity="'Panel'"
    ) == ("Failed to arm away for 'Panel'. Check panel for details.")
    assert t(
        "notification_scenario_failed_no_areas", scenario="Night", entity="'Panel'"
    ) == (
        "Failed to activate Night for 'Panel'. Check panel for details "
        "(no specific areas identified or details missing)."
    )
    assert t(
        "notification_scenario_failed_unknown_zones", scenario="Night", entity="'P'"
    ) == (
        "Failed to activate Night for 'P'. No specific zones found in "
        "problematic state. Check panel."
    )
    assert t("notification_scenario_failed_no_data", action="disarm", entity="'P'") == (
        "Failed to disarm for 'P'. Reason undetermined (data unavailable)."
    )
    assert t(
        "notification_scenario_failed_disarm",
        action="disarm",
        scenario="Off",
        entity="'P'",
    ) == ("Failed to disarm (Off) for 'P'. Check panel.")
    assert t("notification_armed_not_ready_title", target="Area 3") == (
        "Inim Alarm - Area 3 armed with zones not ready"
    )
    assert t("choice_none") == "None (Do Not Assign)"
    assert t("choice_scenario", number=3, name="Night") == "Scenario 3: Night"
    assert t("choice_unnamed_scenario", number=3) == "Unnamed Scenario 3"
    # English keeps the action and error texts as the code gives them
    assert t.action("activate_scenario", "activate scenario 'N' (index 1)") == (
        "activate scenario 'N' (index 1)"
    )


def test_action_words() -> None:
    """A translated action replaces the English words; English stays as given."""
    english = {"notification_action_arm_away": "arm away"}
    italian = Texts({"notification_action_arm_away": "inserire fuori casa"}, english)
    assert italian.action("arm_away", "arm away") == "inserire fuori casa"
    assert Texts(english, english).action("arm_away", "arm away") == "arm away"
    # A language without the text falls back to English, as given
    assert Texts({}, english).action("arm_away", "arm away") == "arm away"


async def test_armed_not_ready_in_italian(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The warning after arming anyway follows Home Assistant's language."""
    hass.config.language = "it"
    panel.state.zone_states[36] = c.ZONE_ALARM
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await arm_area(hass, entry, [3], force=True)
    title = note.call_args.kwargs["title"]
    message = note.call_args.args[1]
    assert title.startswith("Inim Alarm - ")
    assert title.endswith(" inserito con zone non pronte")
    assert "anche se queste zone non sono pronte:\n- " in message
    assert "Zone 37 (37)" in message


async def test_blocked_scenario_in_italian_event_in_english(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The notification is in Italian; the event's message stays English."""
    hass.config.language = "it"
    events = failed_events(hass)
    panel.state.zone_states[0] = c.ZONE_ALARM  # zone 1, area 1
    with (
        patch.object(utils.persistent_notification, "async_create") as note,
        pytest.raises(ZonesNotReadyError),
    ):
        await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    await hass.async_block_till_done()
    assert note.call_args.kwargs["title"].endswith("' bloccato")
    assert note.call_args.args[1].startswith("Impossibile attivare ")
    assert "Queste zone ne impediscono l'attivazione:\n1. '" in note.call_args.args[1]
    assert events[0]["message"].startswith("Cannot activate ")
    assert (
        "The following zones are preventing activation:\n1. '" in (events[0]["message"])
    )


async def test_refusal_in_italian_event_in_english(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A refusal's reason is shown in Italian; the event keeps the English one."""
    hass.config.language = "it"
    events = failed_events(hass)
    panel.state.pins[0] = "9999"
    with (
        patch.object(utils.persistent_notification, "async_create") as note,
        pytest.raises(CommandRefusedError) as err,
    ):
        await call(hass, entry, "button", "press", SCENARIO_3_BUTTON)
    await hass.async_block_till_done()
    assert note.call_args.kwargs["title"].startswith("Inim Alarm - Attivazione '")
    assert note.call_args.kwargs["title"].endswith("' non riuscita")
    assert note.call_args.args[1] != str(err.value)
    assert "La centrale" in note.call_args.args[1]
    assert events[0]["message"] == str(err.value)


async def test_scenario_choices_in_italian(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The scenario choices of the alarm panel's modes follow the language."""
    hass.config.language = "it"
    result = await open_options(hass, entry)
    field = next(
        key for key in result["data_schema"].schema if str(key) == "scenario_arm_home"
    )
    choices = result["data_schema"].schema[field].container
    assert choices["none"] == "Nessuno (non assegnare)"
    assert choices["0"].startswith("Scenario 1: ")
    hass.config_entries.options.async_abort(result["flow_id"])
