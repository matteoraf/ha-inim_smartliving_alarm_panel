"""The SmartLAN/SI board's own reply read over TCP, its masking in a system
report, and the installer code in a Full confidential report."""

import asyncio
import io
import json
import logging
from pathlib import Path
import sys
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm import diagnostics
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
    DATA_PANEL,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.protocol.commands import decode_pin
from custom_components.inim_smartliving_alarm.protocol.masking import (
    Level,
    Privacy,
    mask,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW6,
    SMARTLIVING_515_FW51,
    SMARTLIVING_1050_FW6,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
    SMARTLIVING_10100_FW51,
    PanelModel,
)
from custom_components.inim_smartliving_alarm.protocol.report_blocks import TABLES
from custom_components.inim_smartliving_alarm.protocol.smartlan import (
    SI_REPLY_FIELDS,
    SI_REPLY_LENGTH,
    SI_REPLY_TAIL_LENGTH,
)
from custom_components.inim_smartliving_alarm.system_report import (
    EntryFacts,
    ReportInput,
    build_report,
)
from custom_components.inim_smartliving_alarm.transport import base as transport_base
from custom_components.inim_smartliving_alarm.transport.base import (
    ConnectError,
    ConnectionLost,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import FakePanel, panel_1050
from .fake_panel.discovery import si_reply
from .fake_panel.state import panel_1050_fw51, panel_10100
from .test_slow_polling import ignoring_listener  # noqa: F401 - the fixture
from .test_system_report import (  # noqa: F401 - make_report is the fixture
    DDNS_USER,
    PIN,
    make_report,
    si_reply_with_credentials,
    text_of,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from report_viewer import raw  # noqa: E402

pytestmark = pytest.mark.usefixtures("socket_enabled")

INSTALLER_PIN = "736451"  # distinctive: no other number in a report holds it
INSTALLER_PIN_HEX = bytes(int(d) for d in INSTALLER_PIN).hex(" ")
MARKER = b"reply-marker-xyz"


@pytest.fixture
async def fake() -> FakePanel:
    panel = FakePanel()
    await panel.start()
    yield panel
    await panel.stop()


def transport_for(fake: FakePanel) -> SmartLanSiTransport:
    return SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01)


async def gone(fake: FakePanel) -> bool:
    for _ in range(100):
        if not fake.has_client:
            return True
        await asyncio.sleep(0.01)
    return False


# ----------------------------------------------------------------- protocol


def test_every_reply_byte_has_its_class() -> None:
    """Public: only what is known not to identify; the credentials secret;
    everything else (bytes the board does not use too) personal."""
    privacy = dict.fromkeys(
        range(SI_REPLY_LENGTH + SI_REPLY_TAIL_LENGTH), Privacy.PUBLIC
    )
    covered: set[int] = set()
    for offset, length, kind in SI_REPLY_FIELDS:
        span = set(range(offset, offset + length))
        assert not span & covered, offset  # fields do not overlap
        covered |= span
        for byte in span:
            privacy[byte] = kind
    public = {b for b, p in privacy.items() if p is Privacy.PUBLIC}
    secret = {b for b, p in privacy.items() if p is Privacy.SECRET}
    assert public == {0, 1, 2, 3, 8, 9, 23, 24, 42, 44, 45, 46, 47, 49, 154}
    assert secret == set(range(90, 154)) | set(range(155, 163))
    assert {10, 43, 48, *range(25, 42), *range(86, 90)} <= covered
    assert all(privacy[b] is Privacy.PERSONAL for b in range(163, 190))


def test_tail_masked_below_full() -> None:
    reply = si_reply("192.0.2.5", 5004) + bytes((0xAA,)) + b"5.05" * 6 + bytes(2)
    masked, ranges = mask(reply, SI_REPLY_FIELDS, Level.REDACTED)
    assert (SI_REPLY_LENGTH, SI_REPLY_TAIL_LENGTH) in {
        (r.offset, r.length) for r in ranges
    }
    assert masked[SI_REPLY_LENGTH:] == bytes(SI_REPLY_TAIL_LENGTH)
    shown, ranges = mask(reply, SI_REPLY_FIELDS, Level.FULL)
    assert all(r.offset < SI_REPLY_LENGTH for r in ranges)
    assert shown[SI_REPLY_LENGTH:] == reply[SI_REPLY_LENGTH:]


@pytest.mark.parametrize(
    ("model", "family", "profile", "user_codes"),
    [
        ("505", "6", SMARTLIVING_505_FW6, 30),
        ("505", "5.1", SMARTLIVING_505_FW51, 30),
        ("515", "6", SMARTLIVING_515_FW6, 30),
        ("515", "5.1", SMARTLIVING_515_FW51, 30),
        ("1050", "6", SMARTLIVING_1050_FW6, 50),
        ("1050", "5.1", SMARTLIVING_1050_FW51, 50),
        ("10100", "6", SMARTLIVING_10100_FW6, 100),
        ("10100", "5.1", SMARTLIVING_10100_FW51, 100),
    ],
)
def test_installer_code_entry(
    model: str, family: str, profile: PanelModel, user_codes: int
) -> None:
    """The entry after the user codes, inside the report's PIN-table block;
    the PIN lookup still reads the user codes only."""
    assert profile.codes == user_codes
    assert profile.installer_code_index == user_codes
    block = TABLES[(model, family)].block("pin_table")
    assert block is not None
    assert block.address == profile.pin_table
    assert block.length >= (user_codes + 1) * profile.pin_length
    assert profile.pin_table_length == user_codes * profile.pin_length


@pytest.mark.parametrize(
    ("entry", "pin"),
    [
        (bytes((2, 4, 6, 8, 0xFF, 0xFF)), "2468"),
        (bytes((1, 2, 3, 4, 5, 6)), "123456"),
        (bytes((0, 0, 0, 0, 0xFF, 0xFF)), "0000"),
        (b"\xff" * 6, None),
        (bytes((1, 0x0A, 3, 4, 0xFF, 0xFF)), None),
    ],
)
def test_decode_pin(entry: bytes, pin: str | None) -> None:
    assert decode_pin(entry) == pin


# ---------------------------------------------------------------- transport


async def test_board_reply_over_tcp(
    fake: FakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIND as the first message of a connection of its own, closed after;
    the next read connects again; nothing of it is in the trace."""
    panel = SmartLivingPanel(
        transport_for(fake), PIN, fallback_model=SMARTLIVING_1050_FW6
    )
    await panel.identify()
    trace = len(panel.transport.stats.trace)
    writers = []
    connect = asyncio.open_connection

    async def recorded(*args, **kwargs):
        reader, writer = await connect(*args, **kwargs)
        writers.append(writer)
        return reader, writer

    monkeypatch.setattr(transport_base.asyncio, "open_connection", recorded)
    reply = await panel.transport.board_reply()
    monkeypatch.undo()
    (writer,) = writers
    assert writer.is_closing()  # closed by the transport, not left behind
    assert reply == si_reply(fake.host, fake.port)
    assert fake.find_requests == 1
    assert not panel.transport.connected
    assert await gone(fake)
    assert len(panel.transport.stats.trace) == trace
    assert panel.transport.stats.counters["board_replies"] == 1
    await panel.read_zones()  # connects again
    assert panel.transport.connected
    await panel.close()


async def test_board_reply_with_the_status_tail(fake: FakePanel) -> None:
    fake.find_reply = si_reply(fake.host, fake.port) + bytes((0xAA,)) + bytes(26)
    reply = await transport_for(fake).board_reply()
    assert len(reply) == SI_REPLY_LENGTH + SI_REPLY_TAIL_LENGTH


async def test_board_reply_is_not_logged(
    fake: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """At debug level only the reply's length."""
    reply = bytearray(si_reply(fake.host, fake.port))
    reply[90 : 90 + len(MARKER)] = MARKER
    fake.find_reply = bytes(reply)
    with caplog.at_level(logging.DEBUG, "custom_components.inim_smartliving_alarm"):
        await transport_for(fake).board_reply()
    assert f"{SI_REPLY_LENGTH} bytes, not logged" in caplog.text
    assert MARKER.decode() not in caplog.text
    assert MARKER.hex() not in caplog.text.replace(" ", "")
    assert "FIND" not in caplog.text


async def test_board_reply_unanswered(fake: FakePanel) -> None:
    """A board that drops the request: an error for the transport, no reply
    for the panel."""
    fake.answer_find = False
    transport = transport_for(fake)
    with pytest.raises(ConnectionLost):
        await transport.board_reply()
    assert transport.stats.counters["board_reply_failures"] == 1
    assert await SmartLivingPanel(transport, PIN).board_reply() is None


async def test_board_reply_not_a_find_reply(fake: FakePanel) -> None:
    fake.find_reply = b"XXXX" + bytes(SI_REPLY_LENGTH - 4)
    assert await SmartLivingPanel(transport_for(fake), PIN).board_reply() is None


async def test_board_reply_one_brief_attempt(
    ignoring_listener: tuple[str, int],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board in use ignores the connection: one attempt, cut at the brief
    timeout, also outside slow polling mode."""
    monkeypatch.setattr(transport_base, "BRIEF_CONNECT_TIMEOUT", 0.3)
    host, port = ignoring_listener
    transport = SmartLanSiTransport(host, port, reconnect_pause=0.0)
    assert not transport.shared
    started = time.monotonic()
    with pytest.raises(ConnectError, match="in use by another app"):
        await transport.board_reply()
    assert time.monotonic() - started < 1.5


async def test_no_board_reply_for_smartlan_g() -> None:
    """The SmartLAN/G board has no such reply: nothing is sent."""
    panel = SmartLivingPanel(
        SmartLanGTransport("192.0.2.1", 5004, username="u", password="p"), PIN
    )
    assert await panel.board_reply() is None


# ------------------------------------------------------------------- report


async def test_report_reads_the_board_over_tcp(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TCP first; the broadcast search only when TCP gives nothing."""
    searched = []

    async def broadcast(host: str, board: str, *args, **kwargs) -> bytes:
        searched.append(board)
        return si_reply(panel.host, panel.port)

    monkeypatch.setattr(diagnostics, "async_board_reply", broadcast)
    data = await diagnostics._async_report_input(hass, entry, "r-1", Level.REDACTED)
    assert data.board_reply_source == "tcp"
    assert searched == []
    assert build_report(data)["board"]["source"] == "tcp"

    panel.answer_find = False
    data = await diagnostics._async_report_input(hass, entry, "r-2", Level.REDACTED)
    assert data.board_reply_source == "udp"
    assert searched == [BOARD_SMARTLAN_SI]
    # The entry's own connection works on after the report
    loaded: SmartLivingPanel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
    await loaded.read_zones()


def _facts(board: str) -> EntryFacts:
    return EntryFacts(
        title="Home",
        entry_version="2.2",
        integration_version="0.4.0",
        board=board,
        host="192.168.77.93",
        port=5004,
        options={},
        stored_config={},
        pin=PIN,
    )


@pytest.mark.parametrize(
    ("board", "note"),
    [
        (BOARD_SMARTLAN_SI, "the board can't be reached or is in use by another app"),
        (BOARD_SMARTLAN_G, "Home Assistant may not be on the board's network"),
    ],
)
def test_no_reply_note_per_board(board: str, note: str) -> None:
    report = build_report(
        ReportInput(
            report_id="r",
            created="2026-11-23T10:00:00+01:00",
            level=Level.REDACTED,
            entry=_facts(board),
            reads=None,
        )
    )
    assert report["board"]["source"] is None
    assert report["board"]["reply_note"] == f"no reply ({note})"


@pytest.mark.parametrize(
    ("level", "type_shown", "user_shown"),
    [
        (Level.REDACTED, False, False),
        (Level.FULL, True, False),
        (Level.CONFIDENTIAL, True, True),
    ],
)
def test_reply_fields_by_level(
    level: Level, type_shown: bool, user_shown: bool
) -> None:
    """The type byte from Full; the dynamic-DNS user name only at Full
    confidential, like the passwords."""
    report = build_report(
        ReportInput(
            report_id="r",
            created="2026-11-23T10:00:00+01:00",
            level=level,
            entry=_facts(BOARD_SMARTLAN_SI),
            reads=None,
            board_reply=si_reply_with_credentials("192.168.77.93", 5004),
            board_reply_source="tcp",
        )
    )
    fields = report["board"]["reply"]["fields"]
    assert ("type" in fields) is type_shown
    assert ("dynamic_dns_user" in fields) is user_shown
    assert (DDNS_USER.decode() in text_of(report)) is user_shown
    assert report["board"]["source"] == "tcp"


@pytest.mark.parametrize(
    ("state", "entry"),
    [(panel_1050, 51), (panel_1050_fw51, 51), (panel_10100, 101)],
)
async def test_installer_code_at_full_confidential(
    make_report,  # noqa: F811
    state,
    entry: int,
) -> None:
    """Digits and entry at Full confidential; the PIN table says which
    entry it is."""
    panel_state = state()
    panel_state.installer_pin = INSTALLER_PIN
    report, _ = await make_report(Level.CONFIDENTIAL, panel_state)
    assert report["panel"]["installer_code"] == {"entry": entry, "pin": INSTALLER_PIN}
    blocks = {b["name"]: b for b in report["blocks"]}
    assert blocks["pin_table"]["entries"] == {"installer_code": entry - 1}


@pytest.mark.parametrize("level", [Level.REDACTED, Level.FULL])
async def test_no_installer_code_below_full_confidential(
    make_report,  # noqa: F811
    level: Level,
) -> None:
    state = panel_1050()
    state.installer_pin = INSTALLER_PIN
    report, _ = await make_report(level, state)
    assert "installer_code" not in report["panel"]
    blocks = {b["name"]: b for b in report["blocks"]}
    assert "entries" not in blocks["pin_table"]
    panel_text = json.dumps(report["panel"])
    rows = json.dumps([b["rows"] for b in report["blocks"]])
    for text in (panel_text, rows):
        assert INSTALLER_PIN not in text
        assert INSTALLER_PIN_HEX not in text


async def test_installer_code_empty(make_report) -> None:  # noqa: F811
    report, _ = await make_report(Level.CONFIDENTIAL, panel_1050())
    assert report["panel"]["installer_code"] == {"entry": 51, "pin": None}


# ------------------------------------------------------------------- viewer


async def test_viewer_shows_the_installer_code(
    make_report,  # noqa: F811
    tmp_path: Path,
) -> None:
    from report_viewer import cli  # noqa: PLC0415 - after the path is set

    state = panel_1050()
    state.installer_pin = INSTALLER_PIN
    report, _ = await make_report(Level.CONFIDENTIAL, state)
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"home_assistant": {}, "data": report}))
    out = io.StringIO()
    assert cli.main(["show", str(path), "--no-color"], out=out) == 0
    assert f"{INSTALLER_PIN} (PIN table entry 51)" in out.getvalue()
    (pin_table,) = [b for b in raw.raw_blocks(report) if b.name == "pin_table"]
    assert pin_table.fields[50] == f"installer code: {INSTALLER_PIN}"
    assert pin_table.fields[51].startswith("entry 52 (not a user code): ")
    assert pin_table.fields[0] == f"code 1: {PIN}"

    full, _ = await make_report(Level.FULL, state)
    path.write_text(json.dumps({"home_assistant": {}, "data": full}))
    out = io.StringIO()
    cli.main(["show", str(path), "--no-color"], out=out)
    assert "Installer code" not in out.getvalue()


# ---------------------------------------------------------- review follow-ups


@pytest.mark.parametrize(
    ("state", "firmware"),
    [(panel_1050, "5.07 01050  "), (panel_10100, "5.07 10100  ")],
)
async def test_no_installer_code_on_another_layout(
    make_report,  # noqa: F811
    state,
    firmware: str,
) -> None:
    """A panel read with another model's profile (firmware 5.0): its PIN
    table is not known to hold the installer code, so none is shown, and
    the block labels no entry."""
    panel_state = state()
    panel_state.firmware = firmware
    panel_state.installer_pin = INSTALLER_PIN
    report, _ = await make_report(Level.CONFIDENTIAL, panel_state)
    assert report["panel"]["fallback"] is True
    assert "installer_code" not in report["panel"]
    blocks = {b["name"]: b for b in report["blocks"]}
    assert "entries" not in blocks["pin_table"]
    (pin_table,) = [b for b in raw.raw_blocks(report) if b.name == "pin_table"]
    assert not any(
        "installer" in line or "not a user code" in line for line in pin_table.fields
    )


def test_reply_cut_to_its_length() -> None:
    """Bytes past the reply's 190 are dropped before decoding and masking,
    whichever way the reply was read."""
    extra = b"extra-bytes-marker"
    reply = si_reply("192.0.2.5", 5004) + bytes((0xAA,)) + bytes(26) + extra
    report = build_report(
        ReportInput(
            report_id="r",
            created="2026-11-23T10:00:00+01:00",
            level=Level.REDACTED,
            entry=_facts(BOARD_SMARTLAN_SI),
            reads=None,
            board_reply=reply,
            board_reply_source="udp",
        )
    )
    text = text_of(report)
    assert extra.hex(" ") not in text
    assert extra.decode() not in text
    rows = report["board"]["reply"]["rows"]
    assert sum(len(row.split(": ")[1].split()) for row in rows) == 190


async def test_board_reply_in_slow_polling_mode(fake: FakePanel) -> None:
    """Shared with other apps: the FIND connection is closed by the
    transport, no idle close is left waiting, and the next read connects
    again."""
    transport = transport_for(fake)
    transport.shared = True
    panel = SmartLivingPanel(transport, PIN, fallback_model=SMARTLIVING_1050_FW6)
    await panel.identify()
    await panel.transport.board_reply()
    assert not transport.connected
    assert transport._idle_close is None
    assert await gone(fake)
    await panel.read_zones()
    assert transport.connected
    await asyncio.sleep(transport_base.SHARED_IDLE_CLOSE + 0.2)
    assert not transport.connected  # the read's own idle close
    await panel.close()


async def test_partial_reply_not_kept_in_the_error(fake: FakePanel) -> None:
    """A reply cut short: the error does not carry the part received."""
    fake.find_reply = si_reply(fake.host, fake.port)[:100]
    fake.close_after_find = True
    with pytest.raises(ConnectionLost) as err:
        await transport_for(fake).board_reply()
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__
