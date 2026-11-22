"""The zone sensor's programming attributes and device class.

Decoded from the stored zone and terminal records, by the terminal's
sensor kind: bytes 6-8 of a zone record mean different things for generic,
roller-blind and shock sensors.
"""

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.attribute_values import value_key
from custom_components.inim_smartliving_alarm.binary_sensor import (
    zone_device_class,
    zone_programming_attributes,
)
from custom_components.inim_smartliving_alarm.protocol.config import (
    Balancing,
    SensorKind,
    Terminal,
    TerminalType,
    TimeUnit,
    ZoneConfig,
    ZoneType,
    decode_alarm_pulse_duration,
    decode_terminals,
    decode_time,
    decode_zone_records,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import entity_id_for

pytestmark = pytest.mark.usefixtures("socket_enabled")

MODEL = SMARTLIVING_1050_FW6


def terminal_record(balancing: int, sensor: int, kind: int = 0) -> bytes:
    """A 12-byte terminal record: balancing, sensor byte, terminal type."""
    return bytes(5) + bytes((balancing, sensor)) + bytes(4) + bytes((kind,))


def zone_record(zone_type: int, cycles: int, time: int, window: int, pulses: int):
    """A 9-byte zone record in area 1, without options."""
    return bytes((1, 0, zone_type, 0, 0, cycles, time, window, pulses))


def terminal(balancing: int, sensor: int) -> Terminal:
    data = terminal_record(balancing, sensor) * MODEL.terminals
    return decode_terminals(data, MODEL)[0]


def zone(record: bytes) -> ZoneConfig:
    return decode_zone_records(record * MODEL.zones, MODEL)[0]


# ---------------------------------------------------------------- protocol


@pytest.mark.parametrize(
    ("byte", "value", "unit"),
    [
        (0x00, 0, "seconds"),
        (0x1E, 30, "seconds"),
        (0x7F, 127, "seconds"),
        (0x82, 2, "minutes"),
        (0xFF, 127, "minutes"),
    ],
)
def test_time(byte: int, value: int, unit: str) -> None:
    """Bits 0-6 the value, bit 7 the unit."""
    time = decode_time(byte)
    assert (time.value, time.unit) == (value, TimeUnit(unit))


@pytest.mark.parametrize(
    ("byte", "seconds"),
    [(0, 0), (20, 0.3), (125, 1.875), (0x81, 10), (0x85, 50), (0xFF, 1270)],
)
def test_alarm_pulse_duration(byte: int, seconds: float) -> None:
    """Bits 0-6 count steps of 15 ms, or of 10 s when bit 7 is set.

    20 and 125 are values of a real panel, shown as 20 and 125 "1=15mS".
    """
    assert decode_alarm_pulse_duration(byte).total_seconds() == pytest.approx(seconds)


@pytest.mark.parametrize(
    ("byte", "text"),
    [(0, "0 ms"), (20, "300 ms"), (125, "1875 ms"), (0x82, "20 s"), (0xFF, "1270 s")],
)
def test_alarm_pulse_duration_text(byte: int, text: str) -> None:
    """Whole seconds in s, anything else in ms."""
    attributes = zone_programming_attributes(
        zone(zone_record(0, 15, byte, 0, 1)),
        terminal(Balancing.NORMALLY_CLOSED, 0),
        terminal_index=0,
        second_zone=False,
    )
    assert attributes["alarm_pulse_duration"] == text


@pytest.mark.parametrize(
    ("sensor", "kind"),
    [
        (0x00, SensorKind.GENERIC),
        (0x80, SensorKind.ROLLER_BLIND),
        (0x40, SensorKind.SHOCK),
        (0x3F, SensorKind.GENERIC),
        (0xC0, SensorKind.ROLLER_BLIND),
    ],
)
def test_sensor_kind(sensor: int, kind: SensorKind) -> None:
    """Bit 7 roller blind, bit 6 shock (roller blind if both); others unused."""
    assert terminal(0, sensor).sensor_kind is kind


def test_record_fields() -> None:
    """Every byte of the zone record, typed."""
    record = zone(zone_record(1, 3, 0x9E, 0x05, 4))
    assert record.zone_type is ZoneType.DELAYED
    assert record.alarm_cycles == 3
    assert record.alarm_pulse_duration == timedelta(seconds=300)  # 30 long steps
    assert record.shock_sensitivity == 0x9E
    assert record.pulse_window_time.value == 5
    assert record.pulse_count == 4
    assert zone(zone_record(4, 15, 0, 0, 0)).alarm_cycles is None  # unlimited
    assert zone(zone_record(4, 0, 0, 0, 0)).alarm_cycles == 0  # exhausted


def test_unknown_values_stay_numbers() -> None:
    """Values the enums do not know are kept as they are."""
    assert zone(zone_record(13, 1, 0, 0, 0)).zone_type == 13
    unknown = decode_terminals(terminal_record(9, 0, 12) * MODEL.terminals, MODEL)[0]
    assert unknown.balancing == 9 and not isinstance(unknown.balancing, Balancing)
    assert unknown.terminal_type == 12
    assert not isinstance(unknown.terminal_type, TerminalType)


# -------------------------------------------------------------- attributes


def test_generic_attributes() -> None:
    """A generic sensor: pulse duration, window, pulses; no sensitivity."""
    attributes = zone_programming_attributes(
        zone(zone_record(1, 15, 0x1E, 0x83, 2)),
        terminal(Balancing.DOUBLE_EOL, 0),
        terminal_index=0,
        second_zone=False,
    )
    expected = {
        "assigned_areas": [1],
        "zone_type": "delayed",
        "alarm_cycles": "unlimited",
        "alarm_pulse_duration": "450 ms",  # 30 x 15 ms
        "pulse_window_time": "3 min",
        "pulse_count": 2,
        "balancing": "double_eol",
        "sensor_kind": "generic",
        "terminal_index": 0,
        "terminal_type": "input",
        "second_zone": False,
    }
    assert {key: attributes[key] for key in expected} == expected
    assert "shock_sensitivity" not in attributes
    assert attributes["interior"] is False  # the option flags are there too


def test_shock_and_roller_attributes() -> None:
    """Byte 6 is the sensitivity of a shock sensor, unused for a roller blind."""
    shock = zone_programming_attributes(
        zone(zone_record(4, 2, 25, 0x0A, 50)),
        terminal(Balancing.NORMALLY_CLOSED, 0x40),
        terminal_index=3,
        second_zone=True,
    )
    assert shock["zone_type"] == "24_hour"
    assert shock["shock_sensitivity"] == 25
    assert "alarm_pulse_duration" not in shock
    assert (shock["pulse_count"], shock["second_zone"]) == (50, True)
    roller = zone_programming_attributes(
        zone(zone_record(0, 1, 99, 0x05, 10)),
        terminal(Balancing.NORMALLY_OPEN, 0x80),
        terminal_index=0,
        second_zone=False,
    )
    assert roller["sensor_kind"] == "roller_blind"
    assert "alarm_pulse_duration" not in roller and "shock_sensitivity" not in roller
    assert roller["pulse_window_time"] == "5 s"


@pytest.mark.parametrize(
    "member",
    [*ZoneType, *Balancing, *TerminalType],
    ids=lambda member: f"{type(member).__name__}.{member.name}",
)
def test_value_keys_of_every_member(
    member: ZoneType | Balancing | TerminalType,
) -> None:
    """Each member is its lower-case name; only ZoneType.H24 is written otherwise.

    Members of different enums compare equal by value: Balancing.DOUBLE_ZONE
    and TerminalType.NULL are both 4, like ZoneType.H24.
    """
    expected = "24_hour" if member is ZoneType.H24 else member.name.lower()
    assert value_key(member) == expected


@pytest.mark.parametrize("number", [4, 9])
def test_value_key_plain_numbers(number: int) -> None:
    """Values the enums do not know stay numbers, even one worth ZoneType.H24."""
    assert value_key(number) == number


@pytest.mark.parametrize("second_zone", [False, True])
def test_double_zone_terminal_attributes(second_zone: bool) -> None:
    """Both zones of an instant double-zone terminal: its balancing, not 24_hour."""
    data = (
        terminal_record(Balancing.DOUBLE_ZONE, 0, TerminalType.DOUBLE) * MODEL.terminals
    )
    attributes = zone_programming_attributes(
        zone(zone_record(ZoneType.INSTANT, 15, 20, 0x05, 1)),
        decode_terminals(data, MODEL)[0],
        terminal_index=0,
        second_zone=second_zone,
    )
    expected = {
        "zone_type": "instant",
        "balancing": "double_zone",
        "terminal_type": "double",
        "second_zone": second_zone,
    }
    assert {key: attributes[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("balancing", "sensor", "device_class"),
    [
        (Balancing.NORMALLY_OPEN, 0, BinarySensorDeviceClass.OPENING),
        (Balancing.NORMALLY_CLOSED, 0, BinarySensorDeviceClass.OPENING),
        (Balancing.SINGLE_EOL, 0, BinarySensorDeviceClass.OPENING),
        (Balancing.DOUBLE_EOL, 0, BinarySensorDeviceClass.MOTION),
        (Balancing.DOUBLE_ZONE, 0, None),
        (Balancing.DOUBLE_ZONE_EOL, 0, None),
        (Balancing.PERSONALIZED, 0, None),
        (Balancing.DOUBLE_EOL, 0x40, BinarySensorDeviceClass.VIBRATION),
        (Balancing.DOUBLE_EOL, 0x80, BinarySensorDeviceClass.OPENING),
        (9, 0, None),  # unknown balancing
    ],
)
def test_device_class(
    balancing: int, sensor: int, device_class: BinarySensorDeviceClass | None
) -> None:
    """Shock, roller blind, then the balancing; otherwise none."""
    assert zone_device_class(terminal(balancing, sensor)) == device_class


# ------------------------------------------------------------- end to end


async def test_zone_sensor(hass: HomeAssistant, panel: FakePanel) -> None:
    """A shock zone on the fake 1050: attributes and class, old names gone."""
    panel.state.terminal_records[0] = terminal_record(
        Balancing.NORMALLY_CLOSED, 0x40, TerminalType.INPUT
    )
    panel.state.zone_records[0] = zone_record(ZoneType.H24, 15, 20, 0x84, 30)
    entry = await add_entry(hass, panel)
    state = hass.states.get(entity_id_for(hass, entry, ("binary_sensor", "_zone_1")))
    assert state.attributes["device_class"] == "vibration"
    assert state.attributes["zone_type"] == "24_hour"
    assert state.attributes["shock_sensitivity"] == 20
    assert state.attributes["pulse_window_time"] == "4 min"
    assert state.attributes["sensor_kind"] == "shock"
    for old in (
        "activation_type",
        "duration_ms",
        "time",
        "pulses",
        "balancing_type",
        "sensor_type",
    ):
        assert old not in state.attributes, old
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_double_zone_sensors(hass: HomeAssistant, panel: FakePanel) -> None:
    """Both zones of a double-zone terminal on the fake 1050 (zones 1 and 51)."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel.state.terminal_records[0] = terminal_record(
        Balancing.DOUBLE_ZONE, 0, TerminalType.DOUBLE
    )
    record = zone_record(ZoneType.INSTANT, 15, 20, 0x05, 1)
    panel.state.zone_records[0] = record
    panel.state.zone_records[MODEL.terminals] = record
    entry = await add_entry(hass, panel, zones=(1, MODEL.terminals + 1))
    for number, second_zone in ((1, False), (MODEL.terminals + 1, True)):
        entity = ("binary_sensor", f"_zone_{number}")
        attributes = hass.states.get(entity_id_for(hass, entry, entity)).attributes
        assert attributes["zone_type"] == "instant", number
        assert attributes["balancing"] == "double_zone", number
        assert attributes["terminal_type"] == "double", number
        assert attributes["second_zone"] is second_zone, number
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("model_string", [b"5.07 01050  ", b"6.07 00999  "])
def test_fallback_model_gives_attributes(model_string: bytes) -> None:
    """A model without its own profile: decoded with the fallback layout.

    Its configuration was read with that layout, so its zones still get
    their attributes and device class.
    """
    from custom_components.inim_smartliving_alarm.binary_sensor import (
        _zone_programming,
    )
    from custom_components.inim_smartliving_alarm.stored_config import (
        terminal_records,
        zone_records,
    )

    zones = zone_record(ZoneType.DELAYED, 15, 0x1E, 0x02, 1) * MODEL.zones
    terminals = terminal_record(Balancing.SINGLE_EOL, 0) * MODEL.terminals
    config = {
        "model_string": model_string.hex(),
        "zone_records": zones.hex(),
        "terminal_records": terminals.hex(),
    }
    attributes, device_class = _zone_programming(
        zone_records(config), terminal_records(config), 0
    )
    assert attributes["zone_type"] == "delayed"
    assert attributes["balancing"] == "single_eol"
    assert device_class is BinarySensorDeviceClass.OPENING
