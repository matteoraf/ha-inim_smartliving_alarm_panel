"""Connection handling shared by the SmartLAN transports.

Measured on a SmartLAN/SI board: it closes a connection after about 6.7 s
without requests, and it ignores a new connection that arrives right after
the previous one closed (the client's TCP stack only retries after 1 s);
it may also accept a connection and close it at once. So the transport
keeps one connection, replaces it before it can go idle, pauses briefly
before reconnecting (also when another transport for the same board closed), and repeats a read once if its connection was lost.

At debug level every frame is logged, with the PIN of commands masked;
replies that may hold PINs are not logged.
"""

import asyncio
from collections import Counter, deque
from collections.abc import AsyncIterator
import contextlib
from enum import IntEnum
import heapq
import itertools
import logging
import time
from typing import Any, ClassVar

from ..protocol import registers as reg
from ..protocol.commands import Command, CommandResult, decode_result
from ..protocol.errors import ProtocolError
from ..protocol.frames import (
    Dialect,
    carries_pin,
    check_write_ack,
    describe,
    parse_plain_read_reply,
    parse_read_reply,
    read_eeprom as read_eeprom_frame,
    read_ram as read_ram_frame,
    split,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
CONNECT_ATTEMPTS = 3
REPLY_TIMEOUT = 5.0
RECONNECT_PAUSE = 0.15  # after a close, before the next connection attempt
IDLE_RECONNECT = (
    5.0  # replace a connection idle for longer (the board drops it at ~6.7 s)
)

# When a connection to each address last closed, for every transport: a new
# transport for the same board (after a reload, or a second client) must
# also pause before connecting
_last_close: dict[tuple[str, int], float] = {}


class TransportError(Exception):
    """The panel could not be reached or did not answer."""


class ConnectError(TransportError):
    """No connection could be opened."""


class ConnectionLost(TransportError):
    """The connection closed while waiting for a reply."""


class ReplyTimeout(TransportError):
    """No complete reply arrived in time."""


class Priority(IntEnum):
    """Who goes first when several requests wait (lower first)."""

    COMMAND = 0
    POLL = 1


class PriorityLock:
    """A lock whose waiters are served by priority, then in arrival order."""

    def __init__(self) -> None:
        """Create an unlocked lock."""
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    @property
    def locked(self) -> bool:
        """Whether someone holds the lock."""
        return self._locked

    async def acquire(self, priority: Priority) -> None:
        """Wait for the lock."""
        if not self._locked and not self._waiters:
            self._locked = True
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (int(priority), next(self._order), future)
        heapq.heappush(self._waiters, entry)
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()  # the lock was handed over just before the cancel
            elif entry in self._waiters:
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            raise

    def release(self) -> None:
        """Hand the lock to the next waiter, or unlock."""
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
                return
        self._locked = False


TRACE_LENGTH = 30  # exchanges kept for a system report
ERRORS_KEPT = 5


class TransportStats:
    """Counters, timings and the last exchanges of a transport.

    Kept in memory for a system report, so it never holds a secret: the
    PIN of a command is masked when the request is recorded, a write's
    acknowledgement is kept only as right or wrong, and the reply of a
    sensitive read only as its length.
    """

    def __init__(self) -> None:
        """Start from zero."""
        self.started = time.time()
        self.counters: Counter[str] = Counter()
        self.command_results: Counter[str] = Counter()
        self._round_trips: deque[float] = deque(maxlen=200)  # seconds
        self._connect_times: deque[float] = deque(maxlen=50)
        self.errors: deque[dict[str, Any]] = deque(maxlen=ERRORS_KEPT)
        self.trace: deque[dict[str, Any]] = deque(maxlen=TRACE_LENGTH)

    def exchange(
        self,
        request: bytes,
        reply: bytes | None,
        seconds: float,
        *,
        keep_reply: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Record one exchange; returns its trace entry."""
        self.counters["requests"] += 1
        self.counters["bytes_sent"] += len(request)
        entry: dict[str, Any] = {
            "time": round(time.time(), 3),
            "request": describe(request),
            "ms": round(seconds * 1000, 1),
        }
        if reply is not None:
            self.counters["bytes_received"] += len(reply)
            self._round_trips.append(seconds)
            entry["reply"] = (
                reply.hex(" ") if keep_reply else f"{len(reply)} bytes, not kept"
            )
        if error is not None:
            self.counters[error] += 1
            entry["error"] = error
            self.error(error)
        self.trace.append(entry)
        return entry

    def connected(self, seconds: float) -> None:
        """Record a connection opened."""
        self.counters["connects"] += 1
        self._connect_times.append(seconds)

    def error(self, kind: str) -> None:
        """Record an error (kind only: no address, no data)."""
        self.errors.append({"time": round(time.time(), 3), "kind": kind})

    def as_dict(self) -> dict[str, Any]:
        """The statistics for a report."""

        def timing(values: deque[float]) -> dict[str, float] | None:
            if not values:
                return None
            return {
                "count": len(values),
                "min_ms": round(min(values) * 1000, 1),
                "avg_ms": round(sum(values) / len(values) * 1000, 1),
                "max_ms": round(max(values) * 1000, 1),
            }

        return {
            "since": round(self.started, 3),
            "counters": dict(sorted(self.counters.items())),
            "command_results": dict(sorted(self.command_results.items())),
            "round_trip": timing(self._round_trips),
            "connect": timing(self._connect_times),
            "last_errors": list(self.errors),
        }


class Session:
    """Exclusive use of the connection for one or more requests."""

    def __init__(self, transport: "PanelTransport") -> None:
        """Wrap a transport whose lock is held."""
        self._transport = transport

    async def read(
        self, frame: bytes, length: int, *, sensitive: bool = False
    ) -> bytes:
        """Send a read request and return the checked data.

        A read is repeated once on a new connection if the connection was
        lost; it is not repeated after a timeout. The reply of a sensitive
        read (such as the PIN table) is never logged.
        """
        with_checksum = self._transport.reply_checksum
        for attempt in (1, 2):
            try:
                reply = await self._transport.exchange(
                    frame,
                    length + 1 if with_checksum else length,
                    log_reply=not sensitive,
                )
            except ConnectionLost:
                if attempt == 2:
                    raise
                self._transport.stats.counters["repeated_reads"] += 1
                _LOGGER.debug("Connection lost during a read; repeating it")
                continue
            try:
                if with_checksum:
                    return parse_read_reply(reply, length)
                return parse_plain_read_reply(reply, length)
            except ProtocolError:
                self._transport.stats.counters["checksum_errors"] += 1
                self._transport.stats.error("checksum_error")
                await self._transport.disconnect()  # do not reuse an out-of-step link
                raise
        raise AssertionError("unreachable")

    async def read_ram(
        self, address: int, length: int, *, sensitive: bool = False
    ) -> bytes:
        """Read RAM, in parts if longer than one request allows."""
        return await self._read_parts(address, length, read_ram_frame, sensitive)

    async def read_eeprom(
        self, address: int, length: int, *, sensitive: bool = False
    ) -> bytes:
        """Read EEPROM, in parts if longer than one request allows."""
        return await self._read_parts(address, length, read_eeprom_frame, sensitive)

    async def write(self, frame: bytes, payload: bytes) -> None:
        """Send a write and check its acknowledgement; never repeated.

        The acknowledgement of a command is a checksum over the PIN too, so
        it is only logged as right or wrong.
        """
        pin = carries_pin(frame)
        reply = await self._transport.exchange(frame, 1, log_reply=not pin)
        entry = self._transport.stats.trace[-1]
        try:
            check_write_ack(reply, payload)
        except ProtocolError:
            entry["reply"] = "wrong acknowledgement"
            self._transport.stats.counters["wrong_acks"] += 1
            self._transport.stats.error("wrong_ack")
            if pin:
                _LOGGER.debug("<- wrong acknowledgement")
            await self._transport.disconnect()
            raise
        entry["reply"] = "acknowledged"
        if pin:
            _LOGGER.debug("<- acknowledged")

    async def command(self, command: Command) -> CommandResult:
        """Send a command, then read the panel's result for it."""
        await self.write(command.frame, command.payload)
        result = read_ram_frame(reg.COMMAND_RESULT, 1, self._transport.dialect)
        data = await self.read(result, 1)
        decoded = decode_result(command.kind, data)
        self._transport.stats.command_results[decoded.reason or "ok"] += 1
        return decoded

    async def _read_parts(
        self, address: int, length: int, build, sensitive: bool
    ) -> bytes:
        dialect = self._transport.dialect
        data = b""
        for part_address, part_length in split(address, length, dialect):
            data += await self.read(
                build(part_address, part_length, dialect),
                part_length,
                sensitive=sensitive,
            )
        return data


class PanelTransport:
    """One TCP connection to a SmartLAN board, used one request at a time.

    Subclasses set the board's request dialect and implement the framing
    (_encode and _read_reply).
    """

    dialect: ClassVar[Dialect]
    # Whether a read reply ends with the panel's checksum (SmartLAN/SI) or
    # carries the data alone (SmartLAN/G, which checks it on the board)
    reply_checksum: ClassVar[bool] = True

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        connect_attempts: int = CONNECT_ATTEMPTS,
        reply_timeout: float = REPLY_TIMEOUT,
        reconnect_pause: float = RECONNECT_PAUSE,
        idle_reconnect: float = IDLE_RECONNECT,
    ) -> None:
        """Set up the transport; nothing connects until the first request."""
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.connect_attempts = connect_attempts
        self.reply_timeout = reply_timeout
        self.reconnect_pause = reconnect_pause
        self.idle_reconnect = idle_reconnect
        self.connections_opened = 0
        self.stats = TransportStats()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = PriorityLock()
        self._last_activity = float("-inf")

    @property
    def connected(self) -> bool:
        """Whether a connection is open."""
        return self._writer is not None and not self._writer.is_closing()

    @contextlib.asynccontextmanager
    async def session(
        self, priority: Priority = Priority.POLL
    ) -> AsyncIterator[Session]:
        """Hold the connection for several requests."""
        await self._lock.acquire(priority)
        try:
            yield Session(self)
        finally:
            self._lock.release()

    async def read(
        self, frame: bytes, length: int, priority: Priority = Priority.POLL
    ) -> bytes:
        """Send one read request and return the checked data."""
        async with self.session(priority) as session:
            return await session.read(frame, length)

    async def command(self, command: Command) -> CommandResult:
        """Send one command ahead of any waiting poll and return its result."""
        async with self.session(Priority.COMMAND) as session:
            return await session.command(command)

    async def close(self) -> None:
        """Close the connection once the current request is done."""
        async with self.session(Priority.COMMAND):
            await self.disconnect()

    @contextlib.asynccontextmanager
    async def released(self) -> AsyncIterator[None]:
        """Hold this transport's requests and free its connection meanwhile.

        For another client that must reach the same board, which may accept
        only one connection; this transport reconnects on its next request.
        """
        async with self.session(Priority.COMMAND):
            await self.disconnect()
            yield

    # ------------------------------------------------- with the lock held

    async def exchange(
        self, data: bytes, reply_length: int, *, log_reply: bool = True
    ) -> bytes:
        """Send bytes and read a reply of known length (lock must be held).

        At debug level the request is logged with the PIN masked, and the
        reply unless log_reply is False.
        """
        await self._ensure_connected()
        assert self._reader is not None and self._writer is not None
        debug = _LOGGER.isEnabledFor(logging.DEBUG)
        if debug:
            _LOGGER.debug("-> %s", describe(data))
        sent = time.monotonic()
        try:
            self._writer.write(self._encode(data))
            await self._writer.drain()
            reply = await asyncio.wait_for(
                self._read_reply(self._reader, reply_length), self.reply_timeout
            )
        except TimeoutError as err:
            self._record(data, sent, "timeout")
            await self.disconnect()
            raise ReplyTimeout(f"no reply from {self.host}:{self.port}") from err
        except (OSError, asyncio.IncompleteReadError) as err:
            self._record(data, sent, "connection_lost")
            await self.disconnect()
            raise ConnectionLost(f"connection to {self.host}:{self.port} lost") from err
        except ProtocolError:  # the framing could not decode the reply
            self._record(data, sent, "framing_error")
            await self.disconnect()
            raise
        except asyncio.CancelledError:
            # The reply may still come: a later request on this connection
            # would read it as its own, so the connection goes, without
            # waiting while being cancelled.
            self._record(data, sent, "cancelled")
            _LOGGER.debug("Request cancelled; closing the connection")
            self._drop()
            raise
        self._last_activity = time.monotonic()
        self.stats.exchange(
            data, reply, self._last_activity - sent, keep_reply=log_reply
        )
        if debug:
            if log_reply:
                _LOGGER.debug("<- %s", reply.hex(" "))
            else:
                _LOGGER.debug("<- %d bytes, not logged", len(reply))
        return reply

    def _record(self, data: bytes, sent: float, error: str) -> None:
        self.stats.exchange(
            data, None, time.monotonic() - sent, keep_reply=False, error=error
        )

    async def disconnect(self) -> None:
        """Close the connection (lock must be held)."""
        writer = self._drop()
        if writer is None:
            return
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        _last_close[(self.host, self.port)] = time.monotonic()

    def _drop(self) -> asyncio.StreamWriter | None:
        """Close the connection without waiting for it (lock must be held).

        Returns the closed writer, None if there was no connection.
        """
        writer, self._reader, self._writer = self._writer, None, None
        if writer is None:
            return None
        writer.close()
        _last_close[(self.host, self.port)] = time.monotonic()
        _LOGGER.debug("Disconnected from %s:%s", self.host, self.port)
        return writer

    async def _ensure_connected(self) -> None:
        if self._writer is not None:
            assert self._reader is not None
            idle = time.monotonic() - self._last_activity
            if self._writer.is_closing() or self._reader.at_eof():
                await self.disconnect()
            elif idle > self.idle_reconnect:
                self.stats.counters["idle_replacements"] += 1
                _LOGGER.debug("Connection idle for %.1f s; replacing it", idle)
                await self.disconnect()
        if self._writer is None:
            await self._connect()

    async def _connect(self) -> None:
        error: Exception | None = None
        for attempt in range(1, self.connect_attempts + 1):
            closed = _last_close.get((self.host, self.port), float("-inf"))
            wait = closed + self.reconnect_pause - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            started = time.monotonic()
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), self.connect_timeout
                )
            except OSError as err:  # refused, unreachable or timed out
                error = err
                self.stats.counters["connect_failures"] += 1
                self.stats.error("connect_failure")
                _last_close[(self.host, self.port)] = time.monotonic()
                _LOGGER.debug(
                    "Connection attempt %s to %s:%s failed: %r",
                    attempt,
                    self.host,
                    self.port,
                    err,
                )
                continue
            self.connections_opened += 1
            self._last_activity = time.monotonic()
            self.stats.connected(self._last_activity - started)
            _LOGGER.debug("Connected to %s:%s", self.host, self.port)
            return
        raise ConnectError(f"cannot connect to {self.host}:{self.port}: {error!r}")

    # ---------------------------------------------- framing (per board type)

    def _encode(self, data: bytes) -> bytes:
        """Bytes to send for a request."""
        raise NotImplementedError

    async def _read_reply(self, reader: asyncio.StreamReader, length: int) -> bytes:
        """Read a reply of length bytes (after any decoding).

        Raise ProtocolError for a reply that cannot be decoded.
        """
        raise NotImplementedError
