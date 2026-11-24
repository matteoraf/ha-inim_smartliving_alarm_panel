"""Transport for the SmartLAN/SI board: frames travel as plain TCP bytes.

The board accepts one client at a time; the optional board password and
the SecureSI obfuscation are not supported.
"""

import asyncio

from ..protocol.frames import SMARTLAN_SI
from ..protocol.smartlan import SI_PROBE, SI_REPLY_LENGTH, SI_REPLY_TAIL_LENGTH
from .base import PanelTransport, TransportError


class SmartLanSiTransport(PanelTransport):
    """Plain frames over TCP."""

    dialect = SMARTLAN_SI
    busy_hint = " or is in use by another app"

    async def board_reply(self) -> bytes:
        """The board's own reply to FIND, read over TCP: works from another
        subnet too.

        On a connection of its own, closed afterwards. The reply holds the
        board's credentials when they are set: it is never logged or kept
        in the trace, only counted.
        """
        try:
            reply = await self.first_message(
                SI_PROBE, SI_REPLY_LENGTH, more=SI_REPLY_TAIL_LENGTH
            )
        except TransportError:
            self.stats.counters["board_reply_failures"] += 1
            raise
        self.stats.counters["board_replies"] += 1
        return reply

    def _encode(self, data: bytes) -> bytes:
        return data

    async def _read_reply(self, reader: asyncio.StreamReader, length: int) -> bytes:
        # readexactly() waits for every part of a reply sent in pieces
        return await reader.readexactly(length)
