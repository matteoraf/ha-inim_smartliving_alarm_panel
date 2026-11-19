"""What a SmartLAN/SI board says about itself when found on the network.

Its reply to FIND (163 bytes, 190 with the status tail of board firmware
5.05) starts with IMIN and the board's MAC address, then the board's stored
configuration. Besides the addresses it holds the dynamic-DNS credentials
and the LAN password when they are set, so it is never logged or stored;
each field's privacy is listed with it.
"""

from dataclasses import dataclass
import ipaddress

from .masking import Fields, Privacy

SI_REPLY_MAGIC = b"IMIN"
SI_REPLY_MIN_LENGTH = 25  # up to the TCP port


@dataclass(frozen=True, slots=True)
class SiField:
    """One field of the reply: where it is and how private."""

    offset: int
    length: int
    privacy: Privacy


# The fields with a known meaning
MAC = SiField(4, 6, Privacy.PUBLIC)  # the last 2 bytes; the rest below
MAC_HIDDEN = SiField(4, 4, Privacy.PERSONAL)
CONFIG_TYPE = SiField(10, 1, Privacy.PUBLIC)
IP = SiField(11, 4, Privacy.PERSONAL)
SUBNET_MASK = SiField(15, 4, Privacy.PERSONAL)
GATEWAY = SiField(19, 4, Privacy.PERSONAL)
TCP_PORT = SiField(23, 2, Privacy.PUBLIC)  # big-endian
FIRMWARE = SiField(44, 2, Privacy.PUBLIC)  # major, minor
DNS = SiField(50, 4, Privacy.PERSONAL)
DDNS_HOST = SiField(54, 32, Privacy.PERSONAL)
DDNS_USER = SiField(90, 32, Privacy.PERSONAL)
DDNS_PASSWORD = SiField(122, 32, Privacy.SECRET)
LAN_PASSWORD_ENABLED = SiField(154, 1, Privacy.PUBLIC)
LAN_PASSWORD = SiField(155, 8, Privacy.SECRET)

# Every private part of the reply, unknown bytes as personal
SI_REPLY_FIELDS: Fields = tuple(
    (f.offset, f.length, f.privacy)
    for f in (
        MAC_HIDDEN,
        IP,
        SUBNET_MASK,
        GATEWAY,
        SiField(25, 19, Privacy.PERSONAL),  # meaning not known
        SiField(46, 4, Privacy.PERSONAL),  # meaning not known
        DNS,
        DDNS_HOST,
        SiField(86, 4, Privacy.PERSONAL),  # meaning not known
        DDNS_USER,
        DDNS_PASSWORD,
        LAN_PASSWORD,
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
