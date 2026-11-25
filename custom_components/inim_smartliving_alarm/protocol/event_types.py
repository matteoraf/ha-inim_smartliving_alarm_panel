"""What each event of the panel's log is about.

Every firmware 6 event code (0-113) has a type: a stable key, what its
agent byte refers to, what its location byte refers to, and whether its
area mask holds the areas concerned. Firmware 5.1 codes are first put in
firmware 6 numbering (see event_codes.py).

The table and the rules follow the panel's own rendering of its log
(firmware 6). The arming and disarming events, and the reset and overtime
events, tell how they happened from the combination of agent and location
(see event_refs): a code on a keypad, a key on a reader, a remote command,
a command zone, the patrol time, a timer, a shortcut on a keypad.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from .events import REMOTE_LOCATION, PanelEvent
from .model import PanelModel


class ObjectType(IntEnum):
    """What an event's agent or location byte refers to."""

    NONE = 0
    ZONE = 1  # logical zone index 0..2L-1
    TERMINAL = 2
    USER_CODE = 3
    KEY = 4
    PHONE_NUMBER = 5
    TIMER = 6
    EMERGENCY_KEY = 7
    EXPANDER = 8
    KEYPAD = 9
    READER = 10
    AREA = 11
    SIREN = 12
    SCENARIO = 13
    PROGRAMMABLE_EVENT = 14
    VIDEO_SENSOR = 15


@dataclass(frozen=True, slots=True)
class EventType:
    """One event code: its key and what its agent and location refer to."""

    code: int  # firmware 6 numbering
    key: str  # stable, English
    agent: ObjectType
    location: ObjectType
    areas: bool  # the area mask holds the areas concerned


def _type(
    code: int, key: str, agent: ObjectType, location: ObjectType, areas: bool
) -> EventType:
    return EventType(code, key, agent, location, areas)


_O = ObjectType  # short, for the table below

EVENT_TYPES: tuple[EventType, ...] = (
    _type(0, "zone_alarm", _O.ZONE, _O.NONE, True),
    _type(1, "terminal_tamper", _O.ZONE, _O.NONE, True),
    _type(2, "area_alarm", _O.NONE, _O.NONE, True),
    _type(3, "perimeter_alarm", _O.NONE, _O.NONE, True),
    _type(4, "area_tamper", _O.NONE, _O.NONE, True),
    _type(5, "zone_bypassed", _O.ZONE, _O.USER_CODE, True),
    _type(6, "real_time_zone", _O.ZONE, _O.NONE, True),
    _type(7, "area_not_ready_to_arm", _O.NONE, _O.NONE, True),
    _type(8, "area_away_arm_request", _O.NONE, _O.NONE, True),
    _type(9, "area_stay_arm_request", _O.NONE, _O.NONE, True),
    _type(10, "area_armed_in_away_mode", _O.NONE, _O.NONE, True),
    _type(11, "area_armed_in_stay_mode", _O.NONE, _O.NONE, True),
    _type(12, "area_disarmed", _O.NONE, _O.NONE, True),
    _type(13, "area_alarm_memory_reset", _O.USER_CODE, _O.KEYPAD, True),
    _type(14, "area_exit_time", _O.NONE, _O.NONE, True),
    _type(15, "area_entry_time", _O.NONE, _O.NONE, True),
    _type(16, "area_pre_arm_time", _O.NONE, _O.NONE, True),
    _type(17, "overtime_request", _O.USER_CODE, _O.KEYPAD, True),
    _type(18, "chime_on_area", _O.NONE, _O.NONE, True),
    _type(19, "forced_area_arming", _O.USER_CODE, _O.KEYPAD, True),
    _type(20, "area_failed_to_arm", _O.NONE, _O.NONE, True),
    _type(21, "valid_user_code", _O.USER_CODE, _O.KEYPAD, True),
    _type(22, "valid_key", _O.KEY, _O.READER, True),
    _type(23, "valid_code_at_keypad", _O.NONE, _O.NONE, False),
    _type(24, "valid_key_at_reader", _O.NONE, _O.NONE, False),
    _type(25, "valid_code_on_area", _O.NONE, _O.NONE, False),
    _type(26, "valid_key_on_area", _O.NONE, _O.NONE, False),
    _type(27, "failed_call", _O.PHONE_NUMBER, _O.NONE, False),
    _type(28, "timer_event", _O.TIMER, _O.NONE, False),
    _type(29, "thermostat_on", _O.NONE, _O.KEYPAD, True),
    _type(30, "scenario_activated", _O.SCENARIO, _O.NONE, True),
    _type(31, "programmable_event", _O.PROGRAMMABLE_EVENT, _O.NONE, False),
    _type(32, "emergency_key", _O.EMERGENCY_KEY, _O.KEYPAD, False),
    _type(33, "panic", _O.NONE, _O.NONE, False),
    _type(34, "periodic_event", _O.NONE, _O.NONE, False),
    _type(35, "open_panel_tamper", _O.NONE, _O.NONE, False),
    _type(36, "dislodged_panel_tamper", _O.NONE, _O.NONE, False),
    _type(37, "zone_fuse_fault", _O.NONE, _O.NONE, False),
    _type(38, "ibus_fuse_fault", _O.NONE, _O.NONE, False),
    _type(39, "battery_failure", _O.NONE, _O.NONE, False),
    _type(40, "mains_failure", _O.NONE, _O.NONE, False),
    _type(41, "expander_tamper", _O.EXPANDER, _O.NONE, False),
    _type(42, "keypad_tamper", _O.KEYPAD, _O.NONE, False),
    _type(43, "reader_tamper", _O.READER, _O.NONE, False),
    _type(44, "siren_tamper", _O.SIREN, _O.NONE, False),
    _type(45, "nexus_tamper", _O.NONE, _O.NONE, False),
    _type(46, "livpwr100_tamper", _O.NONE, _O.NONE, False),
    _type(47, "video_sensor_tamper", _O.NONE, _O.NONE, False),
    _type(48, "expander_loss", _O.EXPANDER, _O.NONE, False),
    _type(49, "keypad_loss", _O.KEYPAD, _O.NONE, False),
    _type(50, "reader_loss", _O.READER, _O.NONE, False),
    _type(51, "siren_loss", _O.SIREN, _O.NONE, False),
    _type(52, "nexus_loss", _O.NONE, _O.NONE, False),
    _type(53, "livpwr100_loss", _O.NONE, _O.NONE, False),
    _type(54, "video_sensor_loss", _O.NONE, _O.NONE, False),
    _type(55, "radio_jamming", _O.NONE, _O.NONE, False),
    _type(56, "low_battery_wireless_zone", _O.ZONE, _O.NONE, True),
    _type(57, "wireless_zone_loss", _O.ZONE, _O.NONE, True),
    _type(58, "valid_installer_code", _O.NONE, _O.KEYPAD, False),
    _type(59, "invalid_code", _O.NONE, _O.KEYPAD, False),
    _type(60, "false_key", _O.NONE, _O.READER, False),
    _type(61, "nexus_fault", _O.NONE, _O.NONE, False),
    _type(62, "telephone_line_down", _O.NONE, _O.NONE, False),
    _type(63, "date_and_time_lost", _O.NONE, _O.NONE, False),
    _type(64, "call_queue_full", _O.NONE, _O.NONE, False),
    _type(65, "successful_call", _O.NONE, _O.NONE, False),
    _type(66, "programming_mode", _O.NONE, _O.NONE, False),
    _type(67, "ongoing_call", _O.NONE, _O.NONE, False),
    _type(68, "failed_to_send_sms_text_message", _O.NONE, _O.NONE, False),
    _type(69, "output_fault", _O.ZONE, _O.NONE, False),
    _type(70, "credit_running_low", _O.NONE, _O.NONE, False),
    _type(71, "date_and_time_changed", _O.NONE, _O.NONE, False),
    _type(72, "battery_internal_resistance", _O.NONE, _O.NONE, False),
    _type(73, "shorted_battery", _O.NONE, _O.NONE, False),
    _type(74, "battery_disconnected", _O.NONE, _O.NONE, False),
    _type(75, "power_supply_overload", _O.NONE, _O.NONE, False),
    _type(76, "power_supply_overheating", _O.NONE, _O.NONE, False),
    _type(77, "ground_fault", _O.NONE, _O.NONE, False),
    _type(78, "overvoltage_output_1", _O.NONE, _O.NONE, False),
    _type(79, "overvoltage_output_2", _O.NONE, _O.NONE, False),
    _type(80, "overvoltage_output_3", _O.NONE, _O.NONE, False),
    _type(81, "overvoltage_bus", _O.NONE, _O.NONE, False),
    _type(82, "low_voltage_output_1", _O.NONE, _O.NONE, False),
    _type(83, "low_voltage_output_2", _O.NONE, _O.NONE, False),
    _type(84, "low_voltage_output_3", _O.NONE, _O.NONE, False),
    _type(85, "low_voltage_output_bus", _O.NONE, _O.NONE, False),
    _type(86, "short_circuit_output_1", _O.NONE, _O.NONE, False),
    _type(87, "short_circuit_output_2", _O.NONE, _O.NONE, False),
    _type(88, "short_circuit_output_3", _O.NONE, _O.NONE, False),
    _type(89, "short_circuit_output_bus", _O.NONE, _O.NONE, False),
    _type(90, "output_overload_1", _O.NONE, _O.NONE, False),
    _type(91, "output_overload_2", _O.NONE, _O.NONE, False),
    _type(92, "output_overload_3", _O.NONE, _O.NONE, False),
    _type(93, "output_overload_bus", _O.NONE, _O.NONE, False),
    _type(94, "communication_with_the_power_supply_failed", _O.NONE, _O.NONE, False),
    _type(95, "ongoing_call_on_number_1", _O.NONE, _O.NONE, False),
    _type(96, "ongoing_call_on_number_15", _O.NONE, _O.NONE, False),
    _type(97, "data_sync_between_panel_and_ip2rx", _O.NONE, _O.NONE, False),
    _type(98, "ip_connection_lost", _O.NONE, _O.NONE, False),
    _type(99, "gprs_connection_lost", _O.NONE, _O.NONE, False),
    _type(100, "sia_ip_polling_failed", _O.NONE, _O.NONE, False),
    _type(101, "onvif_camera_2_alarm", _O.NONE, _O.NONE, False),
    _type(102, "onvif_camera_3_alarm", _O.NONE, _O.NONE, False),
    _type(103, "onvif_camera_4_alarm", _O.NONE, _O.NONE, False),
    _type(104, "onvif_camera_5_alarm", _O.NONE, _O.NONE, False),
    _type(105, "free_event_1", _O.NONE, _O.NONE, False),
    _type(106, "free_event_2", _O.NONE, _O.NONE, False),
    _type(107, "free_event_3", _O.NONE, _O.NONE, False),
    _type(108, "free_event_4", _O.NONE, _O.NONE, False),
    _type(109, "free_event_5", _O.NONE, _O.NONE, False),
    _type(110, "free_event_6", _O.NONE, _O.NONE, False),
    _type(111, "free_event_7", _O.NONE, _O.NONE, False),
    _type(112, "free_event_8", _O.NONE, _O.NONE, False),
    _type(113, "free_event_9", _O.NONE, _O.NONE, False),
)

# Codes with special meanings of the agent or location byte
ZONE_BYPASSED = 5
AREA_RESET = 13
OVERTIME_REQUEST = 17
FORCED_ARMING = 19
PANIC = 33
PERIODIC_EVENT = 34
OUTPUT_FAULT = 69
# Arm requests, armed away and stay, disarmed, forced arming: the location
# is a keypad, a reader (from K), a timer (from K + P), remote or none
ARMING_CODES = frozenset((8, 9, 10, 11, 12, FORCED_ARMING))
# Reset and overtime request: a keypad, a reader (from K), or none
RESET_CODES = frozenset((AREA_RESET, OVERTIME_REQUEST))
# Codes whose agent the panel shows as a number, 001-256
NUMBERED_CODES = frozenset((PANIC, PERIODIC_EVENT))


def event_type(code: int | None) -> EventType | None:
    """The type of a firmware 6 event code; None for a code without one."""
    if code is None or not 0 <= code < len(EVENT_TYPES):
        return None
    return EVENT_TYPES[code]


class Via(StrEnum):
    """How an event happened."""

    KEYPAD_CODE = "keypad_code"  # a code typed on a keypad
    READER_KEY = "reader_key"  # a key (tag) on a reader
    REMOTE = "remote"  # app, cloud, Home Assistant or the installer software
    COMMAND_ZONE = "command_zone"
    PATROL_TIME = "patrol_time"  # the patrol time ran out
    TIMER = "timer"
    KEYPAD_SHORTCUT = "keypad_shortcut"
    TELEPHONE = "telephone"
    USER_CODE = "user_code"  # a zone bypassed by a user code
    AUTOMATIC = "automatic"  # a zone bypassed by the panel itself


class Place(StrEnum):
    """Where an event comes from, when it is not a keypad or reader."""

    REMOTE = "remote"
    TELEPHONE = "telephone"
    UNKNOWN = "unknown"  # a location byte outside what the event allows


@dataclass(frozen=True, slots=True)
class Ref:
    """An object an event refers to: its type and 0-based index."""

    type: ObjectType
    index: int


@dataclass(frozen=True, slots=True)
class EventRefs:
    """What one event refers to.

    who: the user code or key that did it; where: the keypad or reader, or
    a Place; about: the objects the event is about (zone, timer, expander,
    keypad, ...); via: how it happened, for the events that tell.
    """

    who: Ref | None = None
    where: Ref | Place | None = None
    about: tuple[Ref, ...] = ()
    via: Via | None = None


_PEOPLE = (ObjectType.USER_CODE, ObjectType.KEY)
_PLACES = (ObjectType.KEYPAD, ObjectType.READER)


def event_refs(event: PanelEvent, model: PanelModel) -> EventRefs:
    """What an event refers to, from its type and its agent and location.

    The model gives K keypads, P readers and T timers: an arming event's
    location counts the keypads from 0, the readers from K and the timers
    from K + P.
    """
    kind = event_type(event.code)
    if kind is None:
        return EventRefs()
    agent, location = event.agent, event.location
    if kind.code in ARMING_CODES:
        return _arming(agent, location, model)
    if kind.code in RESET_CODES:
        return _reset(agent, location, model)
    if kind.code == ZONE_BYPASSED:
        about = () if agent is None else (Ref(ObjectType.ZONE, agent),)
        if location is None:
            return EventRefs(about=about, via=Via.AUTOMATIC)
        if location == REMOTE_LOCATION:
            return EventRefs(about=about, where=Place.REMOTE, via=Via.REMOTE)
        return EventRefs(
            who=Ref(ObjectType.USER_CODE, location), about=about, via=Via.USER_CODE
        )
    who, about = None, ()
    if (
        agent is not None
        and kind.agent is not ObjectType.NONE
        and kind.code not in NUMBERED_CODES
    ):
        ref = Ref(kind.agent, agent)
        if kind.agent in _PEOPLE:
            who = ref
        else:
            about = (ref,)
    where: Ref | Place | None = None
    if kind.location in _PLACES:
        if location == REMOTE_LOCATION:
            where = Place.REMOTE
        elif location is None:
            # A keypad event without a keypad comes from the telephone
            if kind.location is ObjectType.KEYPAD:
                where = Place.TELEPHONE
        else:
            where = Ref(kind.location, location)
    via = Via.TELEPHONE if where is Place.TELEPHONE else None
    return EventRefs(who=who, where=where, about=about, via=via)


def _arming(agent: int | None, location: int | None, model: PanelModel) -> EventRefs:
    """Arm requests, armed, disarmed, forced arming."""
    keypads, readers = model.keypads, model.keypads + model.readers
    timers = readers + model.timers
    if location is None:
        if agent is None:
            return EventRefs(via=Via.PATROL_TIME)
        return EventRefs(about=(Ref(ObjectType.ZONE, agent),), via=Via.COMMAND_ZONE)
    if location == REMOTE_LOCATION:
        who = None if agent is None else Ref(ObjectType.USER_CODE, agent)
        return EventRefs(who=who, where=Place.REMOTE, via=Via.REMOTE)
    if location < keypads:
        where = Ref(ObjectType.KEYPAD, location)
        if agent is None:
            return EventRefs(where=where, via=Via.KEYPAD_SHORTCUT)
        return EventRefs(
            who=Ref(ObjectType.USER_CODE, agent), where=where, via=Via.KEYPAD_CODE
        )
    if location < readers:
        who = None if agent is None else Ref(ObjectType.KEY, agent)
        return EventRefs(
            who=who,
            where=Ref(ObjectType.READER, location - keypads),
            via=Via.READER_KEY,
        )
    if location < timers:
        about = () if agent is None else (Ref(ObjectType.TIMER, agent),)
        return EventRefs(about=about, via=Via.TIMER)
    return EventRefs(where=Place.UNKNOWN)


def _reset(agent: int | None, location: int | None, model: PanelModel) -> EventRefs:
    """Reset and overtime request: by a code on a keypad, a key on a reader,
    or a code from a remote."""
    keypads, readers = model.keypads, model.keypads + model.readers
    if location is None:
        if agent is None:
            return EventRefs()
        return EventRefs(who=Ref(ObjectType.USER_CODE, agent), via=Via.REMOTE)
    if location == REMOTE_LOCATION:
        who = None if agent is None else Ref(ObjectType.USER_CODE, agent)
        return EventRefs(who=who, where=Place.REMOTE, via=Via.REMOTE)
    if location < keypads:
        where = Ref(ObjectType.KEYPAD, location)
        if agent is None:
            return EventRefs(where=where)
        return EventRefs(
            who=Ref(ObjectType.USER_CODE, agent), where=where, via=Via.KEYPAD_CODE
        )
    if location < readers:
        who = None if agent is None else Ref(ObjectType.KEY, agent)
        return EventRefs(
            who=who,
            where=Ref(ObjectType.READER, location - keypads),
            via=Via.READER_KEY,
        )
    return EventRefs(where=Place.UNKNOWN)


def event_number(agent: int | None) -> str | None:
    """The number a panic or periodic event shows, as the panel does
    (001-256)."""
    return None if agent is None else f"{agent + 1:03d}"
