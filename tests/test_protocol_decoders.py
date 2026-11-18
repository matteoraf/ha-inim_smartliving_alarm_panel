"""Protocol codec: decoding of status, configuration and event replies.

Replies come from captures of a real SmartLiving 1050 and from the fake
panel's own encoders (tests/fake_panel), so both sides are cross-checked.
"""

from datetime import datetime

import pytest

from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.config import (
    TerminalType,
    area_mask,
    decode_code_record,
    decode_label,
    decode_labels,
    decode_physical_map,
    decode_scenario_records,
    decode_terminals,
    decode_zone_records,
    find_code_index,
    label_address,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.events import (
    REMOTE_LOCATION,
    EventPointers,
    decode_event_pointers,
    decode_event_record,
    decode_timestamp,
    event_address,
    newest_indexes,
)
from custom_components.inim_smartliving_alarm.protocol.frames import (
    parse_read_reply,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    AreaMode,
    Fault,
    ZoneState,
    decode_active_scenario,
    decode_area_status,
    decode_faults,
    decode_not_ready_zones,
    decode_panel_state,
    decode_zone_inclusion,
    decode_zone_memory,
    decode_zone_states,
)

from .fake_panel import captures as cap, const as c, panel_1050

ALL_AREAS = frozenset(range(1, 11))


def data_of(reply: bytes) -> bytes:
    """Data of a captured reply, after checking its checksum."""
    return parse_read_reply(reply, len(reply) - 1)


# -------------------------------------------------------------------- status


def test_area_status_captures() -> None:
    """Captured area replies: modes, armed areas and alarm memory."""
    idle = decode_area_status(data_of(cap.AREA_STATUS_ALL_DISARMED), SL1050)
    assert set(idle.modes.values()) == {AreaMode.DISARMED}
    assert idle.armed == frozenset()
    assert idle.alarm == idle.tamper == idle.alarm_memory == frozenset()
    assert idle.auto_arm == ALL_AREAS

    area_5 = decode_area_status(data_of(cap.AREA_STATUS_AREA_5_ARMED), SL1050)
    assert area_5.modes[5] is AreaMode.AWAY
    assert area_5.armed == {5}

    areas = decode_area_status(data_of(cap.AREA_STATUS_AREAS_3_TO_7_ARMED), SL1050)
    assert areas.armed == {3, 4, 5, 6, 7}

    memory = decode_area_status(data_of(cap.AREA_STATUS_MEMORY_AREA_4), SL1050)
    assert memory.alarm_memory == {4}
    assert memory.armed == frozenset()


def test_area_status_all_modes_and_masks() -> None:
    """Stay and instant arming and every mask, encoded by the fake panel."""
    state = panel_1050()
    state.area_modes[0] = c.MODE_AWAY
    state.area_modes[1] = c.MODE_STAY
    state.area_modes[8] = c.MODE_INSTANT
    state.area_alarm = 1 << 9  # area 10
    state.area_tamper = 1 << 8
    state.area_tamper_memory = 1 << 1
    state.area_auto_arm = 0b11

    status = decode_area_status(state.encode_area_status(), SL1050)

    assert status.modes[1] is AreaMode.AWAY
    assert status.modes[2] is AreaMode.STAY
    assert status.modes[9] is AreaMode.INSTANT
    assert status.armed == {1, 2, 9}
    assert status.alarm == {10}
    assert status.tamper == {9}
    assert status.tamper_memory == {2}
    assert status.auto_arm == {1, 2}


def test_zone_states() -> None:
    """All four 2-bit states, including second zones of double terminals."""
    assert set(decode_zone_states(data_of(cap.ZONE_STATE_ALL_REST), SL1050)) == {
        ZoneState.STANDBY
    }
    state = panel_1050()
    state.zone_states[5] = c.ZONE_SHORT
    state.zone_states[19] = c.ZONE_ALARM
    state.zone_states[23] = c.ZONE_TAMPER
    state.zone_states[60] = c.ZONE_ALARM  # second zone of terminal 10

    zones = decode_zone_states(state.encode_zone_state(), SL1050)

    assert len(zones) == 100
    assert zones[5] is ZoneState.SHORT
    assert zones[19] is ZoneState.ALARM
    assert zones[23] is ZoneState.TAMPER
    assert zones[60] is ZoneState.ALARM
    assert zones[0] is ZoneState.STANDBY


def test_zone_inclusion() -> None:
    """Inclusion bitmap first, test flags second; padding bits are ignored."""
    idle = decode_zone_inclusion(data_of(cap.ZONE_INCLUSION_ALL_INCLUDED), SL1050)
    assert all(idle.included)
    assert not any(idle.test)
    assert len(idle.included) == len(idle.test) == 100

    data = bytearray(data_of(cap.ZONE_INCLUSION_ALL_INCLUDED))
    data[2] &= ~(1 << 7)  # zone 24 bypassed
    data[13 + 2] |= 1 << 1  # zone 18 in test
    flags = decode_zone_inclusion(bytes(data), SL1050)
    assert [z for z, inc in enumerate(flags.included) if not inc] == [23]
    assert [z for z, test in enumerate(flags.test) if test] == [17]


def test_zone_memory() -> None:
    """Alarm memory per zone, tamper memory per terminal and outputs."""
    memory = decode_zone_memory(data_of(cap.ZONE_MEMORY_ZONE_24), SL1050)
    assert [z for z, set_ in enumerate(memory.alarm_memory) if set_] == [23]
    assert not any(memory.terminal_tamper_memory)
    assert not any(memory.outputs) and not any(memory.fixed_outputs)

    state = panel_1050()
    state.outputs = {5: 1, 49: 100}
    state.fixed_outputs = [False, True, False]
    state.zone_memory_middle = bytes((0x00, 0x04)) + bytes(5)  # terminal 10
    memory = decode_zone_memory(state.encode_zone_memory(), SL1050)
    assert [t for t, on in enumerate(memory.outputs) if on] == [5, 49]
    assert memory.fixed_outputs == (False, True, False)  # O2 on
    assert [t for t, on in enumerate(memory.terminal_tamper_memory) if on] == [10]


def test_active_scenario() -> None:
    """Values from 30 up mean that no scenario matches."""
    assert decode_active_scenario(b"\x01", SL1050) == 1
    assert decode_active_scenario(b"\x1d", SL1050) == 29
    assert decode_active_scenario(b"\x1e", SL1050) is None
    assert decode_active_scenario(b"\xff", SL1050) is None


def test_not_ready_zones() -> None:
    """The captured not-ready reply lists the captured zones."""
    zones = decode_not_ready_zones(data_of(cap.NOT_READY_REPLY), SL1050)
    assert zones == set(cap.NOT_READY_ZONES)


@pytest.mark.parametrize(
    ("raw", "no_area_armed", "programming", "voice_board"),
    [
        ("0a1c", True, False, True),  # read on a real 1050 while idle
        ("0a14", False, False, True),  # read on a real 1050 with an area armed
        ("0a5c", True, True, True),
        ("0a9c", True, False, False),
    ],
)
def test_panel_state(
    raw: str, no_area_armed: bool, programming: bool, voice_board: bool
) -> None:
    """Panel state flags from real and fake values."""
    state = decode_panel_state(bytes.fromhex(raw))
    assert state.no_area_armed is no_area_armed
    assert state.programming is programming
    assert state.voice_board_present is voice_board


def test_faults_and_voltage() -> None:
    """Fault bits from bytes 0, 1, 4 and 5; supply voltage from bytes 2-3."""
    idle = decode_faults(panel_1050().ram_read(c.RAM_FAULTS, c.FAULTS_LENGTH))
    assert idle.active == frozenset()
    assert idle.supply_voltage == 13.8

    data = bytes((0b1001, 0b100000)) + (800).to_bytes(2, "little") + bytes((0x30, 0x01))
    faults = decode_faults(data)
    assert faults.active == {
        Fault.ZONE_FUSE,
        Fault.MAINS,
        Fault.POWER_SUPPLY,
        Fault.PANEL_DISLODGED,
        Fault.PANEL_OPEN,
        Fault.EXPANSION_TAMPER,
    }
    assert faults.supply_voltage == 12.13


@pytest.mark.parametrize(
    ("decode", "length"),
    [
        (lambda d: decode_area_status(d, SL1050), 16),
        (lambda d: decode_zone_states(d, SL1050), 26),
        (lambda d: decode_zone_inclusion(d, SL1050), 26),
        (lambda d: decode_zone_memory(d, SL1050), 28),
        (lambda d: decode_not_ready_zones(d, SL1050), 13),
        (decode_panel_state, 2),
        (decode_faults, 6),
    ],
)
def test_wrong_lengths_are_refused(decode, length: int) -> None:
    """A reply of the wrong length is a protocol error, never a guess."""
    decode(bytes(length))
    with pytest.raises(ProtocolError):
        decode(bytes(length - 1))


# ------------------------------------------------------------- configuration


def test_labels_and_character_set() -> None:
    """Labels are trimmed and use Inim's characters above 0x7F."""
    assert decode_label(b"Ingresso".ljust(16)) == "Ingresso"
    assert decode_label(b"Caff\x8c".ljust(16, b"\x00")) == "Caffé"
    assert decode_label(bytes((0x80, 0x89, 0xBD, 0xBE)).ljust(16)) == "ΓΩž?"
    assert decode_labels(b"A".ljust(16) + b"B".ljust(16), 2) == ["A", "B"]


def test_labels_from_the_fake_panel() -> None:
    """Zone labels through their RAM pointer and the label bank."""
    state = panel_1050()
    address = label_address(state.ram_read(0x4016, 2), SL1050)
    assert address == 0x100A0
    names = decode_labels(state.eeprom_read(address, 100 * 16), 100)
    assert names[:2] == ["Zone 1", "Zone 2"]
    assert names[99] == "Zone 100"


def test_physical_map() -> None:
    """Keypad zones sit in high physical slots."""
    physical = decode_physical_map(data_of(cap.PHYSICAL_MAP_REPLY), SL1050)
    assert physical.slot_of(0) == 0
    assert physical.slot_of(34) == 34
    assert physical.slot_of(35) == 110
    assert physical.slot_of(36) == 112
    assert physical.slot_of(37) is None
    assert physical.terminal_by_slot[111] is None


def test_terminal_records() -> None:
    """Terminal type, balancing and sensor kind."""
    state = panel_1050()
    terminals = decode_terminals(
        state.eeprom_read(SL1050.terminal_records, 50 * 12), SL1050
    )
    assert all(t.has_zone for t in terminals[:37])
    assert not any(t.has_zone for t in terminals[37:])

    record = bytearray(12)
    record[5], record[6], record[10], record[11] = 4, 0x80, 0x40, TerminalType.DOUBLE
    double = decode_terminals(bytes(record) * 50, SL1050)[0]
    assert (double.balancing, double.roller_blind, double.shock_sensor) == (
        4,
        True,
        False,
    )
    assert double.has_zone and double.has_second_zone and not double.has_output
    assert double.output_options == 0x40

    record[11] = TerminalType.OUT_DIMMING
    assert decode_terminals(bytes(record) * 50, SL1050)[0].has_output
    record[11] = 9  # unknown type: kept, neither zone nor output
    unknown = decode_terminals(bytes(record) * 50, SL1050)[0]
    assert (
        unknown.terminal_type == 9 and not unknown.has_zone and not unknown.has_output
    )


def test_zone_records() -> None:
    """Area masks from the fake panel; options bits from a built record."""
    state = panel_1050()
    zones = decode_zone_records(state.eeprom_read(SL1050.zone_records, 900), SL1050)
    assert len(zones) == 100
    assert zones[19].areas == {1}
    assert zones[23].areas == {4}
    assert zones[35].areas == {7}

    record = bytes((0x03, 0x00, 4, 0x05, 0x20)) + bytes(4)
    zone = decode_zone_records(record * 100, SL1050)[0]
    assert zone.areas == {1, 2}
    assert zone.zone_type == 4  # 24 h
    assert zone.interior and zone.unbypassable and zone.hold_up


def test_scenario_records() -> None:
    """Captured scenario records: area modes, icon and output."""
    state = panel_1050()
    scenarios = decode_scenario_records(
        state.eeprom_read(SL1050.scenario_records, 240), SL1050
    )
    assert scenarios[0].area_modes == dict.fromkeys(range(1, 8), ArmMode.AWAY)
    assert scenarios[1].area_modes == dict.fromkeys(range(1, 9), ArmMode.DISARM)
    assert scenarios[1].icon == 0x24
    assert scenarios[2].area_modes == {1: ArmMode.AWAY, 7: ArmMode.AWAY}
    assert scenarios[2].icon == 0x27
    assert scenarios[2].output == 0


def test_code_records() -> None:
    """Captured user codes: master with all areas, standard with areas 1-7."""
    master = decode_code_record(data_of(cap.CODE_RECORD_0_REPLY), SL1050)
    assert master.areas == ALL_AREAS
    assert master.fixed_outputs == {1, 2, 3}
    assert master.outputs == frozenset()
    assert master.code_type == 2
    assert not master.remote_access

    user = decode_code_record(data_of(cap.CODE_RECORD_1_REPLY), SL1050)
    assert user.areas == frozenset(range(1, 8))
    assert user.code_type == 0
    assert area_mask(user.areas) == 0x007F
    assert area_mask(master.areas) == 0x03FF

    record = bytearray(data_of(cap.CODE_RECORD_1_REPLY))
    record[48] = 0x20  # remote access
    record[51] = 0b101  # terminals 0 and 2
    record[57] = 0x80  # terminal 55: beyond L, ignored
    built = decode_code_record(bytes(record), SL1050)
    assert built.remote_access
    assert built.outputs == {0, 2}


def test_find_code_index() -> None:
    """The PIN is looked up without returning other codes' PINs."""
    table = panel_1050().eeprom_read(SL1050.pin_table, 300)
    assert table.startswith(cap.PIN_TABLE_PREFIX)
    assert find_code_index(table, "1234", SL1050) == 0
    assert find_code_index(table, "4321", SL1050) == 1
    assert find_code_index(table, "5678", SL1050) == 2
    assert find_code_index(table, "9999", SL1050) is None


# -------------------------------------------------------------------- events


def test_event_pointers_and_ring() -> None:
    """Pointers from the capture; newest-first indexes on the 1000-entry ring."""
    pointers = decode_event_pointers(data_of(cap.EVENT_POINTERS_REPLY), SL1050)
    assert (pointers.head, pointers.tail, pointers.wrapped) == (0x1D4, 0x1D5, True)
    assert newest_indexes(pointers, SL1050, 3) == [0x1D3, 0x1D2, 0x1D1]

    # wrapped with the head at the start: continue from the end of the ring
    assert newest_indexes(EventPointers(1, 2), SL1050, 3) == [0, 999, 998]
    # not wrapped: only the events written so far
    assert newest_indexes(EventPointers(2, 0), SL1050, 5) == [1, 0]
    assert newest_indexes(EventPointers(0, 0), SL1050, 5) == []

    assert event_address(0, SL1050) == 0x19204
    assert event_address(999, SL1050) == 0x1B523
    with pytest.raises(ProtocolError):
        decode_event_pointers(bytes.fromhex("e8030000"), SL1050)  # head 1000


def test_timestamp() -> None:
    """Seconds since 2000-01-01, panel local time."""
    assert decode_timestamp(0x32883597) == datetime(2026, 11, 12, 8, 30, 15)


@pytest.mark.parametrize(("raw", "text"), cap.EVENT_SAMPLES)
def test_captured_events_decode(raw: str, text: str) -> None:
    """Every captured record decodes; the code is in range."""
    event = decode_event_record(bytes.fromhex(raw)[:9], SL1050)
    assert 0 <= event.code <= 127
    assert event.timestamp.year in (2025, 2026)


def test_event_fields() -> None:
    """Agent, location, areas, code and activation of known records."""
    alarm = decode_event_record(bytes.fromhex("3215782f0100130080"), SL1050)
    assert alarm.timestamp == datetime(2025, 3, 27, 13, 54, 58)
    assert (alarm.areas, alarm.agent, alarm.location) == ({1}, 19, 0)  # zone 20
    assert (alarm.code, alarm.activation) == (0, True)

    end = decode_event_record(bytes.fromhex("3615782f0100130000"), SL1050)
    assert (end.code, end.activation) == (0, False)

    disarm = decode_event_record(bytes.fromhex("bc03602f780000fe8c"), SL1050)
    assert disarm.areas == {4, 5, 6, 7}
    assert (disarm.agent, disarm.location) == (0, REMOTE_LOCATION)  # Master, app
    assert (disarm.code, disarm.activation) == (12, True)

    reset = decode_event_record(bytes.fromhex("caa15f2f7800ffff8d"), SL1050)
    assert (reset.agent, reset.location, reset.code) == (None, None, 13)
