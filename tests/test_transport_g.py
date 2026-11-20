"""The SmartLAN/G transport against the fake panel and a scripted server.

The connection lifecycle is shared with SmartLAN/SI and tested there; these
tests cover the AES framing, the board's EEPROM device code and its larger
requests.
"""

import asyncio
from collections.abc import AsyncGenerator
import logging

import pytest

from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.commands import (
    ArmMode,
    arm_areas,
)
from custom_components.inim_smartliving_alarm.protocol.config import find_code_index
from custom_components.inim_smartliving_alarm.protocol.errors import (
    DecryptionError,
    ProtocolError,
)
from custom_components.inim_smartliving_alarm.protocol.frames import (
    DEVICE_EEPROM_G,
    read_ram,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    decode_area_status,
)
from custom_components.inim_smartliving_alarm.transport.base import ReplyTimeout
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
    encrypted_length,
)

from .fake_panel import FakePanel
from .fake_panel.server import SPACE_EEPROM, TRANSPORT_G

pytestmark = pytest.mark.usefixtures("socket_enabled")

USERNAME = "installer"
PASSWORD = "s3cret"
KEY_HEX = "76c79e7359b5a089dde89b9b9fabe4de"  # MD5("installers3cret")

MODEL = read_ram(reg.MODEL_STRING, 12)
AREAS = read_ram(reg.AREA_STATUS, SL1050.area_status_length)

# Made with openssl (AES-128-CBC, PKCS#7) from the key above and
# iv = key XOR 00..0F, independently of the code under test. A read reply
# is the data alone: the board checks the panel's checksum itself.
MODEL_REQUEST_ENCRYPTED = bytes.fromhex("c0bf36fc8fc5a6d50e22c05754a5fbff")
MODEL_REPLY_ENCRYPTED = bytes.fromhex("ce133590d6dacad811323055b1e92226")
# bytes(range(15)) and bytes(range(16))
REPLY_15_ENCRYPTED = bytes.fromhex("c259075a6b1cbd7ddcc46c0eee7199bf")
REPLY_16_ENCRYPTED = bytes.fromhex(
    "0f9a3d0bbb08aee19149ceaae4a9096e77b4e621826aee6c893493f8c8b8884a"
)
# The model string followed by the panel's checksum, as no known board sends
MODEL_WITH_CHECKSUM_ENCRYPTED = bytes.fromhex("722e432d528c3fe366f1cc1429bdbdd3")


@pytest.fixture
async def g_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake 1050 behind SmartLAN/G with the test credentials."""
    panel = FakePanel(transport=TRANSPORT_G, username=USERNAME, password=PASSWORD)
    await panel.start()
    yield panel
    await panel.stop()


@pytest.fixture
async def transport(g_panel: FakePanel) -> AsyncGenerator[SmartLanGTransport]:
    """SmartLAN/G transport to the fake panel."""
    link = SmartLanGTransport(
        g_panel.host,
        g_panel.port,
        username=USERNAME,
        password=PASSWORD,
        reconnect_pause=0.01,
    )
    yield link
    await link.close()


class ScriptedServer:
    """Records each 16-byte request and answers with a fixed reply."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received: list[bytes] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        try:
            while True:
                self.received.append(await reader.readexactly(16))
                writer.write(self.reply)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()


async def _scripted_read(reply: bytes) -> tuple[ScriptedServer, SmartLanGTransport]:
    server = ScriptedServer(reply)
    await server.start()
    link = SmartLanGTransport(
        "127.0.0.1", server.port, username=USERNAME, password=PASSWORD
    )
    return server, link


# ------------------------------------------------------------- framing


@pytest.mark.parametrize(
    ("length", "wire"),
    [
        (1, 16),  # write acknowledgement
        (8, 16),  # read request
        (12, 16),  # model string
        (15, 16),
        (16, 32),
        (31, 32),
        (32, 48),
        (2000, 2016),  # longest read
    ],
)
def test_encrypted_lengths(length: int, wire: int) -> None:
    """(n // 16 + 1) blocks: PKCS#7 adds a whole block at a block boundary."""
    assert encrypted_length(length) == wire


async def test_wire_format_matches_reference_vectors(socket_enabled: None) -> None:
    """Request and reply match AES vectors made with another implementation."""
    server, link = await _scripted_read(MODEL_REPLY_ENCRYPTED)
    try:
        assert await link.read(MODEL, 12) == b"6.07 01050  "
    finally:
        await link.close()
        await server.stop()
    assert server.received == [MODEL_REQUEST_ENCRYPTED]


@pytest.mark.parametrize(
    ("length", "reply"), [(15, REPLY_15_ENCRYPTED), (16, REPLY_16_ENCRYPTED)]
)
async def test_replies_at_the_block_boundary(
    socket_enabled: None, length: int, reply: bytes
) -> None:
    """15 data bytes fit one block and 16 need two; nothing more is awaited."""
    server, link = await _scripted_read(reply)
    link.reply_timeout = 1
    try:
        assert await link.read(read_ram(reg.MODEL_STRING, length), length) == bytes(
            range(length)
        )
    finally:
        await link.close()
        await server.stop()


async def test_reply_with_a_checksum_is_refused(socket_enabled: None) -> None:
    """A reply of N + 1 bytes is a protocol error, not data, and drops the link."""
    server, link = await _scripted_read(MODEL_WITH_CHECKSUM_ENCRYPTED)
    try:
        with pytest.raises(ProtocolError, match="expected 12 bytes, got 13"):
            await link.read(MODEL, 12)
        assert not link.connected
    finally:
        await link.close()
        await server.stop()


async def test_undecryptable_reply_closes_the_connection(
    socket_enabled: None,
) -> None:
    """A reply that does not decrypt is an error and the link is dropped."""
    server, link = await _scripted_read(bytes(range(16)))
    try:
        with pytest.raises(DecryptionError, match="cannot be decrypted"):
            await link.read(MODEL, 12)
        assert not link.connected
    finally:
        await link.close()
        await server.stop()


# ------------------------------------------------------- against the fake


async def test_one_connection_for_everything(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """Reads, EEPROM reads and status share one encrypted connection."""
    assert await transport.read(MODEL, 12) == b"6.07 01050  "
    status = decode_area_status(await transport.read(AREAS, 16), SL1050)
    assert status.armed == frozenset()
    async with transport.session() as session:
        table = await session.read_eeprom(SL1050.pin_table, SL1050.pin_table_length)
    assert find_code_index(table, "4321", SL1050) == 1

    eeprom = [r for r in g_panel.requests if r.space == SPACE_EEPROM]
    assert [r.raw[2] for r in eeprom] == [DEVICE_EEPROM_G]
    assert eeprom[0].address == SL1050.pin_table
    assert transport.connections_opened == 1
    assert g_panel.connections == 1
    assert g_panel.protocol_errors == []


async def test_cancelled_read_closes_the_connection(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """The reply of a cancelled read is never taken for the next one's.

    The board sends no checksum, so nothing else would tell them apart.
    """
    other = read_ram(reg.AREA_STATUS, 12)
    expected = await transport.read(other, 12)
    assert expected != b"6.07 01050  "
    # The late reply is still on its way on the first connection
    g_panel.single_client = False
    g_panel.reply_delay = 0.2
    task = asyncio.create_task(transport.read(MODEL, 12))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not transport.connected

    g_panel.reply_delay = 0.0
    assert await transport.read(other, 12) == expected
    assert transport.connections_opened == 2
    assert transport.stats.counters["cancelled"] == 1
    assert transport.stats.trace[-2]["error"] == "cancelled"


async def test_long_reads_use_2000_byte_parts(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """The board takes reads of up to 2000 bytes per request."""
    address, length = SL1050.zone_records, 2600
    async with transport.session() as session:
        data = await session.read_eeprom(address, length)
    assert data == g_panel.state.eeprom_read(address, length)
    assert [(r.address, r.length) for r in g_panel.requests] == [
        (address, 2000),
        (address + 2000, 600),
    ]
    assert g_panel.protocol_errors == []


async def test_replies_sent_in_pieces(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """An encrypted reply arriving in several pieces is read whole."""
    g_panel.fragment_replies = True
    g_panel.fragment_delay = 0.002
    assert await transport.read(MODEL, 12) == b"6.07 01050  "


async def test_command_and_result(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """A command (header and payload in one message) is acknowledged."""
    result = await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    assert result.ok
    assert g_panel.state.armed_areas() == [3]
    write = g_panel.requests[0]
    assert write.write
    assert write.payload[6:].startswith(bytes.fromhex("00010000"))

    refused = await transport.command(arm_areas("9999", {4: ArmMode.AWAY}, SL1050))
    assert not refused.ok
    assert refused.reason == "invalid_code"
    assert g_panel.state.armed_areas() == [3]
    assert transport.connections_opened == 1


@pytest.mark.parametrize("length", [12, 15, 16, 31, 32])
async def test_reads_return_exactly_the_data(
    transport: SmartLanGTransport, g_panel: FakePanel, length: int
) -> None:
    """Every length, including n % 16 == 15, gives n bytes without waiting longer."""
    transport.reply_timeout = 1
    address = SL1050.zone_records
    async with transport.session() as session:
        data = await session.read_eeprom(address, length)
    assert data == g_panel.state.eeprom_read(address, length)
    assert g_panel.protocol_errors == []


async def test_board_sending_a_checksum_is_an_error(
    transport: SmartLanGTransport, g_panel: FakePanel
) -> None:
    """A board adding the panel's checksum gives a clear error and a new link."""
    g_panel.g_reply_checksum = True
    with pytest.raises(ProtocolError, match="expected 12 bytes, got 13"):
        await transport.read(MODEL, 12)
    assert not transport.connected
    g_panel.g_reply_checksum = False
    assert await transport.read(MODEL, 12) == b"6.07 01050  "


async def test_wrong_credentials(g_panel: FakePanel) -> None:
    """With the wrong password the board cannot decrypt the request."""
    link = SmartLanGTransport(
        g_panel.host,
        g_panel.port,
        username=USERNAME,
        password="wrong",
        reply_timeout=0.2,
        reconnect_pause=0.01,
    )
    try:
        with pytest.raises(ReplyTimeout):
            await link.read(MODEL, 12)
    finally:
        await link.close()
    assert g_panel.requests == []
    assert g_panel.protocol_errors


# ------------------------------------------------------------------ logging

LOGGER = "custom_components.inim_smartliving_alarm.transport"


async def test_debug_log_shows_plain_frames_without_secrets(
    transport: SmartLanGTransport, caplog: pytest.LogCaptureFixture
) -> None:
    """Frames are logged before encryption, PIN masked; no credentials."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await transport.read(MODEL, 12)
    await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    text = caplog.text
    assert "-> 00 00 00 40 00 00 0c 4c" in text
    assert "<- 36 2e 30 37" in text
    assert "** ** ** ** ** ** | 00 01 00 00" in text
    assert "<- acknowledged" in text
    assert "01 02 03 04" not in text  # the PIN
    for secret in (USERNAME, PASSWORD, KEY_HEX, KEY_HEX[:8]):
        assert secret not in text
    assert MODEL_REQUEST_ENCRYPTED.hex(" ")[:11] not in text  # ciphertext
