"""Every event the panel logs: its name, what it refers to, how it happened.

The type table is checked against Inim's own event table (code, agent type,
location type, areas), and the texts against Inim's English and Italian
names.
"""

import ast
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import async_get_platforms
import pytest

from custom_components.inim_smartliving_alarm.api import LoggedEvent
from custom_components.inim_smartliving_alarm.const import DOMAIN
from custom_components.inim_smartliving_alarm.event_log import (
    ABOUT_KEYS,
    EventLog,
    EventNames,
)
from custom_components.inim_smartliving_alarm.notifications import Texts
from custom_components.inim_smartliving_alarm.protocol.event_codes import (
    FW51_EVENT_CODES,
)
from custom_components.inim_smartliving_alarm.protocol.event_types import (
    EVENT_TYPES,
    EventRefs,
    ObjectType,
    Place,
    Ref,
    Via,
    event_refs,
    event_type,
)
from custom_components.inim_smartliving_alarm.protocol.events import (
    decode_event_record,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_515_FW6,
    SMARTLIVING_1050_FW6 as SL1050,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
)
from custom_components.inim_smartliving_alarm.sensor import InimEventLogSensor

from .conftest import add_entry, label_block
from .event_texts import BundledTexts, event_attributes
from .test_config_flow import unload_all

pytestmark = pytest.mark.usefixtures("socket_enabled")

COMPONENT = (
    Path(__file__).parent.parent / "custom_components" / "inim_smartliving_alarm"
)
_O = ObjectType

# The panel's own event table (as firmware 6 renders its log): code ->
# (agent type, location type, areas flag); the codes not listed have none
INIM_TABLE = {
    0: (_O.ZONE, _O.NONE, True),
    1: (_O.ZONE, _O.NONE, True),
    2: (_O.NONE, _O.NONE, True),
    3: (_O.NONE, _O.NONE, True),
    4: (_O.NONE, _O.NONE, True),
    5: (_O.ZONE, _O.USER_CODE, True),
    6: (_O.ZONE, _O.NONE, True),
    **{code: (_O.NONE, _O.NONE, True) for code in (7, 8, 9, 10, 11, 12, 14, 15)},
    13: (_O.USER_CODE, _O.KEYPAD, True),
    16: (_O.NONE, _O.NONE, True),
    17: (_O.USER_CODE, _O.KEYPAD, True),
    18: (_O.NONE, _O.NONE, True),
    19: (_O.USER_CODE, _O.KEYPAD, True),
    20: (_O.NONE, _O.NONE, True),
    21: (_O.USER_CODE, _O.KEYPAD, True),
    22: (_O.KEY, _O.READER, True),
    27: (_O.PHONE_NUMBER, _O.NONE, False),
    28: (_O.TIMER, _O.NONE, False),
    29: (_O.NONE, _O.KEYPAD, True),
    30: (_O.SCENARIO, _O.NONE, True),
    31: (_O.PROGRAMMABLE_EVENT, _O.NONE, False),
    32: (_O.EMERGENCY_KEY, _O.KEYPAD, False),
    41: (_O.EXPANDER, _O.NONE, False),
    42: (_O.KEYPAD, _O.NONE, False),
    43: (_O.READER, _O.NONE, False),
    44: (_O.SIREN, _O.NONE, False),
    48: (_O.EXPANDER, _O.NONE, False),
    49: (_O.KEYPAD, _O.NONE, False),
    50: (_O.READER, _O.NONE, False),
    51: (_O.SIREN, _O.NONE, False),
    56: (_O.ZONE, _O.NONE, True),
    57: (_O.ZONE, _O.NONE, True),
    58: (_O.NONE, _O.KEYPAD, False),
    59: (_O.NONE, _O.KEYPAD, False),
    60: (_O.NONE, _O.READER, False),
    69: (_O.ZONE, _O.NONE, False),
}
# Phone number 12 named, as the installer software names the cloud's slot
PHONES = {"phone_labels": label_block([""] * 11 + ["Cloud"])}
# General options with the output fault set to report a dirty detector
SET_DIRTY_DETECTOR = {
    "model_string": b"6.07 01050  ".hex(),
    "general_options": bytes((0, 0, 0, 0, 1, 0)).hex(),
}
# A configuration stored from a 1050 on firmware 5.1 (its model string)
FW51 = {"model_string": b"5.10 01050  ".hex()}
CONFIG = {
    "keypad_labels": label_block(["Hall", "Garage"]),
    "code_labels": label_block(["Master", "Anna"]),
    "key_labels": label_block(["Tag Anna"]),
    "reader_labels": label_block(["Front door"]),
    "timer_labels": label_block(["Night"]),
    "expander_labels": label_block(["Cellar"]),
    "siren_labels": label_block(["Outdoor"]),
}


def record(code: int, agent: int = 0xFF, location: int = 0xFF, *, on=True, areas=1):
    """A raw 9-byte record (fixed time)."""
    flags = (0x80 if on else 0) | code
    return (
        bytes.fromhex("bc03602f")
        + areas.to_bytes(2, "little")
        + bytes((agent, location, flags))
    )


def item(raw: bytes, model=SL1050, config=CONFIG, local=None) -> dict:
    event = decode_event_record(raw, model)
    return event_attributes(
        LoggedEvent(0, raw, event), config, None, None, local, model=model
    )


def messages(language: str) -> dict[str, str]:
    path = COMPONENT / "translations" / f"{language}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v["message"] for k, v in data["exceptions"].items()}


# ------------------------------------------------------------------ the table


@pytest.mark.parametrize("code", range(114))
def test_every_code_has_its_type(code: int) -> None:
    """Agent and location types and the areas flag, as Inim's table."""
    kind = event_type(code)
    assert kind is not None and kind.code == code
    expected = INIM_TABLE.get(code) or (_O.NONE, _O.NONE, False)
    assert (kind.agent, kind.location, kind.areas) == expected


def test_keys_are_unique_and_stable() -> None:
    keys = [kind.key for kind in EVENT_TYPES]
    assert len(set(keys)) == len(keys) == 114
    assert keys[:3] == ["zone_alarm", "terminal_tamper", "area_alarm"]
    assert event_type(10).key == "area_armed_in_away_mode"
    assert event_type(30).key == "scenario_activated"
    assert event_type(114) is None
    assert event_type(None) is None


def test_every_code_has_its_texts() -> None:
    """One text per code in every file; Italian is Inim's own."""
    english, italian = messages("en"), messages("it")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    for kind in EVENT_TYPES:
        key = f"event_{kind.key}"
        assert english[key] and italian[key], key
        assert strings["exceptions"][key]["message"] == english[key]
    assert italian["event_area_armed_in_away_mode"] == "Inserimento effettivo di area"
    assert italian["event_zone_alarm"] == "Allarme zona"
    # the panel's own singular, not the plural of Inim's long names
    assert italian["event_zone_bypassed"] == "Esclusione zona"
    assert italian["event_periodic_event"] == "Evento periodico"
    assert english["event_area_armed_in_away_mode"] == "Area armed in away mode"
    assert english["event_ended"] == "{event} (ended)"
    assert italian["event_ended"] == "{event} (ripristino)"


@pytest.mark.parametrize("code", range(128))
def test_firmware_51_codes(code: int) -> None:
    """Every firmware 5.1 code is named: its firmware 6 name, or a free
    event."""
    shown = item(record(code), model=SMARTLIVING_1050_FW51, config=FW51)
    fw6 = FW51_EVENT_CODES[code]
    if fw6 is not None:
        assert shown["event"] == event_type(fw6).key
        assert shown["code"] == fw6
    elif 97 <= code <= 110:
        assert shown["event"] == "free_event"
        assert shown["description"] == f"Free event {code - 96}"
    else:
        assert shown["event"] == "unknown"


def test_firmware_51_disarm_is_the_end_of_arming() -> None:
    shown = item(record(10, on=False), model=SMARTLIVING_1050_FW51, config=FW51)
    assert shown["description"] == "Area armed in away mode (ended)"
    assert shown["ended"] is True


# --------------------------------------------------------------- the rules


def refs(code: int, agent: int = 0xFF, location: int = 0xFF, model=SL1050):
    return event_refs(decode_event_record(record(code, agent, location), SL1050), model)


ARMING = [8, 9, 10, 11, 12, 19]
MODELS = [SMARTLIVING_505_FW6, SMARTLIVING_515_FW6, SL1050, SMARTLIVING_10100_FW6]


@pytest.mark.parametrize("code", ARMING)
def test_arming_rules(code: int) -> None:
    """Keypad, reader, timer, remote, command zone, patrol time, shortcut."""
    found = refs(code, 1, 3)
    assert (found.who, found.where, found.via) == (
        Ref(_O.USER_CODE, 1),
        Ref(_O.KEYPAD, 3),
        Via.KEYPAD_CODE,
    )
    found = refs(code, 0, 12)
    assert (found.who, found.where, found.via) == (
        Ref(_O.KEY, 0),
        Ref(_O.READER, 2),
        Via.READER_KEY,
    )
    found = refs(code, 2, 33)  # a timer: from K + P, the agent names it
    assert (found.who, found.where, found.about, found.via) == (
        None,
        None,
        (Ref(_O.TIMER, 2),),
        Via.TIMER,
    )
    assert refs(code, 0xFF, 30).about == ()  # a timer without its number
    found = refs(code, 1, 0xFE)
    assert (found.who, found.where, found.via) == (
        Ref(_O.USER_CODE, 1),
        Place.REMOTE,
        Via.REMOTE,
    )
    found = refs(code, 61, 0xFF)
    assert (found.about, found.via) == ((Ref(_O.ZONE, 61),), Via.COMMAND_ZONE)
    assert refs(code).via is Via.PATROL_TIME
    found = refs(code, 0xFF, 4)
    assert (found.who, found.where, found.via) == (
        None,
        Ref(_O.KEYPAD, 4),
        Via.KEYPAD_SHORTCUT,
    )
    assert refs(code, 1, 40).where is Place.UNKNOWN  # past the timers


@pytest.mark.parametrize("code", [13, 17])
def test_reset_and_overtime_rules(code: int) -> None:
    """A code on a keypad, a key on a reader (from K), a code from a
    remote (location none); a keypad without a code."""
    found = refs(code, 1, 3)
    assert (found.who, found.where, found.via) == (
        Ref(_O.USER_CODE, 1),
        Ref(_O.KEYPAD, 3),
        Via.KEYPAD_CODE,
    )
    found = refs(code, 0, 10)
    assert (found.who, found.where, found.via) == (
        Ref(_O.KEY, 0),
        Ref(_O.READER, 0),
        Via.READER_KEY,
    )
    found = refs(code, 1, 0xFF)
    assert (found.who, found.where, found.via) == (
        Ref(_O.USER_CODE, 1),
        None,
        Via.REMOTE,
    )
    # at a keypad without a code: the keypad only, no way it was done
    found = refs(code, 0xFF, 3)
    assert (found.who, found.where, found.via) == (None, Ref(_O.KEYPAD, 3), None)
    assert refs(code) == EventRefs()
    assert refs(code, 1, 30).where is Place.UNKNOWN  # past the readers


def test_valid_code_at_keypad_is_the_name_only() -> None:
    """Code 23: the panel shows its name, nothing else."""
    assert refs(23, 1, 3) == EventRefs()
    shown = item(record(23, 1, 3))
    assert shown["description"] == "Valid code at keypad"
    assert not {"authorized_by", "device", "via"} & set(shown)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.name)
def test_location_ranges_per_model(model) -> None:
    """The bounds of keypads (K), readers (K + P) and timers (K + P + T) of
    each model: 5/15/25, 5/15/25, 10/30/40, 15/45/65."""
    k, p, t = model.keypads, model.readers, model.timers
    assert (k, k + p, k + p + t) == {
        5: (5, 15, 25),
        10: (10, 30, 40),
        15: (15, 45, 65),
    }[k]
    assert refs(10, 0, k - 1, model).where == Ref(_O.KEYPAD, k - 1)
    assert refs(10, 0, k, model).where == Ref(_O.READER, 0)
    assert refs(10, 0, k + p - 1, model).where == Ref(_O.READER, p - 1)
    assert refs(10, 0, k + p, model).about == (Ref(_O.TIMER, 0),)
    assert refs(10, t - 1, k + p + t - 1, model).about == (Ref(_O.TIMER, t - 1),)
    assert refs(10, 0, k + p + t, model).where is Place.UNKNOWN
    assert refs(13, 0, k + p - 1, model).where == Ref(_O.READER, p - 1)
    assert refs(13, 0, k + p, model).where is Place.UNKNOWN


def test_bypass_rules() -> None:
    """Event 5: the location is the user code; none: automatic; remote."""
    assert refs(5, 7, 1).who == Ref(_O.USER_CODE, 1)
    assert refs(5, 7, 1).via is Via.USER_CODE
    assert refs(5, 7, 1).about == (Ref(_O.ZONE, 7),)
    assert refs(5, 7).via is Via.AUTOMATIC
    assert refs(5, 7, 0xFE).where is Place.REMOTE


def test_invalid_code_from_keypad_or_telephone() -> None:
    assert refs(59, 0xFF, 1).where == Ref(_O.KEYPAD, 1)
    assert refs(59).where is Place.TELEPHONE
    assert refs(59).via is Via.TELEPHONE


def test_key_events_use_the_reader_index() -> None:
    """A valid or false key: the location is the reader itself."""
    assert refs(22, 0, 1).where == Ref(_O.READER, 1)
    assert refs(60, 0xFF, 1).where == Ref(_O.READER, 1)


def test_emergency_key_and_numbered_events() -> None:
    """32 names the emergency key; 33 and 34 show the agent as a number."""
    shown = item(record(32, 1, 0))
    assert (shown["emergency_key"], shown["device"]) == ("Emergency key 2", "Hall")
    assert item(record(33, 0))["number"] == "001"
    assert item(record(34, 11))["number"] == "012"
    assert refs(33, 0).about == ()


def test_dirty_detector() -> None:
    """With the general option set, an output fault is a dirty smoke
    detector, about a zone."""
    plain = item(record(69, 4))
    assert (plain["event"], plain["description"]) == ("output_fault", "Output fault")
    stored = {**CONFIG, **SET_DIRTY_DETECTOR}
    shown = item(record(69, 4), config=stored, local=BundledTexts("it"))
    assert (shown["event"], shown["description"]) == (
        "detector_dusty",
        "Detector dusty",
    )
    assert shown["description_local"] == "Sensore fumo sporco"
    assert shown["zone"] == "Zone 5 (Unknown)"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "278f9832780000fe8c",
            {"event": "area_disarmed", "via": "remote", "device": "Remote"},
        ),
        (
            "cb8f983200000b001b",
            {"event": "failed_call", "ended": True, "phone_number": "Number 12"},
        ),
        (
            "e18c97327800ff008a",
            {"event": "area_armed_in_away_mode", "via": "keypad_shortcut"},
        ),
        ("206998327800ffff8d", {"event": "area_alarm_memory_reset"}),
        (
            "21699832700006009e",
            {"event": "scenario_activated", "scenario": "Scenario 7 (Unknown)"},
        ),
    ],
)
def test_events_read_on_a_1050(raw: str, expected: dict) -> None:
    """Records from a 1050 on firmware 6.07, as its own log shows them."""
    shown = item(bytes.fromhex(raw), config={})
    assert {key: shown.get(key) for key in expected} == expected


# --------------------------------------------------------------- the items


def test_items_name_what_events_are_about() -> None:
    assert item(record(28, 0))["timer"] == "Night"
    assert item(record(41, 0))["expander"] == "Cellar"
    assert item(record(42, 1))["keypad"] == "Garage"
    assert item(record(43, 0))["reader"] == "Front door"
    assert item(record(44, 0))["siren"] == "Outdoor"
    assert item(record(27, 1))["phone_number"] == "Number 2"  # no name stored
    assert item(record(27, 11), config=PHONES)["phone_number"] == "Cloud"
    assert item(record(31, 3))["programmable_event"] == "Programmable event 4"
    assert "video_sensor" not in item(record(47, 0))  # the panel shows none
    shown = item(record(22, 0, 0))
    assert (shown["authorized_by"], shown["device"]) == ("Tag Anna", "Front door")
    shown = item(record(10, 1, 0xFE))
    assert (shown["authorized_by"], shown["device"], shown["via"]) == (
        "Anna",
        "Remote",
        "remote",
    )
    assert item(record(59))["device"] == "From telephone"
    assert set(ABOUT_KEYS.values()) >= {"timer", "keypad", "zone", "scenario"}


def test_names_beyond_the_tables() -> None:
    assert item(record(28, 9))["timer"] == "Timer 10"
    assert item(record(10, 40, 0))["authorized_by"] == "Code 41"
    assert item(record(0, 140), config={})["zone"] == "Zone 141 (Unknown)"
    assert EventNames({}).user_code(0) == "Master Code"


def test_unknown_code_keeps_the_raw_record() -> None:
    shown = item(record(0x7E, on=False))
    assert shown["event"] == "unknown"
    assert shown["description"] == "Unknown event (code 126) (ended)"
    assert shown["raw_hex_data"] == record(0x7E, on=False).hex()


@pytest.mark.parametrize(
    ("byte", "event", "description"),
    [
        (0xC3, "ongoing_call", "Ongoing call"),
        (0x43, "ongoing_call", "Ongoing call (ended)"),
        (0x88, "area_away_arm_request", "Area away arm request"),
        (0x08, "area_away_arm_request", "Area away arm request (ended)"),
        (0xC1, "successful_call", "Successful call"),
        (0x1C, "timer_event", "Timer event (ended)"),
    ],
)
def test_codes_reported_unknown_before(byte: int, event: str, description: str) -> None:
    """Codes a 10100 on firmware 6 logged that had no name before."""
    raw = record(byte & 0x7F, on=bool(byte & 0x80))
    shown = item(raw, model=SMARTLIVING_10100_FW6)
    assert (shown["event"], shown["description"]) == (event, description)


def test_description_stays_english() -> None:
    shown = item(record(0), local=BundledTexts("it"))
    assert shown["description"] == "Zone alarm"
    assert shown["description_local"] == "Allarme zona"
    shown = item(record(0, on=False), local=BundledTexts("it"))
    assert shown["description_local"] == "Allarme zona (ripristino)"


# ------------------------------------------------- model and missing texts


def test_events_follow_the_model_they_were_decoded_with() -> None:
    """The panel's model decides, not the one of the stored configuration."""
    raw = record(97)
    event = decode_event_record(raw, SMARTLIVING_1050_FW51)
    logged = LoggedEvent(0, raw, event)
    stored_fw6 = {"model_string": b"6.07 01050  ".hex()}
    shown = event_attributes(logged, stored_fw6, None, model=SMARTLIVING_1050_FW51)
    assert (shown["event"], shown["description"]) == ("free_event", "Free event 1")
    # without a model, the stored configuration's
    shown = event_attributes(logged, FW51, None)
    assert shown["event"] == "free_event"


def test_missing_texts_fall_back_to_english() -> None:
    """Texts that could not be loaded give fixed English, never an error."""
    none = Texts({}, {})
    for raw, description in (
        (record(0), "Zone alarm"),
        (record(0, on=False), "Zone alarm (ended)"),
        (record(127), "Unknown event (code 127)"),
    ):
        event = decode_event_record(raw, SL1050)
        shown = event_attributes(LoggedEvent(0, raw, event), CONFIG, None, none, none)
        assert shown["description"] == shown["description_local"] == description
    raw = record(100)
    event = decode_event_record(raw, SMARTLIVING_1050_FW51)
    shown = event_attributes(
        LoggedEvent(0, raw, event), FW51, None, none, none, SMARTLIVING_1050_FW51
    )
    assert shown["description"] == "Free event 4"


def test_texts_that_do_not_fit_fall_back() -> None:
    """A language without the file, or a text with the wrong placeholders,
    gives English; a key with no text at all comes back as the key."""
    assert BundledTexts("xx")("event_zone_alarm") == "Zone alarm"
    english = messages("en")
    broken = Texts({"event_ended": "{wrong} (fine)"}, english)
    assert broken("event_ended", event="Zone alarm") == "Zone alarm (ended)"
    assert Texts({}, {})("notification_relevant_areas") == "notification_relevant_areas"


# ---------------------------------------------------------------- the sensor


def test_event_list_is_left_out_of_the_recorder() -> None:
    assert "event_log" in InimEventLogSensor._unrecorded_attributes


async def test_recorder_is_told_to_leave_the_list_out(
    hass: HomeAssistant, panel
) -> None:
    """Home Assistant merged the attribute into what the recorder skips."""
    await add_entry(hass, panel)
    state = next(
        s for s in hass.states.async_all("sensor") if s.entity_id.endswith("event_log")
    )
    assert state.state_info is not None
    assert "event_log" in state.state_info["unrecorded_attributes"]
    assert "event_log" in state.attributes
    await unload_all(hass)


async def test_sensor_decodes_with_the_panels_model(hass: HomeAssistant, panel) -> None:
    """The sensor shows the events with the model they were decoded with,
    when it differs from the stored configuration's (firmware 6 here)."""
    await add_entry(hass, panel)
    sensor = next(
        entity
        for platform in async_get_platforms(hass, DOMAIN)
        for entity in platform.entities.values()
        if isinstance(entity, InimEventLogSensor)
    )
    raw = record(97)
    logged = LoggedEvent(0, raw, decode_event_record(raw, SMARTLIVING_1050_FW51))
    with patch.object(sensor.coordinator.panel, "_model", SMARTLIVING_1050_FW51):
        events = await sensor._async_event_log()
    assert events.attributes(logged, None)["event"] == "free_event"
    await unload_all(hass)


def test_size_of_a_long_list() -> None:
    """For information: 100 events with long names."""
    long = "X" * 16
    config = {
        "keypad_labels": label_block([long] * 10),
        "code_labels": label_block([long] * 50),
        "area_labels": label_block([long] * 10),
    }
    items = [item(record(10, 1, 2, areas=0x3FF), config=config) for _ in range(100)]
    size = len(json.dumps(items))
    assert size > 16384  # over what the recorder keeps: why it is left out
    print(f"100 events: {size} bytes")


async def test_sensor_in_italian(hass: HomeAssistant, panel) -> None:
    """With Home Assistant in Italian, description_local is Italian and
    description English."""
    hass.config.language = "it"
    await add_entry(hass, panel)
    state = next(
        s for s in hass.states.async_all("sensor") if s.entity_id.endswith("event_log")
    )
    first = state.attributes["event_log"][0]
    assert first["description_local"] != first["description"]
    assert first["description"].startswith(("Zone alarm", "Zone bypassed", "Area"))
    await unload_all(hass)


def test_event_log_takes_its_texts() -> None:
    """The texts come from the caller (Home Assistant's translations): the
    event log has no default and reads no file."""
    with pytest.raises(TypeError):
        EventLog(CONFIG, SL1050)  # type: ignore[call-arg]
    source = (COMPONENT / "event_log.py").read_text(encoding="utf-8")
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names
            if isinstance(node, ast.Import)
            else [ast.alias(node.module or "")]
        )
    }
    assert not {"json", "pathlib", "os", "io"} & imported


@pytest.mark.parametrize("started", [True, False])
@pytest.mark.parametrize("code", range(114))
def test_every_code_renders_in_both_languages(code: int, started: bool) -> None:
    """Every firmware 6 code, started and ended, takes its own text in English
    and in Italian: no placeholder left, and no fallback to English."""
    key = event_type(code).key
    shown = item(record(code, on=started), local=BundledTexts("it"))
    assert shown["event"] == key
    for field, language in (("description", "en"), ("description_local", "it")):
        texts = messages(language)
        name = texts[f"event_{key}"]
        expected = name if started else texts["event_ended"].format(event=name)
        assert shown[field] == expected
        assert "{" not in shown[field]
