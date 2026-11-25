"""How decoded panel values are shown in entity attributes.

Stable English values that templates and automations can rely on: enum
members as lower-case keys, times with a short unit.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    ZONE_STATE_ALARMED,
    ZONE_STATE_CLEAR,
    ZONE_STATE_SHORT,
    ZONE_STATE_TAMPER,
)
from .protocol.commands import ArmMode
from .protocol.config import (
    TERMINAL_NOT_ASSIGNED,
    AreaConfig,
    PanelTime,
    SensorKind,
    Terminal,
    TerminalType,
    TimeUnit,
    ZoneConfig,
    ZoneType,
)
from .protocol.status import AreaMode, ZoneState

if TYPE_CHECKING:
    from .selection import OutputItem

# Enum members whose lower-case name is not how one would write them, by
# enum: IntEnum members of different enums compare equal by value
_VALUE_KEYS: dict[tuple[type, int], str] = {(ZoneType, ZoneType.H24): "24_hour"}

_UNIT_TEXT = {TimeUnit.SECONDS: "s", TimeUnit.MINUTES: "min"}

# What a scenario does to an area, and the modes of the arm_area action
ARM_MODE_TEXT: dict[ArmMode, str] = {
    ArmMode.KEEP: "keep",
    ArmMode.AWAY: "total",
    ArmMode.STAY: "partial",
    ArmMode.INSTANT: "instant",
    ArmMode.DISARM: "disarm",
}

# The modes an area switch can arm in; its option values are their words
SWITCH_ARM_MODES = (ArmMode.AWAY, ArmMode.STAY, ArmMode.INSTANT)
SWITCH_ARM_MODE_OPTIONS = tuple(ARM_MODE_TEXT[mode] for mode in SWITCH_ARM_MODES)


def switch_arm_mode(option: str | None) -> ArmMode:
    """The mode of an area switch option; away (total) for any other value."""
    for mode in SWITCH_ARM_MODES:
        if ARM_MODE_TEXT[mode] == option:
            return mode
    return ArmMode.AWAY


_AREA_MODE_TEXT = {
    AreaMode.AWAY: AREA_MODE_TOTAL,
    AreaMode.STAY: AREA_MODE_PARTIAL,
    AreaMode.INSTANT: AREA_MODE_INSTANT,
    AreaMode.DISARMED: AREA_MODE_DISARMED,
}
_ZONE_STATE_TEXT = {
    ZoneState.SHORT: ZONE_STATE_SHORT,
    ZoneState.STANDBY: ZONE_STATE_CLEAR,
    ZoneState.ALARM: ZONE_STATE_ALARMED,
    ZoneState.TAMPER: ZONE_STATE_TAMPER,
}


def area_mode_text(mode: AreaMode) -> str:
    """An area's arming mode: "total", "partial", "instant", "disarmed" or
    "unknown"."""
    return _AREA_MODE_TEXT.get(mode, "unknown")


def area_status_text(mode: AreaMode) -> str:
    """An area's state: "armed" (any mode), "disarmed" or "unknown"."""
    if mode.armed:
        return "armed"
    return "disarmed" if mode is AreaMode.DISARMED else "unknown"


def zone_state_text(state: ZoneState) -> str:
    """A zone's state: "clear", "alarmed", "short" or "tamper"."""
    return _ZONE_STATE_TEXT[state]


def value_key(value: Any) -> Any:
    """An enum member as a stable English key; other values as they are."""
    if (key := _VALUE_KEYS.get((type(value), value))) is not None:
        return key
    name = getattr(value, "name", None)
    return name.lower() if isinstance(name, str) else value


def terminal_type_key(value: TerminalType | int) -> Any:
    """A terminal type as a stable key; "not_assigned" for a record never
    assigned, any other unknown value as its number."""
    if value == TERMINAL_NOT_ASSIGNED and not isinstance(value, TerminalType):
        return "not_assigned"
    return value_key(value)


def time_text(time: PanelTime) -> str:
    """A panel time with a short unit: "30 s", "2 min"."""
    return f"{time.value} {_UNIT_TEXT[time.unit]}"


def minutes_text(minutes: int) -> str:
    """A number of minutes: "3 min"."""
    return f"{minutes} min"


def duration_text(duration: timedelta) -> str:
    """Whole seconds as "20 s", anything else in milliseconds ("300 ms")."""
    milliseconds = round(duration.total_seconds() * 1000)
    if milliseconds and milliseconds % 1000 == 0:
        return f"{milliseconds // 1000} s"
    return f"{milliseconds} ms"


def zone_programming_attributes(
    zone: ZoneConfig, terminal: Terminal, terminal_index: int, second_zone: bool
) -> dict[str, Any]:
    """A zone's programming as attributes: stable English keys and values.

    Byte 6 of the record is shown per sensor kind: the alarm pulse duration of a
    generic sensor, the sensitivity of a shock sensor, nothing for a roller
    blind.
    """
    kind = terminal.sensor_kind
    attributes: dict[str, Any] = {
        "assigned_areas": sorted(zone.areas),
        "zone_type": value_key(zone.zone_type),
        "alarm_cycles": "unlimited" if zone.alarm_cycles is None else zone.alarm_cycles,
    }
    if kind is SensorKind.GENERIC:
        attributes["alarm_pulse_duration"] = duration_text(zone.alarm_pulse_duration)
    elif kind is SensorKind.SHOCK:
        attributes["shock_sensitivity"] = zone.shock_sensitivity
    attributes |= {
        "pulse_window_time": time_text(zone.pulse_window_time),
        "pulse_count": zone.pulse_count,
        "balancing": value_key(terminal.balancing),
        "sensor_kind": kind.value,
        "terminal_index": terminal_index,
        "terminal_type": terminal_type_key(terminal.terminal_type),
        "second_zone": second_zone,
        **zone.flags,
    }
    return attributes


def area_settings_attributes(area: AreaConfig) -> dict[str, Any]:
    """An area's delays and options as attributes (stable English values)."""
    return {
        "exit_time": time_text(area.exit_time),
        "entry_time": time_text(area.entry_time),
        "entry_time_2": "not used"
        if area.entry_time_2 is None
        else time_text(area.entry_time_2),
        "pre_arm_warning_time": minutes_text(area.pre_arm_warning),
        "patrol_time": minutes_text(area.patrol),
        "timer_program": "none" if area.timer_program is None else area.timer_program,
        "reset_alarm_memory_on_arming": area.reset_alarm_memory_on_arming,
        "auto_arm_partial": area.auto_arm_partial,
        "clear_call_queue_on_disarm": area.clear_call_queue_on_disarm,
    }


def output_attributes(item: "OutputItem") -> dict[str, Any]:
    """An output's programming as attributes (stable English keys)."""
    config = item.config
    attributes: dict[str, Any] = {
        "output_number": item.number,
        "kind": config.kind.value,
    }
    if item.fixed is not None:
        attributes["fixed_output"] = item.fixed
    else:
        attributes["terminal_index"] = item.terminal
    if (time := config.monostable_time) is not None:
        attributes["monostable_time"] = time_text(time)
    if item.home_automation is not None:  # not known on every firmware
        attributes["home_automation"] = item.home_automation
    flags = config.flags
    for name in (
        "normally_closed",
        "flashing",
        "buzzer",
        "keep_on_restoral",
        "toggle",
        "relay_use",
    ):
        attributes[name] = flags[name]
    return attributes
