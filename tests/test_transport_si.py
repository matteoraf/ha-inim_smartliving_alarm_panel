"""The SmartLAN/SI transport against the fake panel and scripted servers."""

import asyncio
from collections.abc import AsyncGenerator
import logging
import time

import pytest

from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.commands import (
    ArmMode,
    arm_areas,
)
from custom_components.inim_smartliving_alarm.protocol.config import find_code_index
from custom_components.inim_smartliving_alarm.protocol.errors import (
    ChecksumError,
    ProtocolError,
)
from custom_components.inim_smartliving_alarm.protocol.frames import (
    checksum,
    read_ram,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    decode_area_status,
)
from custom_components.inim_smartliving_alarm.transport.base import (
    ConnectError,
    ConnectionLost,
    Priority,
    PriorityLock,
    ReplyTimeout,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import wait_until
from .fake_panel import FakePanel

pytestmark = pytest.mark.usefixtures("socket_enabled")

MODEL = read_ram(reg.MODEL_STRING, 12)
AREAS = read_ram(reg.AREA_STATUS, SL1050.area_status_length)


@pytest.fixture
async def transport(fake_panel: FakePanel) -> AsyncGenerator[SmartLanSiTransport]:
    """Transport to the fake panel, which accepts one client at a time."""
    link = SmartLanSiTransport(fake_panel.host, fake_panel.port, reconnect_pause=0.01)
    yield link
    await link.close()


class ScriptedServer:
    """Answers per connection: 'drop' closes after a request, 'ok' replies
    with the model string (or a correct ack), 'bad_ack' sends a wrong ack."""

    def __init__(self, scripts: list[str]) -> None:
        self.scripts = scripts
        self.connections = 0
        self.requests: list[bytes] = []
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
        script = self.scripts[min(self.connections, len(self.scripts) - 1)]
        self.connections += 1
        try:
            while True:
                header = await reader.readexactly(8)
                payload = await reader.readexactly(header[6]) if header[0] == 1 else b""
                self.requests.append(header + payload)
                if script == "drop":
                    break
                if payload:
                    ack = checksum(payload) ^ (0xFF if script == "bad_ack" else 0)
                    writer.write(bytes((ack,)))
                else:
                    data = b"6.07 01050  "
                    writer.write(data + bytes((checksum(data),)))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()


@pytest.fixture
async def scripted(socket_enabled: None) -> AsyncGenerator[ScriptedServer]:
    """A scripted server; tests set its scripts."""
    server = ScriptedServer(["ok"])
    await server.start()
    yield server
    await server.stop()


# ------------------------------------------------------------ normal use


async def test_one_connection_for_everything(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """Reads, long EEPROM reads and status all share one connection."""
    assert await transport.read(MODEL, 12) == b"6.07 01050  "
    status = decode_area_status(await transport.read(AREAS, 16), SL1050)
    assert status.armed == frozenset()
    async with transport.session() as session:
        table = await session.read_eeprom(SL1050.pin_table, SL1050.pin_table_length)
    assert find_code_index(table, "4321", SL1050) == 1

    assert transport.connections_opened == 1
    assert fake_panel.connections == 1
    assert fake_panel.rejected_connections == 0


async def test_replies_sent_in_pieces(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """A reply arriving in several pieces is read whole."""
    fake_panel.fragment_replies = True
    fake_panel.fragment_delay = 0.005
    assert await transport.read(MODEL, 12) == b"6.07 01050  "


async def test_command_and_result(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """A command is acknowledged and its result read from the panel."""
    result = await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    assert result.ok
    assert fake_panel.state.armed_areas() == [3]

    refused = await transport.command(arm_areas("9999", {4: ArmMode.AWAY}, SL1050))
    assert not refused.ok
    assert refused.reason == "invalid_code"
    assert fake_panel.state.armed_areas() == [3]


async def test_commands_go_ahead_of_waiting_polls(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """A command waiting with polls is sent before them."""
    fake_panel.reply_delay = 0.5
    first = asyncio.create_task(transport.read(MODEL, 12))
    # The first poll holds the connection while the others queue up
    await wait_until(lambda: len(fake_panel.requests) == 1)
    polls = [asyncio.create_task(transport.read(AREAS, 16)) for _ in range(2)]
    for _ in range(3):
        await asyncio.sleep(0)  # each poll reaches the lock
    assert len(fake_panel.requests) == 1
    command = asyncio.create_task(
        transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    )
    await asyncio.gather(first, *polls, command)

    order = [
        "write" if request.write else hex(request.address)
        for request in fake_panel.requests
    ]
    assert order == ["0x4000", "write", "0x2004", "0x2000", "0x2000"]


# ------------------------------------------------------------- connection


async def test_idle_connection_is_replaced(fake_panel: FakePanel) -> None:
    """A connection idle for too long is replaced before the next request."""
    link = SmartLanSiTransport(fake_panel.host, fake_panel.port, idle_reconnect=0.1)
    await link.read(MODEL, 12)
    await asyncio.sleep(0.2)  # longer than idle_reconnect
    await link.read(MODEL, 12)  # after the default pause, the old one is gone
    await link.close()
    assert link.connections_opened == 2
    assert fake_panel.rejected_connections == 0


async def test_board_closing_an_idle_connection(socket_enabled: None) -> None:
    """A connection the board closed is reopened transparently."""
    panel = FakePanel(idle_timeout=0.1)
    await panel.start()
    link = SmartLanSiTransport(panel.host, panel.port, reconnect_pause=0.01)
    try:
        await link.read(MODEL, 12)
        await asyncio.sleep(0.3)  # the panel closes the silent connection
        assert await link.read(MODEL, 12) == b"6.07 01050  "
    finally:
        await link.close()
        await panel.stop()
    assert link.connections_opened == 2


async def test_pause_before_reconnecting(
    fake_panel: FakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new connection waits for the pause after the previous close."""
    opened: list[float] = []
    open_connection = asyncio.open_connection

    async def record(*args, **kwargs):
        opened.append(time.monotonic())
        return await open_connection(*args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", record)
    link = SmartLanSiTransport(fake_panel.host, fake_panel.port, reconnect_pause=0.2)
    await link.read(MODEL, 12)
    await link.close()
    closed = time.monotonic()
    await link.read(MODEL, 12)
    await link.close()
    assert opened[1] - closed >= 0.2 - 0.01


async def test_pause_applies_to_a_new_transport(fake_panel: FakePanel) -> None:
    """A second transport to the same board also waits after a close."""
    first = SmartLanSiTransport(fake_panel.host, fake_panel.port, reconnect_pause=0.2)
    await first.read(MODEL, 12)
    await first.close()
    closed = time.monotonic()
    second = SmartLanSiTransport(fake_panel.host, fake_panel.port, reconnect_pause=0.2)
    await second.read(MODEL, 12)
    await second.close()
    assert second.connections_opened == 1
    assert time.monotonic() - closed >= 0.2 - 0.01


async def test_read_repeated_once_after_a_drop(scripted: ScriptedServer) -> None:
    """A read whose connection is dropped is repeated on a new connection."""
    scripted.scripts = ["drop", "ok"]
    link = SmartLanSiTransport("127.0.0.1", scripted.port, reconnect_pause=0.01)
    assert await link.read(MODEL, 12) == b"6.07 01050  "
    await link.close()
    assert scripted.connections == 2


async def test_read_gives_up_after_two_drops(scripted: ScriptedServer) -> None:
    """A read is repeated only once."""
    scripted.scripts = ["drop"]
    link = SmartLanSiTransport("127.0.0.1", scripted.port, reconnect_pause=0.01)
    with pytest.raises(ConnectionLost):
        await link.read(MODEL, 12)
    assert scripted.connections == 2


async def test_command_is_never_repeated(scripted: ScriptedServer) -> None:
    """A command whose connection drops is reported, not sent again."""
    scripted.scripts = ["drop", "ok"]
    link = SmartLanSiTransport("127.0.0.1", scripted.port, reconnect_pause=0.01)
    with pytest.raises(ConnectionLost):
        await link.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    await link.close()
    assert [request[0] for request in scripted.requests] == [0x01]


async def test_wrong_ack_closes_the_connection(scripted: ScriptedServer) -> None:
    """A wrong acknowledgement is an error and the connection is dropped."""
    scripted.scripts = ["bad_ack"]
    link = SmartLanSiTransport("127.0.0.1", scripted.port, reconnect_pause=0.01)
    with pytest.raises(ProtocolError):
        await link.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    assert not link.connected


async def test_reply_timeout(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """A late reply times out, the connection is dropped and the next works."""
    transport.reply_timeout = 0.1
    fake_panel.reply_delay = 0.3
    with pytest.raises(ReplyTimeout):
        await transport.read(MODEL, 12)
    assert not transport.connected
    fake_panel.reply_delay = 0
    await wait_until(lambda: not fake_panel.has_client)  # done with the old one
    assert await transport.read(MODEL, 12) == b"6.07 01050  "


async def test_bad_checksum(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """A corrupted reply raises and the connection is dropped."""
    fake_panel.corrupt_replies = 1
    with pytest.raises(ChecksumError):
        await transport.read(MODEL, 12)
    assert not transport.connected
    await wait_until(lambda: not fake_panel.has_client)  # done with the old one
    assert await transport.read(MODEL, 12) == b"6.07 01050  "


async def test_unreachable_panel(refused_port: int) -> None:
    """Without a listener the connection attempts fail with ConnectError."""
    link = SmartLanSiTransport(
        "127.0.0.1", refused_port, connect_attempts=2, reconnect_pause=0.05
    )
    started = time.monotonic()
    with pytest.raises(ConnectError):
        await link.read(MODEL, 12)
    assert time.monotonic() - started >= 0.05


# ------------------------------------------------------------------ logging

LOGGER = "custom_components.inim_smartliving_alarm.transport.base"


async def test_debug_log_masks_the_pin(
    transport: SmartLanSiTransport, caplog: pytest.LogCaptureFixture
) -> None:
    """Commands are logged without the PIN or its checksum."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    assert "** ** ** ** ** ** | 00 01 00 00" in caplog.text
    assert "01 02 03 04" not in caplog.text
    assert "<- acknowledged" in caplog.text
    assert "<- 00 00" in caplog.text  # command result: success


async def test_debug_log_of_reads(
    transport: SmartLanSiTransport, caplog: pytest.LogCaptureFixture
) -> None:
    """Replies are logged, except those of sensitive reads."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await transport.read(MODEL, 12)
    assert "<- 36 2e 30 37" in caplog.text
    async with transport.session() as session:
        await session.read_eeprom(
            SL1050.pin_table, SL1050.pin_table_length, sensitive=True
        )
    assert "01 02 03 04" not in caplog.text
    assert "<- 251 bytes, not logged" in caplog.text


# ------------------------------------------------- statistics and trace


async def test_trace_never_holds_the_pin(transport: SmartLanSiTransport) -> None:
    """The kept exchanges mask the PIN, keep acks as words and sensitive
    replies as their length."""
    await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    async with transport.session() as session:
        await session.read_eeprom(
            SL1050.pin_table, SL1050.pin_table_length, sensitive=True
        )
    trace = list(transport.stats.trace)
    text = repr(trace)
    assert "01 02 03 04" not in text
    assert "010203" not in text
    command = trace[0]
    assert "** ** ** ** ** **" in command["request"]
    assert command["reply"] == "acknowledged"
    assert trace[1]["reply"] == "00 00"  # the command result
    assert all(t["reply"].endswith("bytes, not kept") for t in trace[2:])


async def test_statistics(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """Counters, timings, command results and the last errors."""
    await transport.read(MODEL, 12)
    await transport.command(arm_areas("1234", {3: ArmMode.AWAY}, SL1050))
    transport.reply_timeout = 0.1
    fake_panel.reply_delay = 0.3
    with pytest.raises(ReplyTimeout):
        await transport.read(MODEL, 12)
    stats = transport.stats.as_dict()
    counters = stats["counters"]
    assert counters["requests"] == 4
    assert counters["connects"] == 1
    assert counters["timeout"] == 1
    assert stats["command_results"] == {"ok": 1}
    assert stats["round_trip"]["count"] == 3
    assert stats["connect"]["count"] == 1
    assert [e["kind"] for e in stats["last_errors"]] == ["timeout"]
    assert transport.stats.trace[-1]["error"] == "timeout"


async def test_the_trace_keeps_the_last_exchanges(
    transport: SmartLanSiTransport,
) -> None:
    """Only the last 30 exchanges are kept."""
    for _ in range(35):
        await transport.read(MODEL, 12)
    assert len(transport.stats.trace) == 30
    assert transport.stats.counters["requests"] == 35


async def test_cancelled_read_closes_the_connection(
    transport: SmartLanSiTransport, fake_panel: FakePanel
) -> None:
    """The reply of a cancelled read is never taken for the next one's."""
    other = read_ram(reg.AREA_STATUS, 12)
    expected = await transport.read(other, 12)
    assert expected != b"6.07 01050  "
    # The late reply is still on its way on the first connection
    fake_panel.single_client = False
    fake_panel.reply_delay = 0.2
    start = len(fake_panel.requests)
    task = asyncio.create_task(transport.read(MODEL, 12))
    await wait_until(lambda: len(fake_panel.requests) > start)  # the reply is late
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not transport.connected

    fake_panel.reply_delay = 0.0
    assert await transport.read(other, 12) == expected
    assert transport.connections_opened == 2
    assert transport.stats.counters["cancelled"] == 1
    assert transport.stats.trace[-2]["error"] == "cancelled"


# ------------------------------------------------------------ priority lock


async def test_priority_lock_order() -> None:
    """Waiters are served by priority, then in arrival order."""
    lock = PriorityLock()
    order: list[str] = []

    async def take(name: str, priority: Priority) -> None:
        await lock.acquire(priority)
        order.append(name)
        await asyncio.sleep(0)
        lock.release()

    await lock.acquire(Priority.POLL)
    tasks = [
        asyncio.create_task(take("poll 1", Priority.POLL)),
        asyncio.create_task(take("command 1", Priority.COMMAND)),
        asyncio.create_task(take("poll 2", Priority.POLL)),
        asyncio.create_task(take("command 2", Priority.COMMAND)),
    ]
    await asyncio.sleep(0)
    lock.release()
    await asyncio.gather(*tasks)
    assert order == ["command 1", "command 2", "poll 1", "poll 2"]
    assert not lock.locked


async def test_priority_lock_cancelled_waiter() -> None:
    """A cancelled waiter neither blocks the lock nor keeps it."""
    lock = PriorityLock()
    await lock.acquire(Priority.POLL)
    waiting = asyncio.create_task(lock.acquire(Priority.COMMAND))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    lock.release()
    assert not lock.locked

    # cancelled right after the lock was handed over: it is released again
    await lock.acquire(Priority.POLL)
    handed = asyncio.create_task(lock.acquire(Priority.POLL))
    await asyncio.sleep(0)
    lock.release()
    handed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handed
    assert not lock.locked
