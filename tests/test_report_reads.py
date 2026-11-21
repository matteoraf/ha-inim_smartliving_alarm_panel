"""Reading a system report's blocks (SmartLivingPanel.read_report) on the fake panels.

Every request the panel receives must lie inside the panel's own report table.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import pytest

from custom_components.inim_smartliving_alarm.api import ReportReads, SmartLivingPanel
from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.model import SMARTLIVING_1050_FW6
from custom_components.inim_smartliving_alarm.protocol.report_layout import (
    ReportTable,
    Space,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import wait_until
from .fake_panel import (
    TRANSPORT_G,
    FakePanel,
    PanelState,
    Request,
    panel_505,
    panel_505_fw51,
    panel_515,
    panel_1050,
    panel_1050_fw51,
    panel_10100,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

PANELS: dict[str, Callable[[], PanelState]] = {
    "1050": panel_1050,
    "10100": panel_10100,
    "505": panel_505,
    "515": panel_515,
    "1050-fw51": panel_1050_fw51,
    "505-fw51": panel_505_fw51,
}


@pytest.fixture
async def run_report() -> AsyncGenerator:
    """Start a fake panel, read a report from it, stop it."""
    started: list[tuple[FakePanel, SmartLivingPanel]] = []

    async def run(
        state: PanelState, *, transport: str = "si", **kwargs
    ) -> tuple[FakePanel, ReportReads]:
        g = transport == "g"
        fake = FakePanel(
            state,
            transport=TRANSPORT_G if g else "smartlan_si",
            username="user" if g else "",
            password="pass" if g else "",
        )
        await fake.start()
        if g:
            link = SmartLanGTransport(
                fake.host,
                fake.port,
                username="user",
                password="pass",
                reconnect_pause=0.01,
            )
        else:
            link = SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01)
        panel = SmartLivingPanel(link, "1234", fallback_model=SMARTLIVING_1050_FW6)
        started.append((fake, panel))
        return fake, await panel.read_report(**kwargs)

    yield run
    for fake, panel in started:
        await panel.close()
        await fake.stop()


def outside_table(requests: list[Request], table: ReportTable) -> list[Request]:
    """The requests that no block of the table allows."""
    ram: list[tuple[int, int]] = []
    eeprom: list[tuple[int, int]] = []
    for block in table.blocks:
        if block.space is Space.RAM:
            ram.append((block.address, block.address + block.length))
        elif block.space is Space.EEPROM:
            eeprom.append((block.address, block.address + block.length))
        elif block.space is Space.LABELS:
            ram.append((block.address, block.address + 2))  # the pointer
        elif block.space is Space.QUERY:
            ram.append((block.address, block.address + 1))
    if table.label_area is not None:
        start, size = table.label_area
        eeprom.append((start, start + size))

    def inside(spans: list[tuple[int, int]], request: Request) -> bool:
        length = 1 if request.address == reg.NOT_READY_ZONES else request.length
        return any(
            lo <= request.address and request.address + length <= hi for lo, hi in spans
        )

    bad = []
    for request in requests:
        if request.write:
            bad.append(request)
        elif request.space == "ram" and not inside(ram, request):
            bad.append(request)
        elif request.space == "eeprom" and not inside(eeprom, request):
            bad.append(request)
        elif request.space not in ("ram", "eeprom"):
            bad.append(request)
    return bad


@pytest.mark.parametrize("name", list(PANELS))
async def test_reads_stay_inside_the_table(run_report, name: str) -> None:
    """Each fake panel: every request lies in its own table; nothing written."""
    fake, reads = await run_report(PANELS[name]())
    assert reads.table.family in ("6", "5.1")
    assert reads.table.model == reads.firmware.model
    assert outside_table(fake.requests, reads.table) == []
    assert {read.block.name for read in reads.reads} == {
        block.name for block in reads.table.blocks
    }
    assert [read for read in reads.reads if read.error] == []
    # Every label table lies in the layout's label area, so all are read
    labels = [r for r in reads.reads if r.block.space is Space.LABELS]
    assert labels
    assert [r.block.name for r in labels if r.data is None] == []


async def test_1050_reads_every_block(run_report) -> None:
    """The verified layout: every block read, labels included."""
    _fake, reads = await run_report(panel_1050())
    assert reads.table.layout_verified
    missing = [r.block.name for r in reads.reads if r.data is None]
    assert missing == []
    by_name = {r.block.name: r for r in reads.reads}
    assert by_name["model_string"].data.startswith(b"6.07")
    assert by_name["not_ready_zones"].data is not None
    assert len(by_name["not_ready_zones"].data) == 30 * reads.model.zone_bitmap_length
    assert by_name["area_labels"].address == 0x10000
    assert by_name["event_log"].block.length == 1000 * 9


async def test_over_smartlan_g(run_report) -> None:
    """The same blocks over SmartLAN/G, in fewer requests."""
    fake, reads = await run_report(panel_1050(), transport="g")
    assert outside_table(fake.requests, reads.table) == []
    assert [r for r in reads.reads if r.data is None] == []
    _fake_si, reads_si = await run_report(panel_1050())
    assert reads.requests < reads_si.requests


async def test_the_pin_table_is_not_read_again(run_report) -> None:
    """An identified panel reads the PIN table once, as a report block."""
    fake, reads = await run_report(panel_1050())
    pin_table = reads.table.block("pin_table")
    reads_of_it = [
        r
        for r in fake.requests
        if r.space == "eeprom" and r.address == pin_table.address
    ]
    assert len(reads_of_it) == 1


async def test_label_tables_outside_the_area_are_not_read(
    run_report, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A label pointer that leads outside the label area is not followed."""
    state = panel_1050()
    _offset, count = state.layout.labels[reg.LABELS_ZONES]
    monkeypatch.setitem(state.layout.labels, reg.LABELS_ZONES, (0x9000, count))
    fake, reads = await run_report(state)
    zone_labels = next(r for r in reads.reads if r.block.name == "zone_labels")
    assert zone_labels.address == 0x9000 + 0x10000
    assert zone_labels.data is None
    assert zone_labels.not_read == "outside the label area"
    assert outside_table(fake.requests, reads.table) == []


async def test_programming_mode_stops_the_programming_reads(run_report) -> None:
    """In programming mode only the registers are read."""
    state = panel_1050()
    state.in_programming = True
    fake, reads = await run_report(state)
    assert reads.programming_mode == "before"
    assert not [r for r in fake.requests if r.space == "eeprom"]
    eeprom = [r for r in reads.reads if r.block.space in (Space.EEPROM, Space.LABELS)]
    assert eeprom
    assert {r.not_read for r in eeprom} == {"programming mode"}


def test_report_limits() -> None:
    """Short sessions, and time enough for the largest table over SmartLAN/SI."""
    from custom_components.inim_smartliving_alarm import api

    assert api.REPORT_SESSION_REQUESTS == 10
    assert api.REPORT_TIME_BUDGET == 120.0


async def test_time_budget(run_report) -> None:
    """Blocks not read within the time budget are marked."""
    _fake, reads = await run_report(panel_1050(), time_budget=0)
    assert {r.not_read for r in reads.reads} == {"time limit"}


async def test_sessions_are_short() -> None:
    """A command waits for at most one short session of the report."""
    state = panel_1050()
    fake = FakePanel(state)
    await fake.start()
    fake.reply_delay = 0.002
    panel = SmartLivingPanel(
        SmartLanSiTransport(fake.host, fake.port, reconnect_pause=0.01), "1234"
    )
    await panel.identify()
    before = len(fake.requests)
    report = asyncio.create_task(panel.read_report(session_size=10))
    await wait_until(lambda: len(fake.requests) > before)  # the report is reading
    assert not report.done()
    count = len(fake.requests)
    await panel.read_ram(reg.MODEL_STRING, 12)  # a POLL read stands in line
    waited = len(fake.requests) - count
    assert not report.done()
    reads = await report
    await panel.close()
    await fake.stop()
    assert reads.requests > 20
    assert waited <= 10 + 1


async def test_unreachable_panel_raises(run_report, refused_port: int) -> None:
    """With no panel at all the report cannot identify it."""
    from custom_components.inim_smartliving_alarm.transport.base import (
        TransportError,
    )

    panel = SmartLivingPanel(
        SmartLanSiTransport(
            "127.0.0.1",
            refused_port,
            connect_attempts=1,
            connect_timeout=0.5,
            reconnect_pause=0,
        ),
        "1234",
    )
    with pytest.raises(TransportError):
        await panel.read_report()
    await panel.close()


@pytest.mark.parametrize(
    ("firmware", "family"), [("5.07 01050  ", "5.0"), ("6.07 02050  ", "basic")]
)
async def test_panels_without_a_profile(run_report, firmware: str, family: str) -> None:
    """5.0x reads its core table; an unknown model only its registers."""
    state = panel_1050()
    state.firmware = firmware
    fake, reads = await run_report(state)
    assert reads.fallback
    assert reads.table.family == family
    assert outside_table(fake.requests, reads.table) == []
    if family == "basic":
        assert not [r for r in fake.requests if r.space == "eeprom"]
    else:  # no label area known: no label read
        labels = [r for r in reads.reads if r.block.space is Space.LABELS]
        assert {r.not_read for r in labels} == {"outside the label area"}
