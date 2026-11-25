"""The panel's events as the event log sensor shows them.

One event read from the panel becomes a dictionary: its description in
English and in Home Assistant's language, a stable key, and the names of
what it refers to, from the stored configuration. The texts are the
integration's translations (the event_* messages). No Home Assistant
imports.
"""

from collections.abc import Mapping
from datetime import tzinfo
from functools import cache
import json
from pathlib import Path
from typing import Any, Protocol

from .api import LoggedEvent
from .protocol.event_types import (
    NUMBERED_CODES,
    OUTPUT_FAULT,
    EventRefs,
    ObjectType,
    Place,
    Ref,
    event_number,
    event_refs,
    event_type,
)
from .protocol.events import CODE_OFFSET, NO_LOCATION
from .protocol.model import FALLBACK_MODEL, PanelModel
from .stored_config import (
    area_names,
    keypad_names,
    name_table,
    output_fault_is_dirty_detector_option,
    scenario_names,
    stored_model,
    zone_names,
)

# Firmware 5.1's free events 1-14 (its codes 97-110) have no firmware 6 code
_FW51_FREE_EVENTS = range(97, 111)
UNKNOWN_EVENT = "unknown"
FREE_EVENT = "free_event"
# The output fault event when the installer set it to report a dirty smoke
# detector (see protocol.config.output_fault_is_dirty_detector)
DIRTY_DETECTOR = "detector_dusty"

# The key an event's object goes under, by its type
ABOUT_KEYS: dict[ObjectType, str] = {
    ObjectType.ZONE: "zone",
    ObjectType.PHONE_NUMBER: "phone_number",
    ObjectType.TIMER: "timer",
    ObjectType.EXPANDER: "expander",
    ObjectType.KEYPAD: "keypad",
    ObjectType.READER: "reader",
    ObjectType.SIREN: "siren",
    ObjectType.SCENARIO: "scenario",
    ObjectType.PROGRAMMABLE_EVENT: "programmable_event",
    ObjectType.EMERGENCY_KEY: "emergency_key",
}
_PLACE_NAMES = {Place.REMOTE: "Remote", Place.TELEPHONE: "From telephone"}


class EventTexts(Protocol):
    """Texts by key with their placeholders filled in (notifications.Texts
    is one); a key with no text comes back as the key itself."""

    def __call__(self, key: str, **placeholders: Any) -> str:
        """The text of a key."""


def fill_text(
    key: str,
    placeholders: Mapping[str, Any],
    strings: Mapping[str, str],
    english: Mapping[str, str],
) -> str:
    """A text with its placeholders filled in: in the language, else in
    English (no text, or one whose placeholders do not fit), else the key
    itself."""
    for template in (strings.get(key), english.get(key)):
        if template:
            try:
                return template.format(**placeholders)
            except (KeyError, IndexError, ValueError):
                continue
    return key


class BundledTexts:
    """The texts of one of the integration's translation files.

    It reads the file: use it outside Home Assistant's event loop (or once,
    before it).
    """

    def __init__(self, language: str = "en") -> None:
        """Load the event texts of a language, English where there are none."""
        self._strings = _messages(language)
        self._english = _messages("en")

    def __call__(self, key: str, **placeholders: Any) -> str:
        """A text with its placeholders filled in (see fill_text)."""
        return fill_text(key, placeholders, self._strings, self._english)


@cache
def _messages(language: str) -> dict[str, str]:
    """A translation file's messages; none if it cannot be read."""
    path = Path(__file__).parent / "translations" / f"{language}.json"
    try:
        exceptions = json.loads(path.read_text(encoding="utf-8")).get("exceptions", {})
        return {key: value["message"] for key, value in exceptions.items()}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return {}


class EventLog:
    """How the event log sensor shows the panel's events.

    Built once for a series of events: it decodes the stored names and the
    general option it needs. model is the profile the events were decoded
    with (the panel's, at run time); by default the stored configuration's.
    description is in English (english texts; the bundled English ones by
    default), description_local in Home Assistant's language (local texts;
    English by default). A text that cannot be found falls back to fixed
    English rather than failing.
    """

    def __init__(
        self,
        stored: Mapping[str, Any],
        model: PanelModel | None = None,
        english: EventTexts | None = None,
        local: EventTexts | None = None,
    ) -> None:
        """Take what the events need from the stored configuration."""
        self._model = model or stored_model(stored) or FALLBACK_MODEL
        self._names = EventNames(stored)
        self._areas = area_names(stored)
        self._dirty_detector = output_fault_is_dirty_detector_option(stored)
        self._english = english or BundledTexts()
        self._local = local or self._english

    def attributes(
        self, logged: LoggedEvent, time_zone: tzinfo | None
    ) -> dict[str, Any]:
        """One event of the event log sensor.

        The time is the panel's local time in time_zone. Names come from the
        stored configuration; device_id and authorized_id are the raw bytes
        in hex.
        """
        event = logged.event
        key, describe = self._description(logged)
        refs = event_refs(event, self._model)
        item: dict[str, Any] = {
            # for a repeated hour (clocks going back) the first one is taken
            "timestamp": event.timestamp.replace(tzinfo=time_zone).isoformat(),
            "description": describe(self._english),
            "description_local": describe(self._local),
            "event": key,
            "code": event.code,
            "ended": not event.activation,
        }
        if refs.via is not None:
            item["via"] = refs.via.value
        for ref in refs.about:
            if (about_key := ABOUT_KEYS.get(ref.type)) is not None:
                item[about_key] = self._names.of(ref)
        if event.code in NUMBERED_CODES and event.agent is not None:
            item["number"] = event_number(event.agent)
        kind = event_type(event.code)
        if kind is not None and kind.areas:
            item["affected_areas"] = (
                ", ".join(
                    _named(self._areas, area - 1, "Area")
                    for area in sorted(event.areas)
                )
                or "None"
            )
        _people_and_places(item, refs, logged, self._names)
        item["raw_hex_data"] = logged.raw.hex()
        return item

    def _description(self, logged: LoggedEvent):
        """The event's key, and its description given the texts of a language."""
        event = logged.event
        kind = event_type(event.code)
        panel_code = logged.raw[CODE_OFFSET] & 0x7F
        free = self._model.event_codes is not None and panel_code in _FW51_FREE_EVENTS
        dirty_detector = event.code == OUTPUT_FAULT and self._dirty_detector
        if dirty_detector:
            key = DIRTY_DETECTOR
        elif kind is not None:
            key = kind.key
        elif free:
            key = FREE_EVENT
        else:
            key = UNKNOWN_EVENT

        def name(texts: EventTexts) -> str:
            if dirty_detector or kind is not None:
                return _say(texts, f"event_{key}", key.replace("_", " ").capitalize())
            if free:
                number = panel_code - _FW51_FREE_EVENTS[0] + 1
                return _say(texts, "event_free", f"Free event {number}", number=number)
            return _say(
                texts,
                "event_unknown",
                f"Unknown event (code {panel_code})",
                code=panel_code,
            )

        def describe(texts: EventTexts) -> str:
            text = name(texts)
            if event.activation:
                return text
            return _say(texts, "event_ended", f"{text} (ended)", event=text)

        return key, describe


def event_attributes(
    logged: LoggedEvent,
    stored: Mapping[str, Any],
    time_zone: tzinfo | None,
    english: EventTexts | None = None,
    local: EventTexts | None = None,
    model: PanelModel | None = None,
) -> dict[str, Any]:
    """One event of the event log sensor (see EventLog); for a series of
    events, build an EventLog once instead."""
    return EventLog(stored, model, english, local).attributes(logged, time_zone)


def _say(texts: EventTexts, key: str, fallback: str, **placeholders: Any) -> str:
    """A text, or the fixed English fallback if there is none."""
    try:
        text = texts(key, **placeholders)
    except (KeyError, IndexError, ValueError):
        return fallback
    return fallback if text == key else text


def _people_and_places(
    item: dict[str, Any], refs: EventRefs, logged: LoggedEvent, names: "EventNames"
) -> None:
    """Who did it (a user code or key) and where (a keypad, a reader, a
    remote command, the telephone), with their raw numbers."""
    if refs.who is not None:
        item["authorized_by"] = names.of(refs.who)
        item["authorized_id"] = f"{refs.who.index:02x}"
    if refs.where is not None:
        location = logged.event.location
        if refs.where is Place.UNKNOWN:
            item["device"] = f"Location {location}"
        elif isinstance(refs.where, Place):
            item["device"] = _PLACE_NAMES[refs.where]
        else:
            item["device"] = names.of(refs.where)
        item["device_id"] = f"{NO_LOCATION if location is None else location:02x}"


def _named(names: list[str], index: int, kind: str) -> str:
    """A scenario, zone or area (0-based) by its label."""
    if 0 <= index < len(names):
        return names[index] or f"{kind} {index + 1}"
    return f"{kind} {index + 1} (Unknown)"


class EventNames:
    """Names of what an event refers to, from the stored panel configuration.

    Names not stored (until the configuration is read again, or label
    tables the integration does not read) fall back to numbers.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Take the names from a stored configuration (each table decoded
        once, when first needed)."""
        self._config = config
        self._keypads = keypad_names(config)
        self._tables: dict[str, list[str]] = {}

    @staticmethod
    def _name(names: list[str], index: int, fallback: str) -> str:
        return (names[index] if 0 <= index < len(names) else "") or fallback

    def of(self, ref: Ref) -> str:
        """The name of an object an event refers to."""
        number = ref.index + 1
        match ref.type:
            case ObjectType.ZONE:
                return _named(self._decoded("zones", zone_names), ref.index, "Zone")
            case ObjectType.SCENARIO:
                return _named(
                    self._decoded("scenarios", scenario_names), ref.index, "Scenario"
                )
            case ObjectType.AREA:
                return _named(self._decoded("areas", area_names), ref.index, "Area")
            case ObjectType.USER_CODE:
                return self.user_code(ref.index)
            case ObjectType.KEYPAD:
                return self._name(self._keypads, ref.index, f"Keypad {number}")
            case ObjectType.KEY:
                return self._table("keys", ref.index, f"Key {number}")
            case ObjectType.READER:
                return self._table("readers", ref.index, f"Reader {number}")
            case ObjectType.EXPANDER:
                return self._table("expanders", ref.index, f"Expander {number}")
            case ObjectType.TIMER:
                return self._table("timers", ref.index, f"Timer {number}")
            case ObjectType.SIREN:
                return self._table("sirens", ref.index, f"Siren {number}")
            case ObjectType.PHONE_NUMBER:
                return self._table("phone_numbers", ref.index, f"Number {number}")
            case ObjectType.TERMINAL:
                return f"Terminal {number}"
            case ObjectType.PROGRAMMABLE_EVENT:
                return f"Programmable event {number}"
            case ObjectType.VIDEO_SENSOR:
                return f"Video sensor {number}"
            case ObjectType.EMERGENCY_KEY:
                return f"Emergency key {number}"
        return f"{ref.type.name.replace('_', ' ').capitalize()} {number}"

    def _decoded(self, kind: str, decode) -> list[str]:
        if kind not in self._tables:
            self._tables[kind] = decode(self._config)
        return self._tables[kind]

    def _table(self, kind: str, index: int, fallback: str) -> str:
        names = self._decoded(kind, lambda config: name_table(config, kind))
        return self._name(names, index, fallback)

    def user_code(self, index: int) -> str:
        """A user code by its name; before the names are stored, as earlier
        versions showed it."""
        codes = self._decoded("codes", lambda config: name_table(config, "codes"))
        if codes:
            return self._name(codes, index, f"Code {index + 1}")
        return "Master Code" if index == 0 else f"User {index}"
