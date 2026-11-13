"""Tests for the fake SmartLiving 1050 used by the integration tests.

The fake panel must reproduce the bytes captured from real hardware, so these
tests talk to it over TCP with a minimal client and compare raw frames.
"""

import asyncio

import pytest

from .fake_panel import (
    TRANSPORT_G,
    FakePanel,
    captures,
    checksum,
    const as c,
    panel_1050,
)
from .fake_panel.server import smartlan_g_cipher, smartlan_g_decrypt, smartlan_g_encrypt

pytestmark = pytest.mark.usefixtures("socket_enabled")

TIMEOUT = 2.0


def read_frame(
    address: int, length: int, *, op: int = 0x00, num_dev: int = 0, device: int = 0
) -> bytes:
    """Build an 8-byte request."""
    header = bytes(
        (
            op,
            num_dev,
            device,
            address >> 8 & 0xFF,
            address & 0xFF,
            length >> 8,
            length & 0xFF,
        )
    )
    return header + bytes((checksum(header),))


def eeprom_frame(address: int, length: int) -> bytes:
    """Build an EEPROM read (SmartLAN/SI encoding)."""
    return read_frame(
        address & 0xFFFF, length, num_dev=address >> 16, device=c.DEVICE_EEPROM
    )


class Client:
    """Minimal client speaking the panel protocol."""

    def __init__(
        self, panel: FakePanel, username: str = "", password: str = ""
    ) -> None:
        self.panel = panel
        self.cipher = (
            smartlan_g_cipher(username, password)
            if panel.transport == TRANSPORT_G
            else None
        )
        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter

    async def __aenter__(self) -> "Client":
        self.reader, self.writer = await asyncio.open_connection(
            self.panel.host, self.panel.port
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.writer.close()

    async def exchange(self, request: bytes, reply_length: int) -> bytes:
        """Send a request (and payload) and read a reply including its checksum."""
        if self.cipher is not None:
            self.writer.write(smartlan_g_encrypt(self.cipher, request))
            await self.writer.drain()
            encrypted_length = reply_length // 16 * 16 + 16
            async with asyncio.timeout(TIMEOUT):
                encrypted = await self.reader.readexactly(encrypted_length)
            return smartlan_g_decrypt(self.cipher, encrypted)
        self.writer.write(request)
        await self.writer.drain()
        async with asyncio.timeout(TIMEOUT):
            return await self.reader.readexactly(reply_length)

    async def read(self, request: bytes, data_length: int) -> bytes:
        """Read data and check the reply checksum."""
        reply = await self.exchange(request, data_length + 1)
        assert checksum(reply[:-1]) == reply[-1], reply.hex()
        return reply[:-1]

    async def command(self, frame: bytes) -> tuple[int, int]:
        """Send a command, return the acknowledgement and the command result."""
        ack = (await self.exchange(frame, 1))[0]
        result = (await self.read(captures.COMMAND_RESULT_REQUEST, 1))[0]
        return ack, result


def command_frame(address: int, payload: bytes) -> bytes:
    """Build a write to a command register."""
    return read_frame(address, len(payload), op=0x01) + payload


PIN_1234 = bytes.fromhex("01020304ffff")


# ------------------------------------------------------------ captured replies


@pytest.mark.parametrize(
    ("request_frame", "expected"),
    [
        (captures.FIRMWARE_REQUEST, captures.FIRMWARE_REPLY),
        (captures.BRAND_REQUEST, captures.BRAND_REPLY),
        (captures.CODE_RECORD_0_REQUEST, captures.CODE_RECORD_0_REPLY),
        (captures.CODE_RECORD_1_REQUEST, captures.CODE_RECORD_1_REPLY),
        (eeprom_frame(c.EEPROM_PHYSICAL_MAP, 130), captures.PHYSICAL_MAP_REPLY),
        (captures.AREA_STATUS_REQUEST, captures.AREA_STATUS_ALL_DISARMED),
        (captures.ZONE_STATE_REQUEST, captures.ZONE_STATE_ALL_REST),
        (captures.ZONE_INCLUSION_REQUEST, captures.ZONE_INCLUSION_ALL_INCLUDED),
        (captures.EVENT_POINTERS_REQUEST, captures.EVENT_POINTERS_REPLY),
        (captures.KEYPAD_1_TEMPERATURE_REQUEST, captures.KEYPAD_1_TEMPERATURE_REPLY),
        (captures.KEYPAD_2_TEMPERATURE_REQUEST, captures.KEYPAD_2_TEMPERATURE_REPLY),
    ],
    ids=[
        "firmware",
        "brand",
        "code_record_0",
        "code_record_1",
        "physical_map",
        "area_status",
        "zone_state",
        "zone_inclusion",
        "event_pointers",
        "keypad_1_temperature",
        "keypad_2_temperature",
    ],
)
async def test_replies_match_captures(
    fake_panel: FakePanel, request_frame: bytes, expected: bytes
) -> None:
    """Static reads return exactly the captured bytes."""
    async with Client(fake_panel) as client:
        assert await client.exchange(request_frame, len(expected)) == expected
    assert fake_panel.protocol_errors == []


async def test_pin_table_and_scenario_records(fake_panel: FakePanel) -> None:
    """The PIN table and scenario records start with the captured bytes."""
    async with Client(fake_panel) as client:
        pins = await client.read(captures.PIN_TABLE_REQUEST, 250)
        scenarios = await client.read(captures.SCENARIO_MODES_REQUEST, 240)
    assert pins.startswith(captures.PIN_TABLE_PREFIX)
    assert scenarios.startswith(captures.SCENARIO_MODES_PREFIX)


@pytest.mark.parametrize(
    ("armed", "alarm_memory", "expected"),
    [
        ({5: c.MODE_AWAY}, 0, captures.AREA_STATUS_AREA_5_ARMED),
        (
            {area: c.MODE_AWAY for area in range(3, 8)},
            0,
            captures.AREA_STATUS_AREAS_3_TO_7_ARMED,
        ),
        ({}, 1 << 3, captures.AREA_STATUS_MEMORY_AREA_4),
        ({}, 1 << 0, captures.AREA_STATUS_MEMORY_AREA_1),
    ],
    ids=["area_5_armed", "areas_3_to_7_armed", "memory_area_4", "memory_area_1"],
)
async def test_area_status_matches_captures(
    fake_panel: FakePanel, armed: dict[int, int], alarm_memory: int, expected: bytes
) -> None:
    """Area modes and masks encode exactly like the captured replies."""
    for area, mode in armed.items():
        fake_panel.state.area_modes[area - 1] = mode
    fake_panel.state.area_alarm_memory = alarm_memory
    async with Client(fake_panel) as client:
        assert (
            await client.exchange(captures.AREA_STATUS_REQUEST, len(expected))
            == expected
        )


@pytest.mark.parametrize(
    ("zone", "expected"),
    [(23, captures.ZONE_MEMORY_ZONE_24), (19, captures.ZONE_MEMORY_ZONE_20)],
    ids=["zone_24", "zone_20"],
)
async def test_zone_memory_matches_captures(
    fake_panel: FakePanel, zone: int, expected: bytes
) -> None:
    """Zone alarm memory encodes like the captured replies."""
    fake_panel.state.zone_memory[zone] = True
    async with Client(fake_panel) as client:
        assert (
            await client.exchange(captures.ZONE_MEMORY_REQUEST, len(expected))
            == expected
        )


async def test_not_ready_zones_match_capture(fake_panel: FakePanel) -> None:
    """Open zones in the areas armed by scenario 0 are reported as captured."""
    for zone in captures.NOT_READY_ZONES:
        fake_panel.state.zone_states[zone] = c.ZONE_ALARM
    async with Client(fake_panel) as client:
        reply = await client.exchange(
            captures.NOT_READY_REQUEST, len(captures.NOT_READY_REPLY)
        )
    assert reply == captures.NOT_READY_REPLY


async def test_zone_state_alarm_bits(fake_panel: FakePanel) -> None:
    """A zone in alarm sets value 0b10 in its 2-bit slot."""
    fake_panel.state.zone_states[19] = c.ZONE_ALARM  # zone 20
    async with Client(fake_panel) as client:
        data = await client.read(captures.ZONE_STATE_REQUEST, c.ZONE_STATE_LENGTH)
    assert data[4] == 0x95
    assert data[25] == fake_panel.state.zone_state_tail


async def test_active_scenario_follows_area_modes(fake_panel: FakePanel) -> None:
    """All disarmed matches scenario 1; one area armed alone matches none."""
    async with Client(fake_panel) as client:
        assert await client.read(captures.ACTIVE_SCENARIO_REQUEST, 1) == b"\x01"
        fake_panel.state.area_modes[3] = c.MODE_AWAY
        assert await client.read(captures.ACTIVE_SCENARIO_REQUEST, 1) == bytes(
            (c.NO_SCENARIO,)
        )


async def test_captured_events_are_in_the_ring(fake_panel: FakePanel) -> None:
    """Each captured event record is readable at its ring index."""
    expected = {raw[:18]: bytes.fromhex(raw) for raw, _text in captures.EVENT_SAMPLES}
    async with Client(fake_panel) as client:
        for index, event in enumerate(fake_panel.state.events):
            if event is None:
                continue
            address = c.EEPROM_EVENTS + index * c.EVENT_RECORD_LENGTH
            reply = await client.exchange(eeprom_frame(address, 9), 10)
            assert reply == expected[reply[:9].hex()]
    newest = fake_panel.state.events[fake_panel.state.event_head - 1]
    assert newest.to_bytes().hex() == "7c74e12f4000230005"


async def test_event_text_record(fake_panel: FakePanel) -> None:
    """Reading 0x1FFF with LEN = index returns the event with its texts."""
    index = fake_panel.state.event_head - 1
    async with Client(fake_panel) as client:
        data = await client.read(
            read_frame(c.RAM_EVENT_TEXT, index), c.EVENT_TEXT_LENGTH
        )
    assert data[0:6] == bytes.fromhex("7c74e12f4000")
    assert data[6:22].decode().strip() == "End of zone 36 b"
    assert data[66 + 20 * 6 : 82 + 20 * 6].decode().strip() == "Area 7"


# ---------------------------------------------------------------- commands


@pytest.mark.parametrize(
    ("frame", "ack"),
    [
        (captures.ARM_AREA_3, captures.ARM_AREA_3_ACK),
        (captures.DISARM_AREA_7, captures.DISARM_AREA_7_ACK),
        (captures.ACTIVATE_SCENARIO_0, captures.ACTIVATE_SCENARIO_0_ACK),
        (captures.BYPASS_SLOT_23, captures.BYPASS_SLOT_23_ACK),
        (captures.RESET_AREA_7, captures.RESET_AREA_7_ACK),
    ],
    ids=["arm_area_3", "disarm_area_7", "scenario_0", "bypass_slot_23", "reset_area_7"],
)
async def test_captured_commands_are_acknowledged(
    fake_panel: FakePanel, frame: bytes, ack: int
) -> None:
    """Captured command frames get the expected acknowledgement and succeed."""
    async with Client(fake_panel) as client:
        assert await client.command(frame) == (ack, c.RESULT_OK)


async def test_commands_change_state(fake_panel: FakePanel) -> None:
    """Arm, bypass, scenario and reset update the live state."""
    state = fake_panel.state
    async with Client(fake_panel) as client:
        await client.command(captures.ARM_AREA_3)
        assert state.armed_areas() == [3]

        await client.command(captures.BYPASS_SLOT_23)
        assert state.zone_included[23] is False
        inclusion = await client.read(
            captures.ZONE_INCLUSION_REQUEST, c.ZONE_INCLUSION_LENGTH
        )
        assert inclusion[2] == 0x7F

        await client.command(captures.ACTIVATE_SCENARIO_0)
        assert state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]
        assert await client.read(captures.ACTIVE_SCENARIO_REQUEST, 1) == b"\x00"

        state.area_alarm_memory = 1 << 6
        state.zone_memory[35] = True
        await client.command(captures.RESET_AREA_7)
        assert state.area_alarm_memory == 0
        assert state.zone_memory[35] is False


async def test_command_writes_an_event(fake_panel: FakePanel) -> None:
    """Arming logs a 9-byte event: areas, code, network location, code 0x8A."""
    head = fake_panel.state.event_head
    async with Client(fake_panel) as client:
        await client.command(captures.ARM_AREA_3)
        assert await client.read(captures.EVENT_POINTERS_REQUEST, 4) == (
            (head + 1).to_bytes(2, "little")
            + fake_panel.state.event_tail.to_bytes(2, "little")
        )
        record = await client.read(eeprom_frame(c.EEPROM_EVENTS + head * 9, 9), 9)
    assert record[4:] == bytes((0x04, 0x00, 0x00, c.LOCATION_NETWORK, 0x8A))


@pytest.mark.parametrize(
    ("setup", "frame", "result"),
    [
        (
            None,
            command_frame(
                c.RAM_CMD_ARM, bytes.fromhex("09090909ffff") + b"\x01" + bytes(7)
            ),
            c.RESULT_INVALID_CODE,
        ),
        (
            None,
            command_frame(
                c.RAM_CMD_ARM, bytes.fromhex("05060708ffff") + b"\x10" + bytes(7)
            ),
            c.RESULT_CODE_NOT_ENABLED,
        ),
        (
            "zone_0_open",
            command_frame(c.RAM_CMD_ARM, PIN_1234 + b"\x01" + bytes(7)),
            c.RESULT_OPEN_ZONES,
        ),
        ("zone_0_open", captures.ACTIVATE_SCENARIO_0, c.RESULT_OPEN_ZONES),
        (
            None,
            command_frame(c.RAM_CMD_BYPASS, PIN_1234 + bytes((200, 0, 1))),
            c.RESULT_SLOT_OUT_OF_RANGE,
        ),
        (
            None,
            command_frame(c.RAM_CMD_BYPASS, PIN_1234 + bytes((40, 0, 1))),
            c.RESULT_SLOT_NOT_CONNECTED,
        ),
        (
            None,
            command_frame(c.RAM_CMD_BYPASS, PIN_1234 + bytes((23, 0, 0))),
            c.RESULT_ALREADY_IN_STATE,
        ),
        (
            None,
            command_frame(c.RAM_CMD_BYPASS, PIN_1234 + bytes((23, 1, 1))),
            c.RESULT_DOUBLE_ZONE,
        ),
        (
            None,
            command_frame(c.RAM_CMD_OUTPUT, PIN_1234 + bytes((23, 1))),
            c.RESULT_NOT_ENABLED,
        ),
    ],
    ids=[
        "arm_wrong_pin",
        "arm_area_not_in_code",
        "arm_open_zone",
        "scenario_open_zone",
        "bypass_slot_out_of_range",
        "bypass_slot_not_connected",
        "include_already_included",
        "bypass_second_zone_of_single_terminal",
        "output_on_input_terminal",
    ],
)
async def test_command_errors(
    fake_panel: FakePanel, setup: str | None, frame: bytes, result: int
) -> None:
    """Commands that cannot run report the panel's result codes."""
    if setup == "zone_0_open":
        fake_panel.state.zone_states[0] = c.ZONE_ALARM
    async with Client(fake_panel) as client:
        _ack, actual = await client.command(frame)
    assert actual == result


async def test_output_command() -> None:
    """An output terminal enabled in the code's output mask can be switched."""
    state = panel_1050()
    state.physical_map[40] = 40
    state.terminal_types[40] = c.TERMINAL_OUTPUT
    record = bytearray(state.code_records[0])
    record[c.CODE_OUTPUT_MASK_OFFSET + 1 + 40 // 8] |= 1 << (40 % 8)
    state.code_records[0] = bytes(record)
    panel = FakePanel(state)
    await panel.start()
    try:
        async with Client(panel) as client:
            frame = command_frame(c.RAM_CMD_OUTPUT, PIN_1234 + bytes((40, 1)))
            assert (await client.command(frame))[1] == c.RESULT_OK
            memory = await client.read(
                captures.ZONE_MEMORY_REQUEST, c.ZONE_MEMORY_LENGTH
            )
            assert memory[c.OUTPUT_IMAGE_OFFSET + 40 // 8] == 1 << (40 % 8)
            detail = await client.read(read_frame(c.RAM_OUTPUT_DETAIL, 40), 8)
            assert detail[0] == 5 and detail[7] == 1
    finally:
        await panel.stop()


# ----------------------------------------------------------------- transports


async def test_smartlan_g_encrypted_session() -> None:
    """SmartLAN/G: AES frames, logical device codes, encrypted acknowledgements."""
    panel = FakePanel(transport=TRANSPORT_G, username="admin", password="secret")
    await panel.start()
    try:
        async with Client(panel, "admin", "secret") as client:
            assert await client.read(captures.FIRMWARE_REQUEST, 12) == b"6.07 01050  "
            pins = read_frame(
                c.EEPROM_PINS & 0xFFFF, 18, num_dev=0, device=c.LOGICAL_EEPROM
            )
            assert await client.read(pins, 18) == captures.PIN_TABLE_PREFIX
            assert await client.command(captures.ARM_AREA_3) == (
                captures.ARM_AREA_3_ACK,
                0,
            )
        assert panel.state.armed_areas() == [3]
    finally:
        await panel.stop()


async def test_smartlan_g_wrong_key_gets_nothing_useful() -> None:
    """A client using the wrong credentials cannot talk to the panel."""
    panel = FakePanel(transport=TRANSPORT_G, username="admin", password="secret")
    await panel.start()
    try:
        async with Client(panel, "admin", "wrong") as client:
            with pytest.raises((TimeoutError, ValueError, asyncio.IncompleteReadError)):
                await client.read(captures.FIRMWARE_REQUEST, 12)
        assert panel.requests == [] or panel.protocol_errors
    finally:
        await panel.stop()


# ------------------------------------------------------------ fault injection


async def test_fragmented_replies(fake_panel: FakePanel) -> None:
    """With fragmentation a single read returns only part of the reply."""
    fake_panel.fragment_replies = True
    async with Client(fake_panel) as client:
        client.writer.write(captures.AREA_STATUS_REQUEST)
        await client.writer.drain()
        async with asyncio.timeout(TIMEOUT):
            first = await client.reader.read(64)
            rest = await client.reader.readexactly(17 - len(first))
    assert len(first) < 17
    assert first + rest == captures.AREA_STATUS_ALL_DISARMED


async def test_corrupted_checksum(fake_panel: FakePanel) -> None:
    """A corrupted reply has a wrong checksum byte."""
    fake_panel.corrupt_replies = 1
    async with Client(fake_panel) as client:
        reply = await client.exchange(captures.FIRMWARE_REQUEST, 13)
    assert checksum(reply[:-1]) != reply[-1]


async def test_drop_after_requests(fake_panel: FakePanel) -> None:
    """The panel can close the connection after a number of requests."""
    fake_panel.drop_after_requests = 1
    async with Client(fake_panel) as client:
        await client.read(captures.FIRMWARE_REQUEST, 12)
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            await client.read(captures.FIRMWARE_REQUEST, 12)


async def test_bad_request_checksum_gets_no_reply(fake_panel: FakePanel) -> None:
    """A request with a wrong checksum is ignored."""
    async with Client(fake_panel) as client:
        client.writer.write(captures.FIRMWARE_REQUEST[:-1] + b"\x00")
        await client.writer.drain()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.3):
                await client.reader.readexactly(1)
    assert fake_panel.protocol_errors


async def test_idle_timeout() -> None:
    """An idle connection is closed, like the panel's ~6.7 s idle timeout."""
    panel = FakePanel(idle_timeout=0.2)
    await panel.start()
    try:
        async with Client(panel) as client:
            await client.read(captures.FIRMWARE_REQUEST, 12)
            async with asyncio.timeout(TIMEOUT):
                assert await client.reader.read(1) == b""
    finally:
        await panel.stop()


async def test_single_client(fake_panel: FakePanel) -> None:
    """Only one client at a time: a second connection is closed at once."""
    async with Client(fake_panel) as first:
        await first.read(captures.FIRMWARE_REQUEST, 12)
        async with Client(fake_panel) as second:
            async with asyncio.timeout(TIMEOUT):
                assert await second.reader.read(1) == b""
        assert await first.read(captures.FIRMWARE_REQUEST, 12) == b"6.07 01050  "
    assert fake_panel.rejected_connections == 1


async def test_oversized_read_is_flagged(fake_panel: FakePanel) -> None:
    """EEPROM reads longer than 250 bytes are recorded as protocol errors."""
    async with Client(fake_panel) as client:
        await client.read(eeprom_frame(c.EEPROM_PINS, 300), 300)
    assert len(fake_panel.protocol_errors) == 1
    assert fake_panel.protocol_errors[0].startswith("read longer than 250 bytes")
