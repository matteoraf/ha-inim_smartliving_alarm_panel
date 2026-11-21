"""The report viewer (scripts/inim_report.py) on reports from the fake panels."""

import base64
import copy
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import pytest

from custom_components.inim_smartliving_alarm.protocol.masking import Level
from custom_components.inim_smartliving_alarm.report_crypto import encrypt

from .test_system_report import make_report  # noqa: F401 - the fixture

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from report_viewer import cli, keys  # noqa: E402

pytestmark = pytest.mark.usefixtures("socket_enabled")

PASSPHRASE = "a passphrase for tests"


def run(*args: str) -> tuple[int, str]:
    """Run one viewer command; its exit code and output."""
    out = io.StringIO()
    code = cli.main(list(args), out=out)
    return code, out.getvalue()


def save(path: Path, report: dict, *, wrap: bool = True) -> Path:
    """A report as Home Assistant downloads it (under "data")."""
    path.write_text(
        json.dumps({"home_assistant": {}, "data": report} if wrap else report)
    )
    return path


@pytest.fixture
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every passphrase prompt gets the test passphrase."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSPHRASE)


@pytest.fixture
async def reports(make_report, tmp_path: Path) -> dict[str, Path]:  # noqa: F811
    """A fake 1050's report at each level, saved."""
    saved = {}
    for level in Level:
        report, _state = await make_report(level)
        saved[str(level)] = save(tmp_path / f"{level}.json", report)
    return saved


# ------------------------------------------------------------------- show


async def test_show_every_level(reports: dict[str, Path]) -> None:
    """Every section is shown; hidden values are shown as such."""
    code, text = run("show", str(reports["redacted"]), "--no-color", "--events", "5")
    assert code == 0
    for title in ("Summary", "Findings", "Areas", "Zones", "Scenarios", "Codes"):
        assert f"\n{title}\n" in f"\n{text}"
    for title in ("Devices", "Outputs", "Status", "Events", "Connection"):
        assert f"\n{title}\n" in text
    assert "…" in text  # cut labels stay cut
    assert "[withheld: secret] at this level" in text
    assert "5 of " in text
    _, full = run("show", str(reports["full"]), "--no-color")
    assert "Are…" in text and "Are…" not in full  # names in full from Full on
    assert "Area 1" in full
    _, confidential = run("show", str(reports["confidential"]), "--no-color")
    assert "secret blocks" not in confidential


async def test_show_details(reports: dict[str, Path]) -> None:
    """--details adds every zone attribute."""
    _, short = run("show", str(reports["full"]), "--no-color")
    _, long = run("show", str(reports["full"]), "--no-color", "--details")
    assert "pulse_count" not in short
    assert "pulse_count" in long


async def test_show_firmware_51(make_report, tmp_path: Path) -> None:  # noqa: F811
    """A 5.12 panel's report: its own layout, not verified on hardware."""
    from .fake_panel import panel_1050_fw51

    report, _state = await make_report(Level.REDACTED, panel_1050_fw51())
    code, text = run("show", str(save(tmp_path / "r.json", report)), "--no-color")
    assert code == 0
    assert "Firmware                     5.12" in text
    assert "Layout verified on hardware  no" in text


def test_show_basic_mode(tmp_path: Path) -> None:
    """A panel without a table: the registers only, said in the summary."""
    report = {
        "report_version": 1,
        "level": "redacted",
        "panel": {"family": "basic", "fallback": True, "profile": "SmartLiving 1050"},
        "configuration": {
            "withheld": "fallback layout",
            "read_with": "SmartLiving 1050",
        },
        "blocks": [{"name": "model_string", "rows": ["04000: 36 2e 30 37"]}],
    }
    code, text = run("show", str(save(tmp_path / "r.json", report)), "--no-color")
    assert code == 0
    assert "Layout                       basic" in text
    assert "Fallback profile             yes" in text


def test_show_withheld_sections(tmp_path: Path) -> None:
    """A section left out by the privacy check says so."""
    report = {
        "report_version": 1,
        "level": "redacted",
        "status": {"error": "self-check"},
        "configuration": {
            "withheld": "fallback layout",
            "read_with": "SmartLiving 1050",
        },
        "errors": [{"where": "status", "error": "self-check"}],
    }
    code, text = run("show", str(save(tmp_path / "r.json", report)), "--no-color")
    assert code == 0
    assert "[withheld: self-check]" in text
    assert "[withheld: fallback layout]" in text
    assert "read with SmartLiving 1050" in text


# ---------------------------------------------------------------- check


def encrypted_file(path: Path, report: dict, public: str) -> Path:
    """An encrypted download, as the diagnostics give it at a Full level."""
    envelope = encrypt(
        json.dumps(report).encode(),
        report_id=report["report_id"],
        level=report["level"],
        public_key=public,
    )
    return save(
        path,
        {
            "report_version": 1,
            "report_id": report["report_id"],
            "level": report["level"],
            "share": "This file can be attached to a GitHub issue.",
            "encrypted": envelope,
            "redacted_view": {**report, "level": "redacted"},
        },
    )


def throwaway_public() -> str:
    key = X25519PrivateKey.generate().public_key()
    from cryptography.hazmat.primitives import serialization

    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


async def test_check_tells_what_to_share(
    reports: dict[str, Path], tmp_path: Path
) -> None:
    """Shareable files exit 0, readable Full files 1, anything else 2."""
    redacted = json.loads(reports["redacted"].read_text())["data"]
    full = json.loads(reports["full"].read_text())["data"]
    private = save(tmp_path / "private.json", {"PRIVATE COPY": "…", **full})
    plain = save(tmp_path / "plain.json", full, wrap=False)
    encrypted = encrypted_file(tmp_path / "enc.json", full, throwaway_public())
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"home_assistant": {}, "data": {"x": 1}}))
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    cases = {
        reports["redacted"]: (0, "Redacted report: safe to share."),
        encrypted: (0, "this is the file to attach"),
        private: (1, "PRIVATE COPY: do not share"),
        plain: (1, "Readable full report: do not share."),
        other: (2, "Not a system report."),
        broken: (2, "cannot be read as JSON"),
    }
    for path, (expected, words) in cases.items():
        code, text = run("check", str(path))
        assert code == expected, path.name
        assert words in text, path.name
    assert redacted["level"] == "redacted"


def test_check_notes_an_unknown_version(tmp_path: Path) -> None:
    """A report of an unknown format version is noted."""
    path = save(tmp_path / "r.json", {"report_version": 9, "level": "redacted"})
    code, text = run("check", str(path))
    assert code == 0
    assert "report_version 9 is not known" in text


# ------------------------------------------------------------------- hex


async def test_hex(reports: dict[str, Path]) -> None:
    """Rows as in the report; withheld blocks say why; unknown blocks fail."""
    report = json.loads(reports["redacted"].read_text())["data"]
    zone_rows = next(b for b in report["blocks"] if b["name"] == "zone_records")["rows"]
    code, text = run("hex", str(reports["redacted"]), "zone_records", "pin_table")
    assert code == 0
    assert all(f"  {row}" in text for row in zone_rows)
    assert "[withheld: secret]" in text
    assert run("hex", str(reports["redacted"]), "no_such_block")[0] == 2


async def test_hex_all(reports: dict[str, Path]) -> None:
    """--all shows every block of the report, in its order."""
    report = json.loads(reports["redacted"].read_text())["data"]
    names = [block["name"] for block in report["blocks"]]
    code, text = run("hex", str(reports["redacted"]), "--all")
    assert code == 0
    # Each block starts with an unindented header: its name first
    headers = [
        line.split(", ")[0]
        for line in text.splitlines()
        if line and not line.startswith(" ")
    ]
    assert headers == names
    assert run("hex", str(reports["redacted"]))[0] == 2  # neither names nor --all


# ------------------------------------------------------------------ diff


async def test_diff(reports: dict[str, Path], tmp_path: Path) -> None:
    """The same report: no difference; a changed byte is found and marked."""
    path = reports["full"]
    assert run("diff", str(path), str(path))[0] == 0
    report = json.loads(path.read_text())["data"]
    changed = copy.deepcopy(report)
    block = next(b for b in changed["blocks"] if b["name"] == "zone_records")
    address, _, cells = block["rows"][0].partition(": ")
    first = cells.split()
    first[0] = "ff" if first[0] != "ff" else "00"
    block["rows"][0] = f"{address}: {' '.join(first)}"
    code, text = run(
        "diff", str(path), str(save(tmp_path / "c.json", changed)), "--blocks"
    )
    assert code == 1
    assert "zone_records: 1 bytes differ" in text
    assert "^^" in text


def _first_value(data: dict) -> list:
    """The path of the first number or text in a nested dict or list."""
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, value in items:
        if isinstance(value, (dict, list)) and value:
            if (path := _first_value(value)) is not None:
                return [key, *path]
        elif isinstance(value, (int, str)) and not isinstance(value, bool):
            return [key]
    return None


def _change(data: dict, path: list) -> None:
    *parents, last = path
    for key in parents:
        data = data[key]
    data[last] = data[last] + 1 if isinstance(data[last], int) else f"{data[last]}!"


async def test_diff_by_part(reports: dict[str, Path], tmp_path: Path) -> None:
    """--blocks, --config and --status each compare their part only."""
    path = reports["full"]
    report = json.loads(path.read_text())["data"]
    changed = copy.deepcopy(report)
    _change(changed["configuration"], _first_value(changed["configuration"]))
    _change(changed["status"], _first_value(changed["status"]))
    other = str(save(tmp_path / "c.json", changed))

    code, text = run("diff", str(path), other, "--config")
    assert code == 1
    assert "configuration." in text
    assert "status." not in text

    code, text = run("diff", str(path), other, "--status")
    assert code == 1
    assert "status." in text
    assert "configuration." not in text

    code, text = run("diff", str(path), other, "--blocks")
    assert code == 0  # no raw byte changed
    assert "No differences" in text


def test_diff_skips_what_one_side_hides(tmp_path: Path) -> None:
    """Masked bytes, cut labels and withheld blocks are not compared."""

    def report(level: str, row: str, label: str, withheld: str | None) -> dict:
        return {
            "report_version": 1,
            "level": level,
            "blocks": [
                {"name": "zone_labels", "rows": [row]},
                {
                    "name": "pin_table",
                    "rows": [] if withheld else ["061f4: 01"],
                    "withheld": withheld,
                },
            ],
            "configuration": {"zones": [{"number": 1, "label": label}]},
        }

    redacted = save(
        tmp_path / "a.json",
        report("redacted", "100a0: 4b 69 74 xx xx", "Kit…", "secret"),
    )
    full = save(
        tmp_path / "b.json", report("full", "100a0: 4b 69 74 63 68", "Kitchen", None)
    )
    code, text = run("diff", str(redacted), str(full))
    assert code == 0
    assert "Levels differ" in text
    assert "pin_table: not comparable (withheld: secret)" in text


# -------------------------------------------------------------- keys


def test_keygen_files_and_output(passphrase, tmp_path: Path) -> None:
    """A private folder and file; only the public key and its ID are printed."""
    folder = tmp_path / "keys"
    code, text = run("keygen", "--dir", str(folder))
    assert code == 0
    files = list(folder.iterdir())
    assert len(files) == 1
    key_id = files[0].name.removeprefix("inim-report-key-").removesuffix(".pem")
    assert f"Key ID: {key_id}" in text
    assert "PRIVATE" not in text and "BEGIN" not in text
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert files[0].read_text().startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----")


def test_keygen_never_overwrites(passphrase, tmp_path: Path, monkeypatch) -> None:
    """The same key ID twice: the second is refused."""
    fixed = X25519PrivateKey.generate()
    monkeypatch.setattr(
        "cryptography.hazmat.primitives.asymmetric.x25519.X25519PrivateKey.generate",
        staticmethod(lambda: fixed),
    )
    assert run("keygen", "--dir", str(tmp_path))[0] == 0
    before = next(tmp_path.iterdir()).read_bytes()
    assert run("keygen", "--dir", str(tmp_path))[0] == 2
    assert next(tmp_path.iterdir()).read_bytes() == before


def test_keygen_refuses_mismatched_passphrases(tmp_path: Path, monkeypatch) -> None:
    """Both prompts must match."""
    answers = iter(["one", "two"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert run("keygen", "--dir", str(tmp_path / "k"))[0] == 2


async def test_decrypt_round_trip(
    reports: dict[str, Path], passphrase, tmp_path: Path
) -> None:
    """A key made by keygen decrypts a report encrypted to it."""
    work = tmp_path / "work"
    work.mkdir()
    folder = work / "keys"
    public, _key_id = keys.keygen(folder)
    full = json.loads(reports["full"].read_text())["data"]
    encrypted = encrypted_file(work / "enc.json", full, public)
    code, text = run("decrypt", str(encrypted), "--key-dir", str(folder))
    assert code == 0
    assert json.loads(text) == full
    out = work / "plain.json"
    assert (
        run("decrypt", str(encrypted), "--key-dir", str(folder), "-o", str(out))[0] == 0
    )
    assert json.loads(out.read_text()) == full
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    # The output file is not overwritten, and nothing is written next to the input
    assert (
        run("decrypt", str(encrypted), "--key-dir", str(folder), "-o", str(out))[0] == 2
    )
    assert sorted(os.listdir(work)) == ["enc.json", "keys", "plain.json"]


async def test_decrypt_refusals(
    reports: dict[str, Path], passphrase, tmp_path: Path
) -> None:
    """Another key, a changed report ID or a missing key: exit 3."""
    folder, other = tmp_path / "keys", tmp_path / "other"
    public, _ = keys.keygen(folder)
    keys.keygen(other)
    full = json.loads(reports["full"].read_text())["data"]
    encrypted = encrypted_file(tmp_path / "enc.json", full, public)
    assert run("decrypt", str(encrypted), "--key-dir", str(other))[0] == 3
    other_key = next(other.iterdir())
    assert run("decrypt", str(encrypted), "-k", str(other_key))[0] == 3
    document = json.loads(encrypted.read_text())
    document["data"]["report_id"] = "changed"
    encrypted.write_text(json.dumps(document))
    assert run("decrypt", str(encrypted), "--key-dir", str(folder))[0] == 3
    assert run("decrypt", str(reports["redacted"]), "--key-dir", str(folder))[0] == 2


async def test_show_decrypt_to_html_in_one_step(
    reports: dict[str, Path], passphrase, tmp_path: Path
) -> None:
    """--decrypt shows the full report; only the page is written, privately."""
    work = tmp_path / "one-step"
    work.mkdir()
    folder = work / "keys"
    public, _ = keys.keygen(folder)
    full = json.loads(reports["full"].read_text())["data"]
    encrypted = encrypted_file(work / "enc.json", full, public)
    page = work / "report.html"
    code, _ = run(
        "show",
        str(encrypted),
        "--decrypt",
        "--key-dir",
        str(folder),
        "--html",
        str(page),
    )
    assert code == 0
    text = page.read_text()
    assert "Area 1" in text  # the full report, not the Redacted view
    assert "Redacted view" not in text
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert sorted(os.listdir(work)) == ["enc.json", "keys", "report.html"]
    code, terminal = run(
        "show", str(encrypted), "--decrypt", "--key-dir", str(folder), "--no-color"
    )
    assert code == 0
    assert "Area 1" in terminal
    _, redacted_view = run("show", str(encrypted), "--no-color")
    assert "this is its Redacted view" in redacted_view


async def test_show_decrypt_refusals(
    reports: dict[str, Path], passphrase, tmp_path: Path
) -> None:
    """--decrypt needs an encrypted file and its own key."""
    folder, other = tmp_path / "keys", tmp_path / "other"
    public, _ = keys.keygen(folder)
    keys.keygen(other)
    full = json.loads(reports["full"].read_text())["data"]
    encrypted = encrypted_file(tmp_path / "enc.json", full, public)
    assert run("show", str(reports["redacted"]), "--decrypt")[0] == 2
    assert run("show", str(encrypted), "--decrypt", "--key-dir", str(other))[0] == 3


def test_decrypt_refuses_a_bad_key_id(
    passphrase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key ID that is not 16 hex characters is refused before any lookup."""

    def no_lookup(*args):
        raise AssertionError("a key file was looked up")

    monkeypatch.setattr(keys, "key_file", no_lookup)
    for key_id in ("../x", "032F4CD3C2378613", "", None, "032f4cd3c237861"):
        document = {
            "report_version": 1,
            "report_id": "r1",
            "level": "full",
            "encrypted": {"scheme": "x", "key_id": key_id},
            "redacted_view": {"report_version": 1, "level": "redacted"},
        }
        path = save(tmp_path / "enc.json", document)
        out = io.StringIO()
        err = io.StringIO()
        monkeypatch.setattr("sys.stderr", err)
        assert (
            cli.main(["decrypt", str(path), "--key-dir", str(tmp_path)], out=out) == 3
        )
        assert "no valid key ID" in err.getvalue()


def test_keygen_keeps_an_existing_folder_as_it_is(passphrase, tmp_path: Path) -> None:
    """A private folder is used unchanged; one others can read is refused."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    assert run("keygen", "--dir", str(private))[0] == 0
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    assert run("keygen", "--dir", str(shared))[0] == 2
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert list(shared.iterdir()) == []


async def test_html_of_a_readable_full_report_is_private(
    reports: dict[str, Path], tmp_path: Path
) -> None:
    """Only a Redacted page may be readable by others."""
    work = tmp_path / "html"
    work.mkdir()
    umask = os.umask(0o022)
    os.umask(umask)
    for level, mode in (
        ("redacted", 0o644 & ~umask),
        ("full", 0o600),
        ("confidential", 0o600),
    ):
        out = work / f"{level}.html"
        assert run("show", str(reports[level]), "--html", str(out))[0] == 0
        assert stat.S_IMODE(out.stat().st_mode) == mode, level


# ------------------------------------------------------------------ HTML


def test_html_is_self_contained_and_escaped(tmp_path: Path) -> None:
    """Markup in a label shows as text; nothing is loaded from elsewhere."""
    report = {
        "report_version": 1,
        "level": "full",
        "report_id": "r1",
        "configuration": {
            "areas": [{"number": 1, "label": "<script>alert(1)</script>"}],
            "zones": [{"number": 1, "label": '"><img src=x onerror=alert(1)>'}],
        },
    }
    out = tmp_path / "r.html"
    code, _ = run("show", str(save(tmp_path / "r.json", report)), "--html", str(out))
    assert code == 0
    page = out.read_text()
    assert page.count("<script") == 1  # the page's own script only
    assert "alert(1)</script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in page
    assert "http://" not in page and "https://" not in page
    tags = re.sub(r'"[^"]*"', '""', page)  # attribute values hold escaped text only
    assert not re.search(r"<[a-z]+[^>]*\ssrc=", tags)  # no element loads a resource
    assert (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
        in page
    )
    assert run("show", str(tmp_path / "r.json"), "--html", str(out))[0] == 2


def test_html_script_builds_no_markup() -> None:
    """The script reads text and data attributes only; it never parses markup."""
    from report_viewer import html

    script = html._SCRIPT
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "eval(",
        "document.write",
        "new Function",
        "setTimeout(",
        "fetch(",
        "XMLHttpRequest",
    ):
        assert forbidden not in script, forbidden


async def test_html_page_layout(reports: dict[str, Path], tmp_path: Path) -> None:
    """Header, numbers, side menu, list with details, raw block reader."""
    out = tmp_path / "r.html"
    assert run("show", str(reports["redacted"]), "--html", str(out))[0] == 0
    page = out.read_text()
    assert "<h1>SmartLiving 1050 · firmware 6.07</h1>" in page
    assert 'class="fbadge warn"' in page  # secret blocks withheld: a finding
    for label in ("Areas armed", "Zones in alarm", "Faults", "Cloud"):
        assert f'<div class="l">{label}</div>' in page
    assert '<nav class="side" aria-label="Sections">' in page
    for pane in ("findings", "zones", "events", "connection", "raw-blocks"):
        assert f'data-pane="{pane}"' in page
    assert '<button type="button" data-chip="alarm"' in page
    assert '<aside class="detail"' in page
    assert 'class="rawlink" href="#raw-zone_records"' in page
    assert '<select data-prefix="area-">' in page
    assert "Report details" in page
    # The reader: formats, slider, masked bytes, fields
    assert '<select class="fmt">' in page
    assert 'type="range" class="len" min="2" max="32"' in page
    assert '<span class="m">xx</span>' in page
    assert '<ol class="fields" hidden>' in page
    assert "badge st-withheld" in page


def test_raw_formats() -> None:
    """Each way to read bytes, with masked bytes shown as dots."""
    from report_viewer import raw

    row = [0x41, None, 0x05, 0xFF]
    fmt = raw.ROW_FORMATTERS
    assert fmt[raw.Format.TEXT](row) == "A··" + "·"
    assert fmt[raw.Format.DECIMAL](row) == " 65   ·   5 255"
    assert fmt[raw.Format.BITS]([0x01, 0x80]) == "00000001 10000000  set: 1, 16"
    assert fmt[raw.Format.BITS]([None]).endswith("set: none")
    assert fmt[raw.Format.NIBBLES]([0x21, None]) == "1/2 ·"
    assert fmt[raw.Format.DIGITS]([1, 2, 3, 4, 0xFF, 0xFF, None, 0x0A]) == "1234__·?"
    assert fmt[raw.Format.U16]([0x34, 0x12, None, 0x00, 0x01]) == "4660 (0x1234)  ·  ·"
    assert raw._u16([0x12, 0x34], big=True) == "4660 (0x1234)"


def test_raw_defaults_and_fields() -> None:
    """Blocks open in their own format; known records decode into fields."""
    from report_viewer import raw

    assert raw.default_format("zone_labels", "labels", False) is raw.Format.TEXT
    assert raw.default_format("general_options", "eeprom", False) is raw.Format.BITS
    assert raw.default_format("label_pointers", "ram", False) is raw.Format.U16
    assert raw.default_format("scenario_records", "eeprom", False) is raw.Format.NIBBLES
    assert raw.default_format("pin_table", "eeprom", False) is raw.Format.DIGITS
    assert raw.default_format("zone_records", "eeprom", True) is raw.Format.FIELDS
    assert raw.default_format("counters", "eeprom", False) is raw.Format.TEXT
    zone = "01 00 00 00 00 00 00 00 00"
    report = {
        "panel": {"model_string": b"6.07 01050  ".hex()},
        "blocks": [
            {
                "name": "zone_records",
                "space": "eeprom",
                "address": "0x05450",
                "length": 18,
                "privacy": "public",
                "rows": [f"05450: {zone} 01 xx 00 00 00 00 00 00 00"],
            },
            {"name": "pin_table", "space": "eeprom", "withheld": "secret", "rows": []},
        ],
    }
    zones, pins = raw.raw_blocks(report)
    assert zones.default is raw.Format.FIELDS
    assert zones.record == 9 and zones.row_length == 9
    assert zones.fields[0].startswith("areas=[1]")
    assert zones.fields[1] == "[masked]"
    assert raw.lines(zones)[1] == ("05459: 01 xx 00 00 00 00 00 00 00", "[masked]")
    assert pins.badge == "withheld: secret"
    assert pins.data == []


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        (
            "SMARTLIVING_1050_FW6",
            {
                ("zone", 3): ("zone_records", 18, 9),
                ("area", 2): ("area_records", 7, 7),
                ("scenario", 2): ("scenario_records", 8, 8),
                ("code", 2): ("code_records", 59, 59),
            },
        ),
        (
            "SMARTLIVING_505_FW6",
            {
                ("zone", 1): ("zone_records", 0, 9),
                ("area", 5): ("area_records", 28, 7),
                ("scenario", 30): ("scenario_records", 145, 5),
                ("code", 30): ("code_records", 29 * 54, 54),
            },
        ),
    ],
)
def test_record_links(model_name: str, expected: dict) -> None:
    """Each item's record: index times the decoders' record size."""
    from report_viewer import view

    from custom_components.inim_smartliving_alarm.protocol import model as models

    model = getattr(models, model_name)
    for (kind, number), link in expected.items():
        assert view.record_link(kind, {"number": number}, model) == link
    assert view.record_link("output", {"terminal_index": 4}, model) == (
        "terminal_records",
        48,
        12,
    )
    assert view.record_link("output", {"fixed_output": 2}, model) == (
        "fixed_output_records",
        2,
        2,
    )
    assert view.record_link("output", {"number": 5}, model) is None
    assert view.record_link("zone", {"number": 1}, None) is None


async def test_every_item_kind_links_to_its_record(
    reports: dict[str, Path], tmp_path: Path
) -> None:
    """Areas, zones, scenarios, codes and outputs open their records."""
    out = tmp_path / "r.html"
    assert run("show", str(reports["full"]), "--html", str(out))[0] == 0
    page = out.read_text()
    for block in (
        "zone_records",
        "area_records",
        "scenario_records",
        "code_records",
        "fixed_output_records",
    ):
        assert f'class="rawlink" href="#raw-{block}"' in page, block
    assert 'data-block="zone_records" data-offset="18" data-length="9"' in page


def test_script_links_bytes_by_data_attributes() -> None:
    """Hover and jump work through data-i indexes and class toggles only."""
    from report_viewer import html

    script = html._SCRIPT
    assert "setAttribute('data-i'" in script
    assert "classList.add('lit')" in script
    assert "classList.add('hit')" in script


def test_header_and_headline() -> None:
    """The header line and the four numbers."""
    from report_viewer import view

    report = {
        "report_id": "0123456789abcdef",
        "level": "full",
        "created": "2026-11-19T18:00:00+01:00",
        "panel": {"model": "1050", "firmware": "6.07"},
        "board": {"type": "smartlan_si"},
        "status": {
            "areas": {"modes": {"1": "disarmed", "2": "total"}},
            "zone_states": ["standby", "alarm"],
            "faults": {"active": ["battery"]},
            "cloud": {"configured": "yes"},
        },
    }
    title, subtitle = view.header(report)
    assert title == "SmartLiving 1050 · firmware 6.07"
    assert (
        subtitle == "smartlan_si · report 01234567… · full · 2026-11-19T18:00:00+01:00"
    )
    assert view.headline(report) == [
        ("Areas armed", "1 / 2", "warn"),
        ("Zones in alarm", "1", "bad"),
        ("Faults", "1", "warn"),
        ("Cloud", "On", "ok"),
    ]
    assert view.headline({})[3] == ("Cloud", "not read", "dim")


# ---------------------------------------------------------------- guard


def test_runs_without_home_assistant(tmp_path: Path) -> None:
    """The viewer and the modules it imports need no Home Assistant."""
    path = save(tmp_path / "r.json", {"report_version": 1, "level": "redacted"})
    code = (
        "import runpy, sys\n"
        "sys.modules['homeassistant'] = None\n"
        f"sys.argv = ['inim_report', 'show', {str(path)!r}, '--no-color']\n"
        "sys.path.insert(0, " + repr(str(SCRIPTS)) + ")\n"
        "from report_viewer import loader\n"
        "for name in ('report_crypto', 'system_report', 'panel_view',"
        " 'protocol.report_layout', 'protocol.masking'):\n"
        "    loader.module(name)\n"
        f"runpy.run_path({str(SCRIPTS / 'inim_report.py')!r}, run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Summary" in result.stdout
