"""Building system reports (system_report.py) from the fake panels, per level."""

from collections.abc import AsyncGenerator
import json
import re

import pytest

from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
)
from custom_components.inim_smartliving_alarm.legacy import configuration_dict
from custom_components.inim_smartliving_alarm.protocol.commands import encode_pin
from custom_components.inim_smartliving_alarm.protocol.masking import Level
from custom_components.inim_smartliving_alarm.protocol.model import SMARTLIVING_1050_FW6
from custom_components.inim_smartliving_alarm.protocol.report_layout import Space
from custom_components.inim_smartliving_alarm.system_report import (
    EntryFacts,
    Forbidden,
    ReportInput,
    _forbidden,
    build_report,
    report_text,
    self_check,
)
from custom_components.inim_smartliving_alarm.transport.base import PanelTransport
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import FakePanel, PanelState, panel_505, panel_1050, panel_10100
from .fake_panel.discovery import g_reply, si_reply
from .fake_panel.server import TRANSPORT_G

pytestmark = pytest.mark.usefixtures("socket_enabled")

PIN = "918273"
PHONE = b"+390123456789\x00"
KEY_SERIAL = bytes.fromhex("c0ffee15deadbeef")
CANARY = b"CANARY-OUTSIDE-TABLES"
SERIAL = "1234567890125678"
DDNS_USER = b"ddns-user-xyz"
DDNS_PASSWORD = b"ddns-pass-xyz"
LAN_PASSWORD = b"lanpw123"
LABELS = ("Kitchen window", "Tast Zona Giorno", "Master")
G_USERNAME = "installer-xyz"
G_PASSWORD = "g-secret-xyz"
G_PANEL_NAME = "Villa Rossi panel"


def planted_state(state: PanelState) -> PanelState:
    """A panel with the PIN, a phone number, a key serial and a canary."""
    state.pins[0] = PIN
    state.serial_number = SERIAL
    return state


def si_reply_with_credentials(host: str, port: int) -> bytes:
    """A SmartLAN/SI reply with dynamic DNS and the LAN password set."""
    reply = bytearray(si_reply(host, port))
    reply[54 : 54 + 14] = b"home.ddns.test"
    reply[90 : 90 + len(DDNS_USER)] = DDNS_USER
    reply[122 : 122 + len(DDNS_PASSWORD)] = DDNS_PASSWORD
    reply[154] = 1
    reply[155:163] = LAN_PASSWORD
    return bytes(reply)


def free_spans(table, size: int = 0x20000) -> list[tuple[int, int]]:
    """EEPROM spans outside every block and the label area."""
    used = sorted(
        [
            (b.address, b.address + b.length)
            for b in table.blocks
            if b.space is Space.EEPROM
        ]
        + ([(table.label_area[0], sum(table.label_area))] if table.label_area else [])
    )
    spans, last = [], 0
    for start, end in used:
        if start - last >= len(CANARY):
            spans.append((last, start))
        last = max(last, end)
    return spans


@pytest.fixture
async def make_report() -> AsyncGenerator:
    """Build a report from a fake panel at a level."""
    started: list[tuple[FakePanel, SmartLivingPanel]] = []

    async def make(
        level: Level,
        state: PanelState | None = None,
        *,
        board: str = BOARD_SMARTLAN_SI,
        username: str = G_USERNAME,
    ) -> tuple[dict, PanelState]:
        state = planted_state(state or panel_1050())
        transport: PanelTransport
        if board == BOARD_SMARTLAN_G:
            fake = FakePanel(
                state, transport=TRANSPORT_G, username=username, password=G_PASSWORD
            )
            await fake.start()
            transport = SmartLanGTransport(
                fake.host,
                fake.port,
                username=username,
                password=G_PASSWORD,
                reconnect_pause=0.01,
            )
        else:
            fake = FakePanel(state)
            await fake.start()
            transport = SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01)
        panel = SmartLivingPanel(transport, PIN, fallback_model=SMARTLIVING_1050_FW6)
        started.append((fake, panel))
        config = configuration_dict(await panel.read_configuration())
        table = (await panel.read_report()).table
        pin_table = table.block("pin_table")
        phone = table.block("phone_numbers")
        serials = table.block("key_serials")
        if phone is not None and serials is not None:  # not on the 5.0x core
            state.eeprom_extra[phone.address] = PHONE
            state.eeprom_extra[serials.address] = KEY_SERIAL
        for start, _end in free_spans(table):
            state.eeprom_extra[start] = CANARY
        assert pin_table is not None
        reads = await panel.read_report()
        facts = EntryFacts(
            title="Villa Rossi alarm",
            entry_version="1.5",
            integration_version="0.4.0",
            board=board,
            host="192.168.77.93",
            port=5004,
            options={"scan_interval": 5},
            stored_config=config,
            entities=[
                {
                    "kind": "binary_sensor",
                    "unique_suffix": "zone_1",
                    "enabled": True,
                    "entity_id": "binary_sensor.kitchen_window",
                    "name": "Kitchen window",
                }
            ],
            pin=PIN,
            username=username if board == BOARD_SMARTLAN_G else None,
            password=G_PASSWORD if board == BOARD_SMARTLAN_G else None,
        )
        board_reply = (
            g_reply(fake, G_PANEL_NAME)
            if board == BOARD_SMARTLAN_G
            else si_reply_with_credentials("192.168.77.93", 5004)
        )
        report = build_report(
            ReportInput(
                report_id="r-test",
                created="2026-11-19T10:00:00+01:00",
                level=level,
                entry=facts,
                reads=reads,
                board_reply=board_reply,
                transport_stats=panel.transport.stats.as_dict(),
                trace=list(panel.transport.stats.trace),
                user_code={"found": True, "number": 1},
            )
        )
        return report, state

    yield make
    for fake, panel in started:
        await panel.close()
        await fake.stop()


def text_of(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False)


def hex_spaced(data: bytes) -> str:
    return data.hex(" ")


def test_labels_used_are_distinctive() -> None:
    """The fake panel's zone 1 label is set below; checked here once."""
    assert len(LABELS[0]) > 3


@pytest.fixture(autouse=True)
def kitchen_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zone 1 of the fake 1050 gets a label no report text could contain."""
    original = panel_1050

    def labelled() -> PanelState:
        state = original()
        from .fake_panel import const as c

        state.labels[c.RAM_LABELS_ZONES][0] = LABELS[0]
        return state

    monkeypatch.setitem(globals(), "panel_1050", labelled)


async def test_redacted_holds_nothing_private(make_report) -> None:
    """Redacted: no PIN, secrets, credentials, host, full labels or serial."""
    report, _state = await make_report(Level.REDACTED)
    text = text_of(report)
    assert report["level"] == "redacted"
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []
    for forbidden in (
        PIN,
        encode_pin(PIN).hex(),
        hex_spaced(encode_pin(PIN)),
        hex_spaced(PHONE[:8]),
        hex_spaced(KEY_SERIAL),
        DDNS_USER.decode(),
        DDNS_PASSWORD.decode(),
        LAN_PASSWORD.decode(),
        hex_spaced(DDNS_PASSWORD[:6]),
        hex_spaced(LAN_PASSWORD[:6]),
        "192.168.77.93",
        SERIAL,
        SERIAL[:12],
        "home.ddns.test",
        "Villa Rossi",
        "binary_sensor.kitchen_window",
        hex_spaced(CANARY[:8]),
        *LABELS,
        hex_spaced(LABELS[0].encode()[3:9]),
    ):
        assert forbidden not in text, forbidden
    assert report["panel"]["serial"] == "…5678"
    assert report["board"]["reply"]["fields"]["mac"].endswith("34:56")
    assert report["board"]["reply"]["fields"]["dynamic_dns_host"] == "set"
    blocks = {b["name"]: b for b in report["blocks"]}
    for name in ("pin_table", "phone_numbers", "key_serials", "code_buffer"):
        assert blocks[name]["withheld"] == "secret"
        assert blocks[name]["rows"] == []
        assert blocks[name]["sha256"] is None
    zones = blocks["zone_labels"]
    assert zones["masked"]
    assert zones["rows"][0].split(": ")[1].startswith(LABELS[0].encode()[:3].hex(" "))
    assert report["configuration"]["zones"][0]["label"] == "Kit…"


async def test_full_has_names_but_no_secrets(make_report) -> None:
    """Full: labels, serial and addresses; still no PIN or credentials."""
    report, _state = await make_report(Level.FULL)
    text = text_of(report)
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []
    assert LABELS[0] in text
    assert SERIAL in text
    assert "192.168.77.93" in text
    for forbidden in (
        PIN,
        hex_spaced(encode_pin(PIN)),
        hex_spaced(PHONE[:8]),
        hex_spaced(KEY_SERIAL),
        DDNS_PASSWORD.decode(),
        LAN_PASSWORD.decode(),
        hex_spaced(DDNS_PASSWORD[:6]),
        hex_spaced(LAN_PASSWORD[:6]),
        hex_spaced(CANARY[:8]),
    ):
        assert forbidden not in text, forbidden
    blocks = {b["name"]: b for b in report["blocks"]}
    assert blocks["pin_table"]["withheld"] == "secret"


async def test_smartlan_g_redacted(make_report) -> None:
    """SmartLAN/G, Redacted: no user name, password or panel name."""
    report, _state = await make_report(Level.REDACTED, board=BOARD_SMARTLAN_G)
    text = text_of(report)
    assert report["level"] == "redacted"
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []
    for forbidden in (G_USERNAME, G_PASSWORD, G_PANEL_NAME, PIN):
        assert forbidden not in text, forbidden
    assert report["blocks"]


@pytest.mark.xfail(
    strict=True,
    reason="a one-character SmartLAN/G user name matches almost any text, so "
    "the privacy check leaves out nearly every block at Redacted",
)
async def test_smartlan_g_short_user_name(make_report) -> None:
    """A short user name does not empty the Redacted report."""
    report, _state = await make_report(
        Level.REDACTED, board=BOARD_SMARTLAN_G, username="a"
    )
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []


async def test_smartlan_g_full(make_report) -> None:
    """SmartLAN/G, Full: the panel name, never the password or the PIN."""
    report, _state = await make_report(Level.FULL, board=BOARD_SMARTLAN_G)
    text = text_of(report)
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []
    assert G_PANEL_NAME in text
    for forbidden in (G_PASSWORD, PIN, hex_spaced(encode_pin(PIN))):
        assert forbidden not in text, forbidden


async def test_confidential_has_the_secrets(make_report) -> None:
    """Full confidential: the PIN table, phone numbers and passwords too."""
    report, _state = await make_report(Level.CONFIDENTIAL)
    text = text_of(report)
    assert hex_spaced(encode_pin(PIN)) in text
    assert hex_spaced(KEY_SERIAL) in text
    fields = report["board"]["reply"]["fields"]
    assert fields["lan_password"] == LAN_PASSWORD.decode()
    assert fields["dynamic_dns_password"] == DDNS_PASSWORD.decode()
    assert hex_spaced(CANARY[:8]) not in text  # never read at any level


async def test_fresh_and_cached_hashes(make_report) -> None:
    """Blocks also stored in the entry show both hashes; equal when unchanged."""
    report, _state = await make_report(Level.FULL)
    blocks = {b["name"]: b for b in report["blocks"]}
    zone_records = blocks["zone_records"]["sha256"]
    assert zone_records["fresh"] == zone_records["cached"]
    assert blocks["zone_records"]["source"] == "fresh"


@pytest.mark.parametrize("make_state", [panel_505, panel_10100])
async def test_physical_map_parts_match_the_stored_map(make_report, make_state) -> None:
    """Each part of the physical map is compared with its own stored slice."""
    report, _state = await make_report(Level.FULL, make_state())
    blocks = {b["name"]: b for b in report["blocks"]}
    for name in ("physical_map", "physical_map_expanders", "physical_map_keypads"):
        hashes = blocks[name]["sha256"]
        assert hashes["cached"] is not None, name
        assert hashes["fresh"] == hashes["cached"], name


async def test_sections_are_decoded(make_report) -> None:
    """Status, events and the configuration view are filled in."""
    report, _state = await make_report(Level.REDACTED)
    assert report["status"]["areas"] is not None
    assert len(report["status"]["not_ready_by_scenario"]) == 30
    assert report["events"]["records"]
    assert report["configuration"]["zones"]
    assert report["panel"]["layout_verified"] is True
    assert report["panel"]["fallback"] is False
    assert report["status"]["cloud"] == {
        "configured": "no",
        "bit_0": False,
        "bit_1": False,
        "raw": "00",
        "last_result": "00000000",
    }


def test_self_check_drops_a_section_with_the_pin() -> None:
    """A section holding the PIN is replaced, the others kept."""
    report = {
        "status": {"note": f"code {PIN} typed"},
        "events": {"ok": 1},
        "created": f"2026-11-19 {PIN}",
    }
    forbidden = Forbidden(plain_pin=re.compile(rf"(?<![0-9]){PIN}(?![0-9])"))
    hits = self_check(report, forbidden)
    assert hits == ["status"]
    assert report["status"] == {"error": "self-check"}
    assert report["events"] == {"ok": 1}


def test_self_check_withholds_only_the_matching_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One block holding the PIN is withheld; the other blocks stay."""
    spaced = hex_spaced(encode_pin(PIN))
    forbidden = Forbidden(
        anywhere=(("pin_encoded", re.compile(re.escape(spaced))),),
    )
    report = {
        "blocks": [
            {"name": "zone_records", "rows": ["05450: 01 02"], "sha256": {"x": 1}},
            {"name": "event_queue", "rows": [f"1714e: {spaced}"], "sha256": {"x": 1}},
        ],
    }
    assert self_check(report, forbidden) == ["blocks/event_queue"]
    first, second = report["blocks"]
    assert first["rows"] == ["05450: 01 02"]
    assert second["withheld"] == "self-check"
    assert second["rows"] == [] and second["sha256"] is None
    assert "blocks/event_queue was left out" in caplog.text
    assert "(pin_encoded)" in caplog.text
    assert spaced not in caplog.text


def test_pin_digits_in_hex_data_are_no_hit() -> None:
    """Row addresses and hashes may hold the PIN's digits by chance."""
    forbidden = Forbidden(plain_pin=re.compile(r"(?<![0-9])1720(?![0-9])"))
    report = {
        "blocks": [
            {
                "name": "event_queue",
                "address": "0x1714E",
                "rows": ["1720e: 00 01 xx 03"],
                "sha256": {"fresh": "ab1720cd" * 8, "cached": None},
            }
        ],
        "events": {"records": [{"raw": "a1720b0000"}]},
    }
    assert self_check(report, forbidden) == []


@pytest.mark.parametrize("level", [Level.REDACTED, Level.FULL])
def test_a_value_that_is_the_pin_is_a_hit(level: Level) -> None:
    """A string value equal to the PIN is found wherever it is."""
    facts = EntryFacts(
        title="Home",
        entry_version="1.5",
        integration_version="0.4.0",
        board=BOARD_SMARTLAN_SI,
        host="192.168.77.93",
        port=5004,
        options={},
        stored_config={},
        pin="1720",
    )
    forbidden = _forbidden(
        ReportInput(
            report_id="r",
            created="2026-11-19T10:00:00+01:00",
            level=level,
            entry=facts,
            reads=None,
        )
    )
    for report in (
        {"status": {"note": "1720"}},
        {"blocks": [{"name": "zone_records", "rows": [], "label": "1720"}]},
        {"integration": {"options": {"code": ["1720"]}}},
    ):
        assert self_check(report, forbidden), report
    assert forbidden.match_kind("1720") == "pin_digits"


def test_self_check_looks_at_text_values_only() -> None:
    """Keys, numbers, times and identifier-like values are not matched."""
    forbidden = Forbidden(
        plain_pin=re.compile(r"(?<![0-9])2026(?![0-9])"), labels=("test",)
    )
    report = {
        "status": {"test": True, "zone": "test", "year": 2026},
        "events": {"time": "2026-11-19T10:00:00", "ring_size": 2026},
        "configuration": {"label": "tes…"},
    }
    assert self_check(report, forbidden) == []
    leaking = {"configuration": {"label": "my test zone"}, "panel": {"x": "pin 2026"}}
    assert sorted(self_check(leaking, forbidden)) == ["configuration", "panel"]


async def test_short_pin_and_common_label_keep_every_section(
    make_report, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PIN like a year and a zone labelled "test" drop no section."""
    monkeypatch.setitem(globals(), "PIN", "2026")
    state = panel_1050()
    from .fake_panel import const as c

    state.labels[c.RAM_LABELS_ZONES][1] = "test"
    report, _state = await make_report(Level.REDACTED, state)
    assert [e for e in report["errors"] if e.get("error") == "self-check"] == []
    for section in ("panel", "status", "events", "blocks", "configuration"):
        assert report[section] != {"error": "self-check"}, section
    text = text_of(report)
    assert hex_spaced(encode_pin("2026")) not in text


async def test_size_of_a_10100_report(make_report) -> None:
    """The largest panel's report stays well under 5 MB."""
    report, _state = await make_report(Level.CONFIDENTIAL, panel_10100())
    assert len(report_text(report)) < 5_000_000


def test_report_without_the_panel() -> None:
    """An unreachable panel: the stored configuration and the error."""
    facts = EntryFacts(
        title="Home",
        entry_version="1.5",
        integration_version="0.4.0",
        board=BOARD_SMARTLAN_SI,
        host="192.168.77.93",
        port=5004,
        options={},
        stored_config={"areas": {"names": ["Kitchen area", "Garage"]}},
        pin=PIN,
    )
    report = build_report(
        ReportInput(
            report_id="r-off",
            created="2026-11-19T10:00:00+01:00",
            level=Level.REDACTED,
            entry=facts,
            reads=None,
            read_error="connect_error",
        )
    )
    assert report["errors"][0] == {"where": "panel", "error": "connect_error"}
    assert report["blocks"] == []
    assert report["status"] is None
    assert report["board"]["reply"] is None
    assert report["board"]["reply_note"] == (
        "no reply (Home Assistant may not be on the board's network)"
    )
    assert report["errors"] == [{"where": "panel", "error": "connect_error"}]
    labels = [a["label"] for a in report["configuration"]["areas"]]
    assert labels == ["Kit…", "Gar…"]
    assert "192.168.77.93" not in text_of(report)


@pytest.mark.parametrize(
    ("level", "withheld"),
    [(Level.REDACTED, True), (Level.FULL, True), (Level.CONFIDENTIAL, False)],
)
async def test_fallback_configuration(
    make_report, level: Level, withheld: bool
) -> None:
    """Read with another model's layout: shown only at Full confidential."""
    state = panel_1050()
    state.firmware = "5.07 01050  "
    report, _state = await make_report(level, state)
    configuration = report["configuration"]
    assert report["panel"]["fallback"] is True
    assert report["panel"]["family"] == "5.0"
    assert ("withheld" in configuration) is withheld
    assert configuration["read_with"] == "SmartLiving 1050"
