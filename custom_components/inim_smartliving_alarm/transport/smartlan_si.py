"""Transport for the SmartLAN/SI board: frames travel as plain TCP bytes.

The board accepts one client at a time; the optional board password and
the SecureSI obfuscation are not supported.
"""

import asyncio

from ..protocol.frames import SMARTLAN_SI
from .base import PanelTransport


class SmartLanSiTransport(PanelTransport):
    """Plain frames over TCP."""

    dialect = SMARTLAN_SI

    def _encode(self, data: bytes) -> bytes:
        return data

    async def _read_reply(self, reader: asyncio.StreamReader, length: int) -> bytes:
        # readexactly() waits for every part of a reply sent in pieces
        return await reader.readexactly(length)
