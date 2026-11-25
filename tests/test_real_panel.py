"""Opt-in tests against a real SmartLiving 1050 (firmware 6.x) on SmartLAN/SI.

Skipped unless INIM_PANEL_HOST is set; never run in CI. Disable any other
local client first (Home Assistant integration, Inim app on the local
network): the panel accepts one local connection at a time.

    INIM_PANEL_HOST=192.168.1.93 pytest tests/test_real_panel.py

Environment variables:

- INIM_PANEL_HOST: IP address of the SmartLAN/SI (host names are not
  resolved in tests).
- INIM_PANEL_PORT: TCP port, 5004 by default.
- INIM_PANEL_RECORD=1: save every request and reply of each test to
  tests/real_panel/recordings/ (git-ignored). Recordings hold the panel's
  names and event log: keep them local; commit only fixtures with the names
  replaced.
- INIM_PANEL_WRITE=1 and INIM_TEST_PIN: also run the write tests, which arm
  (total, partial, instant) and disarm area 4 and bypass and include zone 18
  only, and check the panel's answer to commands that change nothing
  (include an included zone 18, disarm a disarmed area 4). Type the PIN
  without storing it, and unset it afterwards:

      read -rs INIM_TEST_PIN && export INIM_TEST_PIN
      INIM_PANEL_HOST=... INIM_PANEL_WRITE=1 pytest tests/test_real_panel.py
      unset INIM_TEST_PIN

- INIM_PANEL_WRONG_PIN=1: send one disarm of area 4 (only while it is
  disarmed) with a PIN that matches no user code, to see the panel's
  answer. The panel may log it as an invalid code; it needs no PIN.
- INIM_PANEL_PROGRAMMING=1: run while the panel is in programming mode from
  a keypad. Checks that the integration reads the programming flag and does
  not send a command (a disarm of area 4) then; read-only, needs no PIN.

All traffic goes through a local proxy (tests/real_panel/proxy.py) that
refuses every write in read-only mode and, in write mode, every write other
than arming (any mode) or disarming area 4 and bypass/include of zone 18.
Area 4 is armed only while it is disarmed without alarm, tamper or alarm
memory and all its zones are at rest. Every test first reads the model
string and is skipped unless the panel is a 1050 with firmware 6.x, whose
memory layout the proxy and the tests assume. The write tests are also
skipped while the panel is in programming mode.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
import ipaddress
import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
import pytest_socket

from custom_components.inim_smartliving_alarm.api import (
    PANEL_ERRORS,
    EventBatch,
    PanelStatus,
    ProgrammingModeError,
    SmartLivingPanel,
    create_panel,
)
from custom_components.inim_smartliving_alarm.attribute_values import (
    area_mode_text,
    area_status_text,
    zone_state_text,
)
from custom_components.inim_smartliving_alarm.errors import (
    CommandRefusedError,
    PanelProgrammingError,
    async_send_command,
)
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.errors import (
    ProtocolError,
    UnsupportedPanelError,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    area_names,
    keypad_names,
    scenario_area_modes,
    scenario_names,
    store,
    stored_zones,
    zone_names,
)
from custom_components.inim_smartliving_alarm.transport.base import TransportError

from .event_texts import event_attributes
from .fake_panel import FakePanel, checksum
from .real_panel.proxy import CONNECT_DEADLINE, PanelProxy, WriteGuard

HOST = os.environ.get("INIM_PANEL_HOST", "")
PORT = int(os.environ.get("INIM_PANEL_PORT", "5004"))
RECORD = bool(os.environ.get("INIM_PANEL_RECORD"))
WRITE = bool(os.environ.get("INIM_PANEL_WRITE"))
WRONG_PIN = bool(os.environ.get("INIM_PANEL_WRONG_PIN"))
PROGRAMMING = bool(os.environ.get("INIM_PANEL_PROGRAMMING"))
RECORDINGS = Path(__file__).parent / "real_panel" / "recordings"

# The only area and zone the write tests may change (owner's choice)
WRITE_AREA = 4
WRITE_ZONE = 18

# No command is sent in read-only mode, so any PIN will do
READ_ONLY_PIN = "000000"
# Tried in turn for the wrong-PIN test; the first that matches no user code
# of the panel is used
WRONG_PIN_CANDIDATES = ("975318", "864208", "135797")

# Seconds to wait for a reply: the proxy may retry its connection to the
# panel for up to CONNECT_DEADLINE seconds first
REPLY_TIMEOUT = CONNECT_DEADLINE + 10

# RAM read directly; plain RAM reads may start anywhere inside a block
RAM_MODEL = 0x4000  # 12 ASCII bytes, e.g. "6.07 01050  "
MODEL_LENGTH = 12
RAM_MODEL_FIELD = 0x4005  # the 5-character model number inside that string
RAM_PANEL_STATE = 0x0D28  # panel state flags (stato_centrale), 2 bytes
PANEL_STATE_NO_AREA_ARMED = 0x08  # byte 1, bit 3
PANEL_STATE_PROGRAMMING = 0x40  # byte 1, bit 6
RAM_AREA_STATUS = 0x2000  # area modes, one nibble per area, then masks
AREA_STATUS_LENGTH = 16  # on the 1050
ARMED_MODES = (1, 2, 3)  # away, stay, instant (4 = disarmed)
RAM_SERIAL_AREA = 0xFEA0  # production serial-number area
SERIAL_AREA_LENGTH = 52
RAM_HW_SIGNATURE = 0xFECC
RAM_BRAND = 0xFED2

pytestmark = [
    pytest.mark.real_panel,
    pytest.mark.skipif(not HOST, reason="set INIM_PANEL_HOST to test a real panel"),
]

ProxyFactory = Callable[[WriteGuard | None], Awaitable[PanelProxy]]


def client_for(host: str, port: int, pin: str) -> SmartLivingPanel:
    """A panel that waits as long as the proxy may take to reach the panel."""
    return create_panel(host, port, pin, reply_timeout=REPLY_TIMEOUT)


async def read_status(client: SmartLivingPanel) -> PanelStatus | None:
    """The live status; None if it could not be read."""
    try:
        return await client.read_status()
    except PANEL_ERRORS:
        return None


async def read_events(client: SmartLivingPanel, count: int) -> EventBatch | None:
    """The newest events; None if the log could not be read."""
    try:
        return await client.read_events(count=count)
    except PANEL_ERRORS:
        return None


def area_states(status: PanelStatus) -> dict[int, str]:
    """Each area's state as the area switches show it."""
    return {area: area_status_text(mode) for area, mode in status.areas.modes.items()}


async def read_stored(client: SmartLivingPanel) -> dict[str, Any] | None:
    """The panel configuration as stored in the entry; None if not read."""
    try:
        return store(await client.read_configuration())
    except (
        TransportError,
        ProtocolError,
        UnsupportedPanelError,
        ProgrammingModeError,
    ):
        return None


async def accepted(command: Awaitable[Any]) -> bool:
    """Whether the panel accepted a command (False if it was not sent)."""
    try:
        return (await command).ok
    except (TransportError, ProtocolError, ProgrammingModeError):
        return False


@pytest.fixture
def allow_panel_host(socket_enabled: None) -> None:
    """Let the tests connect to the panel as well as to 127.0.0.1."""
    try:
        ipaddress.ip_address(HOST)
    except ValueError:
        pytest.fail(f"INIM_PANEL_HOST must be an IP address, not {HOST!r}")
    pytest_socket.socket_allow_hosts(["127.0.0.1", HOST])


@pytest.fixture
async def make_proxy(
    request: pytest.FixtureRequest, allow_panel_host: None
) -> AsyncGenerator[ProxyFactory]:
    """Start proxies to the panel; check and optionally save them afterwards."""
    proxies: list[PanelProxy] = []

    async def make(guard: WriteGuard | None = None) -> PanelProxy:
        proxy = PanelProxy(HOST, PORT, guard)
        await proxy.start()
        proxies.append(proxy)
        return proxy

    async def stop_all() -> None:
        for proxy in proxies:
            await proxy.stop()

    # The proxy's address checks and these tests assume a 1050 on FW 6.x
    try:
        model = await raw_read(await make(None), RAM_MODEL, MODEL_LENGTH)
    except BaseException:
        await stop_all()
        raise
    if model[5:10] != b"01050" or not model.startswith(b"6."):
        await stop_all()
        pytest.skip(
            "written for a SmartLiving 1050 with firmware 6.x, not "
            f"{model.decode('ascii', 'replace').strip()!r}"
        )

    yield make

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for index, proxy in enumerate(proxies):
        await proxy.stop()
        if RECORD:
            proxy.save(RECORDINGS / f"{request.node.name}-{stamp}-{index}.json")
    for proxy in proxies:
        assert proxy.blocked == [], "the proxy refused requests"
        assert proxy.errors == []


@pytest.fixture
async def proxy(make_proxy: ProxyFactory) -> PanelProxy:
    """A read-only proxy to the panel."""
    return await make_proxy(None)


@pytest.fixture
async def api(proxy: PanelProxy) -> AsyncGenerator[SmartLivingPanel]:
    """Client reading the real panel through the read-only proxy."""
    client = client_for(proxy.host, proxy.port, READ_ONLY_PIN)
    yield client
    await client.close()


async def read_system_info(proxy: PanelProxy) -> dict[str, Any]:
    """Read the model and firmware string (one request) as version and type."""
    text = (await raw_read(proxy, RAM_MODEL, MODEL_LENGTH)).decode("ascii")
    parts = text.strip().split(" ")
    return {"version": parts[0], "type": parts[1] if len(parts) > 1 else ""}


def require(result: Any, what: str) -> Any:
    """Fail with a hint if the panel did not answer."""
    assert result is not None, (
        f"no {what} from the panel at {HOST}:{PORT}; is another client "
        "(Home Assistant, the Inim app) connected?"
    )
    return result


async def raw_read(proxy: PanelProxy, address: int, length: int) -> bytes:
    """Read plain RAM through the proxy and return the data, checksum checked."""
    header = bytes(
        (0x00, 0x00, 0x00, address >> 8, address & 0xFF, length >> 8, length & 0xFF)
    )
    header += bytes((checksum(header),))
    reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
    try:
        writer.write(header)
        await writer.drain()
        try:
            reply = await asyncio.wait_for(
                reader.readexactly(length + 1), REPLY_TIMEOUT
            )
        except (TimeoutError, asyncio.IncompleteReadError):
            pytest.fail(
                f"no reply to a read of RAM 0x{address:04X} from {HOST}:{PORT}; "
                "is another client (Home Assistant, the Inim app) connected, "
                "or did the board stop accepting connections?"
            )
    finally:
        writer.close()
        await writer.wait_closed()
    assert checksum(reply[:-1]) == reply[-1], f"bad checksum at 0x{address:04X}"
    return reply[:-1]


def armed_areas(area_status: bytes) -> list[int]:
    """Areas (1-10) in any armed mode, from the area status register."""
    armed = []
    for area in range(1, 11):
        byte = area_status[(area - 1) // 2]
        mode = byte >> 4 if area % 2 == 0 else byte & 0x0F
        if mode in ARMED_MODES:
            armed.append(area)
    return armed


async def fake_reply(fake: FakePanel, header: bytes) -> bytes:
    """Reply of the fake panel to a raw read request."""
    reader, writer = await asyncio.open_connection(fake.host, fake.port)
    try:
        writer.write(header)
        await writer.drain()
        reply = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), 0.3)
            except TimeoutError:
                break
            if not chunk:
                break
            reply += chunk
        return reply
    finally:
        writer.close()
        await writer.wait_closed()


# --------------------------------------------------------------- read-only


async def test_model_and_firmware(proxy: PanelProxy) -> None:
    """The panel is a 1050 with firmware 6.x."""
    info = await read_system_info(proxy)

    assert info["type"] == "01050"
    assert info["version"].startswith("6.")


async def test_configuration_decodes_like_the_fake(
    hass: HomeAssistant, api: SmartLivingPanel, panel: FakePanel
) -> None:
    """The configuration has the same structure as the fake panel's."""
    fake_api = client_for(panel.host, panel.port, "1234")
    real = require(await read_stored(api), "configuration")
    try:
        fake = require(await read_stored(fake_api), "fake configuration")
    finally:
        await fake_api.close()

    assert real.keys() == fake.keys()
    for names in (area_names, zone_names, scenario_names, keypad_names):
        assert len(names(real)) == len(names(fake)), names.__name__
        assert all(isinstance(name, str) for name in names(real)), names.__name__

    modes = scenario_area_modes(real)
    assert len(modes) == len(scenario_area_modes(fake))
    for areas in modes.values():
        assert set(areas) == set(range(1, 11))

    zones = stored_zones(real)
    assert len(zones) == len(stored_zones(fake))
    for zone in zones:
        assert set(zone.areas) <= set(range(1, 11))
        assert zone.slot is None or 0 <= zone.slot < 130


async def test_live_status(hass: HomeAssistant, api: SmartLivingPanel) -> None:
    """Every area and zone has a decoded status."""
    status = require(await read_status(api), "status")

    assert set(status.areas.modes) == set(range(1, 11))
    assert status.areas.alarm_memory <= set(range(1, 11))
    # every zone number of a 1050: zones 1-50 and second zones 51-100
    assert len(status.zone_states) == 100
    assert len(status.zone_inclusion.included) == 100
    assert len(status.zone_memory.alarm_memory) == 100
    assert len(status.raw_active_scenario) == 1


async def test_recent_events(hass: HomeAssistant, api: SmartLivingPanel) -> None:
    """The newest events are read and get plausible timestamps."""
    # any zone will do: only the plausibility of the times is checked
    batch = require(await read_events(api, 5), "event log")

    assert batch.events, "the event log is empty"
    # The panel clock may run a little ahead of this machine's
    latest = datetime.now(UTC) + timedelta(days=1)
    for logged in batch.events:
        item = event_attributes(logged, {}, UTC)
        when = datetime.fromisoformat(item["timestamp"])
        assert datetime(2015, 1, 1, tzinfo=UTC) < when < latest
        assert item["description"]


async def test_replies_have_the_fake_layout(
    hass: HomeAssistant, api: SmartLivingPanel, proxy: PanelProxy, panel: FakePanel
) -> None:
    """Every read gets a valid reply as long as the fake panel's."""
    require(await read_stored(api), "configuration")
    require(await read_status(api), "status")
    require(await read_events(api, 3), "event log")

    # PIN-table replies are not kept by the proxy, so they cannot be compared
    reads = [
        exchange
        for exchange in proxy.exchanges
        if not exchange.write and not exchange.sensitive
    ]
    assert reads
    for exchange in reads:
        reply = bytes(exchange.reply)
        assert len(reply) >= 1, exchange.header.hex()
        assert checksum(reply[:-1]) == reply[-1], exchange.header.hex()
        expected = await fake_reply(panel, exchange.header)
        assert len(reply) == len(expected), exchange.header.hex()


async def test_ram_read_at_an_offset(proxy: PanelProxy) -> None:
    """A RAM read may start inside a block: 0x4005 returns the model number."""
    model = await raw_read(proxy, RAM_MODEL, MODEL_LENGTH)
    field = await raw_read(proxy, RAM_MODEL_FIELD, 5)

    assert field == model[5:10] == b"01050"


async def test_panel_state_flags(proxy: PanelProxy) -> None:
    """Panel state byte 1 bit 3 is set exactly when no area is armed."""
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    areas = await raw_read(proxy, RAM_AREA_STATUS, AREA_STATUS_LENGTH)

    no_area_armed = bool(state[1] & PANEL_STATE_NO_AREA_ARMED)
    assert no_area_armed == (armed_areas(areas) == []), (
        f"state {state.hex()}, armed areas {armed_areas(areas)}"
    )


async def test_serial_number_area(proxy: PanelProxy) -> None:
    """The serial-number area reads whole and ends with signature and brand."""
    area = await raw_read(proxy, RAM_SERIAL_AREA, SERIAL_AREA_LENGTH)
    signature = await raw_read(proxy, RAM_HW_SIGNATURE, 2)
    brand = await raw_read(proxy, RAM_BRAND, 2)

    offset = RAM_HW_SIGNATURE - RAM_SERIAL_AREA
    assert area[offset : offset + 2] == signature
    offset = RAM_BRAND - RAM_SERIAL_AREA
    assert area[offset : offset + 2] == brand


# ------------------------------------------------------------------ writes


@pytest.fixture
def write_pin() -> str:
    """The PIN for the write tests, from the environment only."""
    if not WRITE:
        pytest.skip("set INIM_PANEL_WRITE=1 to run the write tests")
    pin = os.environ.get("INIM_TEST_PIN", "")
    if not pin.isdigit():
        pytest.skip("set INIM_TEST_PIN (digits only) to run the write tests")
    return pin


@pytest.fixture
def quiet_api_log() -> Iterator[None]:
    """Keep frames out of the debug log during write tests (PIN masked anyway)."""
    logger = logging.getLogger("custom_components.inim_smartliving_alarm.transport")
    level = logger.level
    logger.setLevel(logging.INFO)
    yield
    logger.setLevel(level)


@pytest.fixture
async def panel_config(hass: HomeAssistant, api: SmartLivingPanel) -> dict[str, Any]:
    """The panel configuration, read before the write proxy connects."""
    config = require(await read_stored(api), "configuration")
    # The board takes one client: free the connection for the write proxy
    await api.close()
    return config


@pytest.fixture
def area_zones(panel_config: dict[str, Any]) -> list[int]:
    """Zones (1-based) assigned to the write-test area."""
    return [
        zone.number for zone in stored_zones(panel_config) if WRITE_AREA in zone.areas
    ]


@pytest.fixture
def zone_slot(panel_config: dict[str, Any]) -> int:
    """Physical terminal slot of the write-test zone, read from the panel."""
    zone = stored_zones(panel_config)[WRITE_ZONE - 1]
    assert zone.slot is not None, f"zone {WRITE_ZONE} has no physical slot"
    return zone.slot


@pytest.fixture
async def write_api(
    write_pin: str,
    quiet_api_log: None,
    proxy: PanelProxy,
    make_proxy: ProxyFactory,
    zone_slot: int,
) -> AsyncGenerator[SmartLivingPanel]:
    """Client that may change area 4 and zone 18 only."""
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    if state[1] & PANEL_STATE_PROGRAMMING:
        pytest.skip("the panel is in programming mode, which ignores commands")
    guarded = await make_proxy(WriteGuard(area=WRITE_AREA, zone_slot=zone_slot))
    client = client_for(guarded.host, guarded.port, write_pin)
    yield client
    await client.close()


@pytest.mark.parametrize(
    ("mode", "arming_mode"),
    [(ArmMode.AWAY, "total"), (ArmMode.STAY, "partial"), (ArmMode.INSTANT, "instant")],
)
async def test_arm_and_disarm_area(
    hass: HomeAssistant,
    write_api: SmartLivingPanel,
    area_zones: list[int],
    mode: ArmMode,
    arming_mode: str,
) -> None:
    """Area 4 can be armed in each mode and disarmed; no other area changes.

    The panel may accept arming with a zone not at rest and then go into
    alarm, so the area is armed only when all its zones are at rest; an
    alarm after arming disarms at once and fails the test.
    """
    before = require(await read_status(write_api), "status")
    areas = before.areas
    if (
        area_states(before)[WRITE_AREA] != "disarmed"
        or WRITE_AREA in areas.alarm_memory
        or WRITE_AREA in areas.alarm
        or WRITE_AREA in areas.tamper
    ):
        pytest.skip(f"area {WRITE_AREA} is not disarmed and quiet")
    not_at_rest = [
        zone
        for zone in area_zones
        if zone_state_text(before.zone_states[zone - 1]) != "clear"
    ]
    if not_at_rest:
        pytest.skip(f"zones {not_at_rest} of area {WRITE_AREA} are not at rest")

    try:
        if mode is ArmMode.AWAY:  # the area switch's command
            assert await accepted(write_api.arm_areas({WRITE_AREA: ArmMode.AWAY}))
        else:
            result = await write_api.arm_areas({WRITE_AREA: mode})
            assert result.ok, result.reason
        armed = await read_status(write_api)
        assert armed is not None
        assert WRITE_AREA not in armed.areas.alarm, (
            f"area {WRITE_AREA} went into alarm after arming; disarmed, "
            "reset its alarm memory before the next run"
        )
        statuses = area_states(armed)
        assert statuses[WRITE_AREA] == "armed"
        assert area_mode_text(armed.areas.modes[WRITE_AREA]) == arming_mode
        assert {k: v for k, v in statuses.items() if k != WRITE_AREA} == {
            k: v for k, v in area_states(before).items() if k != WRITE_AREA
        }
    finally:
        assert await accepted(write_api.arm_areas({WRITE_AREA: ArmMode.DISARM}))

    after = await read_status(write_api)
    assert after is not None
    assert area_states(after)[WRITE_AREA] == "disarmed"


async def test_bypass_and_include_zone(
    hass: HomeAssistant, write_api: SmartLivingPanel, zone_slot: int
) -> None:
    """Zone 18 can be bypassed and included again."""
    slot = zone_slot
    before = require(await read_status(write_api), "status")
    if not before.zone_inclusion.included[WRITE_ZONE - 1]:
        pytest.skip(f"zone {WRITE_ZONE} is not included")

    try:
        assert await accepted(write_api.bypass_zone(slot, bypass=True))
        bypassed = await read_status(write_api)
        assert bypassed is not None
        assert bypassed.zone_inclusion.included[WRITE_ZONE - 1] is False
    finally:
        assert await accepted(write_api.bypass_zone(slot, bypass=False))

    after = await read_status(write_api)
    assert after is not None
    assert after.zone_inclusion.included[WRITE_ZONE - 1] is True


async def test_include_an_included_zone(
    hass: HomeAssistant, write_api: SmartLivingPanel, zone_slot: int
) -> None:
    """Including zone 18 while it is included: the panel reports no change."""
    before = require(await read_status(write_api), "status")
    if not before.zone_inclusion.included[WRITE_ZONE - 1]:
        pytest.skip(f"zone {WRITE_ZONE} is not included")

    with pytest.raises(CommandRefusedError) as err:
        await async_send_command(
            write_api.bypass_zone(zone_slot, bypass=False), f"Zone {WRITE_ZONE}"
        )
    print(f"include of an included zone: result {err.value.code:#04x}")
    assert err.value.reason == "already_in_state", err.value.code

    after = await read_status(write_api)
    assert after is not None
    assert after.zone_inclusion.included[WRITE_ZONE - 1] is True


async def test_disarm_a_disarmed_area(
    hass: HomeAssistant, write_api: SmartLivingPanel
) -> None:
    """Disarming area 4 while it is disarmed changes nothing."""
    before = require(await read_status(write_api), "status")
    statuses = area_states(before)
    if statuses[WRITE_AREA] != "disarmed":
        pytest.skip(f"area {WRITE_AREA} is not disarmed")

    result = await write_api.arm_areas({WRITE_AREA: ArmMode.DISARM})
    print(f"disarm of a disarmed area: result {result.code:#04x}")
    assert result.ok, result.code

    after = await read_status(write_api)
    assert after is not None
    assert area_states(after) == statuses


@pytest.fixture
async def wrong_pin_api(
    hass: HomeAssistant,
    quiet_api_log: None,
    proxy: PanelProxy,
    make_proxy: ProxyFactory,
    zone_slot: int,
) -> AsyncGenerator[SmartLivingPanel]:
    """Client with a PIN that matches no user code; may change area 4 only."""
    if not WRONG_PIN:
        pytest.skip("set INIM_PANEL_WRONG_PIN=1 to send a command with a wrong PIN")
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    if state[1] & PANEL_STATE_PROGRAMMING:
        pytest.skip("the panel is in programming mode, which ignores commands")
    guarded = await make_proxy(WriteGuard(area=WRITE_AREA, zone_slot=zone_slot))
    for pin in WRONG_PIN_CANDIDATES:
        client = client_for(guarded.host, guarded.port, pin)
        await client.identify()
        if not client.user_code_found:
            break
        await client.close()
    else:
        pytest.skip("every candidate PIN matches a user code of the panel")
    yield client
    await client.close()


async def test_wrong_pin_is_refused(
    hass: HomeAssistant, wrong_pin_api: SmartLivingPanel
) -> None:
    """A PIN that matches no user code is refused; nothing changes."""
    before = require(await read_status(wrong_pin_api), "status")
    statuses = area_states(before)
    if statuses[WRITE_AREA] != "disarmed":
        pytest.skip(f"area {WRITE_AREA} is not disarmed")

    with pytest.raises(CommandRefusedError) as err:
        await async_send_command(
            wrong_pin_api.arm_areas({WRITE_AREA: ArmMode.DISARM}),
            f"Area {WRITE_AREA}",
        )
    print(f"command with a wrong PIN: result {err.value.code:#04x}")
    assert err.value.reason == "invalid_code", err.value.code

    after = await read_status(wrong_pin_api)
    assert after is not None
    assert area_states(after) == statuses


async def test_programming_mode_stops_commands(proxy: PanelProxy) -> None:
    """In programming mode the flag is read and no command is sent.

    The client goes through the read-only proxy, which would refuse (and
    report) any write: the command must stop before it is sent.
    """
    if not PROGRAMMING:
        pytest.skip(
            "enter programming mode at a keypad, then set INIM_PANEL_PROGRAMMING=1"
        )
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    if not state[1] & PANEL_STATE_PROGRAMMING:
        pytest.fail(f"the panel is not in programming mode (state {state.hex()})")

    client = client_for(proxy.host, proxy.port, READ_ONLY_PIN)
    try:
        status = await client.read_status()
        assert status.panel_state is not None
        assert status.panel_state.programming
        with pytest.raises(PanelProgrammingError) as err:
            await async_send_command(
                client.arm_areas({WRITE_AREA: ArmMode.DISARM}),
                f"Area {WRITE_AREA}",
            )
        assert not err.value.sent
    finally:
        await client.close()
    assert proxy.blocked == []
