"""A recording TCP proxy between the API and a SmartLiving panel (SmartLAN/SI).

Every request and its reply are recorded. Write frames are refused unless a
WriteGuard allows them: a refused write is not forwarded, the connection is
closed and the refusal is listed in PanelProxy.blocked.

Recordings never contain the PIN: write payloads start with the 6-byte PIN,
which is replaced by FF bytes, and the 1-byte write acknowledgement (a
checksum over the payload, PIN included) is not stored. Reads of the PIN
table are forwarded (the client looks up its user code there) but only the
length of their reply is kept, never its bytes. Requests with any other
opcode than plain or peripheral read and write are refused like a forbidden
write.

Plain SmartLAN/SI only: the optional SI password and the "SecureSI" XOR
obfuscation are not supported (nor are they by the integration).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import ClassVar

OP_READ = 0x00
OP_WRITE = 0x01
OP_PERIPHERAL_READ = 0x10
OP_PERIPHERAL_WRITE = 0x11
READ_OPS = (OP_READ, OP_PERIPHERAL_READ)
WRITE_OPS = (OP_WRITE, OP_PERIPHERAL_WRITE)
DEVICE_EEPROM = 0x01

# User PINs in EEPROM on a 1050 with firmware 6.x: 50 codes of 6 bytes
PIN_TABLE_START = 0x061F4
PIN_TABLE_END = PIN_TABLE_START + 50 * 6

REGISTER_ARM = 0x2006  # PIN + 8 bytes, one nibble per area
REGISTER_BYPASS = 0x2009  # PIN + physical slot + second-zone flag + action

ACTION_KEEP = 0
ACTION_ARM_TOTAL = 1
ACTION_DISARM = 4

PIN_LENGTH = 6

# The SmartLAN/SI board ignores a connection attempt that arrives right after
# the previous connection closed (the client's TCP stack only retries after
# 1 s); a short pause avoids that. Attempts that still get no answer in time
# are retried until the deadline.
RECONNECT_PAUSE = 0.15
CONNECT_TIMEOUT = 5.0
CONNECT_DEADLINE = 20.0


def reads_pin_table(header: bytes) -> bool:
    """Whether a request reads any byte of the PIN table."""
    if header[0] != OP_READ or header[2] != DEVICE_EEPROM:
        return False
    start = header[1] << 16 | header[3] << 8 | header[4]
    end = start + (header[5] << 8 | header[6])
    return start < PIN_TABLE_END and end > PIN_TABLE_START


def area_actions(nibbles: bytes) -> dict[int, int]:
    """Action per area (1-based) in an arm payload, as the panel decodes it."""
    actions = {}
    for index, byte in enumerate(nibbles):
        actions[2 * index + 1] = byte & 0x0F
        actions[2 * index + 2] = byte >> 4
    return actions


@dataclass(frozen=True)
class WriteGuard:
    """The only writes forwarded to the panel: one area and one zone."""

    area: int  # 1-based area number
    zone_slot: int  # physical terminal slot of the zone

    def refusal(self, header: bytes, payload: bytes) -> str | None:
        """Why a write frame is refused, or None if it may be forwarded."""
        op, device = header[0], header[2]
        register = header[3] << 8 | header[4]
        if op != OP_WRITE or device != 0:
            return f"write outside RAM commands: {header.hex()}"

        if register == REGISTER_ARM:
            if len(payload) != PIN_LENGTH + 8:
                return f"arm frame of unexpected length {len(payload)}"
            touched = {
                area: action
                for area, action in area_actions(payload[PIN_LENGTH:]).items()
                if action != ACTION_KEEP
            }
            if set(touched) != {self.area}:
                return f"arm frame touches areas {sorted(touched)}, only {self.area} allowed"
            if touched[self.area] not in (ACTION_ARM_TOTAL, ACTION_DISARM):
                return f"arm frame asks mode {touched[self.area]} for area {self.area}"
            return None

        if register == REGISTER_BYPASS:
            if len(payload) != PIN_LENGTH + 3:
                return f"bypass frame of unexpected length {len(payload)}"
            slot, second, action = payload[PIN_LENGTH:]
            if slot != self.zone_slot or second != 0 or action not in (0, 1):
                return (
                    f"bypass frame for slot {slot} (second zone {second}, action "
                    f"{action}); only slot {self.zone_slot} allowed"
                )
            return None

        return f"write to register 0x{register:04X} is not allowed"


@dataclass
class Exchange:
    """One request and the reply received for it."""

    header: bytes
    payload: bytes = b""  # PIN already replaced by FF bytes
    sensitive: bool = False  # a PIN-table read: its reply bytes are not kept
    reply: bytearray = field(default_factory=bytearray)
    reply_length: int = 0
    chunks: list[int] = field(default_factory=list)  # sizes as received
    sent_at: float = field(default_factory=time.monotonic)
    first_reply_at: float | None = None

    @property
    def write(self) -> bool:
        """Whether the request writes to the panel."""
        return self.header[0] in WRITE_OPS

    def as_record(self) -> dict[str, object]:
        """JSON-friendly form, without the write acknowledgement."""
        delay = (
            None
            if self.first_reply_at is None
            else round((self.first_reply_at - self.sent_at) * 1000, 1)
        )
        return {
            "request": self.header.hex(),
            "payload": self.payload.hex(),
            "reply": "" if self.write or self.sensitive else self.reply.hex(),
            "reply_length": self.reply_length,
            "sensitive": self.sensitive,
            "chunks": self.chunks,
            "reply_ms": delay,
        }


class PanelProxy:
    """Listens on 127.0.0.1 and forwards each client to the panel."""

    # When any proxy last closed a connection to the panel: tests use several
    # proxies one after the other, and the pause applies across all of them.
    _last_upstream_close: ClassVar[float] = float("-inf")
    # Connections to the panel that are open or still closing, across proxies
    _open_upstreams: ClassVar[int] = 0

    def __init__(
        self,
        panel_host: str,
        panel_port: int,
        guard: WriteGuard | None = None,
        *,
        reconnect_pause: float = RECONNECT_PAUSE,
        connect_timeout: float = CONNECT_TIMEOUT,
        connect_deadline: float = CONNECT_DEADLINE,
    ) -> None:
        """Create the proxy; call start() to begin listening."""
        self.panel_host = panel_host
        self.panel_port = panel_port
        self.guard = guard
        self.reconnect_pause = reconnect_pause
        self.connect_timeout = connect_timeout
        self.connect_deadline = connect_deadline
        self.exchanges: list[Exchange] = []
        self.blocked: list[str] = []
        self.errors: list[str] = []
        # monotonic times of each connection to the panel opening and closing
        self.connected_at: list[float] = []
        self.closed_at: list[float] = []
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task] = set()
        # One client at a time, like the panel itself
        self._client_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start listening on a free local port."""
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)

    async def stop(self) -> None:
        """Stop listening and close all connections."""
        if self._server is not None:
            self._server.close()
        for task in self._handlers:
            task.cancel()
        await asyncio.gather(*self._handlers, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()

    @property
    def host(self) -> str:
        """Address the API connects to."""
        return "127.0.0.1"

    @property
    def port(self) -> int:
        """Port the API connects to."""
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    def save(self, path: Path) -> None:
        """Write the recorded exchanges as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "exchanges": [exchange.as_record() for exchange in self.exchanges],
            "blocked": self.blocked,
            "errors": self.errors,
        }
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")

    # --------------------------------------------------------------- relaying

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._handlers.add(task)
        try:
            async with self._client_lock:
                await self._relay(client_reader, client_writer)
        finally:
            self._handlers.discard(task)
            client_writer.close()

    async def _connect_panel(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        deadline = time.monotonic() + self.connect_deadline
        # Another proxy may still be closing its connection. The count is shared
        # across proxies and test event loops, so poll instead of an Event.
        while PanelProxy._open_upstreams and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        wait = PanelProxy._last_upstream_close + self.reconnect_pause - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        while True:
            timeout = min(self.connect_timeout, deadline - time.monotonic())
            try:
                connection = await asyncio.wait_for(
                    asyncio.open_connection(self.panel_host, self.panel_port),
                    max(timeout, 0.01),
                )
            except OSError as err:  # refused, unreachable or timed out
                if time.monotonic() + self.reconnect_pause >= deadline:
                    self.errors.append(
                        f"cannot connect to the panel within "
                        f"{self.connect_deadline:g} s: {err!r}"
                    )
                    raise
                await asyncio.sleep(self.reconnect_pause)
                continue
            PanelProxy._open_upstreams += 1
            self.connected_at.append(time.monotonic())
            return connection

    async def _relay(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            panel_reader, panel_writer = await self._connect_panel()
        except OSError:
            return
        current: list[Exchange] = []  # the exchange waiting for its reply

        async def panel_to_client() -> None:
            while chunk := await panel_reader.read(4096):
                if current:
                    exchange = current[0]
                    if exchange.first_reply_at is None:
                        exchange.first_reply_at = time.monotonic()
                    if not exchange.sensitive:
                        exchange.reply.extend(chunk)
                    exchange.reply_length += len(chunk)
                    exchange.chunks.append(len(chunk))
                else:
                    self.errors.append(f"unsolicited bytes: {chunk.hex()}")
                client_writer.write(chunk)
                await client_writer.drain()

        replies = asyncio.create_task(panel_to_client())
        try:
            while True:
                try:
                    header = await client_reader.readexactly(8)
                    payload = b""
                    if header[0] in WRITE_OPS:
                        length = header[5] << 8 | header[6]
                        payload = await client_reader.readexactly(length)
                except asyncio.IncompleteReadError:
                    break  # the client closed the connection

                reason = None
                if header[0] not in READ_OPS + WRITE_OPS:
                    reason = f"unknown opcode 0x{header[0]:02X}: {header.hex()}"
                elif header[0] in WRITE_OPS:
                    reason = (
                        "writes are not allowed in read-only mode"
                        if self.guard is None
                        else self.guard.refusal(header, payload)
                    )
                if reason is not None:
                    self.blocked.append(reason)
                    break  # nothing reaches the panel; both sides are closed

                redacted = (
                    b"\xff" * PIN_LENGTH + payload[PIN_LENGTH:] if payload else b""
                )
                current[:] = [Exchange(header, redacted, reads_pin_table(header))]
                self.exchanges.append(current[0])
                panel_writer.write(header + payload)
                await panel_writer.drain()
        finally:
            # Close first and book-keep in a finally: stop() may cancel this
            # task while it waits below
            panel_writer.close()
            replies.cancel()
            try:
                await asyncio.gather(replies, return_exceptions=True)
                with contextlib.suppress(OSError):
                    await panel_writer.wait_closed()
            finally:
                closed = time.monotonic()
                self.closed_at.append(closed)
                PanelProxy._last_upstream_close = closed
                PanelProxy._open_upstreams -= 1
