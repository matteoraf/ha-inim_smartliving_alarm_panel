"""Finding SmartLAN boards on the local network.

SmartLAN/SI: the ASCII probe FIND is broadcast to UDP 1460; each board
answers on UDP 5001 with IMIN, its MAC address (bytes 4-9) and its network
configuration (TCP port in bytes 23-24, big-endian).

SmartLAN/G: hello_smartlang is broadcast to UDP 7000; each board broadcasts
"firmware|model|panel name|board firmware|port" to UDP 7001, or "err" when
it has no panel information.

Both boards only take part on their own network segment, so only boards on
the same subnet as the caller answer. A board's address is the source
address of its reply. No Home Assistant imports.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging

from .const import BOARD_SMARTLAN_G, BOARD_SMARTLAN_SI

_LOGGER = logging.getLogger(__name__)

BROADCAST = "255.255.255.255"
SCAN_TIME = 3.0  # seconds to wait for replies

SI_PROBE = b"FIND"
SI_PROBE_PORT = 1460
SI_REPLY_PORT = 5001
# The board compares the request as a C string: end it with NUL
G_PROBE = b"hello_smartlang\x00"
G_PROBE_PORT = 7000
G_REPLY_PORT = 7001

_SI_REPLY_MIN_LENGTH = 25


class DiscoveryError(Exception):
    """No reply port could be opened."""


@dataclass(frozen=True, slots=True)
class DiscoveredBoard:
    """A SmartLAN board that answered."""

    board: str  # BOARD_SMARTLAN_SI or BOARD_SMARTLAN_G
    host: str
    port: int | None  # TCP port for the panel protocol, if the board said
    mac: str | None = None  # SmartLAN/SI
    firmware: str | None = None  # SmartLAN/G: panel firmware, e.g. "6.07"
    model: str | None = None  # SmartLAN/G: panel model, e.g. "01050"
    panel_name: str | None = None  # SmartLAN/G


def parse_si_reply(data: bytes, host: str) -> DiscoveredBoard | None:
    """A SmartLAN/SI FIND reply, or None if it is not one."""
    if len(data) < _SI_REPLY_MIN_LENGTH or not data.startswith(b"IMIN"):
        return None
    return DiscoveredBoard(
        BOARD_SMARTLAN_SI,
        host,
        port=int.from_bytes(data[23:25], "big") or None,
        mac=data[4:10].hex(":"),
    )


def parse_g_reply(data: bytes, host: str) -> DiscoveredBoard | None:
    """A SmartLAN/G reply, or None if it is not one."""
    text = data.decode("ascii", errors="replace").strip("\x00\r\n ")
    if text == "err":
        return DiscoveredBoard(BOARD_SMARTLAN_G, host, port=None)
    parts = text.split("|")
    if len(parts) < 5:
        return None
    firmware, model, name, _board_firmware, port = (part.strip() for part in parts[:5])
    return DiscoveredBoard(
        BOARD_SMARTLAN_G,
        host,
        port=int(port) if port.isdigit() else None,
        firmware=firmware or None,
        model=model or None,
        panel_name=name or None,
    )


class _Collector(asyncio.DatagramProtocol):
    def __init__(
        self,
        parse: Callable[[bytes, str], DiscoveredBoard | None],
        found: dict[tuple[str, str, int | None], DiscoveredBoard],
    ) -> None:
        self._parse = parse
        self._found = found

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        board = self._parse(data, addr[0])
        if board is None:
            _LOGGER.debug("Ignored a discovery reply from %s", addr[0])
            return
        _LOGGER.debug("Found %s at %s:%s", board.board, board.host, board.port)
        self._found[(board.board, board.host, board.port)] = board


async def discover(
    scan_time: float = SCAN_TIME,
    *,
    target: str = BROADCAST,
    si_ports: tuple[int, int] = (SI_PROBE_PORT, SI_REPLY_PORT),
    g_ports: tuple[int, int] = (G_PROBE_PORT, G_REPLY_PORT),
) -> list[DiscoveredBoard]:
    """Probe for SmartLAN/SI and SmartLAN/G boards and collect the replies.

    Each probe is sent from the socket that listens for its replies. If one
    reply port is busy, the other board type is still searched; if neither
    can be opened, DiscoveryError is raised.
    """
    loop = asyncio.get_running_loop()
    found: dict[tuple[str, str, int | None], DiscoveredBoard] = {}
    sockets: list[asyncio.DatagramTransport] = []
    try:
        for (probe_port, reply_port), probe, parse in (
            (si_ports, SI_PROBE, parse_si_reply),
            (g_ports, G_PROBE, parse_g_reply),
        ):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda parse=parse: _Collector(parse, found),
                    local_addr=("0.0.0.0", reply_port),
                    allow_broadcast=True,
                )
            except OSError as err:
                _LOGGER.warning("Cannot listen on UDP port %s: %s", reply_port, err)
                continue
            sockets.append(transport)
            transport.sendto(probe, (target, probe_port))
        if not sockets:
            raise DiscoveryError("no discovery reply port could be opened")
        await asyncio.sleep(scan_time)
    finally:
        for transport in sockets:
            transport.close()
    return sorted(found.values(), key=lambda board: (board.host, board.board))
