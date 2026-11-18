"""Protocol codec: request frames, model profiles and commands.

Requests and command frames are compared with frames captured from a real
SmartLiving 1050 (tests/fake_panel/captures.py).
"""

import pytest

from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.commands import (
    ArmMode,
    CommandKind,
    activate_scenario,
    arm_areas,
    bypass_zone,
    clear_call_queue,
    decode_result,
    encode_pin,
    reset_alarm_memory,
    set_output,
    stop_sirens,
)
from custom_components.inim_smartliving_alarm.protocol.errors import (
    ChecksumError,
    InvalidPinError,
    ProtocolError,
    UnsupportedPanelError,
)
from custom_components.inim_smartliving_alarm.protocol.frames import (
    SMARTLAN_G,
    SMARTLAN_SI,
    active_scenario_request,
    carries_pin,
    check_write_ack,
    describe,
    not_ready_request,
    parse_read_reply,
    read_eeprom,
    read_ram,
    split,
    write_ram,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
    SMARTLIVING_10100_FW6 as SL10100,
    model_for,
    parse_model_string,
)

from .fake_panel import captures as cap, const as c

# ------------------------------------------------------------------ requests


@pytest.mark.parametrize(
    ("request_frame", "captured"),
    [
        (read_ram(reg.MODEL_STRING, reg.MODEL_STRING_LENGTH), cap.FIRMWARE_REQUEST),
        (read_ram(reg.BRAND, 2), cap.BRAND_REQUEST),
        (read_eeprom(SL1050.pin_table, 250), cap.PIN_TABLE_REQUEST),
        (read_eeprom(SL1050.code_records, 59), cap.CODE_RECORD_0_REQUEST),
        (read_eeprom(SL1050.code_records + 59, 59), cap.CODE_RECORD_1_REQUEST),
        (
            read_eeprom(SL1050.scenario_records, 30 * SL1050.scenario_record_length),
            cap.SCENARIO_MODES_REQUEST,
        ),
        (
            read_ram(reg.AREA_STATUS, SL1050.area_status_length),
            cap.AREA_STATUS_REQUEST,
        ),
        (read_ram(reg.ZONE_STATE, SL1050.zone_state_length), cap.ZONE_STATE_REQUEST),
        (
            read_ram(reg.ZONE_INCLUSION, SL1050.zone_inclusion_length),
            cap.ZONE_INCLUSION_REQUEST,
        ),
        (
            read_ram(reg.ZONE_MEMORY, SL1050.zone_memory_length),
            cap.ZONE_MEMORY_REQUEST,
        ),
        (read_ram(reg.COMMAND_RESULT, 1), cap.COMMAND_RESULT_REQUEST),
        (
            read_ram(reg.EVENT_POINTERS, reg.EVENT_POINTERS_LENGTH),
            cap.EVENT_POINTERS_REQUEST,
        ),
        (active_scenario_request(0x007F), cap.ACTIVE_SCENARIO_REQUEST),
        (not_ready_request(0, SL1050), cap.NOT_READY_REQUEST),
    ],
)
def test_requests_match_captures(request_frame: bytes, captured: bytes) -> None:
    """Built requests are byte for byte the captured ones."""
    assert request_frame == captured


def test_active_scenario_request_carries_the_whole_area_mask() -> None:
    """Areas 9-10 travel in num_dev (the integration used to send 0x007F)."""
    frame = active_scenario_request(0x03FF)
    assert frame[:7] == bytes.fromhex("0003001ffbff01")


def test_long_reads_are_split() -> None:
    """Reads longer than 250 bytes are split at consecutive addresses."""
    assert split(SL1050.zone_records, 900) == [
        (0x05450, 250),
        (0x0554A, 250),
        (0x05644, 250),
        (0x0573E, 150),
    ]


@pytest.mark.parametrize(
    "build",
    [
        lambda: read_ram(reg.AREA_STATUS, 0),
        lambda: read_ram(reg.AREA_STATUS, 251),
        lambda: read_eeprom(0x1000000, 1),
        lambda: read_eeprom(SL1050.pin_table, 251),
        lambda: read_eeprom(SL1050.pin_table, 2001, SMARTLAN_G),
        lambda: read_ram(reg.MODEL_STRING, 2001, SMARTLAN_G),
        lambda: write_ram(reg.ARM_AREAS, bytes(251)),
        lambda: write_ram(reg.ARM_AREAS, b""),
        lambda: active_scenario_request(0x10000),
        lambda: not_ready_request(30, SL1050),
    ],
)
def test_invalid_requests_are_refused(build) -> None:
    """Out-of-range addresses, lengths and parameters raise ValueError."""
    with pytest.raises(ValueError):
        build()


def test_smartlan_g_dialect() -> None:
    """SmartLAN/G: EEPROM device code 0xFF, reads up to 2000 bytes; RAM as SI."""
    assert read_eeprom(SL1050.pin_table, 250, SMARTLAN_SI) == cap.PIN_TABLE_REQUEST
    frame = read_eeprom(SL1050.pin_table, 250, SMARTLAN_G)
    assert frame[:7] == bytes.fromhex("0000ff61f400fa")
    assert frame[7] == sum(frame[:7]) & 0xFF
    assert read_eeprom(SL1050.event_log, 2000, SMARTLAN_G)[:3] == b"\x00\x01\xff"
    assert read_ram(reg.MODEL_STRING, 12, SMARTLAN_G) == read_ram(reg.MODEL_STRING, 12)
    assert split(SL1050.zone_records, 4500, SMARTLAN_G) == [
        (0x05450, 2000),
        (0x05C20, 2000),
        (0x063F0, 500),
    ]
    assert split(SL1050.zone_records, 900, SMARTLAN_SI) == split(
        SL1050.zone_records, 900
    )


def test_reply_checks() -> None:
    """Replies are checked for length and checksum."""
    assert parse_read_reply(cap.FIRMWARE_REPLY, 12) == b"6.07 01050  "
    with pytest.raises(ProtocolError):
        parse_read_reply(cap.FIRMWARE_REPLY, 11)
    with pytest.raises(ChecksumError):
        parse_read_reply(cap.FIRMWARE_REPLY[:-1] + b"\x00", 12)

    command = arm_areas("1234", {3: ArmMode.AWAY}, SL1050)
    check_write_ack(bytes((cap.ARM_AREA_3_ACK,)), command.payload)
    with pytest.raises(ProtocolError):
        check_write_ack(b"\x00", command.payload)


# -------------------------------------------------------------------- models


def test_model_string() -> None:
    """The 1050 on firmware 6.07 is recognised and supported."""
    info = parse_model_string(b"6.07 01050  ")
    assert (info.version, info.major, info.model) == ("6.07", 6, "1050")
    assert model_for(info) is SL1050


def test_10100_on_firmware_6() -> None:
    """The 10100 on firmware 6.x has its own profile, marked experimental."""
    model = model_for(parse_model_string(b"6.08 10100  "))
    assert model is SL10100
    assert model.experimental
    assert not SL1050.experimental


def test_10100_profile_matches_the_fake_layout() -> None:
    """Counts, record sizes and addresses agree with the independent layout."""
    lay = c.LAYOUT_10100
    assert (SL10100.areas, SL10100.terminals, SL10100.physical_slots) == (
        lay.areas,
        lay.terminals,
        lay.physical_slots,
    )
    assert (SL10100.codes, SL10100.keypads, SL10100.readers) == (
        lay.codes,
        lay.keypads,
        lay.readers,
    )
    assert (SL10100.expanders, SL10100.keys, SL10100.timers) == (
        lay.expanders,
        lay.keys,
        lay.timers,
    )
    assert (
        SL10100.code_record_length,
        SL10100.code_output_mask_length,
        SL10100.scenario_record_length,
        SL10100.event_ring_size,
    ) == (
        lay.code_record_length,
        lay.code_output_mask_length,
        lay.scenario_record_length,
        lay.event_ring_size,
    )
    assert (
        SL10100.pin_table,
        SL10100.code_records,
        SL10100.zone_records,
        SL10100.terminal_records,
        SL10100.physical_map,
        SL10100.scenario_records,
        SL10100.event_log,
    ) == (
        lay.eeprom_pins,
        lay.eeprom_codes,
        lay.eeprom_zones,
        lay.eeprom_terminals,
        lay.eeprom_physical_map,
        lay.eeprom_scenario_modes,
        lay.eeprom_events,
    )


@pytest.mark.parametrize(
    ("model", "lengths"),
    [
        # area status, zone state, inclusion, memory, PIN table,
        # zone records, terminal records, zone labels (2L)
        (SL1050, (16, 26, 26, 28, 300, 900, 600, 100)),
        (SL10100, (18, 51, 52, 53, 600, 1800, 1200, 200)),
    ],
)
def test_status_and_record_lengths(model, lengths: tuple[int, ...]) -> None:
    """Every request length is the one the model expects."""
    assert (
        model.area_status_length,
        model.zone_state_length,
        model.zone_inclusion_length,
        model.zone_memory_length,
        model.pin_table_length,
        model.zones * model.zone_record_length,
        model.terminals * model.terminal_record_length,
        model.zones,
    ) == lengths


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"5.11 01050  ", "firmware 5.11"),
        (b"6.07 00999  ", "SmartLiving 999"),
        (b"5.11 00999  ", "SmartLiving 999"),  # the model is checked first
        (b"5.11 10100  ", "firmware 5.11"),
    ],
)
def test_unsupported_panels(text: bytes, reason: str) -> None:
    """Older firmware and other models are refused with a clear reason."""
    with pytest.raises(UnsupportedPanelError, match=reason):
        model_for(parse_model_string(text))


def test_not_a_model_string() -> None:
    """Garbage is a protocol error, not a model."""
    with pytest.raises(ProtocolError):
        parse_model_string(b"\x00" * 12)


def test_1050_sizes() -> None:
    """Derived lengths match the status registers of the 1050."""
    assert SL1050.zones == 100
    assert SL1050.area_status_length == 16
    assert SL1050.zone_state_length == 26
    assert SL1050.zone_inclusion_length == 26
    assert SL1050.zone_memory_length == 28
    assert SL1050.zone_bitmap_length == 13
    assert SL1050.pin_table_length == 300


# ------------------------------------------------------------------ commands


@pytest.mark.parametrize(
    ("command", "frame", "ack"),
    [
        (
            arm_areas("1234", {3: ArmMode.AWAY}, SL1050),
            cap.ARM_AREA_3,
            cap.ARM_AREA_3_ACK,
        ),
        (
            arm_areas("1234", {7: ArmMode.DISARM}, SL1050),
            cap.DISARM_AREA_7,
            cap.DISARM_AREA_7_ACK,
        ),
        (
            activate_scenario("1234", 0, SL1050),
            cap.ACTIVATE_SCENARIO_0,
            cap.ACTIVATE_SCENARIO_0_ACK,
        ),
        (
            bypass_zone("1234", 0x17, SL1050, bypass=True),
            cap.BYPASS_SLOT_23,
            cap.BYPASS_SLOT_23_ACK,
        ),
        (
            reset_alarm_memory("1234", [7], SL1050),
            cap.RESET_AREA_7,
            cap.RESET_AREA_7_ACK,
        ),
    ],
)
def test_commands_match_captures(command, frame: bytes, ack: int) -> None:
    """Command frames and acknowledgements are the captured ones."""
    assert command.frame == frame
    assert command.expected_ack == ack


def test_arm_several_areas_and_modes() -> None:
    """Odd areas are low nibbles, even areas high nibbles; all modes encode."""
    command = arm_areas(
        "1234",
        {1: ArmMode.AWAY, 2: ArmMode.STAY, 9: ArmMode.INSTANT, 10: ArmMode.DISARM},
        SL1050,
    )
    assert command.payload[6:] == bytes.fromhex("2100000043000000")


def test_other_commands() -> None:
    """Payloads of the commands without captures."""
    assert set_output("1234", 2, 100, SL1050).payload[6:] == bytes((2, 100))
    assert bypass_zone("1234", 9, SL1050, bypass=False, second_zone=True).payload[
        6:
    ] == bytes((9, 1, 0))
    assert stop_sirens("1234").payload[6:] == b"\x00"
    assert clear_call_queue("1234").payload[6:] == b"\x01"
    assert stop_sirens("1234").register == reg.STOP_SIRENS_OR_CALLS


@pytest.mark.parametrize(
    "build",
    [
        lambda: arm_areas("1234", {11: ArmMode.AWAY}, SL1050),
        lambda: arm_areas("1234", {0: ArmMode.AWAY}, SL1050),
        lambda: reset_alarm_memory("1234", [11], SL1050),
        lambda: bypass_zone("1234", 130, SL1050, bypass=True),
        lambda: set_output("1234", 2, 101, SL1050),
        lambda: activate_scenario("1234", 30, SL1050),
    ],
)
def test_invalid_commands_are_refused(build) -> None:
    """Out-of-range areas, slots, values and scenarios raise ValueError."""
    with pytest.raises(ValueError):
        build()


def test_pin_encoding() -> None:
    """Digits one per byte, padded with 0xFF; anything else is refused."""
    assert encode_pin("1234") == bytes.fromhex("01020304ffff")
    assert encode_pin("123456") == bytes.fromhex("010203040506")
    for pin in ("", "1234567", "12a4", "١٢٣٤"):
        with pytest.raises(InvalidPinError):
            encode_pin(pin)


@pytest.mark.parametrize(
    ("kind", "code", "reason"),
    [
        (CommandKind.ARM, 0x00, None),
        (CommandKind.ARM, 0x02, "open_zones"),
        (CommandKind.ARM, 0x03, "code_not_enabled"),
        (CommandKind.ARM, 0xFF, "invalid_code"),
        (CommandKind.RESET, 0x02, "code_not_enabled"),
        (CommandKind.BYPASS, 0x02, "already_in_state"),
        (CommandKind.BYPASS, 0x06, "cannot_be_bypassed"),
        (CommandKind.OUTPUT, 0x07, "areas_armed"),
        (CommandKind.SCENARIO, 0x02, "refused"),
        (CommandKind.BYPASS, 0x09, "unknown"),
    ],
)
def test_command_results(kind: CommandKind, code: int, reason: str | None) -> None:
    """Result codes mean different things for each command type."""
    result = decode_result(kind, bytes((code,)))
    assert result.reason == reason
    assert result.ok is (code == 0)


def test_describe_masks_the_pin() -> None:
    """Frames are logged in hex with the PIN of commands masked."""
    assert carries_pin(cap.ARM_AREA_3)
    assert describe(cap.ARM_AREA_3) == (
        "01 00 00 20 06 00 0e 35 | ** ** ** ** ** ** | 00 01 00 00 00 00 00 00"
    )
    assert describe(cap.BYPASS_SLOT_23).endswith("| ** ** ** ** ** ** | 17 00 01")
    assert not carries_pin(cap.FIRMWARE_REQUEST)
    assert describe(cap.FIRMWARE_REQUEST) == "00 00 00 40 00 00 0c 4c"
    other = write_ram(0x1234, b"\x01\x02")
    assert not carries_pin(other)
    assert describe(other).endswith("| 01 02")
