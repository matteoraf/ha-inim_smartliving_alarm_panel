"""The system report marks zone numbers that are not zones on the panel, and
names a terminal record never assigned; the viewer shows both."""

import copy
from pathlib import Path
import re

import pytest

from custom_components.inim_smartliving_alarm.attribute_values import (
    terminal_type_key,
)
from custom_components.inim_smartliving_alarm.panel_view import configuration_view
from custom_components.inim_smartliving_alarm.protocol.config import (
    TERMINAL_NOT_ASSIGNED,
    TerminalType,
)
from custom_components.inim_smartliving_alarm.protocol.masking import Level

from .fake_panel import FakePanel, const as c, panel_1050, panel_10100
from .test_outputs import with_outputs
from .test_report_viewer import run, save
from .test_system_report import make_report  # noqa: F401 - the fixture
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

# Terminals 74-99 of the 10100 as a real panel left them: never assigned
NOT_ASSIGNED = range(73, 99)


def unassigned_10100():
    """A fake 10100 whose terminals 74-99 are records never assigned."""
    state = panel_10100()
    for terminal in NOT_ASSIGNED:
        state.terminal_types[terminal] = TERMINAL_NOT_ASSIGNED
        state.physical_map = [
            c.UNUSED_SLOT if slot == terminal else slot for slot in state.physical_map
        ]
    return state


def zone_items(report_or_view: dict) -> dict[int, dict]:
    """The configuration view's zone items by number."""
    view = report_or_view.get("configuration", report_or_view)
    return {item["number"]: item for item in view["zones"]}


def without_marker(report: dict) -> dict:
    """The same report as a version without the marker wrote it."""
    old = copy.deepcopy(report)
    for item in old["configuration"]["zones"]:
        item.pop("is_zone", None)
    return old


# ---------------------------------------------------------- terminal type


def test_terminal_type_not_assigned() -> None:
    """0xFF is named; known types keep their keys; other values stay numbers."""
    assert terminal_type_key(TERMINAL_NOT_ASSIGNED) == "not_assigned"
    assert terminal_type_key(TerminalType.DOUBLE) == "double"
    assert terminal_type_key(TerminalType.NULL) == "null"
    assert terminal_type_key(0x42) == 0x42


# -------------------------------------------------------- configuration


async def test_is_zone_by_terminal_type() -> None:
    """Zones of input, input/output and double terminals; second zones of
    double terminals only; output and unused terminals are not zones."""
    state = with_outputs(panel_1050())
    state.terminal_types[0] = c.TERMINAL_DOUBLE
    fake = FakePanel(state)
    await fake.start()
    try:
        zones = zone_items(configuration_view(await stored_configuration(fake)))
    finally:
        await fake.stop()
    assert zones[1]["is_zone"] is True  # double terminal
    assert zones[51]["is_zone"] is True  # its second zone
    assert zones[2]["is_zone"] is True  # input
    assert zones[52]["is_zone"] is False  # second zone of an input
    assert zones[41]["is_zone"] is False  # output terminal
    assert zones[45]["is_zone"] is False  # unused (null) terminal


def test_no_marker_without_records() -> None:
    """Without decodable zone records whether a number is a zone is unknown."""
    stored = {"zone_labels": (b"Door".ljust(16) * 3).hex()}
    zones = configuration_view(stored)["zones"]
    assert [item["number"] for item in zones] == [1, 2, 3]
    assert all("is_zone" not in item for item in zones)


async def test_report_of_a_10100_with_records_never_assigned(
    make_report,  # noqa: F811
) -> None:
    """Their zones and second zones are marked and named, at every level."""
    for level in (Level.REDACTED, Level.FULL):
        report, _state = await make_report(level, unassigned_10100())
        zones = zone_items(report)
        for number in (74, 99, 174, 199):
            assert zones[number]["is_zone"] is False, number
            assert zones[number]["terminal_type"] == "not_assigned", number
        assert zones[1]["is_zone"] is True
        assert zones[1]["terminal_type"] == "input"
        assert zones[73]["terminal_type"] == "null"  # unused, not never assigned


# ------------------------------------------------------------------ viewer


@pytest.fixture
async def marked(make_report, tmp_path: Path) -> dict:  # noqa: F811
    """A 1050 report with the unused terminal 40 in alarm (its raw state)."""
    state = panel_1050()
    state.zone_states[39] = c.ZONE_ALARM
    report, _state = await make_report(Level.REDACTED, state)
    return report


HEADLINE_ALARM = re.compile(
    r'<div class="l">Zones in alarm</div><div class="v">(\d+)</div>'
)


def page_of(report: dict, tmp_path: Path, name: str) -> str:
    """The report's HTML page."""
    page = tmp_path / f"{name}.html"
    code, _text = run(
        "show", str(save(tmp_path / f"{name}.json", report)), "--html", str(page)
    )
    assert code == 0
    return page.read_text(encoding="utf-8")


def test_viewer_marks_the_zones(marked: dict, tmp_path: Path) -> None:
    """A Zone column; the counts leave out the numbers that are not zones."""
    code, text = run("show", str(save(tmp_path / "r.json", marked)), "--no-color")
    assert code == 0
    assert "Zone states (63 not zones left out)" in text
    assert re.search(r"\bZone\b", text)
    headline = HEADLINE_ALARM.search(page_of(marked, tmp_path, "r"))
    assert headline is not None
    assert headline.group(1) == "0"  # zone 40's terminal is not a zone


def test_viewer_html_chip(marked: dict, tmp_path: Path) -> None:
    """The page has a "Not zones" chip and tags those rows."""
    html = page_of(marked, tmp_path, "r")
    assert 'data-chip="not-zone"' in html
    assert "Not zones 63" in html
    assert len(re.findall(r'data-tags="[^"]*\bnot-zone\b', html)) == 63


def test_viewer_without_the_marker(marked: dict, tmp_path: Path) -> None:
    """A report without the marker (as 0.4 wrote it) shows as before."""
    old = without_marker(marked)
    code, text = run("show", str(save(tmp_path / "old.json", old)), "--no-color")
    assert code == 0
    assert "not zones left out" not in text
    html = page_of(old, tmp_path, "old")
    assert 'data-chip="not-zone"' not in html
    assert "not-zone" not in html.split("<script")[0].split("</style>")[-1]
    headline = HEADLINE_ALARM.search(html)
    assert headline is not None
    assert headline.group(1) == "1"  # every zone number counted, as before


def test_check_is_unchanged(marked: dict, tmp_path: Path) -> None:
    """Whether a report may be shared does not depend on the marker."""
    new = run("check", str(save(tmp_path / "new.json", marked)))
    old = run("check", str(save(tmp_path / "old.json", without_marker(marked))))
    assert new == old
