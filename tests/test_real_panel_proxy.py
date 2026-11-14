"""The proxy used by the real-panel tests, checked against the fake panel.

These run everywhere: they show that the proxy forwards reads, refuses
forbidden writes and PIN table reads before they reach the panel, and keeps
the PIN out of recordings.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import json
from pathlib import Path
import socket
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.inim_api import InimAlarmAPI

from .fake_panel import FakePanel, checksum
from .real_panel.proxy import (
    RECONNECT_PAUSE,
    PanelProxy,
    WriteGuard,
    area_actions,
    reads_pin_table,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

AREA = 4
ZONE_SLOT = 17  # zone 18 of the fake panel
PIN = "1234"
PIN_HEX = "01020304ffff"

SYSTEM_INFO = bytes.fromhex("0000004000000c4c")
PIN_TABLE_READ = bytes.fromhex("00000161f400fa50")

ProxyFactory = Callable[[WriteGuard | None], Awaitable[PanelProxy]]


@pytest.fixture
async def make_proxy(panel: FakePanel) -> AsyncGenerator[ProxyFactory]:
    """Start proxies to the fake panel."""
    proxies: list[PanelProxy] = []

    async def make(guard: WriteGuard | None = None) -> PanelProxy:
        proxy = PanelProxy(panel.host, panel.port, guard)
        await proxy.start()
        proxies.append(proxy)
        return proxy

    yield make
    for proxy in proxies:
        await proxy.stop()


def api_for(proxy: PanelProxy) -> InimAlarmAPI:
    """API client connected through the proxy."""
    return InimAlarmAPI(host=proxy.host, port=proxy.port, pin_code_str=PIN)


async def run(
    hass: HomeAssistant, func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Run a blocking API call in the Home Assistant executor."""
    return await hass.async_add_executor_job(lambda: func(*args, **kwargs))


def read_system_info(api: InimAlarmAPI) -> dict[str, Any] | None:
    """Connect, read the model and firmware string and disconnect."""
    if not api.connect():
        return None
    try:
        return api.get_system_info()
    finally:
        api.disconnect()


async def raw_exchange(proxy: PanelProxy, request: bytes) -> bytes:
    """Send one raw request through the proxy and collect the reply."""
    reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
    try:
        writer.write(request)
        await writer.drain()
        reply = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), 0.5)
            except TimeoutError:
                break
            if not chunk:
                break
            reply += chunk
        return reply
    finally:
        writer.close()
        await writer.wait_closed()


def closed_port() -> int:
    """A local TCP port nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def writes(panel: FakePanel) -> list[int]:
    """Registers written on the fake panel."""
    return [request.address for request in panel.requests if request.write]


def test_area_actions_order() -> None:
    """Odd areas are the low nibble, even areas the high nibble of a byte."""
    actions = area_actions(bytes.fromhex("0010000000000000"))
    assert {area: action for area, action in actions.items() if action} == {4: 1}


@pytest.mark.parametrize(
    ("header", "reads"),
    [
        ("00000161f400fa50", True),  # first batch of the PIN table
        ("00000162ee003283", True),  # second batch
        ("00000161ee000600", False),  # ends where the table starts
        ("000001632c003b00", False),  # code records, right after the table
        ("00000061f400fa00", False),  # same address in RAM
    ],
)
def test_pin_table_detection(header: str, reads: bool) -> None:
    """Only EEPROM reads overlapping the PIN table are detected."""
    assert reads_pin_table(bytes.fromhex(header)) is reads


async def test_reads_are_forwarded_and_recorded(
    hass: HomeAssistant, make_proxy: ProxyFactory
) -> None:
    """A read reaches the panel; request, reply and chunks are recorded."""
    proxy = await make_proxy(None)

    info = await run(hass, read_system_info, api_for(proxy))

    assert info["version"] == "6.07"
    assert len(proxy.exchanges) == 1
    exchange = proxy.exchanges[0]
    assert exchange.header == SYSTEM_INFO
    assert checksum(exchange.reply[:-1]) == exchange.reply[-1]
    assert sum(exchange.chunks) == len(exchange.reply)
    assert proxy.blocked == []


async def test_reply_fragments_are_recorded(
    make_proxy: ProxyFactory, panel: FakePanel
) -> None:
    """A reply sent in pieces is forwarded whole and its pieces are counted."""
    proxy = await make_proxy(None)
    panel.fragment_replies = True
    panel.fragment_delay = 0.01

    reply = await raw_exchange(proxy, SYSTEM_INFO)

    assert reply == bytes(proxy.exchanges[0].reply)
    assert len(proxy.exchanges[0].chunks) > 1


async def test_writes_are_refused_in_read_only_mode(
    hass: HomeAssistant, make_proxy: ProxyFactory, panel: FakePanel
) -> None:
    """Without a guard no write reaches the panel."""
    proxy = await make_proxy(None)

    assert not await run(
        hass, api_for(proxy).execute_arm_disarm_areas, areas_to_arm=[AREA]
    )

    assert panel.state.armed_areas() == []
    assert writes(panel) == []
    assert proxy.blocked == ["writes are not allowed in read-only mode"]


async def test_pin_table_reads_are_refused(
    make_proxy: ProxyFactory, panel: FakePanel
) -> None:
    """A read of the PIN table is not forwarded."""
    proxy = await make_proxy(None)

    assert await raw_exchange(proxy, PIN_TABLE_READ) == b""

    assert panel.requests == []
    assert proxy.blocked == [f"read of the PIN table: {PIN_TABLE_READ.hex()}"]


async def test_unknown_opcodes_are_refused(
    make_proxy: ProxyFactory, panel: FakePanel
) -> None:
    """Opcodes other than plain or peripheral read/write are not forwarded."""
    proxy = await make_proxy(WriteGuard(area=AREA, zone_slot=ZONE_SLOT))
    header = bytes((0x7E, 0x00, 0x00, 0x20, 0x06, 0x00, 0x0E))
    header += bytes((checksum(header),))

    assert await raw_exchange(proxy, header) == b""

    assert panel.requests == []
    assert proxy.blocked == [f"unknown opcode 0x7E: {header.hex()}"]


async def test_guard_allows_the_chosen_area_and_zone(
    hass: HomeAssistant, make_proxy: ProxyFactory, panel: FakePanel
) -> None:
    """Area 4 and zone 18 can be changed and restored."""
    api = api_for(await make_proxy(WriteGuard(area=AREA, zone_slot=ZONE_SLOT)))

    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[AREA])
    assert panel.state.armed_areas() == [AREA]
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_disarm=[AREA])
    assert panel.state.armed_areas() == []

    assert await run(hass, api.execute_set_zone_excluded_status, ZONE_SLOT, True)
    assert panel.state.zone_included[17] is False
    assert await run(hass, api.execute_set_zone_excluded_status, ZONE_SLOT, False)
    assert panel.state.zone_included[17] is True


@pytest.mark.parametrize(
    ("command", "args", "kwargs"),
    [
        ("execute_arm_disarm_areas", (), {"areas_to_arm": [3]}),
        ("execute_arm_disarm_areas", (), {"areas_to_arm": [3, AREA]}),
        ("execute_arm_disarm_areas", (), {"areas_to_disarm": [1]}),
        ("execute_set_zone_excluded_status", (16, True), {}),
        ("execute_activate_scenario", (0,), {}),
        ("execute_reset_area_alarm", (AREA,), {}),
    ],
)
async def test_guard_refuses_everything_else(
    hass: HomeAssistant,
    make_proxy: ProxyFactory,
    panel: FakePanel,
    command: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Other areas, other zones and other commands never reach the panel."""
    proxy = await make_proxy(WriteGuard(area=AREA, zone_slot=ZONE_SLOT))
    panel.state.area_alarm_memory = 1 << (AREA - 1)  # something to reset

    await run(hass, getattr(api_for(proxy), command), *args, **kwargs)

    assert writes(panel) == []
    assert len(proxy.blocked) == 1


async def test_recordings_do_not_contain_the_pin(
    hass: HomeAssistant, make_proxy: ProxyFactory, tmp_path: Path
) -> None:
    """Write payloads are saved without the PIN and without their ack."""
    proxy = await make_proxy(WriteGuard(area=AREA, zone_slot=ZONE_SLOT))
    api = api_for(proxy)
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_arm=[AREA])
    assert await run(hass, api.execute_arm_disarm_areas, areas_to_disarm=[AREA])

    path = tmp_path / "recording.json"
    proxy.save(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    assert PIN_HEX not in text
    written = [item for item in data["exchanges"] if item["payload"]]
    assert len(written) == 2
    for item in written:
        assert item["payload"].startswith("ff" * 6)
        assert item["reply"] == ""
        assert item["reply_length"] == 1


# ------------------------------------------------------- panel connections

# asyncio.sleep() may wake up a little early
TOLERANCE = 0.01


async def test_reconnects_are_spaced(
    hass: HomeAssistant, make_proxy: ProxyFactory
) -> None:
    """A new connection to the panel waits a moment after the last one closed."""
    first = await make_proxy(None)
    second = await make_proxy(None)

    await run(hass, read_system_info, api_for(first))
    await run(hass, read_system_info, api_for(first))
    await run(hass, read_system_info, api_for(second))

    assert len(first.connected_at) == len(first.closed_at) == 2
    assert first.connected_at[1] - first.closed_at[0] >= RECONNECT_PAUSE - TOLERANCE
    # The pause also applies to another proxy
    assert second.connected_at[0] - first.closed_at[1] >= RECONNECT_PAUSE - TOLERANCE


async def test_connect_attempts_that_hang_are_retried(
    hass: HomeAssistant, panel: FakePanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection attempt the panel never answers is given up and retried."""
    open_connection = asyncio.open_connection
    attempts: list[int] = []

    async def first_attempt_hangs(host: str, port: int, *args: Any, **kwargs: Any):
        if port == panel.port:
            attempts.append(port)
            if len(attempts) == 1:
                await asyncio.sleep(3600)  # like a connection request the board drops
        return await open_connection(host, port, *args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", first_attempt_hangs)
    proxy = PanelProxy(panel.host, panel.port, connect_timeout=0.2)
    await proxy.start()
    try:
        info = await run(hass, read_system_info, api_for(proxy))
    finally:
        await proxy.stop()

    assert info["version"] == "6.07"
    assert len(attempts) == 2
    assert proxy.errors == []


async def test_unreachable_panel_gives_up_after_the_deadline() -> None:
    """Without an answer before the deadline the client connection is closed."""
    proxy = PanelProxy(
        "127.0.0.1", closed_port(), connect_deadline=0.5, reconnect_pause=0.1
    )
    await proxy.start()
    try:
        started = asyncio.get_running_loop().time()
        assert await raw_exchange(proxy, SYSTEM_INFO) == b""
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        await proxy.stop()

    # Gives up once a further attempt could not start before the deadline
    assert 0.5 - 0.1 <= elapsed < 3
    assert len(proxy.errors) == 1
    assert proxy.errors[0].startswith("cannot connect to the panel within 0.5 s")
    assert proxy.exchanges == []
