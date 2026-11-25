"""The keypads' thermostats: their settings block, temperature and writes.

Each keypad has a 195-byte thermostat block in EEPROM, at the model's
thermostat address + 195 x keypad (0-based):

  byte 0       regulation mode (ThermostatMode)
  byte 1       season (Season): winter heats, summer cools
  byte 2       manual setpoint
  bytes 3-26   daily program: one setpoint per hour, 00 to 23
  bytes 27-194 weekly program: 24 x 7 setpoints, hour by hour, each hour
               holding the 7 weekdays (Sunday first)

A setpoint byte b means (b + 100) / 10 degrees Celsius: 10.0 to 35.5 in
steps of 0.1. In antifreeze the panel holds 5.0 degrees and heats, whatever
the season. The panel follows its own clock for the programs.

The room temperature is the keypad's own reading, a peripheral read of 2
bytes: unsigned tenths of a degree, as the panel itself compares it.

Only bytes 0-2 (mode, season, manual setpoint) are ever written, one byte
at a time, with the write the panel keeps for these settings: it does not
count as a change to the programming, so the panel does not restart.
"""

from dataclasses import dataclass
from enum import IntEnum

from .errors import ProtocolError
from .frames import SMARTLAN_SI, Dialect, header
from .model import PanelModel

BLOCK_LENGTH = 195
# Read with each poll: mode, season, manual setpoint and the daily program
HEAD_LENGTH = 27
DAILY_PROGRAM = 3  # offset of hour 00
WEEKLY_PROGRAM = 27  # offset of hour 00, Sunday
HOURS = 24
WEEKDAYS = 7

SETPOINT_MIN = 10.0  # degrees Celsius, byte 0
SETPOINT_MAX = 35.5  # byte 255
ANTIFREEZE_SETPOINT = 5.0

# The keypad's room temperature: 2 bytes of the keypad's own memory
KEYPAD_DEVICE = 2  # peripheral class of the keypads
TEMPERATURE_ADDRESS = 0x075A
TEMPERATURE_LENGTH = 2
# A reading above this is no room temperature (a keypad without a sensor,
# or a value below 0 degrees in a form the panel does not compare)
TEMPERATURE_MAX = 80.0

# Keypad status block: keypads enabled (bytes 0-1), thermostat on now
# (bytes 2-3), season (4-5), one mode byte per keypad (6-20); firmware 6
KEYPAD_STATUS = 0x1FF2
KEYPAD_STATUS_LENGTH = 21
_THERMOSTAT_ON = 2


class ThermostatMode(IntEnum):
    """Regulation mode of a keypad's thermostat (block byte 0)."""

    OFF = 0
    MANUAL = 1
    DAILY = 2  # the daily program
    WEEKLY = 3  # the weekly program
    ANTIFREEZE = 4


class Season(IntEnum):
    """Season of a keypad's thermostat (block byte 1)."""

    WINTER = 0  # heats below the setpoint
    SUMMER = 1  # cools above the setpoint


class ThermostatSetting(IntEnum):
    """The block bytes that may be written: their offsets."""

    MODE = 0
    SEASON = 1
    SETPOINT = 2


@dataclass(frozen=True, slots=True)
class ThermostatHead:
    """The first 27 bytes of a thermostat block, decoded.

    mode and season are None for a value the panel does not define.
    """

    mode: ThermostatMode | None
    season: Season | None
    setpoint: float  # manual setpoint, degrees Celsius
    daily: tuple[float, ...]  # daily program, hours 00-23
    raw: bytes

    def program_setpoint(self, hour: int) -> float:
        """The daily program's setpoint for an hour (0-23)."""
        if not 0 <= hour < HOURS:
            raise ValueError(f"hour out of range: {hour}")
        return self.daily[hour]


def thermostat_address(model: PanelModel, keypad: int) -> int:
    """EEPROM address of a keypad's (0-based) thermostat block."""
    if not 0 <= keypad < model.keypads:
        raise ValueError(f"keypad out of range: {keypad}")
    return model.thermostat_records + BLOCK_LENGTH * keypad


def panel_weekday(python_weekday: int) -> int:
    """The panel's weekday (0 = Sunday ... 6 = Saturday) for Python's
    (0 = Monday ... 6 = Sunday)."""
    if not 0 <= python_weekday < WEEKDAYS:
        raise ValueError(f"weekday out of range: {python_weekday}")
    return (python_weekday + 1) % WEEKDAYS


def weekly_offset(hour: int, weekday: int) -> int:
    """Offset in the block of the weekly setpoint for an hour and weekday.

    hour 0-23; weekday 0 = Sunday ... 6 = Saturday.
    """
    if not 0 <= hour < HOURS:
        raise ValueError(f"hour out of range: {hour}")
    if not 0 <= weekday < WEEKDAYS:
        raise ValueError(f"weekday out of range: {weekday}")
    return WEEKLY_PROGRAM + WEEKDAYS * hour + weekday


def decode_setpoint(raw: int) -> float:
    """A setpoint byte in degrees Celsius."""
    return (raw + 100) / 10


def encode_setpoint(celsius: float) -> int:
    """Degrees Celsius (10.0-35.5) as a setpoint byte, to the nearest 0.1."""
    tenths = round(celsius * 10)
    if not round(SETPOINT_MIN * 10) <= tenths <= round(SETPOINT_MAX * 10):
        raise ValueError(f"setpoint out of range: {celsius}")
    return tenths - 100


def decode_head(data: bytes) -> ThermostatHead:
    """Decode the first 27 bytes of a thermostat block."""
    if len(data) != HEAD_LENGTH:
        raise ProtocolError(f"thermostat: {len(data)} bytes, {HEAD_LENGTH} expected")
    mode = data[ThermostatSetting.MODE]
    season = data[ThermostatSetting.SEASON]
    return ThermostatHead(
        mode=ThermostatMode(mode) if mode in ThermostatMode else None,
        season=Season(season) if season in Season else None,
        setpoint=decode_setpoint(data[ThermostatSetting.SETPOINT]),
        daily=tuple(
            decode_setpoint(b) for b in data[DAILY_PROGRAM : DAILY_PROGRAM + HOURS]
        ),
        raw=bytes(data),
    )


def decode_temperature(data: bytes) -> float | None:
    """A keypad's room temperature in degrees Celsius.

    None when it is no room temperature (above TEMPERATURE_MAX).
    """
    if len(data) != TEMPERATURE_LENGTH:
        raise ProtocolError(
            f"temperature: {len(data)} bytes, {TEMPERATURE_LENGTH} expected"
        )
    celsius = int.from_bytes(data, "little") / 10
    return celsius if celsius <= TEMPERATURE_MAX else None


def decode_thermostats_on(data: bytes, model: PanelModel) -> frozenset[int]:
    """The keypads (0-based) whose thermostat is calling now (heating or
    cooling), from the keypad status block."""
    if len(data) != KEYPAD_STATUS_LENGTH:
        raise ProtocolError(
            f"keypad status: {len(data)} bytes, {KEYPAD_STATUS_LENGTH} expected"
        )
    bits = int.from_bytes(data[_THERMOSTAT_ON : _THERMOSTAT_ON + 2], "little")
    return frozenset(k for k in range(model.keypads) if bits >> k & 1)


def temperature_request(keypad: int, model: PanelModel, dialect: Dialect) -> bytes:
    """Request for a keypad's (0-based) room temperature."""
    if not 0 <= keypad < model.keypads:
        raise ValueError(f"keypad out of range: {keypad}")
    return header(
        dialect.peripheral_read_op,
        keypad,
        KEYPAD_DEVICE + dialect.peripheral_device_offset,
        TEMPERATURE_ADDRESS,
        TEMPERATURE_LENGTH,
    )


def setting_write(
    model: PanelModel,
    keypad: int,
    setting: ThermostatSetting,
    value: int,
    dialect: Dialect = SMARTLAN_SI,
) -> tuple[bytes, bytes]:
    """The frame and payload that write one setting of a keypad's thermostat.

    The only write of a thermostat block: the mode (a ThermostatMode), the
    season (a Season) or the manual setpoint (a setpoint byte), of a keypad
    (0-based) of the model. Anything else raises ValueError.
    """
    setting = ThermostatSetting(setting)
    valid = {
        ThermostatSetting.MODE: value in ThermostatMode,
        ThermostatSetting.SEASON: value in Season,
        ThermostatSetting.SETPOINT: 0 <= value <= 0xFF,
    }[setting]
    if not valid:
        raise ValueError(f"{setting.name.lower()} out of range: {value}")
    address = thermostat_address(model, keypad) + setting
    payload = bytes((value,))
    frame = header(
        dialect.no_programming_write_op,
        address >> 16,
        dialect.no_programming_device,
        address & 0xFFFF,
        len(payload),
    )
    return frame + payload, payload
