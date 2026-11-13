"""TCP server emulating a SmartLiving 1050 behind SmartLAN/SI or SmartLAN/G."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import const as c
from .state import PanelState, checksum, panel_1050

TRANSPORT_SI = "smartlan_si"
TRANSPORT_G = "smartlan_g"

SPACE_RAM = "ram"
SPACE_EEPROM = "eeprom"
SPACE_FLASH = "flash"
SPACE_PERIPHERAL = "peripheral"


@dataclass(frozen=True)
class Request:
    """A request received by the fake panel, after decoding."""

    raw: bytes  # 8-byte header as received (decrypted for SmartLAN/G)
    write: bool
    space: str
    device: int  # peripheral class (1 = reader, 2 = keypad, ...) or 0
    instance: int  # NUM_DEV byte: bank, peripheral index or extra parameter
    address: int  # 24-bit for EEPROM/flash, 16-bit otherwise
    length: int  # 16-bit LEN field
    payload: bytes = b""


def smartlan_g_cipher(username: str, password: str) -> Cipher:
    """AES-128-CBC with key = MD5(user + password) and iv = key XOR 00..0F."""
    key = hashlib.md5((username + password).encode("utf-8")).digest()
    iv = bytes(k ^ i for i, k in enumerate(key))
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def smartlan_g_encrypt(cipher: Cipher, data: bytes) -> bytes:
    """Encrypt a whole message (PKCS#7 padded)."""
    padder = padding.PKCS7(128).padder()
    encryptor = cipher.encryptor()
    return (
        encryptor.update(padder.update(data) + padder.finalize()) + encryptor.finalize()
    )


def smartlan_g_decrypt(cipher: Cipher, data: bytes) -> bytes:
    """Decrypt a whole message and remove the PKCS#7 padding."""
    decryptor = cipher.decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    plain = decryptor.update(data) + decryptor.finalize()
    return unpadder.update(plain) + unpadder.finalize()


class FakePanel:
    """A fake SmartLiving 1050 listening on a local TCP port."""

    def __init__(
        self,
        state: PanelState | None = None,
        *,
        transport: str = TRANSPORT_SI,
        username: str = "",
        password: str = "",
        idle_timeout: float | None = None,
        single_client: bool = True,
    ) -> None:
        """Create the panel. Call start() to begin listening."""
        self.state = state or panel_1050()
        self.transport = transport
        self.idle_timeout = idle_timeout
        self.single_client = single_client
        self._cipher = smartlan_g_cipher(username, password)

        self.requests: list[Request] = []
        self.protocol_errors: list[str] = []
        self.connections = 0
        self.rejected_connections = 0

        # Fault injection, changeable while the panel runs
        self.fragment_replies = False
        self.reply_delay = 0.0
        self.drop_after_requests: int | None = None
        self.corrupt_replies = 0

        self._server: asyncio.Server | None = None
        self._client: asyncio.StreamWriter | None = None

    # --------------------------------------------------------------- lifecycle

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Start listening; port 0 picks a free port."""
        self._server = await asyncio.start_server(self._handle_client, host, port)

    async def stop(self) -> None:
        """Close the client connection and stop listening."""
        if self._client is not None:
            self._client.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def host(self) -> str:
        """Address the panel listens on."""
        assert self._server is not None
        return self._server.sockets[0].getsockname()[0]

    @property
    def port(self) -> int:
        """Port the panel listens on."""
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------- connection

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.single_client and self._client is not None:
            self.rejected_connections += 1
            writer.close()
            return
        self._client = writer
        self.connections += 1
        handled = 0
        try:
            while True:
                request = await self._read_request(reader)
                if request is None:
                    continue
                handled += 1
                reply = self._dispatch(request)
                await self._send(writer, reply)
                if (
                    self.drop_after_requests is not None
                    and handled >= self.drop_after_requests
                ):
                    break
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            pass
        finally:
            if self._client is writer:
                self._client = None
            writer.close()

    async def _read(self, reader: asyncio.StreamReader, count: int) -> bytes:
        if self.idle_timeout is None:
            return await reader.readexactly(count)
        async with asyncio.timeout(self.idle_timeout):
            return await reader.readexactly(count)

    async def _read_request(self, reader: asyncio.StreamReader) -> Request | None:
        if self.transport == TRANSPORT_G:
            first = await self._read(reader, 16)
            header = self._cipher.decryptor().update(first)[:8]
            rest = b""
            if header[0] in (c.OP_WRITE, c.OP_PERIPHERAL_WRITE):
                total = (8 + header[6]) // 16 * 16 + 16
                rest = await self._read(reader, total - 16) if total > 16 else b""
            try:
                plain = smartlan_g_decrypt(self._cipher, first + rest)
            except ValueError:
                self.protocol_errors.append(
                    f"cannot decrypt request: {(first + rest).hex()}"
                )
                return None
            header, payload = plain[:8], plain[8:]
        else:
            header = await self._read(reader, 8)
            payload = b""
            if header[0] in (c.OP_WRITE, c.OP_PERIPHERAL_WRITE):
                payload = await self._read(reader, header[6])

        if checksum(header[:7]) != header[7]:
            self.protocol_errors.append(f"bad header checksum: {header.hex()}")
            return None
        request = self._decode(header, payload)
        self.requests.append(request)
        if (
            self.transport == TRANSPORT_SI
            and not request.write
            and request.space in (SPACE_EEPROM, SPACE_FLASH)
            and request.length > c.MAX_READ_LENGTH
        ):
            self.protocol_errors.append(f"read longer than 250 bytes: {header.hex()}")
        return request

    def _decode(self, header: bytes, payload: bytes) -> Request:
        op, num_dev, device = header[0], header[1], header[2]
        address16 = header[3] << 8 | header[4]
        length = header[5] << 8 | header[6]
        write = op in (c.OP_WRITE, c.OP_PERIPHERAL_WRITE)
        peripheral_op = op in (c.OP_PERIPHERAL_READ, c.OP_PERIPHERAL_WRITE)

        if self.transport == TRANSPORT_SI:
            if peripheral_op:
                space, peripheral = SPACE_PERIPHERAL, device + 1
            else:
                space = {
                    c.DEVICE_RAM: SPACE_RAM,
                    c.DEVICE_EEPROM: SPACE_EEPROM,
                    c.DEVICE_FLASH: SPACE_FLASH,
                }.get(device, SPACE_RAM)
                peripheral = 0
        else:
            peripheral = 0
            if device == 0:
                space = SPACE_RAM
            elif device in (c.LOGICAL_EEPROM, c.LOGICAL_EEPROM_NO_PROG):
                space = SPACE_EEPROM
            elif device == c.LOGICAL_FLASH:
                space = SPACE_FLASH
            else:
                space, peripheral = SPACE_PERIPHERAL, device

        address = address16
        if space in (SPACE_EEPROM, SPACE_FLASH):
            address = num_dev << 16 | address16
        return Request(
            header, write, space, peripheral, num_dev, address, length, payload
        )

    async def _send(self, writer: asyncio.StreamWriter, reply: bytes) -> None:
        if self.corrupt_replies and reply:
            self.corrupt_replies -= 1
            reply = reply[:-1] + bytes(((reply[-1] + 1) & 0xFF,))
        if self.transport == TRANSPORT_G:
            reply = smartlan_g_encrypt(self._cipher, reply)
        if self.reply_delay:
            await asyncio.sleep(self.reply_delay)
        if self.fragment_replies:
            for byte in reply:
                writer.write(bytes((byte,)))
                await writer.drain()
                await asyncio.sleep(0)
        else:
            writer.write(reply)
            await writer.drain()

    # --------------------------------------------------------------- protocol

    def _dispatch(self, request: Request) -> bytes:
        if request.write:
            return self._handle_write(request)
        data = self._handle_read(request)
        return data + bytes((checksum(data),))

    def _handle_read(self, request: Request) -> bytes:
        state = self.state
        if request.space == SPACE_RAM:
            address = request.address
            if address == c.RAM_ACTIVE_SCENARIO:
                mask = request.instance << 8 | request.length >> 8
                return bytes((state.active_scenario_for(mask),))
            if address == c.RAM_NOT_READY:
                return state.not_ready_zones(request.length >> 8, request.length & 0xFF)
            if address == c.RAM_OUTPUT_DETAIL:
                return state.output_detail(request.instance, request.length & 0xFF)
            if address == c.RAM_EVENT_TEXT:
                return state.event_text(request.length)
            if address == c.RAM_EVENT_POINTERS:
                return state.event_pointers()[: request.length]
            return state.ram_read(address, request.length)
        if request.space == SPACE_EEPROM:
            return state.eeprom_read(request.address, request.length)
        if (
            request.space == SPACE_PERIPHERAL
            and request.device == 2
            and request.address == c.KEYPAD_TEMPERATURE
        ):
            return state.keypad_temperature(request.instance)
        return bytes(request.length)

    def _handle_write(self, request: Request) -> bytes:
        handlers = {
            c.RAM_CMD_ARM: self.state.execute_arm,
            c.RAM_CMD_OUTPUT: self.state.execute_output,
            c.RAM_CMD_RESET: self.state.execute_reset,
            c.RAM_CMD_BYPASS: self.state.execute_bypass,
            c.RAM_CMD_SCENARIO: self.state.execute_scenario,
            c.RAM_CMD_STOP: self.state.execute_stop,
        }
        if request.space == SPACE_RAM and request.address in handlers:
            self.state.command_result = handlers[request.address](request.payload)
        return bytes((checksum(request.payload),))
