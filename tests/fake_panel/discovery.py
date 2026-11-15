"""UDP discovery responder for the fake panel, like the SmartLAN boards.

SmartLAN/SI: answers FIND with the IMIN reply (MAC, then the board's
configuration: IP, mask, gateway, TCP port); 163 bytes from UDP port 1460
on a real board (checked on the owner's board). SmartLAN/G: answers
hello_smartlang with "firmware|model|panel name|board firmware|port".

Tests run it on 127.0.0.1 with free ports: it listens on the probe port and
sends the reply to the sender's address on the reply port.
"""

from __future__ import annotations

import asyncio
import ipaddress

from .server import TRANSPORT_G, FakePanel

SI_REPLY_LENGTH = 163  # as a real SmartLAN/SI board sends over UDP
FAKE_MAC = bytes.fromhex("0008dc123456")
BOARD_FIRMWARE = "6.12"


def si_reply(ip: str, port: int) -> bytes:
    """The SmartLAN/SI FIND reply for a board at ip:port."""
    data = bytearray(SI_REPLY_LENGTH)
    data[0:4] = b"IMIN"
    data[4:10] = FAKE_MAC
    data[10] = 2  # configuration type (not decoded); 2 on a real board
    data[11:15] = ipaddress.IPv4Address(ip).packed
    data[15:19] = ipaddress.IPv4Address("255.255.255.0").packed
    data[19:23] = ipaddress.IPv4Address("192.168.1.1").packed
    data[23:25] = port.to_bytes(2, "big")
    return bytes(data)


def g_reply(panel: FakePanel, panel_name: str) -> bytes:
    """The SmartLAN/G reply: panel firmware and model from its model string."""
    firmware, model = panel.state.firmware.split()[:2]
    return f"{firmware}|{model}|{panel_name}|{BOARD_FIRMWARE}|{panel.port}".encode()


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, owner: DiscoveryResponder) -> None:
        self._owner = owner
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        owner = self._owner
        owner.probes.append(data)
        reply = owner.reply_for(data)
        if reply is not None and self.transport is not None:
            self.transport.sendto(reply, (addr[0], owner.reply_port))


class DiscoveryResponder:
    """Answers discovery probes for a fake panel."""

    def __init__(
        self, panel: FakePanel, reply_port: int, *, panel_name: str = "Casa"
    ) -> None:
        """Answer for panel, sending replies to reply_port."""
        self.panel = panel
        self.reply_port = reply_port
        self.panel_name = panel_name
        self.no_panel_info = False  # SmartLAN/G: answer "err"
        self.probes: list[bytes] = []
        self._transport: asyncio.DatagramTransport | None = None

    def reply_for(self, probe: bytes) -> bytes | None:
        """The reply to a probe, or None if the board would not answer."""
        if self.panel.transport == TRANSPORT_G:
            if probe.rstrip(b"\x00") != b"hello_smartlang":
                return None
            if self.no_panel_info:
                return b"err"
            return g_reply(self.panel, self.panel_name)
        if probe != b"FIND":
            return None
        return si_reply(self.panel.host, self.panel.port)

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Listen for probes; port 0 picks a free port."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Responder(self), local_addr=(host, port)
        )

    @property
    def port(self) -> int:
        """Port the responder listens on (the probe port)."""
        assert self._transport is not None
        return self._transport.get_extra_info("sockname")[1]

    async def stop(self) -> None:
        """Stop listening."""
        if self._transport is not None:
            self._transport.close()
