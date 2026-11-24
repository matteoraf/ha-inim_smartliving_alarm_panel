"""The keypads' thermostats: protocol, reads and writes, entities.

Reads and writes are tested on the fake panel, which counts every EEPROM
write that would count as programming (a real panel restarts after one):
no thermostat test may cause one.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
import logging
import time

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import thermostats
from custom_components.inim_smartliving_alarm.api import (
    ProgrammingModeError,
    SmartLivingPanel,
    ThermostatNotSetError,
    create_panel,
)
from custom_components.inim_smartliving_alarm.const import (
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.polling import (
    SLOW_RIDERS,
    PollGroup,
    PollSchedule,
    intervals,
    slow_intervals,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.frames import (
    SMARTLAN_G,
    SMARTLAN_SI,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW6,
    SMARTLIVING_515_FW51,
    SMARTLIVING_1050_FW6,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
    SMARTLIVING_10100_FW51,
    PanelModel,
)
from custom_components.inim_smartliving_alarm.protocol.thermostat import (
    HEAD_LENGTH,
    Season,
    ThermostatMode,
    ThermostatSetting,
    decode_head,
    decode_setpoint,
    decode_temperature,
    decode_thermostats_on,
    encode_setpoint,
    setting_write,
    temperature_request,
    thermostat_address,
    weekly_offset,
)
from custom_components.inim_smartliving_alarm.selection import available_thermostats
from custom_components.inim_smartliving_alarm.thermostats import (
    TemperatureBackoff,
    program_time,
)

from .conftest import items_form, offered, proposed, wait_until
from .fake_panel import (
    FakePanel,
    PanelState,
    const as c,
    panel_1050,
    panel_1050_fw51,
    panel_10100,
)
from .fake_panel.server import TRANSPORT_G
from .test_config_flow import open_options, unload_all
from .test_reconfigure_refresh import reread
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

MODELS = [
    (SMARTLIVING_505_FW6, 0x06B00),
    (SMARTLIVING_515_FW6, 0x06B00),
    (SMARTLIVING_1050_FW6, 0x18400),
    (SMARTLIVING_10100_FW6, 0x18400),
    (SMARTLIVING_505_FW51, 0x07740),
    (SMARTLIVING_515_FW51, 0x07740),
    (SMARTLIVING_1050_FW51, 0x07740),
    (SMARTLIVING_10100_FW51, 0x18000),
]


def block(
    mode: int = 0,
    season: int = 0,
    setpoint: float = 20.0,
    daily: list[float] | None = None,
    weekly: dict[tuple[int, int], float] | None = None,
) -> bytes:
    """A thermostat block (195 bytes)."""
    data = bytearray(195)
    data[0], data[1], data[2] = mode, season, encode_setpoint(setpoint)
    for hour, celsius in enumerate(daily or [18.0] * 24):
        data[3 + hour] = encode_setpoint(celsius)
    for (hour, weekday), celsius in (weekly or {}).items():
        data[weekly_offset(hour, weekday)] = encode_setpoint(celsius)
    return bytes(data)


# ----------------------------------------------------------------- protocol


def test_frames_match_the_vendor_examples() -> None:
    """Writes and the temperature read, byte for byte as Inim's own app sends
    them (SmartLAN/SI), and their SmartLAN/G form."""
    frames = {
        setting: setting_write(SMARTLIVING_515_FW6, 0, setting, 0)[0][:8]
        for setting in ThermostatSetting
    }
    assert frames[ThermostatSetting.MODE] == bytes.fromhex("1100fc6b00000179")
    assert frames[ThermostatSetting.SEASON] == bytes.fromhex("1100fc6b0100017a")
    assert frames[ThermostatSetting.SETPOINT] == bytes.fromhex("1100fc6b0200017b")
    frame, payload = setting_write(SMARTLIVING_1050_FW6, 0, ThermostatSetting.MODE, 1)
    assert frame == bytes.fromhex("1101fc840000019301")
    assert payload == b"\x01"
    frame, _ = setting_write(
        SMARTLIVING_1050_FW6, 0, ThermostatSetting.MODE, 1, SMARTLAN_G
    )
    assert frame[:7] == bytes.fromhex("0101fd84000001")
    assert temperature_request(0, SMARTLIVING_1050_FW6, SMARTLAN_SI) == (
        bytes.fromhex("100001075a000274")
    )
    assert temperature_request(1, SMARTLIVING_1050_FW6, SMARTLAN_SI) == (
        bytes.fromhex("100101075a000275")
    )
    assert temperature_request(0, SMARTLIVING_1050_FW6, SMARTLAN_G) == (
        bytes.fromhex("000002075a000265")
    )


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        (ThermostatSetting.MODE, 5),
        (ThermostatSetting.SEASON, 2),
        (ThermostatSetting.SETPOINT, 256),
        (ThermostatSetting.SETPOINT, -1),
    ],
)
def test_writes_refuse_other_values(setting: ThermostatSetting, value: int) -> None:
    with pytest.raises(ValueError):
        setting_write(SMARTLIVING_1050_FW6, 0, setting, value)


def test_writes_refuse_other_bytes_and_keypads() -> None:
    """Only bytes 0-2, only the model's keypads."""
    with pytest.raises(ValueError):
        setting_write(SMARTLIVING_1050_FW6, 0, 3, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        setting_write(SMARTLIVING_1050_FW6, 10, ThermostatSetting.MODE, 0)
    with pytest.raises(ValueError):
        setting_write(SMARTLIVING_505_FW6, -1, ThermostatSetting.MODE, 0)


def test_setpoints() -> None:
    """(b + 100) / 10: 10.0 to 35.5 degrees, to the nearest tenth."""
    assert decode_setpoint(0) == 10.0
    assert decode_setpoint(255) == 35.5
    assert encode_setpoint(10.0) == 0
    assert encode_setpoint(35.5) == 255
    assert encode_setpoint(21.04) == 110
    assert encode_setpoint(21.06) == 111
    for celsius in (9.9, 35.6):
        with pytest.raises(ValueError):
            encode_setpoint(celsius)


def test_temperatures() -> None:
    """Unsigned tenths; above 80.0 degrees it is no room temperature."""
    assert decode_temperature(bytes.fromhex("f500")) == 24.5
    assert decode_temperature(bytes.fromhex("1501")) == 27.7
    assert decode_temperature((800).to_bytes(2, "little")) == 80.0
    assert decode_temperature((801).to_bytes(2, "little")) is None
    assert decode_temperature(b"\xff\xff") is None  # -0.1 in two's complement
    with pytest.raises(ProtocolError):
        decode_temperature(b"\x01")


def test_program_offsets() -> None:
    """Daily: byte 3 + hour. Weekly: byte 27 + 7 x hour + weekday, Sunday 0."""
    assert weekly_offset(0, 0) == 27
    assert weekly_offset(0, 6) == 33
    assert weekly_offset(1, 0) == 34
    assert weekly_offset(23, 6) == 194
    for hour, weekday in ((24, 0), (0, 7), (-1, 0)):
        with pytest.raises(ValueError):
            weekly_offset(hour, weekday)
    head = decode_head(block(daily=[10.0 + hour for hour in range(24)])[:HEAD_LENGTH])
    assert head.program_setpoint(0) == 10.0
    assert head.program_setpoint(23) == 33.0


def test_head() -> None:
    head = decode_head(block(mode=3, season=1, setpoint=21.5)[:HEAD_LENGTH])
    assert head.mode is ThermostatMode.WEEKLY
    assert head.season is Season.SUMMER
    assert head.setpoint == 21.5
    assert len(head.daily) == 24
    odd = decode_head(bytes((9, 7)) + bytes(25))
    assert odd.mode is None
    assert odd.season is None
    with pytest.raises(ProtocolError):
        decode_head(bytes(26))


def test_thermostats_on() -> None:
    """Bytes 2-3 of the keypad status block; keypads beyond the model's
    ignored."""
    data = bytearray(21)
    data[2], data[3] = 0b0000_0101, 0b0100_0010  # keypads 1, 3, 10, 15
    assert decode_thermostats_on(bytes(data), SMARTLIVING_1050_FW6) == {0, 2, 9}
    assert decode_thermostats_on(bytes(data), SMARTLIVING_10100_FW6) == {0, 2, 9, 14}
    with pytest.raises(ProtocolError):
        decode_thermostats_on(bytes(20), SMARTLIVING_1050_FW6)


@pytest.mark.parametrize(
    ("model", "base"), MODELS, ids=lambda m: getattr(m, "name", "")
)
def test_addresses(model: PanelModel, base: int) -> None:
    """Each profile's thermostat blocks, 195 bytes per keypad."""
    assert thermostat_address(model, 0) == base
    assert thermostat_address(model, model.keypads - 1) == base + 195 * (
        model.keypads - 1
    )
    with pytest.raises(ValueError):
        thermostat_address(model, model.keypads)


def test_keypad_status_only_on_firmware_6() -> None:
    assert all(model.keypad_status for model, _ in MODELS[:4])
    assert not any(model.keypad_status for model, _ in MODELS[4:])


def test_program_time() -> None:
    """Weekday 0 = Sunday."""
    assert program_time(datetime(2026, 11, 22, 7, 30)) == (7, 0)  # a Sunday
    assert program_time(datetime(2026, 11, 28, 23, 0)) == (23, 6)  # a Saturday
    assert program_time(datetime(2026, 11, 23, 0, 5)) == (0, 1)


# ------------------------------------------------------------- the schedule


def test_backoff() -> None:
    """Left out of the next 1, 2, 4 ... reads, at most 10 minutes; back at
    once when it answers."""
    backoff = TemperatureBackoff(interval=60)
    assert backoff.record([0, 1], failed={1}) == ({1}, set())
    left_out = [backoff.skipped() for _ in range(2)]
    assert left_out == [{1}, frozenset()]
    assert backoff.record([0, 1], failed={1}) == (set(), set())
    assert [1 in backoff.skipped() for _ in range(3)] == [True, True, False]
    for _ in range(20):
        backoff.record([1], failed={1})
    skips = 0
    while 1 in backoff.skipped():
        skips += 1
    assert skips == 10  # 600 s at 60 s
    assert backoff.record([1], failed=set()) == (set(), {1})
    assert backoff.skipped() == frozenset()


def test_thermostats_group_only_when_selected() -> None:
    assert PollGroup.THERMOSTATS not in intervals({})
    assert PollGroup.THERMOSTATS not in intervals({"thermostats": []})
    assert intervals({"thermostats": [1]})[PollGroup.THERMOSTATS] == 60
    assert (
        intervals({"thermostats": [1], "poll_interval_thermostats": 5})[
            PollGroup.THERMOSTATS
        ]
        == 10
    )


@pytest.mark.parametrize(
    ("own", "slow", "every"),
    [(60, 30, 60), (60, 45, 90), (10, 30, 30), (600, 300, 600)],
)
def test_slow_interval_is_a_multiple(own: float, slow: float, every: float) -> None:
    options = {
        "thermostats": [1],
        "poll_interval_thermostats": own,
        "slow_polling_interval": slow,
    }
    found = slow_intervals(options)
    assert found[PollGroup.THERMOSTATS] == every
    assert found[PollGroup.ZONES] == slow


def test_thermostats_ride_along_in_slow_mode() -> None:
    """With timers firing late, the thermostats are read on every 2nd
    wake-up of the others, never on a wake-up of their own."""
    slow = slow_intervals({"thermostats": [1]})  # 30 s, thermostats 60 s
    schedule = PollSchedule(slow, riders=SLOW_RIDERS)
    schedule.read_all(0.0)
    now = 0.0
    with_thermostats = []
    for _ in range(12):
        now += schedule.next_wake(now) + 0.05  # every timer fires a bit late
        due = schedule.due(now)
        assert PollGroup.ZONES in due  # never the thermostats alone
        for group in due:
            schedule.read(group, now)
        with_thermostats.append(PollGroup.THERMOSTATS in due)
    assert with_thermostats == [False, True] * 6


def test_a_rider_alone_is_read_on_its_own() -> None:
    schedule = PollSchedule({PollGroup.THERMOSTATS: 60.0}, riders=SLOW_RIDERS)
    assert schedule.due(0.0) == [PollGroup.THERMOSTATS]


def test_a_rider_waits_for_the_others() -> None:
    """Due alone, the thermostats are not read; due a little after the
    others, they are read with them."""
    schedule = PollSchedule(
        {PollGroup.ZONES: 30.0, PollGroup.THERMOSTATS: 60.0}, riders=SLOW_RIDERS
    )
    schedule.read(PollGroup.ZONES, 40.0)
    schedule.read(PollGroup.THERMOSTATS, 0.0)
    assert schedule.due(61.0) == []  # only the thermostats are due
    assert schedule.next_wake(61.0) == 9  # the zones, due at 70 s
    schedule.read(PollGroup.ZONES, 70.0)
    schedule.read(PollGroup.THERMOSTATS, 70.3)  # read a little late
    # At 100 s the zones are due, the thermostats 0.3 s later: read now
    assert schedule.due(100.0) == [PollGroup.ZONES]
    assert schedule.due(130.0) == [PollGroup.ZONES, PollGroup.THERMOSTATS]


async def test_slow_polling_mode_reads_them_along(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """In slow polling mode the thermostats ride along with the other groups,
    on every 2nd read at the defaults."""
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_set_slow_polling(True)
    assert coordinator.schedule.riders == {PollGroup.THERMOSTATS}
    assert coordinator.schedule.intervals[PollGroup.THERMOSTATS] == 60
    assert coordinator.schedule.intervals[PollGroup.ZONES] == 30
    await coordinator.async_set_slow_polling(False)
    assert coordinator.schedule.riders == frozenset()
    await unload_all(hass)


# ---------------------------------------------------------------- the API


def thermostat_panel(state: PanelState | None = None) -> PanelState:
    """The fake 1050: keypad 1 manual in winter at 21.0, calling; keypad 2
    off in summer; both with a sensor."""
    state = state or panel_1050()
    state.thermostat_blocks = {
        0: block(mode=1, season=0, setpoint=21.0),
        1: block(mode=0, season=1, setpoint=24.0),
    }
    state.thermostats_on = {0}
    return state


@pytest.fixture
async def fake() -> AsyncGenerator[FakePanel]:
    panel = FakePanel(thermostat_panel())
    await panel.start()
    yield panel
    await panel.stop()


@pytest.fixture
async def api(fake: FakePanel) -> AsyncGenerator[SmartLivingPanel]:
    panel = create_panel(fake.host, fake.port, "1234", reply_timeout=0.3)
    yield panel
    await panel.close()


def thermostat_writes(fake: FakePanel) -> list[tuple[int, bytes]]:
    return list(fake.no_programming_writes)


async def test_read(fake: FakePanel, api: SmartLivingPanel) -> None:
    reading = await api.read_thermostats([0, 1], hour=8, weekday=1)
    first, second = reading.thermostats[0], reading.thermostats[1]
    assert first.head.mode is ThermostatMode.MANUAL
    assert first.head.setpoint == 21.0
    assert first.on is True
    assert first.temperature == 24.5
    assert first.weekly_setpoint is None
    assert second.head.mode is ThermostatMode.OFF
    assert second.head.season is Season.SUMMER
    assert second.on is False
    assert second.temperature == 27.7
    assert reading.temperature_failed == frozenset()
    # The status block once, then per keypad 27 bytes and the temperature
    reads = [(r.space, r.address, r.length) for r in fake.requests[-5:]]
    assert reads == [
        ("ram", c.RAM_KEYPAD_STATUS, 21),
        ("eeprom", 0x18400, 27),
        ("peripheral", c.KEYPAD_TEMPERATURE, 2),
        ("eeprom", 0x18400 + 195, 27),
        ("peripheral", c.KEYPAD_TEMPERATURE, 2),
    ]


async def test_weekly_setpoint_is_read_for_the_hour(
    fake: FakePanel, api: SmartLivingPanel
) -> None:
    """In the weekly program, the one byte for this hour and weekday."""
    fake.state.thermostat_blocks[0] = block(mode=3, weekly={(8, 1): 22.5})
    reading = await api.read_thermostats([0], hour=8, weekday=1)
    assert reading.thermostats[0].weekly_setpoint == 22.5
    weekly = [r for r in fake.requests if r.space == "eeprom" and r.length == 1]
    assert [r.address for r in weekly] == [0x18400 + 27 + 7 * 8 + 1]


async def test_unanswered_temperature(fake: FakePanel, api: SmartLivingPanel) -> None:
    """A keypad that does not answer: its temperature unknown, the others
    read; one left out is not asked."""
    fake.state.silent_keypads = {0}
    reading = await api.read_thermostats([0, 1], hour=8, weekday=1)
    assert reading.thermostats[0].temperature is None
    assert reading.thermostats[0].head.mode is ThermostatMode.MANUAL
    assert reading.thermostats[1].temperature == 27.7
    assert reading.temperature_failed == {0}
    asked = len(fake.requests)
    reading = await api.read_thermostats(
        [0, 1], hour=8, weekday=1, skip_temperature={0}
    )
    assert reading.temperature_failed == frozenset()
    temperature_reads = [
        r for r in fake.requests[asked:] if r.space == "peripheral" and r.instance == 0
    ]
    assert temperature_reads == []


async def test_probe(fake: FakePanel, api: SmartLivingPanel) -> None:
    """Keypads that answer with a room temperature; not one without a
    sensor, nor one that does not answer."""
    fake.state.peripherals["keypads"] = [0, 1, 2, 3]
    fake.state.keypad_temperature_raw[2] = 0xFFFF
    fake.state.silent_keypads = {3}
    assert await api.probe_thermostats([0, 1, 2, 3, 40]) == {0, 1}


async def test_firmware_51_has_no_status_block(socket_enabled) -> None:
    fake = FakePanel(thermostat_panel(panel_1050_fw51()))
    await fake.start()
    panel = create_panel(fake.host, fake.port, "1234")
    try:
        reading = await panel.read_thermostats([0], hour=8, weekday=1)
        assert reading.thermostats[0].on is None
        assert reading.thermostats[0].head.mode is ThermostatMode.MANUAL
        assert all(r.address != c.RAM_KEYPAD_STATUS for r in fake.requests)
        assert any(r.address == 0x07740 for r in fake.requests)
    finally:
        await panel.close()
        await fake.stop()


async def test_write(fake: FakePanel, api: SmartLivingPanel) -> None:
    """Season, setpoint, mode, in that order; read back."""
    head = await api.set_thermostat(1, season=0, setpoint=22.5, mode=1)
    assert head.mode is ThermostatMode.MANUAL
    assert head.season is Season.WINTER
    assert head.setpoint == 22.5
    base = 0x18400 + 195
    assert thermostat_writes(fake) == [
        (base + 1, b"\x00"),
        (base + 2, bytes((125,))),
        (base, b"\x01"),
    ]
    assert fake.programming_writes == []
    assert fake.state.thermostat_head(1) == bytes((1, 0, 125))


async def test_no_write_when_nothing_changes(
    fake: FakePanel, api: SmartLivingPanel
) -> None:
    head = await api.set_thermostat(0, season=0, setpoint=21.0, mode=1)
    assert head.setpoint == 21.0
    assert thermostat_writes(fake) == []
    await api.set_thermostat(0, setpoint=21.04)  # the same tenth
    assert thermostat_writes(fake) == []


async def test_write_refused_in_programming(
    fake: FakePanel, api: SmartLivingPanel
) -> None:
    fake.state.in_programming = True
    with pytest.raises(ProgrammingModeError):
        await api.set_thermostat(0, mode=0)
    assert thermostat_writes(fake) == []


async def test_write_not_kept(fake: FakePanel, api: SmartLivingPanel) -> None:
    """A value the panel did not keep is an error."""
    fake.state.eeprom_extra[0x18400] = b"\x01"  # pins the mode at MANUAL

    original = fake._handle_write

    def ignore_thermostat(request):
        reply = original(request)
        fake.state.eeprom_extra[0x18400] = b"\x01"
        return reply

    fake._handle_write = ignore_thermostat
    with pytest.raises(ThermostatNotSetError):
        await api.set_thermostat(0, mode=0)


async def test_setpoint_out_of_range(api: SmartLivingPanel) -> None:
    with pytest.raises(ValueError):
        await api.set_thermostat(0, setpoint=36.0)


async def test_smartlan_g(socket_enabled) -> None:
    """The same reads and writes in the SmartLAN/G dialect."""
    fake = FakePanel(
        thermostat_panel(), transport=TRANSPORT_G, username="u", password="p"
    )
    await fake.start()
    panel = create_panel(
        fake.host, fake.port, "1234", board="smartlan_g", username="u", password="p"
    )
    try:
        reading = await panel.read_thermostats([0], hour=8, weekday=1)
        assert reading.thermostats[0].temperature == 24.5
        await panel.set_thermostat(0, mode=0)
        assert fake.no_programming_writes == [(0x18400, b"\x00")]
        assert fake.programming_writes == []
        writes = [r.raw for r in fake.requests if r.write]
        assert writes[0][:3] == bytes((0x01, 0x01, 0xFD))
    finally:
        await panel.close()
        await fake.stop()


# ------------------------------------------------------------ the integration


@pytest.fixture
async def thermostats_fake() -> AsyncGenerator[FakePanel]:
    panel = FakePanel(thermostat_panel())
    await panel.start()
    yield panel
    await panel.stop()


async def add_with_thermostats(
    hass: HomeAssistant, fake: FakePanel, thermostats=None
) -> ConfigEntry:
    """The setup flow, ticking these thermostats (None: as proposed)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": fake.host, "port": fake.port, "pin": "1234", "panel_name": "Home"},
    )
    assert result["step_id"] == "items"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        items_form(
            result,
            {"areas": [1], "zones": [1], "scenarios": [1], "thermostats": thermostats},
        ),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"event_log_size": 5}
    )
    await hass.async_block_till_done()
    return result["result"]


def entity_id(hass: HomeAssistant, entry: ConfigEntry, platform: str, suffix: str):
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{suffix}"
    )


async def refresh(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR].async_refresh()
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, service: str, target: str, **data) -> None:
    await hass.services.async_call(
        CLIMATE, service, {ATTR_ENTITY_ID: target, **data}, blocking=True
    )


async def test_setup_proposes_keypads_with_a_sensor(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """The enabled keypads are offered; those that answer are ticked."""
    thermostats_fake.state.peripherals["keypads"] = [0, 1, 2]  # 3: no sensor
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": thermostats_fake.host,
            "port": thermostats_fake.port,
            "pin": "1234",
            "panel_name": "Home",
        },
    )
    assert offered(result, "thermostats") == [1, 2, 3]
    assert proposed(result, "thermostats") == [1, 2]
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_setup_without_keypads_with_a_sensor(
    hass: HomeAssistant, socket_enabled
) -> None:
    fake = FakePanel(panel_10100())
    await fake.start()
    try:
        entry = await add_with_thermostats(hass, fake)
        assert entry.options["thermostats"] == []
        assert hass.states.async_entity_ids(CLIMATE) == []
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_entities(hass: HomeAssistant, thermostats_fake: FakePanel) -> None:
    entry = await add_with_thermostats(hass, thermostats_fake)
    assert entry.options["thermostats"] == [1, 2]
    first = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
    assert first.name == "Home Thermostat 1 (Tast Zona Giorno)"
    assert first.state == HVACMode.HEAT
    assert first.attributes[ATTR_PRESET_MODE] == "manual"
    assert first.attributes[ATTR_TEMPERATURE] == 21.0
    assert first.attributes[ATTR_CURRENT_TEMPERATURE] == 24.5
    assert first.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert first.attributes["min_temp"] == 10.0
    assert first.attributes["max_temp"] == 35.5
    assert first.attributes["target_temp_step"] == 0.5
    second = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_2"))
    assert second.state == HVACMode.OFF
    assert second.attributes[ATTR_PRESET_MODE] is None
    assert second.attributes[ATTR_TEMPERATURE] == 24.0  # the manual setpoint
    assert second.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF
    sensor = hass.states.get(entity_id(hass, entry, "sensor", "keypad_temperature_2"))
    assert sensor.name == "Home Keypad 2 temperature (Tast Garage)"
    assert sensor.state == "27.7"
    assert sensor.attributes["device_class"] == "temperature"
    assert sensor.attributes["unit_of_measurement"] == "°C"
    await unload_all(hass)


@pytest.mark.parametrize(
    ("mode", "season", "hvac", "preset", "target"),
    [
        (0, 0, HVACMode.OFF, None, 20.0),
        (1, 0, HVACMode.HEAT, "manual", 20.0),
        (1, 1, HVACMode.COOL, "manual", 20.0),
        (2, 0, HVACMode.HEAT, "daily", "hour"),
        (2, 1, HVACMode.COOL, "daily", "hour"),
        (3, 0, HVACMode.HEAT, "weekly", "week"),
        (3, 1, HVACMode.COOL, "weekly", "week"),
        (4, 0, HVACMode.HEAT, "antifreeze", 5.0),
        (4, 1, HVACMode.HEAT, "antifreeze", 5.0),  # antifreeze always heats
    ],
)
async def test_mapping(
    hass: HomeAssistant,
    thermostats_fake: FakePanel,
    mode: int,
    season: int,
    hvac: HVACMode,
    preset: str | None,
    target: float | str,
) -> None:
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    hour, weekday = program_time(dt_util.now())
    thermostats_fake.state.thermostat_blocks[0] = block(
        mode=mode,
        season=season,
        daily=[10.0 + h for h in range(24)],
        weekly={(hour, weekday): 30.5},
    )
    thermostats_fake.state.thermostats_on = set()
    await refresh(hass, entry)
    state = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
    assert state.state == hvac
    assert state.attributes[ATTR_PRESET_MODE] == preset
    expected = {"hour": 10.0 + dt_util.now().hour, "week": 30.5}.get(target, target)
    assert state.attributes[ATTR_TEMPERATURE] == expected
    action = HVACAction.OFF if hvac == HVACMode.OFF else HVACAction.IDLE
    assert state.attributes[ATTR_HVAC_ACTION] == action
    await unload_all(hass)


async def test_cooling(hass: HomeAssistant, thermostats_fake: FakePanel) -> None:
    thermostats_fake.state.thermostat_blocks[0] = block(mode=1, season=1)
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    state = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING
    await unload_all(hass)


async def test_no_action_on_firmware_51(hass: HomeAssistant, socket_enabled) -> None:
    fake = FakePanel(thermostat_panel(panel_1050_fw51()))
    await fake.start()
    try:
        entry = await add_with_thermostats(hass, fake, [1])
        state = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
        assert state.state == HVACMode.HEAT
        assert ATTR_HVAC_ACTION not in state.attributes
        await unload_all(hass)
    finally:
        await fake.stop()


async def setup_one(hass: HomeAssistant, fake: FakePanel, **values) -> tuple:
    """Keypad 1's thermostat set as given (a block()), and its entity ID."""
    fake.state.thermostat_blocks[0] = block(**values)
    entry = await add_with_thermostats(hass, fake, [1])
    del fake.no_programming_writes[:]
    return entry, entity_id(hass, entry, CLIMATE, "thermostat_1")


B0 = 0x18400  # keypad 1's block on the 1050


@pytest.mark.parametrize(
    ("start", "service", "data", "writes", "after"),
    [
        # From off, heat or cool: the season, then the last mode it was on in
        (
            {"mode": 0, "season": 1},
            SERVICE_SET_HVAC_MODE,
            {"hvac_mode": "heat"},
            [(B0 + 1, 0), (B0, 1)],
            ("heat", "manual"),
        ),
        # On: the season only
        (
            {"mode": 2, "season": 0},
            SERVICE_SET_HVAC_MODE,
            {"hvac_mode": "cool"},
            [(B0 + 1, 1)],
            ("cool", "daily"),
        ),
        # From antifreeze, cool: the season, then the last mode it was on in
        (
            {"mode": 4, "season": 0},
            SERVICE_SET_HVAC_MODE,
            {"hvac_mode": "cool"},
            [(B0 + 1, 1), (B0, 1)],
            ("cool", "manual"),
        ),
        # In antifreeze, heat: nothing (it already heats), not even the season
        (
            {"mode": 4, "season": 1},
            SERVICE_SET_HVAC_MODE,
            {"hvac_mode": "heat"},
            [],
            ("heat", "antifreeze"),
        ),
        # In antifreeze, turn on: nothing
        ({"mode": 4}, SERVICE_TURN_ON, {}, [], ("heat", "antifreeze")),
        (
            {"mode": 3},
            SERVICE_SET_HVAC_MODE,
            {"hvac_mode": "off"},
            [(B0, 0)],
            ("off", None),
        ),
        ({"mode": 1}, SERVICE_TURN_OFF, {}, [(B0, 0)], ("off", None)),
        ({"mode": 0}, SERVICE_TURN_ON, {}, [(B0, 1)], ("heat", "manual")),
        (
            {"mode": 0},
            SERVICE_SET_PRESET_MODE,
            {"preset_mode": "weekly"},
            [(B0, 3)],
            ("heat", "weekly"),
        ),
        # The setpoint: in manual it is all
        (
            {"mode": 1},
            SERVICE_SET_TEMPERATURE,
            {"temperature": 22.5},
            [(B0 + 2, 125)],
            ("heat", "manual"),
        ),
        # Off: kept for later, the thermostat stays off
        (
            {"mode": 0},
            SERVICE_SET_TEMPERATURE,
            {"temperature": 22.5},
            [(B0 + 2, 125)],
            ("off", None),
        ),
        # In a program or antifreeze: to manual
        (
            {"mode": 2},
            SERVICE_SET_TEMPERATURE,
            {"temperature": 22.5},
            [(B0 + 2, 125), (B0, 1)],
            ("heat", "manual"),
        ),
        (
            {"mode": 4},
            SERVICE_SET_TEMPERATURE,
            {"temperature": 22.5},
            [(B0 + 2, 125), (B0, 1)],
            ("heat", "manual"),
        ),
    ],
)
async def test_actions(
    hass: HomeAssistant,
    thermostats_fake: FakePanel,
    start: dict,
    service: str,
    data: dict,
    writes: list[tuple[int, int]],
    after: tuple[str, str | None],
) -> None:
    """Each action's writes, in order; the entity shows the result at once."""
    entry, target = await setup_one(hass, thermostats_fake, **start)
    await call(hass, service, target, **data)
    assert [(a, p[0]) for a, p in thermostats_fake.no_programming_writes] == writes
    assert thermostats_fake.programming_writes == []
    state = hass.states.get(target)
    assert (state.state, state.attributes[ATTR_PRESET_MODE]) == after
    await unload_all(hass)


async def test_turned_on_in_the_last_mode(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """The last mode it was on in, also after a restart."""
    entry, target = await setup_one(hass, thermostats_fake, mode=3)
    await call(hass, SERVICE_TURN_OFF, target)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    del thermostats_fake.no_programming_writes[:]
    await call(hass, SERVICE_TURN_ON, target)
    assert thermostats_fake.no_programming_writes == [(B0, b"\x03")]
    await unload_all(hass)


async def test_refused_in_programming(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    entry, target = await setup_one(hass, thermostats_fake, mode=1)
    thermostats_fake.state.in_programming = True
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, SERVICE_TURN_OFF, target)
    assert err.value.translation_key == "panel_programming"
    assert thermostats_fake.no_programming_writes == []
    await unload_all(hass)


async def test_unanswered_keypad_stays_available_for_the_rest(
    hass: HomeAssistant, thermostats_fake: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A keypad that stops answering: its temperature unknown, logged once,
    left out of the next read; everything else stays available."""
    entry = await add_with_thermostats(hass, thermostats_fake)
    hass.data[DOMAIN][entry.entry_id][DATA_PANEL].transport.reply_timeout = 0.2
    thermostats_fake.state.silent_keypads = {0}
    with caplog.at_level(logging.WARNING):
        await refresh(hass, entry)
        await refresh(hass, entry)
    assert caplog.text.count("keypad 1 did not answer the temperature read") == 1
    first = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
    assert first.state == HVACMode.HEAT
    assert first.attributes[ATTR_CURRENT_TEMPERATURE] is None
    sensor = hass.states.get(entity_id(hass, entry, "sensor", "keypad_temperature_1"))
    assert sensor.state == "unknown"
    second = hass.states.get(entity_id(hass, entry, "sensor", "keypad_temperature_2"))
    assert second.state == "27.7"
    # Left out of the second read
    asked = [
        r
        for r in thermostats_fake.requests
        if r.space == "peripheral" and r.instance == 0
    ]
    # The check and the first read at setup, the first refresh here
    assert len(asked) == 3
    thermostats_fake.state.silent_keypads = set()
    with caplog.at_level(logging.INFO):
        await refresh(hass, entry)
    assert "keypad 1 answers the temperature read again" in caplog.text
    await unload_all(hass)


async def test_older_entry_is_checked_after_setup(
    hass: HomeAssistant, thermostats_fake: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """An entry without the selection: set up first, then its keypads are
    checked once; the option is written and the thermostats are added and
    read, without a reload."""
    config = await stored_configuration(thermostats_fake)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        title="Old",
        data={
            "host": thermostats_fake.host,
            "port": thermostats_fake.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Old",
            DATA_INITIAL_PANEL_CONFIG: config,
        },
        options={"areas": [1], "zones": [1], "scenarios": [1]},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await wait_until(lambda: "thermostats" in entry.options)

        def shown(keypad: int) -> bool:
            target = entity_id(hass, entry, CLIMATE, f"thermostat_{keypad}")
            state = target and hass.states.get(target)
            return bool(state) and state.state == HVACMode.HEAT

        await wait_until(lambda: shown(1))
    assert entry.options["thermostats"] == [1, 2]
    assert entity_id(hass, entry, "sensor", "keypad_temperature_2") is not None
    assert "reloading entry" not in caplog.text
    assert entry.state is ConfigEntryState.LOADED
    # Not checked again at the next start
    probes = sum(r.space == "peripheral" for r in thermostats_fake.requests)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["thermostats"] == [1, 2]
    after = sum(r.space == "peripheral" for r in thermostats_fake.requests)
    assert after - probes == 2  # the two thermostats' first read only
    await unload_all(hass)


async def test_no_keypad_with_a_sensor_changes_nothing(
    hass: HomeAssistant, socket_enabled, caplog: pytest.LogCaptureFixture
) -> None:
    """The check finds none: the empty selection is kept, nothing added."""
    fake = FakePanel(panel_10100())
    await fake.start()
    try:
        config = await stored_configuration(fake)
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            minor_version=2,
            title="Old",
            data={
                "host": fake.host,
                "port": fake.port,
                "pin": "1234",
                "board": "smartlan_si",
                "panel_name": "Old",
                DATA_INITIAL_PANEL_CONFIG: config,
            },
            options={"areas": [1], "zones": [1], "scenarios": [1]},
        )
        entry.add_to_hass(hass)
        with caplog.at_level(logging.INFO):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await wait_until(lambda: "thermostats" in entry.options)
        assert entry.options["thermostats"] == []
        assert "keypads with a thermostat: none" in caplog.text
        assert hass.states.async_entity_ids(CLIMATE) == []
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_unticked_thermostats_are_removed(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    entry = await add_with_thermostats(hass, thermostats_fake)
    result = await open_options(hass, entry, "items")
    assert proposed(result, "thermostats") == [1, 2]
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        items_form(
            result,
            {"areas": None, "zones": None, "scenarios": None, "thermostats": [2]},
        ),
    )
    await hass.async_block_till_done()
    assert entity_id(hass, entry, CLIMATE, "thermostat_1") is None
    assert entity_id(hass, entry, "sensor", "keypad_temperature_1") is None
    assert entity_id(hass, entry, CLIMATE, "thermostat_2") is not None
    await unload_all(hass)


async def test_not_offered_without_a_profile(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """A configuration read with another model's layout offers no keypads."""
    config = await stored_configuration(thermostats_fake)
    assert available_thermostats(config) == [1, 2]
    config["model_string"] = b"5.05 01050  ".hex()
    assert available_thermostats(config) == []


async def test_unavailable_until_read(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """The thermostat's entities follow the panel's availability."""
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    target = entity_id(hass, entry, CLIMATE, "thermostat_1")
    await thermostats_fake.stop()
    await refresh(hass, entry)
    assert hass.states.get(target).state == STATE_UNAVAILABLE
    await unload_all(hass)


def test_fake_layouts_match_the_profiles() -> None:
    """The fake panel's thermostat addresses, typed apart, agree."""
    assert c.LAYOUT_1050.eeprom_thermostats == SMARTLIVING_1050_FW6.thermostat_records
    assert c.LAYOUT_10100.eeprom_thermostats == SMARTLIVING_10100_FW6.thermostat_records
    assert c.LAYOUT_505.eeprom_thermostats == SMARTLIVING_505_FW6.thermostat_records
    assert c.LAYOUT_515.eeprom_thermostats == SMARTLIVING_515_FW6.thermostat_records
    assert (
        c.LAYOUT_1050_FW51.eeprom_thermostats
        == SMARTLIVING_1050_FW51.thermostat_records
    )
    assert (
        c.LAYOUT_505_FW51.eeprom_thermostats == SMARTLIVING_505_FW51.thermostat_records
    )


# ------------------------------------------------------------ review follow-ups


async def test_write_during_a_poll_is_not_undone(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """A write landing between two group reads of a poll: the poll, which
    copied the status before it, does not show the old setting again; the
    next thermostat read gives the panel's own."""
    entry, target = await setup_one(hass, thermostats_fake, mode=1, setpoint=21.0)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    coordinator._polling = True  # scheduled reads, read by hand below
    original = coordinator._read_group

    async def read_group(group):
        reading = await original(group)
        if group is PollGroup.ZONES:  # the write lands after the first group
            await call(hass, SERVICE_SET_TEMPERATURE, target, temperature=23.0)
            assert hass.states.get(target).attributes[ATTR_TEMPERATURE] == 23.0
        return reading

    coordinator._read_group = read_group
    coordinator.schedule = PollSchedule(
        {PollGroup.ZONES: 1.0, PollGroup.AREAS: 2.0, PollGroup.THERMOSTATS: 60.0}
    )
    coordinator.schedule.read(PollGroup.THERMOSTATS, time.monotonic())  # not due
    async with coordinator._reading:
        await coordinator._read_due_groups()
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes[ATTR_TEMPERATURE] == 23.0
    coordinator._read_group = original
    coordinator._polling = False
    # Set at the keypad afterwards: the next thermostat read shows it
    thermostats_fake.state.eeprom_extra[B0 + 2] = bytes((encode_setpoint(19.5),))
    await refresh(hass, entry)
    assert hass.states.get(target).attributes[ATTR_TEMPERATURE] == 19.5
    await unload_all(hass)


async def test_write_during_a_poll_that_fails(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """A poll that fails after a write keeps the written setting."""
    entry, target = await setup_one(hass, thermostats_fake, mode=1, setpoint=21.0)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    coordinator._polling = True  # scheduled reads, read by hand below
    original = coordinator._read_group

    async def read_group(group):
        if group is PollGroup.AREAS:
            raise ProtocolError("unreadable")
        reading = await original(group)
        await call(hass, SERVICE_SET_TEMPERATURE, target, temperature=23.0)
        return reading

    coordinator._read_group = read_group
    coordinator.schedule = PollSchedule({PollGroup.ZONES: 1.0, PollGroup.AREAS: 2.0})
    async with coordinator._reading:
        await coordinator._read_due_groups()
    assert coordinator.data.status.thermostats[0].head.setpoint == 23.0
    coordinator._read_group = original
    coordinator._polling = False
    await unload_all(hass)


async def test_unreadable_temperature(fake: FakePanel, api: SmartLivingPanel) -> None:
    """A temperature reply that cannot be read counts as unanswered: the
    rest is read; the check leaves that keypad out."""
    fake.unreadable_keypads = {0}
    reading = await api.read_thermostats([0, 1], hour=8, weekday=1)
    assert reading.thermostats[0].temperature is None
    assert reading.thermostats[0].head.mode is ThermostatMode.MANUAL
    assert reading.thermostats[1].temperature == 27.7
    assert reading.temperature_failed == {0}
    assert await api.probe_thermostats([0, 1]) == {1}


async def test_unreadable_keypad_keeps_the_rest_available(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    entry = await add_with_thermostats(hass, thermostats_fake)
    thermostats_fake.unreadable_keypads = {0}
    await refresh(hass, entry)
    first = hass.states.get(entity_id(hass, entry, CLIMATE, "thermostat_1"))
    assert first.state == HVACMode.HEAT
    assert first.attributes[ATTR_CURRENT_TEMPERATURE] is None
    second = hass.states.get(entity_id(hass, entry, "sensor", "keypad_temperature_2"))
    assert second.state == "27.7"
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert coordinator._temperature_backoff.skipped() == {0}  # left out next
    await unload_all(hass)


async def test_check_lets_the_reads_through(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """Keypads that do not answer hold the status reads up one at a time,
    not for the whole check."""
    thermostats_fake.state.peripherals["keypads"] = [0, 1, 2, 3, 4]
    thermostats_fake.state.silent_keypads = {2, 3, 4}
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    coordinator.panel.transport.reply_timeout = 0.3
    check = asyncio.create_task(coordinator.async_probe_thermostats([2, 3, 4]))
    await asyncio.sleep(0.1)
    await coordinator.async_refresh()
    assert not check.done()  # the refresh did not wait for the whole check
    assert await check == frozenset()
    await unload_all(hass)


async def test_failing_thermostat_read_in_a_full_read(
    hass: HomeAssistant, thermostats_fake: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A thermostat block that cannot be read fails a full read as a panel
    error, not an unexpected one."""
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    thermostats_fake.unreadable_eeprom = [range(B0, B0 + 27)]
    with caplog.at_level(logging.DEBUG):
        await refresh(hass, entry)
    assert "Unexpected error" not in caplog.text
    target = entity_id(hass, entry, CLIMATE, "thermostat_1")
    assert hass.states.get(target).state == STATE_UNAVAILABLE
    await unload_all(hass)


async def test_backoff_follows_slow_polling(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """A silent keypad's wait is capped in time at the interval in use."""
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "poll_interval_thermostats": 10}
    )
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert coordinator._temperature_backoff.interval == 10
    await coordinator.async_set_slow_polling(True)
    assert coordinator._temperature_backoff.interval == 30
    await coordinator.async_set_slow_polling(False)
    assert coordinator._temperature_backoff.interval == 10
    await unload_all(hass)


@pytest.mark.scheduled_polling
async def test_slow_polling_reads_them_in_the_same_connection(
    hass: HomeAssistant, thermostats_fake: FakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In slow polling mode the thermostats are read in the connection of the
    other groups' read, never in one of their own."""
    clock = [1000.0]
    monkeypatch.setattr(
        "custom_components.inim_smartliving_alarm.coordinator._now", lambda: clock[0]
    )
    entry = await add_with_thermostats(hass, thermostats_fake, [1])
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_set_slow_polling(True)
    await wait_until(lambda: not thermostats_fake.has_client)
    reads_with_thermostats = 0
    for _ in range(4):
        clock[0] += 30.05  # each wake-up a little late
        before = thermostats_fake.connections
        asked = len(thermostats_fake.requests)
        async with coordinator._reading:
            await coordinator._read_due_groups()
        await wait_until(lambda: not thermostats_fake.has_client)
        assert thermostats_fake.connections - before == 1
        new = thermostats_fake.requests[asked:]
        if any(r.space == "peripheral" for r in new):
            reads_with_thermostats += 1
            assert any(r.address == c.RAM_ZONE_STATE for r in new)
    assert reads_with_thermostats == 2  # every 2nd read at the defaults
    await coordinator.async_set_slow_polling(False)
    await unload_all(hass)


def test_new_keypads_are_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keypads enabled since are added; the others stay as the user left
    them; nothing before the first check."""
    monkeypatch.setattr(thermostats, "available_thermostats", lambda config: [1, 2])
    options = {"thermostats": [2]}  # keypad 1 unticked
    assert thermostats.with_new_keypads(options, {}, [1, 2, 3]) == [2, 3]
    assert thermostats.with_new_keypads(options, {}, [1, 2]) is None
    assert thermostats.with_new_keypads({}, {}, [3]) is None


async def test_reconfigure_adds_new_keypads(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """A keypad enabled since gets its thermostat at a reconfigure; one the
    user unticked stays unticked."""
    entry = await add_with_thermostats(hass, thermostats_fake, [2])
    thermostats_fake.state.peripherals["keypads"] = [0, 1, 2]
    thermostats_fake.state.keypad_temperatures[2] = 21.3
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": thermostats_fake.host, "port": thermostats_fake.port},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.options["thermostats"] == [2, 3]
    assert entity_id(hass, entry, CLIMATE, "thermostat_3") is not None
    assert entity_id(hass, entry, CLIMATE, "thermostat_1") is None
    await unload_all(hass)


async def test_reread_adds_new_keypads(
    hass: HomeAssistant, thermostats_fake: FakePanel
) -> None:
    """The same when the panel configuration is read again."""
    entry = await add_with_thermostats(hass, thermostats_fake, [2])
    thermostats_fake.state.peripherals["keypads"] = [0, 1, 2]
    thermostats_fake.state.keypad_temperatures[2] = 21.3
    await reread(hass, entry.entry_id)
    assert entry.options["thermostats"] == [2, 3]
    assert entity_id(hass, entry, CLIMATE, "thermostat_3") is not None
    assert entity_id(hass, entry, CLIMATE, "thermostat_1") is None
    await unload_all(hass)
