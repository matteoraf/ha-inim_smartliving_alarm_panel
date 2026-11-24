"""What a SmartLAN board says about itself when found on the network.

SmartLAN/SI: its reply to FIND (163 bytes, 190 with the status tail of
board firmware 5.05) starts with IMIN and the board's MAC address, then the
board's stored configuration. The board answers FIND as a UDP broadcast on
its own network, and as the first message of a TCP connection from
anywhere. Besides the addresses it holds the dynamic-DNS credentials and
the LAN password when they are set, so it is never logged or stored; each
byte's privacy is listed below, and bytes the board does not use count as
personal.

SmartLAN/G: its reply is the text "firmware|model|panel name|board
firmware|port", or "err" when it has no panel information.
"""

from dataclasses import dataclass
import ipaddress

from .masking import Fields, Privacy

# The two kinds of board, as an entry stores them
BOARD_SMARTLAN_SI = "smartlan_si"
BOARD_SMARTLAN_G = "smartlan_g"

SI_PROBE = b"FIND"
SI_REPLY_MAGIC = b"IMIN"
SI_REPLY_MIN_LENGTH = 25  # up to the TCP port
SI_REPLY_LENGTH = 163
SI_REPLY_TAIL_LENGTH = 27  # board firmware 5.05 only


@dataclass(frozen=True, slots=True)
class SiField:
    """One field of the reply: where it is and how private."""

    offset: int
    length: int
    privacy: Privacy


# Every byte of the reply, in order (the magic IMIN is bytes 0-3, public)
MAC = SiField(4, 6, Privacy.PUBLIC)  # the last 2 bytes; the rest below
MAC_HIDDEN = SiField(4, 4, Privacy.PERSONAL)
CONFIG_TYPE = SiField(10, 1, Privacy.PERSONAL)  # not used by the board
IP = SiField(11, 4, Privacy.PERSONAL)
SUBNET_MASK = SiField(15, 4, Privacy.PERSONAL)
GATEWAY = SiField(19, 4, Privacy.PERSONAL)
TCP_PORT = SiField(23, 2, Privacy.PUBLIC)  # big-endian
UNKNOWN_25 = SiField(25, 17, Privacy.PERSONAL)  # not used by the board
CLOUD_INTERVAL = SiField(42, 1, Privacy.PUBLIC)  # a timer of the cloud link
UNKNOWN_43 = SiField(43, 1, Privacy.PERSONAL)  # not used by the board
FIRMWARE = SiField(44, 2, Privacy.PUBLIC)  # major, minor
CLOUD_ENABLE = SiField(46, 1, Privacy.PUBLIC)  # turns the cloud link on
CLOUD_GATE = SiField(47, 1, Privacy.PUBLIC)  # with byte 49, enables the cloud timer
UNKNOWN_48 = SiField(48, 1, Privacy.PERSONAL)  # not used by the board
NETWORK_CONFIGURED = SiField(49, 1, Privacy.PUBLIC)  # set when a DHCP lease applies
DNS = SiField(50, 4, Privacy.PERSONAL)
DDNS_HOST = SiField(54, 32, Privacy.PERSONAL)
UNKNOWN_86 = SiField(86, 4, Privacy.PERSONAL)  # not used by the board
DDNS_USER = SiField(90, 32, Privacy.SECRET)  # with the host, an account
DDNS_PASSWORD = SiField(122, 32, Privacy.SECRET)
LAN_PASSWORD_ENABLED = SiField(154, 1, Privacy.PUBLIC)
LAN_PASSWORD = SiField(155, 8, Privacy.SECRET)
# Board firmware 5.05: a status block (version text, socket states)
STATUS_TAIL = SiField(SI_REPLY_LENGTH, SI_REPLY_TAIL_LENGTH, Privacy.PERSONAL)

# Every private part of the reply; the bytes not listed are public
SI_REPLY_FIELDS: Fields = tuple(
    (f.offset, f.length, f.privacy)
    for f in (
        MAC_HIDDEN,
        CONFIG_TYPE,
        IP,
        SUBNET_MASK,
        GATEWAY,
        UNKNOWN_25,
        UNKNOWN_43,
        UNKNOWN_48,
        DNS,
        DDNS_HOST,
        UNKNOWN_86,
        DDNS_USER,
        DDNS_PASSWORD,
        LAN_PASSWORD,
        STATUS_TAIL,
    )
)


@dataclass(frozen=True, slots=True)
class SiReply:
    """The decoded reply; fields past the end of a short reply are None."""

    mac: bytes
    config_type: int
    ip: str
    subnet_mask: str
    gateway: str
    port: int
    firmware: str | None = None
    dns: str | None = None
    ddns_host: str | None = None
    ddns_user: str | None = None
    ddns_password: str | None = None
    lan_password_enabled: bool | None = None
    lan_password: str | None = None

    @property
    def mac_shown(self) -> bytes:
        """The MAC address bytes that are not personal."""
        return self.mac[MAC_HIDDEN.offset + MAC_HIDDEN.length - MAC.offset :]


def _raw(data: bytes, field: SiField) -> bytes | None:
    end = field.offset + field.length
    return data[field.offset : end] if len(data) >= end else None


def _address(data: bytes, field: SiField) -> str | None:
    raw = _raw(data, field)
    return None if raw is None else str(ipaddress.IPv4Address(raw))


def _text(data: bytes, field: SiField) -> str | None:
    raw = _raw(data, field)
    return None if raw is None else raw.split(b"\0")[0].decode("ascii", "replace")


def decode_si_reply(data: bytes) -> SiReply | None:
    """A FIND reply, or None if it is not one."""
    if len(data) < SI_REPLY_MIN_LENGTH or not data.startswith(SI_REPLY_MAGIC):
        return None
    firmware = _raw(data, FIRMWARE)
    enabled = _raw(data, LAN_PASSWORD_ENABLED)
    return SiReply(
        mac=data[MAC.offset : MAC.offset + MAC.length],
        config_type=data[CONFIG_TYPE.offset],
        ip=_address(data, IP) or "",
        subnet_mask=_address(data, SUBNET_MASK) or "",
        gateway=_address(data, GATEWAY) or "",
        port=int.from_bytes(_raw(data, TCP_PORT) or b"", "big"),
        firmware=None if firmware is None else f"{firmware[0]}.{firmware[1]:02d}",
        dns=_address(data, DNS),
        ddns_host=_text(data, DDNS_HOST),
        ddns_user=_text(data, DDNS_USER),
        ddns_password=_text(data, DDNS_PASSWORD),
        lan_password_enabled=None if enabled is None else bool(enabled[0]),
        lan_password=_text(data, LAN_PASSWORD),
    )


# ------------------------------------------------------------------ SmartLAN/G

G_REPLY_ERROR = "err"  # the board has no panel information
# The reply's fields in order, each with its privacy
G_REPLY_FIELDS: tuple[tuple[str, Privacy], ...] = (
    ("firmware", Privacy.PUBLIC),
    ("model", Privacy.PUBLIC),
    ("panel_name", Privacy.PERSONAL),
    ("board_firmware", Privacy.PUBLIC),
    ("port", Privacy.PUBLIC),
)


@dataclass(frozen=True, slots=True)
class GReply:
    """The decoded reply; empty fields are None."""

    firmware: str | None
    model: str | None
    panel_name: str | None
    board_firmware: str | None
    port: int | None


def split_g_reply(data: bytes) -> list[str]:
    """The reply's text fields, as sent."""
    return data.decode("ascii", errors="replace").strip("\x00\r\n ").split("|")


def decode_g_reply(data: bytes) -> GReply | None:
    """A reply with every field, or None if it is not one (or "err")."""
    parts = split_g_reply(data)
    if len(parts) < len(G_REPLY_FIELDS):
        return None
    firmware, model, name, board_firmware, port = (
        part.strip() for part in parts[: len(G_REPLY_FIELDS)]
    )
    return GReply(
        firmware=firmware or None,
        model=model or None,
        panel_name=name or None,
        board_firmware=board_firmware or None,
        port=int(port) if port.isdigit() else None,
    )
