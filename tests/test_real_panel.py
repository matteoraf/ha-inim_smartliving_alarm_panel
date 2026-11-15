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

from custom_components.inim_smartliving_alarm.errors import (
    CommandRefusedError,
    async_send_command,
)
from custom_components.inim_smartliving_alarm.legacy import (
    LegacyPanelClient,
    create_client,
)
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode

from .fake_panel import FakePanel, checksum
from .real_panel.proxy import CONNECT_DEADLINE, PanelProxy, WriteGuard

HOST = os.environ.get("INIM_PANEL_HOST", "")
PORT = int(os.environ.get("INIM_PANEL_PORT", "5004"))
RECORD = bool(os.environ.get("INIM_PANEL_RECORD"))
WRITE = bool(os.environ.get("INIM_PANEL_WRITE"))
WRONG_PIN = bool(os.environ.get("INIM_PANEL_WRONG_PIN"))
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


def client_for(host: str, port: int, pin: str) -> LegacyPanelClient:
    """A client that waits as long as the proxy may take to reach the panel."""
    return create_client(host, port, pin, reply_timeout=REPLY_TIMEOUT)


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
async def api(proxy: PanelProxy) -> AsyncGenerator[LegacyPanelClient]:
    """Client reading the real panel through the read-only proxy."""
    client = client_for(proxy.host, proxy.port, READ_ONLY_PIN)
    yield client
    await client.close()


async def run(
    hass: HomeAssistant,
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call a client method (hass kept so the tests read as before)."""
    return await func(*args, **kwargs)


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
    hass: HomeAssistant, api: LegacyPanelClient, panel: FakePanel
) -> None:
    """The configuration has the same structure as the fake panel's."""
    fake_api = create_client(panel.host, panel.port, "1234")
    real = require(
        await run(hass, api.get_initial_panel_configuration), "configuration"
    )
    try:
        fake = await run(hass, fake_api.get_initial_panel_configuration)
    finally:
        await fake_api.close()

    assert real["errors"] == []
    assert real.keys() == fake.keys()
    for section, key in (
        ("areas", "names"),
        ("zones", "zone_names"),
        ("scenarios", "names"),
        ("keyboard_names", "names"),
    ):
        assert len(real[section][key]) == len(fake[section][key]), section
        assert all(isinstance(name, str) for name in real[section][key]), section

    activations = real["scenario_activations"]
    assert len(activations) == len(fake["scenario_activations"])
    for activation in activations:
        assert set(activation["area_actions"]) == set(range(1, 11))

    zones = real["zones_config"]["zones_config_detailed"]
    assert len(zones) == len(fake["zones_config"]["zones_config_detailed"])
    for zone in zones:
        assert set(zone["assigned_areas"]) <= set(range(1, 11))
        assert 0 <= zone.get("internal_index", 0) < 130


async def test_live_status(hass: HomeAssistant, api: LegacyPanelClient) -> None:
    """Every area and zone has a decoded status."""
    status = require(await run(hass, api.get_live_status_snapshot), "status")

    assert status["errors"] == []
    assert set(status["areas_status"]["area_statuses"]) == set(range(1, 11))
    assert set(status["areas_status"]["triggered_areas"]) <= set(range(1, 11))
    for key, statuses in (
        ("zones_status", "zone_statuses"),
        ("zones_excluded_status", "zone_excluded_statuses"),
        ("zones_triggered_status", "zone_triggered_statuses"),
    ):
        # every zone number of a 1050: zones 1-50 and second zones 51-100
        assert set(status[key][statuses]) == set(range(1, 101)), key
    assert status["active_scenario"] is not None


async def test_recent_events(hass: HomeAssistant, api: LegacyPanelClient) -> None:
    """The newest events are read and get plausible timestamps."""
    # any zone will do: only the plausibility of the times is checked
    result = require(
        await run(hass, api.execute_get_compact_events, count=5, time_zone=UTC),
        "event log",
    )

    events = result["events"]
    assert events, "the event log is empty"
    # The panel clock may run a little ahead of this machine's
    latest = datetime.now(UTC) + timedelta(days=1)
    for event in events:
        when = datetime.fromisoformat(event["timestamp_iso"])
        assert datetime(2015, 1, 1, tzinfo=UTC) < when < latest
        assert event["action_description"]


async def test_replies_have_the_fake_layout(
    hass: HomeAssistant, api: LegacyPanelClient, proxy: PanelProxy, panel: FakePanel
) -> None:
    """Every read gets a valid reply as long as the fake panel's."""
    require(await run(hass, api.get_initial_panel_configuration), "configuration")
    require(await run(hass, api.get_live_status_snapshot), "status")
    require(await run(hass, api.execute_get_compact_events, count=3), "event log")

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
async def panel_config(hass: HomeAssistant, api: LegacyPanelClient) -> dict[str, Any]:
    """The panel configuration, read before the write proxy connects."""
    config = require(
        await run(hass, api.get_initial_panel_configuration), "configuration"
    )
    # The board takes one client: free the connection for the write proxy
    await api.close()
    return config


@pytest.fixture
def area_zones(panel_config: dict[str, Any]) -> list[int]:
    """Zones (1-based) assigned to the write-test area."""
    return [
        zone["zone_index"] + 1
        for zone in panel_config["zones_config"]["zones_config_detailed"]
        if WRITE_AREA in zone["assigned_areas"]
    ]


@pytest.fixture
def zone_slot(panel_config: dict[str, Any]) -> int:
    """Physical terminal slot of the write-test zone, read from the panel."""
    zone = panel_config["zones_config"]["zones_config_detailed"][WRITE_ZONE - 1]
    assert "internal_index" in zone, f"zone {WRITE_ZONE} has no physical slot"
    return zone["internal_index"]


@pytest.fixture
async def write_api(
    write_pin: str,
    quiet_api_log: None,
    proxy: PanelProxy,
    make_proxy: ProxyFactory,
    zone_slot: int,
) -> AsyncGenerator[LegacyPanelClient]:
    """Client that may change area 4 and zone 18 only."""
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    if state[1] & PANEL_STATE_PROGRAMMING:
        pytest.skip("the panel is in programming mode, which refuses commands")
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
    write_api: LegacyPanelClient,
    area_zones: list[int],
    mode: ArmMode,
    arming_mode: str,
) -> None:
    """Area 4 can be armed in each mode and disarmed; no other area changes.

    The panel may accept arming with a zone not at rest and then go into
    alarm, so the area is armed only when all its zones are at rest; an
    alarm after arming disarms at once and fails the test.
    """
    before = require(await run(hass, write_api.get_live_status_snapshot), "status")
    areas = before["areas_status"]
    if (
        areas["area_statuses"][WRITE_AREA] != "disarmed"
        or WRITE_AREA in areas["triggered_areas"]
        or WRITE_AREA in areas["alarm_areas"]
        or WRITE_AREA in areas["tamper_areas"]
    ):
        pytest.skip(f"area {WRITE_AREA} is not disarmed and quiet")
    zone_statuses = before["zones_status"]["zone_statuses"]
    not_at_rest = [zone for zone in area_zones if zone_statuses.get(zone) != "clear"]
    if not_at_rest:
        pytest.skip(f"zones {not_at_rest} of area {WRITE_AREA} are not at rest")

    try:
        if mode is ArmMode.AWAY:  # the area switch's command
            assert await run(
                hass, write_api.execute_arm_disarm_areas, areas_to_arm=[WRITE_AREA]
            )
        else:
            result = await write_api.panel.arm_areas({WRITE_AREA: mode})
            assert result.ok, result.reason
        armed = await run(hass, write_api.get_live_status_snapshot)
        assert WRITE_AREA not in armed["areas_status"]["alarm_areas"], (
            f"area {WRITE_AREA} went into alarm after arming; disarmed, "
            "reset its alarm memory before the next run"
        )
        statuses = armed["areas_status"]["area_statuses"]
        assert statuses[WRITE_AREA] == "armed"
        assert armed["areas_status"]["area_modes"][WRITE_AREA] == arming_mode
        assert {k: v for k, v in statuses.items() if k != WRITE_AREA} == {
            k: v for k, v in areas["area_statuses"].items() if k != WRITE_AREA
        }
    finally:
        assert await run(
            hass, write_api.execute_arm_disarm_areas, areas_to_disarm=[WRITE_AREA]
        )

    after = await run(hass, write_api.get_live_status_snapshot)
    assert after["areas_status"]["area_statuses"][WRITE_AREA] == "disarmed"


async def test_bypass_and_include_zone(
    hass: HomeAssistant, write_api: LegacyPanelClient, zone_slot: int
) -> None:
    """Zone 18 can be bypassed and included again."""
    slot = zone_slot
    before = require(await run(hass, write_api.get_live_status_snapshot), "status")
    excluded = before["zones_excluded_status"]["zone_excluded_statuses"]
    if excluded[WRITE_ZONE] != "enabled":
        pytest.skip(f"zone {WRITE_ZONE} is not included")

    try:
        assert await run(hass, write_api.execute_set_zone_excluded_status, slot, True)
        bypassed = await run(hass, write_api.get_live_status_snapshot)
        statuses = bypassed["zones_excluded_status"]["zone_excluded_statuses"]
        assert statuses[WRITE_ZONE] == "disabled"
    finally:
        assert await run(hass, write_api.execute_set_zone_excluded_status, slot, False)

    after = await run(hass, write_api.get_live_status_snapshot)
    statuses = after["zones_excluded_status"]["zone_excluded_statuses"]
    assert statuses[WRITE_ZONE] == "enabled"


async def test_include_an_included_zone(
    hass: HomeAssistant, write_api: LegacyPanelClient, zone_slot: int
) -> None:
    """Including zone 18 while it is included: the panel reports no change."""
    before = require(await run(hass, write_api.get_live_status_snapshot), "status")
    excluded = before["zones_excluded_status"]["zone_excluded_statuses"]
    if excluded[WRITE_ZONE] != "enabled":
        pytest.skip(f"zone {WRITE_ZONE} is not included")

    with pytest.raises(CommandRefusedError) as err:
        await async_send_command(
            write_api.panel.bypass_zone(zone_slot, bypass=False), f"Zone {WRITE_ZONE}"
        )
    print(f"include of an included zone: result {err.value.code:#04x}")
    assert err.value.reason == "already_in_state", err.value.code

    after = await run(hass, write_api.get_live_status_snapshot)
    statuses = after["zones_excluded_status"]["zone_excluded_statuses"]
    assert statuses[WRITE_ZONE] == "enabled"


async def test_disarm_a_disarmed_area(
    hass: HomeAssistant, write_api: LegacyPanelClient
) -> None:
    """Disarming area 4 while it is disarmed changes nothing."""
    before = require(await run(hass, write_api.get_live_status_snapshot), "status")
    statuses = before["areas_status"]["area_statuses"]
    if statuses[WRITE_AREA] != "disarmed":
        pytest.skip(f"area {WRITE_AREA} is not disarmed")

    result = await write_api.panel.arm_areas({WRITE_AREA: ArmMode.DISARM})
    print(f"disarm of a disarmed area: result {result.code:#04x}")
    assert result.ok, result.code

    after = await run(hass, write_api.get_live_status_snapshot)
    assert after["areas_status"]["area_statuses"] == statuses


@pytest.fixture
async def wrong_pin_api(
    hass: HomeAssistant,
    quiet_api_log: None,
    proxy: PanelProxy,
    make_proxy: ProxyFactory,
    zone_slot: int,
) -> AsyncGenerator[LegacyPanelClient]:
    """Client with a PIN that matches no user code; may change area 4 only."""
    if not WRONG_PIN:
        pytest.skip("set INIM_PANEL_WRONG_PIN=1 to send a command with a wrong PIN")
    state = await raw_read(proxy, RAM_PANEL_STATE, 2)
    if state[1] & PANEL_STATE_PROGRAMMING:
        pytest.skip("the panel is in programming mode, which refuses commands")
    guarded = await make_proxy(WriteGuard(area=WRITE_AREA, zone_slot=zone_slot))
    for pin in WRONG_PIN_CANDIDATES:
        client = client_for(guarded.host, guarded.port, pin)
        await client.panel.identify()
        if not client.panel.user_code_found:
            break
        await client.close()
    else:
        pytest.skip("every candidate PIN matches a user code of the panel")
    yield client
    await client.close()


async def test_wrong_pin_is_refused(
    hass: HomeAssistant, wrong_pin_api: LegacyPanelClient
) -> None:
    """A PIN that matches no user code is refused; nothing changes."""
    before = require(await run(hass, wrong_pin_api.get_live_status_snapshot), "status")
    statuses = before["areas_status"]["area_statuses"]
    if statuses[WRITE_AREA] != "disarmed":
        pytest.skip(f"area {WRITE_AREA} is not disarmed")

    with pytest.raises(CommandRefusedError) as err:
        await async_send_command(
            wrong_pin_api.panel.arm_areas({WRITE_AREA: ArmMode.DISARM}),
            f"Area {WRITE_AREA}",
        )
    print(f"command with a wrong PIN: result {err.value.code:#04x}")
    assert err.value.reason == "invalid_code", err.value.code

    after = await run(hass, wrong_pin_api.get_live_status_snapshot)
    assert after["areas_status"]["area_statuses"] == statuses
