"""Names of user codes, keys, readers and other objects, read from the panel."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry
from custom_components.inim_smartliving_alarm.api import LoggedEvent, SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.event_log import (
    EventNames,
    event_attributes,
)
from custom_components.inim_smartliving_alarm.protocol.event_types import (
    ObjectType,
    Ref,
    event_refs,
)
from custom_components.inim_smartliving_alarm.protocol.events import (
    decode_event_record,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
    SMARTLIVING_10100_FW6,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    BLOCKS,
    name_table,
    peripherals,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import label_block
from .fake_panel import FakePanel, panel_10100
from .test_config_flow import open_options, unload_all
from .test_init_baseline import entity_id_for
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

NAME_TABLES = (
    "code_labels",
    "key_labels",
    "reader_labels",
    "expander_labels",
    "timer_labels",
    "siren_labels",
)


# ------------------------------------------------------------------- API


async def test_configuration_has_the_names(fake_panel: FakePanel) -> None:
    """Codes, keys, readers, expanders, timers and sirens: one name each."""
    transport = SmartLanSiTransport(fake_panel.host, fake_panel.port)
    panel = SmartLivingPanel(transport, "1234")
    try:
        config = await panel.read_configuration()
    finally:
        await panel.close()
    assert config.code_labels.names[:2] == ["Master", "User 2"]
    assert len(config.code_labels.names) == 50
    assert config.key_labels.names[:3] == ["Key Anna", "Key Marco", ""]
    assert len(config.key_labels.names) == 100
    assert config.reader_labels.names[:2] == ["PK Giorno", "PK Garage"]
    assert len(config.reader_labels.names) == 20
    assert config.expander_labels.names[0] == "Exp Kitchen"
    assert len(config.expander_labels.names) == 20
    assert config.timer_labels.names[0] == "Timer 1"
    assert config.siren_labels.names[0] == "Outdoor siren"
    assert len(config.siren_labels.names) == 10


async def test_the_names_are_stored(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Setup stores the name tables with the rest of the configuration."""
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    for table in NAME_TABLES:
        assert table in config, table
    assert name_table(config, "readers")[:2] == ["PK Giorno", "PK Garage"]


# -------------------------------------------------------------- event log

CONFIG_1050 = {
    "keypad_labels": label_block(["Hall", "Garage", *[""] * 8]),
    "code_labels": label_block(["Master", "Anna", ""]),
    "key_labels": label_block(["Tag Anna", ""]),
    "reader_labels": label_block(["Front door", ""]),
}


def shown(agent: int, location: int, config=CONFIG_1050) -> tuple[str, str]:
    """(device, authorized_by) of an arming event, "-" where there is none."""
    raw = bytes.fromhex("bc03602f0100") + bytes((agent, location, 0x8A))
    event = decode_event_record(raw, SL1050)
    item = event_attributes(LoggedEvent(0, raw, event), config, None)
    return item.get("device", "-"), item.get("authorized_by", "-")


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (0, "Hall"),
        (1, "Garage"),
        (2, "Keypad 3"),  # no name on the panel
        (10, "Front door"),  # readers come after the 10 keypads
        (11, "Reader 2"),
        (0xFE, "Remote"),
        (0xFF, "-"),
    ],
)
def test_event_locations(location: int, expected: str) -> None:
    """Keypads, then readers, then remote and none."""
    assert shown(0xFF if location == 0xFF else 0, location)[0] == expected


@pytest.mark.parametrize(
    ("agent", "location", "expected"),
    [
        (0, 0xFE, "Master"),
        (1, 0, "Anna"),
        (2, 0, "Code 3"),  # no name on the panel
        (0, 10, "Tag Anna"),  # on a reader the agent is a key
        (1, 10, "Key 2"),
        (0xFF, 0xFF, "-"),
    ],
)
def test_event_agents(agent: int, location: int, expected: str) -> None:
    """User codes, or keys on a reader."""
    assert shown(agent, location)[1] == expected


def test_readers_follow_the_models_keypads() -> None:
    """A 10100 has 15 keypads: its first reader is location 15."""
    config = {
        "keypad_labels": label_block([""] * 15),
        "reader_labels": label_block(["Gate"]),
    }
    names = EventNames(config)
    event = decode_event_record(
        bytes.fromhex("bc03602f010000 0c8a".replace(" ", "")), SL1050
    )
    keypad = event_refs(event, SMARTLIVING_10100_FW6).where
    assert names.of(keypad) == "Keypad 13"
    event = decode_event_record(bytes.fromhex("bc03602f0100000f8a"), SL1050)
    assert names.of(event_refs(event, SMARTLIVING_10100_FW6).where) == "Gate"


def test_configuration_without_the_names() -> None:
    """A configuration stored by an earlier version names them as before."""
    names = EventNames({})
    assert names.user_code(0) == "Master Code"
    assert names.user_code(3) == "User 3"
    assert names.of(Ref(ObjectType.KEYPAD, 9)) == "Keypad 10"
    assert names.of(Ref(ObjectType.READER, 0)) == "Reader 1"
    assert names.of(Ref(ObjectType.KEY, 4)) == "Key 5"


async def test_event_log_shows_the_names(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The fake's "Areas 4-5-6-7 disarmed" came from code 0, remotely."""
    hass.config_entries.async_update_entry(  # reloads with all 23 events
        entry, options={**entry.options, "event_log_size": 50}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id_for(hass, entry, ("sensor", "_event_log")))
    disarmed = [
        event
        for event in state.attributes["event_log"]
        if event.get("authorized_id") == "00" and event.get("device_id") == "fe"
    ]
    assert disarmed
    assert disarmed[0]["authorized_by"] == "Master"
    assert disarmed[0]["device"] == "Remote"


# --------------------------------------------------------------- upgrade


async def test_old_configuration_is_read_again_once(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A configuration without the names is read again at the first start."""
    stored = await stored_configuration(panel)
    for table in (*NAME_TABLES, "peripherals", "general_options"):
        del stored[table]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=1,
        title="Old",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Old",
            DATA_INITIAL_PANEL_CONFIG: stored,
        },
        options={"areas": [1], "zones": [1], "scenarios": [1], "thermostats": []},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "for what is not stored yet" in caplog.text
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    assert name_table(config, "codes")[0] == "Master"
    assert peripherals(config).keypads == (0, 1)
    assert entry.options["zones"] == [1]  # the selection is kept

    caplog.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "for what is not stored yet" not in caplog.text
    await unload_all(hass)


# The stored blocks an entry needs; one missing makes the first start read the
# configuration again. Listed here, not taken from the code, so that a block
# dropped from the code's list fails its case.
REQUIRED_BLOCKS = (
    "model_string",
    "area_labels",
    "zone_labels",
    "scenario_labels",
    "keypad_labels",
    "code_labels",
    "key_labels",
    "reader_labels",
    "expander_labels",
    "timer_labels",
    "siren_labels",
    "phone_labels",
    "zone_records",
    "terminal_records",
    "scenario_records",
    "area_records",
    "general_options",
    "peripherals",
    "physical_map",
    "fixed_output_records",
    "fixed_output_labels",
    "user_code_outputs",
    "extra_output_options",
)


def test_every_block_is_required() -> None:
    """The code's list of blocks is the one above."""
    assert set(BLOCKS) == set(REQUIRED_BLOCKS)


@pytest.mark.parametrize("missing", REQUIRED_BLOCKS)
async def test_each_required_block_is_read_again_once(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    missing: str,
) -> None:
    """Any one required block missing: one re-read at the first start only."""
    stored = await stored_configuration(panel)
    del stored[missing]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=1,
        title="Old",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Old",
            DATA_INITIAL_PANEL_CONFIG: stored,
        },
        options={"areas": [1], "zones": [1], "scenarios": [1], "thermostats": []},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert caplog.text.count("panel configuration read again") == 1
    assert missing in entry.data[DATA_INITIAL_PANEL_CONFIG]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert "panel configuration read again" not in caplog.text
    await unload_all(hass)


async def test_reader_names_option_is_dropped(hass: HomeAssistant) -> None:
    """1.5: the reader names typed in the options are no longer used."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=4,
        data={},
        options={"reader_names": "Front, Back", "event_log_size": 30},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.options) == {"event_log_size": 30}


async def test_no_reader_names_field(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The general settings no longer ask for reader names."""
    result = await open_options(hass, entry)
    assert "reader_names" not in {str(key) for key in result["data_schema"].schema}
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_10100_names(socket_enabled: None) -> None:
    """The 10100 has its own counts: 100 codes, 150 keys, 30 readers."""
    fake = FakePanel(panel_10100())
    await fake.start()
    transport = SmartLanSiTransport(fake.host, fake.port)
    panel = SmartLivingPanel(transport, "1234")
    try:
        config = await panel.read_configuration()
    finally:
        await panel.close()
        await fake.stop()
    assert len(config.code_labels.names) == 100
    assert len(config.key_labels.names) == 150
    assert len(config.reader_labels.names) == 30
    assert len(config.expander_labels.names) == 40
    assert len(config.timer_labels.names) == 20
