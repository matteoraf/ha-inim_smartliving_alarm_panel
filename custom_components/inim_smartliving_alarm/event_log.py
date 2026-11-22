"""The panel's events as the event log sensor shows them.

One event read from the panel becomes a dictionary of English texts and
names from the stored configuration. No Home Assistant imports.
"""

from collections.abc import Mapping
from datetime import tzinfo
from typing import Any

from .api import LoggedEvent
from .protocol.events import (
    AREA_ACTIONS,
    AREA_ZONE_ALARM,
    AREA_ZONE_ALARM_END,
    AREAS_ARMED,
    AREAS_ARMED_END,
    AREAS_DISARMED,
    AREAS_DISARMED_END,
    AREAS_RESET,
    FAILED_CALL,
    FAILED_CALL_END,
    INSTALLER_CODE_INSERTED,
    NO_AGENT,
    NO_LOCATION,
    PROGRAMMING_ENDED,
    PROGRAMMING_STARTED,
    REMOTE_LOCATION,
    SCENARIO_ACTIVATED,
    VALID_CODE,
    VALID_KEY_ACTION,
    ZONE_ACTIONS,
    ZONE_EXCLUDED,
    ZONE_EXCLUDED_END,
    reader_index,
)
from .protocol.model import FALLBACK_MODEL
from .stored_config import (
    area_names,
    keypad_names,
    name_table,
    scenario_names,
    zone_names,
)

# What each event says; other codes are "Unknown Action (0xNN)"
EVENT_TEXTS = {
    SCENARIO_ACTIVATED: "Scenario Activated",
    AREA_ZONE_ALARM: "Area/Zone Alarm",
    AREA_ZONE_ALARM_END: "Area/Zone Alarm End",
    AREAS_ARMED: "Area(s) Armed",
    AREAS_DISARMED: "Area(s) Disarmed",
    AREAS_RESET: "Area(s) Reset",
    ZONE_EXCLUDED: "Zone Excluded",
    ZONE_EXCLUDED_END: "End of Zone Excluded",
    AREAS_ARMED_END: "End of Area(s) Armed",
    AREAS_DISARMED_END: "End of Area(s) Disarmed",
    INSTALLER_CODE_INSERTED: "Installer Code Inserted",
    PROGRAMMING_STARTED: "Programming Started",
    PROGRAMMING_ENDED: "Programming Ended",
    FAILED_CALL: "Failed Call",
    FAILED_CALL_END: "End of Failed Call",
    VALID_CODE: "Valid Code",
    VALID_KEY_ACTION: "Valid Key",
}


def event_attributes(
    logged: LoggedEvent, stored: Mapping[str, Any], time_zone: tzinfo | None
) -> dict[str, Any]:
    """One event of the event log sensor.

    The time is the panel's local time in time_zone; scenario, zone, areas,
    device and user are named from the stored configuration; device_id and
    authorized_id are the raw bytes in hex ("ff" for none).
    """
    event = logged.event
    action = event.action
    agent = NO_AGENT if event.agent is None else event.agent
    location = NO_LOCATION if event.location is None else event.location
    item: dict[str, Any] = {
        # for a repeated hour (clocks going back) the first one is taken
        "timestamp": event.timestamp.replace(tzinfo=time_zone).isoformat(),
        "description": EVENT_TEXTS.get(action, f"Unknown Action (0x{action:02X})"),
    }
    if action == SCENARIO_ACTIVATED:
        item["scenario"] = _named(scenario_names(stored), agent, "Scenario")
    elif action in ZONE_ACTIONS:
        item["zone"] = _named(zone_names(stored), agent, "Zone")
    if action in AREA_ACTIONS or action in ZONE_ACTIONS:
        areas = area_names(stored)
        item["affected_areas"] = (
            ", ".join(_named(areas, area - 1, "Area") for area in sorted(event.areas))
            or "None"
        )
    if action in AREA_ACTIONS:
        names = EventNames(stored)
        item["device_id"] = f"{location:02x}"
        item["authorized_id"] = f"{agent:02x}"
        if action == VALID_KEY_ACTION:
            item["authorized_by"] = names.key(agent)
            item["device"] = names.reader(location)
        else:
            item["device"] = names.location(location)
            item["authorized_by"] = names.agent(agent, location)
    item["raw_hex_data"] = logged.raw.hex()
    return item


def _named(names: list[str], index: int, kind: str) -> str:
    """A scenario, zone or area (0-based) by its label."""
    if 0 <= index < len(names):
        return names[index] or f"{kind} {index + 1}"
    return f"{kind} {index + 1} (Unknown)"


class EventNames:
    """Names of what an event refers to, from the stored panel configuration.

    An event's location is a keypad (0 to K-1), a reader (K to K+P-1),
    254 for a remote command (app, cloud, Home Assistant or the installer
    software) or 255 for none; its agent is a user code, or a key when the
    location is a reader. K and P are the model's keypad and reader counts.
    Names not stored (until the configuration is read again) fall back to
    numbers.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Take the names from a stored configuration."""
        self._keypads = keypad_names(config)
        self._codes = name_table(config, "codes")
        self._keys = name_table(config, "keys")
        self._readers = name_table(config, "readers")

    @staticmethod
    def _name(names: list[str], index: int, fallback: str) -> str:
        return (names[index] if 0 <= index < len(names) else "") or fallback

    @property
    def _keypad_count(self) -> int:
        # A configuration without keypad names: the 1050's
        return len(self._keypads) or FALLBACK_MODEL.keypads

    def _reader_index(self, location: int) -> int | None:
        return reader_index(location, self._keypad_count)

    def location(self, location: int) -> str:
        """Where the event comes from."""
        if location == REMOTE_LOCATION:
            return "Remote"
        if location == NO_LOCATION:
            return "-"
        if location < self._keypad_count:
            return self._name(self._keypads, location, f"Keypad {location + 1}")
        if (reader := self._reader_index(location)) is not None:
            return self.reader(reader)
        return f"Location {location}"

    def agent(self, agent: int, location: int | None) -> str:
        """Who caused the event: a user code, or a key used on a reader."""
        if agent == NO_AGENT:
            return "-"
        if location is not None and self._reader_index(location) is not None:
            return self.key(agent)
        if self._codes:
            return self._name(self._codes, agent, f"Code {agent + 1}")
        return "Master Code" if agent == 0 else f"User {agent}"

    def key(self, index: int) -> str:
        """A key (tag), by its 0-based index."""
        return self._name(self._keys, index, f"Key {index + 1}")

    def reader(self, index: int) -> str:
        """A reader, by its 0-based index."""
        return self._name(self._readers, index, f"Reader {index + 1}")
