"""The SmartLiving 10100 (firmware 6.x, experimental) against a fake 10100.

The fake's 10100 layout is typed independently of the integration's model
profile.
"""

from collections.abc import AsyncGenerator
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.legacy import create_client
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_10100_FW6,
)

from .conftest import add_entry
from .fake_panel import FakePanel, const as c, panel_10100
from .test_init_baseline import call, refresh, state_of
from .test_zones import has_entity

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake 10100; terminal 1 double, terminal 100 a double input on slot 99."""
    state = panel_10100()
    state.terminal_types[0] = c.TERMINAL_DOUBLE
    state.terminal_types[99] = c.TERMINAL_DOUBLE
    state.physical_map[99] = 99
    fake = FakePanel(state)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def entry(hass: HomeAssistant, panel: FakePanel) -> AsyncGenerator[ConfigEntry]:
    """The integration set up with the proposed (model-based) limits."""
    config_entry = await add_entry(hass, panel, limit_areas=None, limit_zones=None)
    yield config_entry
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_identified_as_10100(
    panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """The 10100 profile is used, with a warning that it is experimental."""
    client = create_client(panel.host, panel.port, "1234")
    try:
        with caplog.at_level(logging.WARNING):
            config = await client.get_initial_panel_configuration()
        assert client.panel.model is SMARTLIVING_10100_FW6
        assert "SmartLiving 10100 is experimental" in caplog.text
        assert client.panel.user_code_found
        assert config["errors"] == []
        assert config["system_info"]["type"] == "10100"
        assert config["areas"]["names"] == [f"Area {n}" for n in range(1, 16)]
        assert len(config["zones"]["zone_names"]) == 200
        assert len(config["zones_config"]["zones_config_detailed"]) == 200
        # scenario 3 arms area 15 only: all 15 areas are read from the record
        assert config["scenario_activations"][2]["area_actions"] == {
            **dict.fromkeys(range(1, 15), "keep"),
            15: "arm",
        }

        status = await client.get_live_status_snapshot()
        assert status["areas_status"]["area_modes"] == dict.fromkeys(
            range(1, 16), "disarmed"
        )
        assert len(status["zones_status"]["zone_statuses"]) == 200
        assert panel.protocol_errors == []
    finally:
        await client.close()


async def test_setup_proposes_every_area_and_zone(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The proposed limits are the panel's own: 15 areas, zone numbers 1-200."""
    assert entry.options["limit_areas"] == 15
    assert entry.options["limit_zones"] == 200
    for area in (1, 15):
        assert has_entity(hass, entry, ("switch", f"_area_switch_{area}"))
        assert has_entity(hass, entry, ("binary_sensor", f"_area_{area}_alarm_bs"))
    for zone, expected in ((1, True), (60, True), (61, False), (101, True)):
        assert has_entity(hass, entry, ("binary_sensor", f"_zone_{zone}")) is expected
    # terminal 100: a double terminal on slot 99, zones 100 and 200
    for zone in (100, 200):
        assert has_entity(hass, entry, ("switch", f"_zone_enabled_switch_{zone}"))
    assert not has_entity(hass, entry, ("binary_sensor", "_zone_102"))


async def test_area_15_and_scenarios(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Area 15 is armed from its switch and by a scenario; its alarm shows."""
    area_15 = ("switch", "_area_switch_15")
    await call(hass, entry, "switch", "turn_on", area_15)
    assert panel.state.armed_areas() == [15]
    await refresh(hass, entry)
    assert state_of(hass, entry, area_15) == "on"
    await call(hass, entry, "switch", "turn_off", area_15)
    assert panel.state.armed_areas() == []

    await call(hass, entry, "button", "press", ("button", "_scenario_button_2"))
    assert panel.state.armed_areas() == [15]

    panel.state.area_alarm = 1 << 14
    await refresh(hass, entry)
    assert state_of(hass, entry, ("binary_sensor", "_area_15_alarm_bs")) == "on"


async def test_second_zones_up_to_200(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Zone 200 has its own state and is bypassed as a second zone."""
    panel.state.zone_states[199] = c.ZONE_TAMPER
    await refresh(hass, entry)
    assert state_of(hass, entry, ("binary_sensor", "_zone_tamper_200")) == "on"
    assert state_of(hass, entry, ("binary_sensor", "_zone_tamper_100")) == "off"

    await call(
        hass, entry, "switch", "turn_off", ("switch", "_zone_enabled_switch_200")
    )
    assert panel.state.zone_included[199] is False
    assert panel.state.zone_included[99] is True


async def test_configuration_of_another_layout_is_read_again(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A 10100 stored with the 1050's layout is read again once at startup."""
    old = FakePanel()  # a 1050: its configuration has the 1050's sizes
    await old.start()
    client = create_client(old.host, old.port, "1234")
    try:
        stored = await client.get_initial_panel_configuration()
    finally:
        await client.close()
        await old.stop()

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=3,
        title="10100",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Big",
            DATA_INITIAL_PANEL_CONFIG: stored,
        },
        options={"limit_areas": 16, "limit_zones": 200, "limit_scenarios": 30},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert "does not fit the SmartLiving 10100" in caplog.text
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    assert len(config["areas"]["names"]) == 15
    assert len(config["zones_config"]["zones_config_detailed"]) == 200
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_area_switch_15"
    )

    # Read once: a restart finds a configuration that fits
    caplog.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "does not fit" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_1050_setup_proposes_its_own_counts(hass: HomeAssistant) -> None:
    """On a 1050 the proposed limits are 10 areas and zone numbers 1-100."""
    fake = FakePanel()
    await fake.start()
    try:
        entry = await add_entry(hass, fake, limit_areas=None, limit_zones=None)
        assert entry.options["limit_areas"] == 10
        assert entry.options["limit_zones"] == 100
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await fake.stop()


async def test_options_cap_an_old_area_limit(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """An area limit of 16 (allowed by older versions) is offered as 15."""
    entry = await add_entry(hass, panel, limit_areas=None, limit_zones=None)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "limit_areas": 16}
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {
        str(key): key.default()
        for key in result["data_schema"].schema
        if key.default is not vol.UNDEFINED
    }
    assert defaults["limit_areas"] == 15
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options["limit_areas"] == 15
    assert await hass.config_entries.async_unload(entry.entry_id)
