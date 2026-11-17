"""Zones by terminal type: second zones of double terminals, output and
unused terminals, zone tamper, and the entry update to version 1.3."""

from collections.abc import AsyncGenerator
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.legacy import create_client

from .conftest import ALL, add_entry
from .fake_panel import FakePanel, const as c
from .test_init_baseline import call, entity_id_for, refresh, state_of

pytestmark = pytest.mark.usefixtures("socket_enabled")

# Keys the zone list gained in version 1.3
NEW_ZONE_KEYS = (
    "terminal_index",
    "terminal_type_val",
    "terminal_type_desc",
    "second_zone",
)


def zone_entities(zone: int) -> list[tuple[str, str]]:
    """Platform and unique ID suffix of every entity of a zone (1-based)."""
    return [
        ("binary_sensor", f"_zone_{zone}"),
        ("binary_sensor", f"_zone_triggered_{zone}"),
        ("binary_sensor", f"_zone_tamper_{zone}"),
        ("switch", f"_zone_enabled_switch_{zone}"),
    ]


def has_entity(
    hass: HomeAssistant, entry: ConfigEntry, entity: tuple[str, str]
) -> bool:
    """Whether the entry has an entity with this platform and unique ID suffix."""
    platform, suffix = entity
    return (
        er.async_get(hass).async_get_entity_id(
            platform, DOMAIN, f"{entry.entry_id}{suffix}"
        )
        is not None
    )


def original_name(
    hass: HomeAssistant, entry: ConfigEntry, entity: tuple[str, str]
) -> str | None:
    """The name the integration gives an entity (without the device name)."""
    registry = er.async_get(hass)
    return registry.async_get(entity_id_for(hass, entry, entity)).original_name


@pytest.fixture
async def mixed_entry(
    hass: HomeAssistant, panel: FakePanel
) -> AsyncGenerator[ConfigEntry]:
    """Terminal 1 double, 3 output, 4 input/output; every zone imported."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel.state.terminal_types[2] = c.TERMINAL_OUTPUT
    panel.state.terminal_types[3] = c.TERMINAL_IN_OUT
    entry = await add_entry(hass, panel, zones=ALL)
    yield entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_zone_entities_follow_the_terminal_types(
    hass: HomeAssistant, mixed_entry: ConfigEntry
) -> None:
    """Zones of input, input/output and double terminals only, plus second zones."""
    for zone, expected in (
        (1, True),  # double terminal: first zone
        (51, True),  # ... and its second zone
        (3, False),  # output terminal (e.g. a siren)
        (53, False),  # second zone of a terminal that is not double
        (4, True),  # input/output terminal
        (37, True),
        (38, False),  # unused terminal
        (52, False),
    ):
        for entity in zone_entities(zone):
            assert has_entity(hass, mixed_entry, entity) is expected, (zone, entity)


async def test_second_zone_state_and_bypass(
    hass: HomeAssistant, mixed_entry: ConfigEntry, panel: FakePanel
) -> None:
    """The second zone has its own state and is bypassed with the second-zone flag."""
    sensor = ("binary_sensor", "_zone_51")
    switch = ("switch", "_zone_enabled_switch_51")
    assert original_name(hass, mixed_entry, sensor) == "Zone 51 (Zone 51)"

    panel.state.zone_states[c.NUM_TERMINALS] = c.ZONE_ALARM
    await refresh(hass, mixed_entry)
    assert state_of(hass, mixed_entry, sensor) == "on"
    assert state_of(hass, mixed_entry, ("binary_sensor", "_zone_1")) == "off"

    await call(hass, mixed_entry, "switch", "turn_off", switch)
    assert panel.state.zone_included[c.NUM_TERMINALS] is False
    assert panel.state.zone_included[0] is True
    await refresh(hass, mixed_entry)
    assert state_of(hass, mixed_entry, switch) == "off"


async def test_second_zones_can_be_left_out(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Zones that are not ticked get no entities, second zones included."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    entry = await add_entry(hass, panel, zones=range(1, 51))
    assert has_entity(hass, entry, ("binary_sensor", "_zone_1"))
    assert not has_entity(hass, entry, ("binary_sensor", "_zone_51"))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zone_tamper_short_and_flags(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Tamper and short turn the tamper sensor on; test and tamper memory are attributes."""
    zone_sensor = ("binary_sensor", "_zone_20")
    tamper = ("binary_sensor", "_zone_tamper_20")
    memory = ("binary_sensor", "_zone_triggered_20")
    assert state_of(hass, entry, tamper) == "off"

    for value, text in ((c.ZONE_SHORT, "short"), (c.ZONE_TAMPER, "tamper")):
        panel.state.zone_states[19] = value
        await refresh(hass, entry)
        assert state_of(hass, entry, tamper) == "on"
        assert state_of(hass, entry, zone_sensor) == "off"
        state = hass.states.get(entity_id_for(hass, entry, zone_sensor))
        assert state.attributes["status_text"] == text

    panel.state.zone_states[19] = c.ZONE_REST
    panel.state.zone_inclusion_tail = bytes((0, 0, 0x08)) + bytes(10)  # zone 20
    panel.state.zone_memory_middle = bytes((0, 0, 0x08)) + bytes(4)  # terminal 20
    await refresh(hass, entry)
    assert state_of(hass, entry, tamper) == "off"
    state = hass.states.get(entity_id_for(hass, entry, zone_sensor))
    assert state.attributes["test"] is True
    assert state.attributes["terminal_type"] == "Input"
    assert original_name(hass, entry, memory) == "Zone 20 Alarm memory (Zone 20)"
    state = hass.states.get(entity_id_for(hass, entry, memory))
    assert state.attributes["tamper_memory"] is True


async def test_leftover_zone_entities_are_reported_not_removed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entity of an unused terminal from an older version is logged and kept."""
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_zone_38",
        config_entry=entry,
        suggested_object_id="test_panel_zone_38",
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    warnings = [r.getMessage() for r in caplog.records if "not zones" in r.getMessage()]
    assert len(warnings) == 1
    assert old.entity_id in warnings[0]
    assert "binary_sensor.test_panel_zone_37" not in warnings[0]
    assert registry.async_get(old.entity_id) is not None


# --------------------------------------------------------------- migration


async def stored_configuration(panel: FakePanel) -> dict:
    """The configuration a fresh setup stores for the fake panel."""
    client = create_client(panel.host, panel.port, "1234")
    try:
        return await client.get_initial_panel_configuration()
    finally:
        await client.close()


def as_stored_by_1_2(config: dict) -> dict:
    """The same configuration as version 1.2 stored it (first 50 zones only)."""
    zones_config = config["zones_config"]
    return {
        **config,
        "zones": {**config["zones"], "zone_names": config["zones"]["zone_names"][:50]},
        "zones_config": {
            **zones_config,
            "zones_config_detailed": [
                {k: v for k, v in zone.items() if k not in NEW_ZONE_KEYS}
                for zone in zones_config["zones_config_detailed"][:50]
            ],
        },
    }


async def test_migration_rebuilds_the_zone_list(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """1.2 -> 1.3 gives the zone list a fresh setup stores, without the panel."""
    panel.state.terminal_types[0] = c.TERMINAL_DOUBLE
    fresh = await stored_configuration(panel)
    host, port = panel.host, panel.port
    await panel.stop()  # the update must not need the panel

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=2,
        data={
            "host": host,
            "port": port,
            "pin": "1234",
            "board": "smartlan_si",
            DATA_INITIAL_PANEL_CONFIG: as_stored_by_1_2(fresh),
        },
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 5
    assert entry.data[DATA_INITIAL_PANEL_CONFIG] == fresh


async def test_migration_without_raw_data_keeps_the_zones(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """Without the raw records the stored zone list is kept, with a warning."""
    stored = as_stored_by_1_2(await stored_configuration(panel))
    del stored["zones_config"]["raw_hex_part2_full_response"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=2,
        data={"host": "192.0.2.10", "port": 5004, DATA_INITIAL_PANEL_CONFIG: stored},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 5
    assert entry.data[DATA_INITIAL_PANEL_CONFIG] == stored
    assert "Re-read panel configuration" in caplog.text
