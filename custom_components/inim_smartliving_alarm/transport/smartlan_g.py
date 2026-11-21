"""Transport for the SmartLAN/G board: every message is AES encrypted.

AES-128-CBC with PKCS#7 padding; the key is MD5(user name + password) of
the board's web credentials and the IV is the key XOR 00..0F. Each message
is encrypted on its own with the same key and IV. A read request is one
block, a write is header and payload together. A read reply is the data
alone: the board checks the panel's checksum on its serial side and
encrypts only the n data bytes, which take (n // 16 + 1) blocks. A write
acknowledgement is the panel's 1-byte ack in one block.

The board never closes an idle connection; the shared lifecycle (replace an
idle connection, pause before reconnecting) is kept anyway, as it costs
little. EEPROM requests use the board's own device code and reads may be
up to 2000 bytes.

Experimental: tested against the fake panel only, not on a real board.
Frames are logged before encryption (PIN masked); the user name, the
password and the key are never logged.
"""

import asyncio
import hashlib

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..protocol.errors import DecryptionError
from ..protocol.frames import SMARTLAN_G
from .base import PanelTransport

BLOCK_SIZE = 16


def smartlan_g_cipher(username: str, password: str) -> Cipher:
    """AES-128-CBC with key = MD5(user name + password), iv = key XOR 00..0F."""
    # MD5 is fixed by the protocol, not a choice made here
    key = hashlib.md5(
        (username + password).encode("utf-8"), usedforsecurity=False
    ).digest()
    iv = bytes(byte ^ index for index, byte in enumerate(key))
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def encrypted_length(length: int) -> int:
    """Bytes on the wire for a message of length plain bytes."""
    return (length // BLOCK_SIZE + 1) * BLOCK_SIZE


class SmartLanGTransport(PanelTransport):
    """Frames encrypted with the board's credentials."""

    dialect = SMARTLAN_G
    reply_checksum = False

    def __init__(
        self,
        host: str,
        port: int,
        *,
        username: str,
        password: str,
        **options: float,
    ) -> None:
        """Set up the transport; options are those of PanelTransport."""
        super().__init__(host, port, **options)
        self._cipher = smartlan_g_cipher(username, password)

    def _encode(self, data: bytes) -> bytes:
        padder = padding.PKCS7(BLOCK_SIZE * 8).padder()
        encryptor = self._cipher.encryptor()
        plain = padder.update(data) + padder.finalize()
        return encryptor.update(plain) + encryptor.finalize()

    async def _read_reply(self, reader: asyncio.StreamReader, length: int) -> bytes:
        # readexactly() waits for every part of a reply sent in pieces
        encrypted = await reader.readexactly(encrypted_length(length))
        decryptor = self._cipher.decryptor()
        unpadder = padding.PKCS7(BLOCK_SIZE * 8).unpadder()
        plain = decryptor.update(encrypted) + decryptor.finalize()
        try:
            return unpadder.update(plain) + unpadder.finalize()
        except ValueError as err:
            raise DecryptionError(
                "reply cannot be decrypted (wrong user name or password?)"
            ) from err
