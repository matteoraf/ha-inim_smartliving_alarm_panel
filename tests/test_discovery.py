"""Finding SmartLAN boards (discovery.py) against the fake boards' responders.

Everything runs on 127.0.0.1 with free ports: nothing is broadcast.
"""

from collections.abc import AsyncGenerator
import socket

import pytest

from custom_components.inim_smartliving_alarm.discovery import (
    DiscoveredBoard,
    DiscoveryError,
    async_board_reply,
    discover,
    parse_g_reply,
    parse_si_reply,
)
from custom_components.inim_smartliving_alarm.protocol.masking import Privacy
from custom_components.inim_smartliving_alarm.protocol.smartlan import (
    G_REPLY_FIELDS,
    GReply,
    decode_g_reply,
    decode_si_reply,
    split_g_reply,
)

from .fake_panel import FakePanel
from .fake_panel.discovery import DiscoveryResponder, free_udp_port, si_reply
from .fake_panel.server import TRANSPORT_G

pytestmark = pytest.mark.usefixtures("socket_enabled")

SCAN = 1.0  # time for the fake boards to answer, with slack for slow runners


@pytest.fixture
async def boards(
    socket_enabled: None,
) -> AsyncGenerator[tuple[DiscoveryResponder, DiscoveryResponder]]:
    """A SmartLAN/SI and a SmartLAN/G fake board, each with a responder."""
    si_panel = FakePanel()
    g_panel = FakePanel(transport=TRANSPORT_G)
    await si_panel.start()
    await g_panel.start()
    si = DiscoveryResponder(si_panel, free_udp_port())
    g = DiscoveryResponder(g_panel, free_udp_port(), panel_name="Casa al mare")
    await si.start()
    await g.start()
    yield si, g
    for responder in (si, g):
        await responder.stop()
        await responder.panel.stop()


def ports(si: DiscoveryResponder, g: DiscoveryResponder) -> dict:
    """discover() arguments for the two responders."""
    return {
        "target": "127.0.0.1",
        "si_ports": (si.port, si.reply_port),
        "g_ports": (g.port, g.reply_port),
    }


# ------------------------------------------------------------------ replies


def test_si_reply() -> None:
    """IMIN, MAC and the TCP port in bytes 23-24."""
    board = parse_si_reply(si_reply("192.168.1.93", 5004), "192.168.1.93")
    assert board == DiscoveredBoard(
        "smartlan_si", "192.168.1.93", 5004, mac="00:08:dc:12:34:56"
    )
    assert parse_si_reply(b"IMIN" + bytes(10), "10.0.0.1") is None  # too short
    assert parse_si_reply(b"XXXX" + bytes(186), "10.0.0.1") is None


def test_g_reply() -> None:
    """Fields separated by |, no terminator; err without panel information."""
    board = parse_g_reply(b"6.07|01050|Casa\n|6.12|6004", "192.168.1.50")
    assert board == DiscoveredBoard(
        "smartlan_g",
        "192.168.1.50",
        6004,
        firmware="6.07",
        model="01050",
        panel_name="Casa",
    )
    assert parse_g_reply(b"err", "192.168.1.50") == DiscoveredBoard(
        "smartlan_g", "192.168.1.50", None
    )
    assert parse_g_reply(b"hello", "192.168.1.50") is None


def test_g_reply_fields() -> None:
    """The reply's fields in order; only the panel name is personal."""
    assert split_g_reply(b"6.07|01050|Casa\n|6.12|6004\x00") == [
        "6.07",
        "01050",
        "Casa\n",
        "6.12",
        "6004",
    ]
    assert [name for name, _ in G_REPLY_FIELDS] == [
        "firmware",
        "model",
        "panel_name",
        "board_firmware",
        "port",
    ]
    assert [
        name for name, privacy in G_REPLY_FIELDS if privacy is not Privacy.PUBLIC
    ] == ["panel_name"]
    assert decode_g_reply(b"6.07|01050||6.12|") == GReply(
        "6.07", "01050", None, "6.12", None
    )
    assert decode_g_reply(b"6.07|01050") is None
    assert decode_g_reply(b"err") is None


def test_si_mac_shown() -> None:
    """The last two bytes of the MAC address are not personal."""
    reply = decode_si_reply(si_reply("192.168.1.50", 5004))
    assert reply is not None
    assert reply.mac_shown == reply.mac[-2:]


# ------------------------------------------------------------------- search


async def test_finds_both_boards(boards) -> None:
    """Both board types answer; each gets only its own probe."""
    si, g = boards
    found = await discover(SCAN, **ports(si, g))

    assert {board.board for board in found} == {"smartlan_si", "smartlan_g"}
    si_board = next(board for board in found if board.board == "smartlan_si")
    g_board = next(board for board in found if board.board == "smartlan_g")
    assert (si_board.host, si_board.port) == ("127.0.0.1", si.panel.port)
    assert (g_board.host, g_board.port) == ("127.0.0.1", g.panel.port)
    assert g_board.panel_name == "Casa al mare"
    assert g_board.model == "01050"
    assert si.probes == [b"FIND"]
    assert g.probes == [b"hello_smartlang\x00"]


async def test_nothing_found(socket_enabled: None) -> None:
    """Without boards the search ends empty after the wait."""
    found = await discover(
        SCAN,
        target="127.0.0.1",
        si_ports=(free_udp_port(), free_udp_port()),
        g_ports=(free_udp_port(), free_udp_port()),
    )
    assert found == []


async def test_busy_reply_port(boards) -> None:
    """If one reply port is taken, the other board type is still found."""
    si, g = boards
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("0.0.0.0", si.reply_port))
        found = await discover(SCAN, **ports(si, g))
    assert [board.board for board in found] == ["smartlan_g"]


async def test_no_reply_port(boards) -> None:
    """If neither reply port can be opened, the search fails."""
    si, g = boards
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy_si,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy_g,
    ):
        busy_si.bind(("0.0.0.0", si.reply_port))
        busy_g.bind(("0.0.0.0", g.reply_port))
        with pytest.raises(DiscoveryError):
            await discover(SCAN, **ports(si, g))


# ------------------------------------------------------ one board's reply


async def test_board_reply_of_one_board(boards) -> None:
    """The probe goes to the target (broadcast by default); the reply of the
    entry's own board is kept raw."""
    si, g = boards
    reply = await async_board_reply(
        "127.0.0.1",
        "smartlan_si",
        1.0,
        target="127.0.0.1",
        si_ports=(si.port, si.reply_port),
    )
    assert reply == si_reply(si.panel.host, si.panel.port)
    assert si.probes == [b"FIND"]
    reply = await async_board_reply(
        "127.0.0.1",
        "smartlan_g",
        1.0,
        target="127.0.0.1",
        g_ports=(g.port, g.reply_port),
    )
    assert reply is not None
    assert reply.split(b"|")[2] == b"Casa al mare"


async def test_board_reply_from_another_host_is_ignored(boards) -> None:
    """Another board answering the broadcast is not kept."""
    si, _g = boards
    reply = await async_board_reply(
        "127.0.0.2",
        "smartlan_si",
        0.3,
        target="127.0.0.1",
        si_ports=(si.port, si.reply_port),
    )
    assert reply is None


def test_free_ports_are_distinct() -> None:
    """The helper never hands out a port twice, nor a real reply port."""
    ports = [free_udp_port() for _ in range(50)]
    assert len(set(ports)) == len(ports)
    assert not {5001, 7001} & set(ports)
