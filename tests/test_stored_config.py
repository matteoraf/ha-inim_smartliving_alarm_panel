"""The stored panel configuration: its blocks, the update from entry version
1, the automatic re-read and the one lookup of a zone's physical slot."""

from collections.abc import Callable
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry
from custom_components.inim_smartliving_alarm.api import (
    ProgrammingModeError,
    SmartLivingPanel,
    create_panel,
)
from custom_components.inim_smartliving_alarm.config_flow import (
    InimAlarmConfigFlow,
)
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.panel_config import (
    PanelConfigError,
    PanelUnreachable,
    async_fetch_panel_configuration,
)
from custom_components.inim_smartliving_alarm.protocol.config import (
    PhysicalMap,
    TerminalType,
    is_zone_terminal,
    output_address,
)
from custom_components.inim_smartliving_alarm.protocol.errors import (
    DecryptionError,
    ProtocolError,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    BLOCKS,
    NAME_TABLES,
    migrate_v1,
    missing_blocks,
    stored_zones,
    system_info,
    zones_to_create,
)
from custom_components.inim_smartliving_alarm.transport.base import TransportError
from custom_components.inim_smartliving_alarm.transport.smartlan_g import (
    SmartLanGTransport,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import (
    FakePanel,
    PanelState,
    const as c,
    panel_505,
    panel_515,
    panel_1050,
    panel_1050_fw51,
    panel_10100,
)
from .test_config_flow import unload_all
from .test_entity_snapshot import code_labels
from .test_outputs import with_outputs
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

ENTRIES = Path(__file__).parent / "fixtures" / "entries"
RELEASES = sorted(path.stem for path in ENTRIES.glob("*.json"))

# The blocks entry version 1 never stored as read
NOT_IN_VERSION_1 = {
    "code_labels",
    "key_labels",
    "reader_labels",
    "expander_labels",
    "timer_labels",
    "siren_labels",
    "phone_labels",
}
# Blocks the releases before 0.4.0a3 / 0.4.0b1 did not store at all
NOT_BEFORE_0_4_0A3 = {"peripherals"}
NOT_BEFORE_0_4_0B1 = {
    "general_options",
    "area_records",
    "fixed_output_records",
    "fixed_output_labels",
    "physical_map",
    "user_code_outputs",
    "extra_output_options",
}


def fixture(release: str) -> dict[str, Any]:
    """The entry a release stored for the fake 1050."""
    return json.loads((ENTRIES / f"{release}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ store


@pytest.mark.parametrize(
    "profile",
    [panel_1050, panel_10100, panel_505, panel_515, panel_1050_fw51],
    ids=lambda builder: builder.__name__,
)
async def test_store_keeps_every_block_as_read(
    profile: Callable[[], PanelState],
) -> None:
    """Every block, as hex; nothing derived; nothing to read again."""
    fake = FakePanel(profile())
    await fake.start()
    try:
        stored = await stored_configuration(fake)
    finally:
        await fake.stop()
    assert list(stored) == list(BLOCKS)
    assert all(isinstance(value, str) for value in stored.values())
    for value in stored.values():
        bytes.fromhex(value)
    assert missing_blocks(stored) == set()


def test_entries_of_this_version_are_major_version_2() -> None:
    """0.4 refuses an entry of a newer major version, so it never runs on
    the blocks it cannot read."""
    assert (InimAlarmConfigFlow.VERSION, InimAlarmConfigFlow.MINOR_VERSION) == (2, 2)


# -------------------------------------------------------------- migration


@pytest.mark.parametrize("release", RELEASES)
def test_migrate_v1_keeps_the_blocks_as_read(release: str) -> None:
    """Each block version 1 stored as read is kept under its name; the seven
    name tables (and what a release did not store) are left to the re-read."""
    old = fixture(release)["data"][DATA_INITIAL_PANEL_CONFIG]
    migrated = migrate_v1(old)

    expected_missing = set(NOT_IN_VERSION_1)
    if release in ("0.3.2", "0.4.0a1", "0.4.0a2"):
        expected_missing |= NOT_BEFORE_0_4_0A3 | NOT_BEFORE_0_4_0B1
    elif release == "0.4.0a3":
        expected_missing |= NOT_BEFORE_0_4_0B1
    assert missing_blocks(migrated) == expected_missing
    assert set(migrated) == set(BLOCKS) - expected_missing
    assert migrated["model_string"] == old["system_info"]["raw_hex"]
    assert migrated["area_labels"] == old["areas"]["raw_hex"]
    assert migrated["zone_labels"] == old["zones"]["raw_hex_full"]
    assert (
        migrated["zone_records"] == (old["zones_config"]["raw_hex_part1_full_response"])
    )
    assert (
        migrated["terminal_records"]
        == (old["zones_config"]["raw_hex_part2_full_response"])
    )
    assert migrated["scenario_records"] == "".join(
        activation["raw_activation_hex"]
        for activation in sorted(
            old["scenario_activations"], key=lambda item: item["scenario_index"]
        )
    )


async def test_migrated_blocks_equal_a_fresh_read(fake_panel: FakePanel) -> None:
    """The blocks an entry of 0.4.0b2 kept are byte for byte what the panel
    gives today, the scenario records rebuilt from the per-scenario ones."""
    fresh = await stored_configuration(fake_panel)
    migrated = migrate_v1(fixture("0.4.0b2")["data"][DATA_INITIAL_PANEL_CONFIG])
    assert migrated == {name: fresh[name] for name in migrated}


@pytest.mark.parametrize(
    ("change", "kept"),
    [
        (lambda old: old.pop("scenario_activations"), False),
        (lambda old: old["scenario_activations"].pop(3), False),
        (lambda old: old["scenario_activations"][4].pop("raw_activation_hex"), False),
        (lambda old: old["scenario_activations"].reverse(), True),  # any order
    ],
    ids=["none", "one missing", "one without its record", "out of order"],
)
def test_scenario_records_only_when_complete(
    change: Callable[[dict], Any], kept: bool
) -> None:
    """A gap in the per-scenario records leaves the block to the re-read."""
    old = fixture("0.4.0b2")["data"][DATA_INITIAL_PANEL_CONFIG]
    expected = migrate_v1(old)["scenario_records"]
    change(old)
    migrated = migrate_v1(old)
    assert migrated.get("scenario_records") == (expected if kept else None)


@pytest.mark.parametrize("release", RELEASES)
def test_migrate_v1_twice_changes_nothing(release: str) -> None:
    """A configuration already in the current shape is kept as it is."""
    migrated = migrate_v1(fixture(release)["data"][DATA_INITIAL_PANEL_CONFIG])
    assert migrate_v1(migrated) == migrated


async def test_migrate_v1_keeps_a_fresh_configuration(fake_panel: FakePanel) -> None:
    """What this version stores goes through unchanged."""
    fresh = await stored_configuration(fake_panel)
    assert migrate_v1(fresh) == fresh


@pytest.mark.parametrize(
    "old",
    [
        {},
        {"errors": [], "system_info": None, "areas": None},  # a failed read
        {"system_info": {"raw_hex": 12}, "areas": {"names": ["A"]}},
    ],
)
def test_migrate_v1_skips_what_is_not_a_block(old: dict) -> None:
    """Missing, empty or malformed parts are left out, never a crash."""
    assert migrate_v1(old) == {}


def test_short_blocks_are_not_decoded() -> None:
    """A block shorter than the model's is migrated as it is and not used."""
    old = fixture("0.4.0b2")["data"][DATA_INITIAL_PANEL_CONFIG]
    old["zones_config"]["raw_hex_part1_full_response"] = old["zones_config"][
        "raw_hex_part1_full_response"
    ][:-18]
    migrated = migrate_v1(old)
    # Without decodable zone records each zone number is a bare zone
    assert stored_zones(migrated)[0].terminal is None
    assert len(stored_zones(migrated)) == 100


@pytest.mark.parametrize("release", RELEASES)
async def test_entry_migration_to_2_2(hass: HomeAssistant, release: str) -> None:
    """Every 1.x entry ends at 2.2 with the migrated configuration, without
    the single polling interval."""
    stored = fixture(release)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=stored["version"],
        minor_version=stored["minor_version"],
        title=stored["title"],
        data=stored["data"],
        options=stored["options"],
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert "polling_interval" not in entry.options
    assert entry.data[DATA_INITIAL_PANEL_CONFIG] == migrate_v1(
        stored["data"][DATA_INITIAL_PANEL_CONFIG]
    )
    assert entry.data["board"] == "smartlan_si"
    assert not {"limit_areas", "limit_zones", "limit_scenarios"} & set(entry.options)


async def test_entry_without_a_configuration(hass: HomeAssistant) -> None:
    """An entry whose setup never stored a configuration keeps none."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=5,
        data={"host": "192.0.2.10", "port": 5004, DATA_INITIAL_PANEL_CONFIG: {}},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data[DATA_INITIAL_PANEL_CONFIG] == {}


# ------------------------------------------------------- re-read at setup


def entry_of(panel: FakePanel, release: str) -> MockConfigEntry:
    """The entry a release stored, pointing to the fake panel."""
    stored = fixture(release)
    return MockConfigEntry(
        domain=DOMAIN,
        version=stored["version"],
        minor_version=stored["minor_version"],
        title=stored["title"],
        data={**stored["data"], "host": panel.host, "port": panel.port},
        # Its keypads count as checked: no reload after the first start
        options={**stored["options"], "thermostats": []},
    )


def bypass_switches(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    """The entry's zone bypass switches."""
    return [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if "_zone_enabled_switch_" in entity.unique_id
    ]


async def test_failed_re_read_is_tried_at_every_start(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """An entry of 0.3 stored no physical slots: its bypass switches come once
    a re-read succeeds, at a later start, without anything to do."""
    panel.unreadable_eeprom = [code_labels(panel.state)]
    entry = entry_of(panel, "0.3.2")
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "cannot read the panel configuration again" in caplog.text
    assert "Re-read panel configuration" in caplog.text
    assert bypass_switches(hass, entry) == []
    assert missing_blocks(entry.data[DATA_INITIAL_PANEL_CONFIG])

    # Next start, the panel still cannot deliver it: tried and logged again
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert "cannot read the panel configuration again" in caplog.text
    assert bypass_switches(hass, entry) == []

    # Then it can: read at the next start, the switches are there
    panel.unreadable_eeprom = []
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert "panel configuration read again" in caplog.text
    assert missing_blocks(entry.data[DATA_INITIAL_PANEL_CONFIG]) == set()
    assert len(bypass_switches(hass, entry)) == 37  # the fake's zones

    # And not again after that
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert "reading the panel configuration again" not in caplog.text
    await unload_all(hass)


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        (lambda stored: stored.update(physical_map="zz"), {"physical_map"}),
        (
            lambda stored: stored.update(physical_map=stored["physical_map"][:-2]),
            {"physical_map"},
        ),
        (
            lambda stored: stored.update(code_labels=stored["code_labels"][:32]),
            {"code_labels"},
        ),
        (
            lambda stored: stored.update(model_string=b"not a model ".hex()),
            {"model_string"},
        ),
        (lambda stored: stored.update(user_code_outputs=""), set()),  # no code
        (lambda stored: stored.pop("siren_labels"), {"siren_labels"}),
    ],
    ids=["not hex", "short map", "short labels", "bad model", "no code", "absent"],
)
async def test_blocks_that_do_not_decode_are_missing(
    fake_panel: FakePanel, change: Callable[[dict], Any], missing: set[str]
) -> None:
    """Invalid hex, or a length other than the model's, counts as missing."""
    stored = await stored_configuration(fake_panel)
    change(stored)
    assert missing_blocks(stored) == missing


async def test_short_physical_map_is_read_again(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored map that does not fit: read again at the next start, and the
    bypass switches appear without anything to do."""
    stored = await stored_configuration(panel)
    stored["physical_map"] = stored["physical_map"][:-2]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=1,
        title="Short map",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Short map",
            DATA_INITIAL_PANEL_CONFIG: stored,
        },
        options={
            "areas": [1],
            "zones": list(range(1, 51)),
            "scenarios": [1],
            "thermostats": [],
        },
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "panel configuration read again" in caplog.text
    assert missing_blocks(entry.data[DATA_INITIAL_PANEL_CONFIG]) == set()
    assert len(bypass_switches(hass, entry)) == 37  # the fake's zones
    await unload_all(hass)


async def test_failed_setup_closes_the_panel(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """An unexpected error after the first poll frees the board's only
    connection and keeps nothing of the entry."""
    entry = entry_of(panel, "0.4.0b2")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.inim_smartliving_alarm._remove_entities_not_imported",
        side_effect=RuntimeError("unexpected"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert panel.connections >= 1
    assert not panel.has_client
    other = create_panel(panel.host, panel.port, "1234")
    try:
        await other.identify()
    finally:
        await other.close()


# ----------------------------------------------------- reading the panel


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (ProgrammingModeError(), "panel_programming"),
        # A SmartLAN/G reply that does not decrypt: the board's user name or
        # password is not the stored one
        (
            DecryptionError("reply cannot be decrypted (wrong user name or password?)"),
            "auth_failed",
        ),
        # Another reply error is an invalid configuration, whatever it says
        (ProtocolError("authentication refused"), "invalid_panel_configuration"),
        (ProtocolError("reply checksum"), "invalid_panel_configuration"),
        (ValueError("labels"), "invalid_panel_configuration"),
    ],
)
async def test_configuration_errors(
    hass: HomeAssistant, error: Exception, key: str
) -> None:
    """Each failure is one typed error with its key."""
    panel = create_panel("192.0.2.10", 5004, "1234")
    with (
        patch.object(SmartLivingPanel, "read_configuration", side_effect=error),
        pytest.raises(PanelConfigError) as err,
    ):
        await async_fetch_panel_configuration(hass, panel)
    assert err.value.key == key
    assert str(err.value) == key


async def test_configuration_unreachable(hass: HomeAssistant) -> None:
    """A connection failure is PanelUnreachable."""
    panel = create_panel("192.0.2.10", 5004, "1234")
    with (
        patch.object(
            SmartLivingPanel, "read_configuration", side_effect=TransportError("x")
        ),
        pytest.raises(PanelUnreachable),
    ):
        await async_fetch_panel_configuration(hass, panel)


async def test_configuration_without_a_model_string(hass: HomeAssistant) -> None:
    """A blank model string is an invalid reply."""
    panel = create_panel("192.0.2.10", 5004, "1234")
    blank = MagicMock(model_string=b" " * 12)
    with (
        patch.object(SmartLivingPanel, "read_configuration", return_value=blank),
        pytest.raises(PanelConfigError) as err,
    ):
        await async_fetch_panel_configuration(hass, panel)
    assert err.value.key == "invalid_panel_response"


def test_create_panel_by_board() -> None:
    """The board type picks the transport; the 1050 is the fallback layout."""
    si = create_panel("192.0.2.10", 5004, "1234")
    g = create_panel(
        "192.0.2.10", 5004, "1234", board="smartlan_g", username="u", password="p"
    )
    assert isinstance(si.transport, SmartLanSiTransport)
    assert isinstance(g.transport, SmartLanGTransport)
    with pytest.raises(ValueError):
        create_panel("192.0.2.10", 5004, "1234", board="other")


async def test_released_frees_the_only_connection(fake_panel: FakePanel) -> None:
    """While released, another client can reach a board that takes one."""
    panel = create_panel(fake_panel.host, fake_panel.port, "1234")
    other = create_panel(fake_panel.host, fake_panel.port, "1234")
    try:
        await panel.identify()
        async with panel.released():
            await other.identify()
            await other.close()
        assert (await panel.read_status()) is not None
    finally:
        await panel.close()
        await other.close()
    assert fake_panel.rejected_connections == 0


# ------------------------------------------------------- physical slots


def test_slots_of_every_slot() -> None:
    """Every slot holding the terminal, the last one included; first wins."""
    physical = PhysicalMap((0, 5, None, 7, 5, 9))
    assert physical.slots_of(5) == (1, 4)
    assert physical.slot_of(5) == 1
    assert physical.slots_of(9) == (5,)  # the last slot
    assert physical.slot_of(9) == 5
    assert physical.slots_of(8) == ()
    assert physical.slot_of(8) is None


def test_output_address_takes_the_first_slot_from_3() -> None:
    """A terminal output is addressed by its first slot of 3 or more."""
    model = SMARTLIVING_1050_FW6
    slots: list[int | None] = [None] * model.physical_slots
    slots[1] = slots[40] = slots[60] = 4  # terminal 5 (output 5)
    slots[model.physical_slots - 1] = 6  # terminal 7, the last slot only
    physical = PhysicalMap(tuple(slots))
    assert output_address(5, physical, model) == 40
    assert output_address(7, physical, model) == model.physical_slots - 1


@pytest.mark.parametrize(
    ("terminal_type", "second_zone", "expected"),
    [
        (TerminalType.INPUT, False, True),
        (TerminalType.IN_OUT, False, True),
        (TerminalType.DOUBLE, False, True),
        (TerminalType.DOUBLE, True, True),
        (TerminalType.INPUT, True, False),
        (TerminalType.OUTPUT, False, False),
        (TerminalType.NULL, False, False),
        (0xFF, False, False),  # unknown type
    ],
)
def test_is_zone_terminal(
    terminal_type: int, second_zone: bool, expected: bool
) -> None:
    """Zones of input, input/output and double terminals; second zones of
    double terminals only."""
    assert is_zone_terminal(terminal_type, second_zone) is expected


async def test_zone_slot_is_the_first_physical_slot() -> None:
    """A zone's slot is its terminal's first slot, the last slot included."""
    state = panel_1050()
    state.terminal_types[37] = c.TERMINAL_INPUT
    state.zone_areas[37] = 1 << 2
    state.physical_map[129] = 37  # zone 38: only in the last slot
    state.physical_map[120] = 5  # zone 6: in slots 5 and 120
    fake = FakePanel(state)
    await fake.start()
    try:
        stored = await stored_configuration(fake)
    finally:
        await fake.stop()
    zones = {zone.number: zone for zone in stored_zones(stored)}
    assert zones[38].slot == 129
    assert zones[6].slot == 5
    assert zones[56].slot == 5  # the second zone shares its terminal's slot
    assert zones[56].exists is False  # terminal 6 is not double
    created = zones_to_create(stored, {6, 38, 56})
    assert [zone.number for zone in created] == [6, 38]


def test_zones_without_records() -> None:
    """Without zone records every labelled zone number is a bare zone."""
    stored = {"zone_labels": (b"Door".ljust(16) * 3).hex()}
    assert [zone.number for zone in stored_zones(stored)] == [1, 2, 3]
    assert all(zone.exists and zone.slot is None for zone in stored_zones(stored))


async def test_system_info_from_the_model_string(fake_panel: FakePanel) -> None:
    """Version and type, from the stored model string only."""
    info = system_info(await stored_configuration(fake_panel))
    assert info is not None
    assert (info.version, info.type) == ("6.07", "01050")
    assert system_info({}) is None


def test_name_tables_are_the_seven_blocks() -> None:
    """The event-name tables map to the blocks version 1 never kept."""
    assert set(NAME_TABLES.values()) == NOT_IN_VERSION_1


async def test_outputs_profile_is_stored(socket_enabled: None) -> None:
    """Outputs are decoded from the stored blocks alone."""
    fake = FakePanel(with_outputs(panel_1050()))
    await fake.start()
    try:
        stored = await stored_configuration(fake)
    finally:
        await fake.stop()
    assert missing_blocks(stored) == set()


@pytest.mark.parametrize(
    ("model_string", "version", "type_"),
    [
        (b"6.07 01050  ", "6.07", "01050"),
        (b"6.07 10100  ", "6.07", "10100"),
        (b"5.10 00515  ", "5.10", "00515"),
        (b"garbage", "Unknown", "Unknown"),
    ],
)
def test_system_info(model_string: bytes, version: str, type_: str) -> None:
    """The firmware version and the panel type as the model string writes them."""
    info = system_info({"model_string": model_string.hex()})
    assert info is not None
    assert (info.version, info.type) == (version, type_)


@pytest.mark.parametrize("release", RELEASES)
async def test_every_release_entry_sets_up(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    release: str,
) -> None:
    """An entry stored by each release is migrated and set up at its first
    start, with nothing logged as an error."""
    entry = entry_of(panel, release)
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    errors = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
        and record.name.startswith("custom_components.inim_smartliving_alarm")
    ]
    assert errors == []
    assert not missing_blocks(entry.data[DATA_INITIAL_PANEL_CONFIG])
    assert await hass.config_entries.async_unload(entry.entry_id)
