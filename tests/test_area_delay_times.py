"""Each area's delays and options, shown on its arming mode select.

The bytes are the area records of a real SmartLiving 1050: the Garage (area
6) has 120 s to leave and to disarm, area 2 has 30 s, the others none.
"""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.inim_smartliving_alarm as integration
from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.protocol.config import (
    TimeUnit,
    decode_area_records,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6,
    SMARTLIVING_10100_FW6,
)
from custom_components.inim_smartliving_alarm.select import area_settings_attributes
from custom_components.inim_smartliving_alarm.selection import area_records
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import add_entry
from .fake_panel import FakePanel, panel_10100
from .test_config_flow import unload_all
from .test_init_baseline import entity_id_for
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

NONE = bytes.fromhex("000000030005a0")
A2 = bytes.fromhex("1e1e00030005a0")
GARAGE = bytes.fromhex("787800030005a0")
# The 70 bytes read from the real panel (areas 1-10)
REAL_1050 = NONE + A2 + NONE * 3 + GARAGE + NONE * 4


# ---------------------------------------------------------------- protocol


def test_real_area_records() -> None:
    """The real panel's ten records."""
    areas = decode_area_records(REAL_1050, SMARTLIVING_1050_FW6)
    assert len(areas) == 10
    garage, area_2, area_1 = areas[5], areas[1], areas[0]
    assert (garage.exit_time.value, garage.exit_time.unit) == (120, TimeUnit.SECONDS)
    assert garage.entry_time.value == 120
    assert garage.entry_time_2 is None  # not used
    assert (area_2.exit_time.value, area_2.entry_time.value) == (30, 30)
    assert area_1.exit_time.value == 0
    for area in areas:
        assert (area.pre_arm_warning, area.patrol) == (3, 5)
        assert area.timer_program is None
        assert area.reset_alarm_memory_on_arming
        assert not area.auto_arm_partial
        assert area.clear_call_queue_on_disarm


def test_minutes_entry_time_2_timer_and_options() -> None:
    """Bit 7 of a time: minutes; entry time 2 when not 0; timer bits 0-3."""
    record = bytes((0x82, 0x1E, 0x85, 1, 0xFF, 10, 0x43))
    area = decode_area_records(record * 10, SMARTLIVING_1050_FW6)[0]
    assert (area.exit_time.value, area.exit_time.unit) == (2, TimeUnit.MINUTES)
    assert (area.entry_time_2.value, area.entry_time_2.unit) == (5, TimeUnit.MINUTES)
    assert area.timer_program == 3
    assert area.auto_arm_partial
    assert not area.reset_alarm_memory_on_arming
    assert not area.clear_call_queue_on_disarm


def test_area_records_length() -> None:
    """A records of 7 bytes."""
    with pytest.raises(ProtocolError):
        decode_area_records(REAL_1050[:-1], SMARTLIVING_1050_FW6)


def test_attributes() -> None:
    """Short units; "not used" and "none" where nothing is set."""
    garage = decode_area_records(REAL_1050, SMARTLIVING_1050_FW6)[5]
    assert area_settings_attributes(garage) == {
        "exit_time": "120 s",
        "entry_time": "120 s",
        "entry_time_2": "not used",
        "pre_arm_warning_time": "3 min",
        "patrol_time": "5 min",
        "timer_program": "none",
        "reset_alarm_memory_on_arming": True,
        "auto_arm_partial": False,
        "clear_call_queue_on_disarm": True,
    }
    other = bytes((0x82, 0x1E, 0x85, 1, 0, 10, 0x43)) * 10
    area = decode_area_records(other, SMARTLIVING_1050_FW6)[0]
    attributes = area_settings_attributes(area)
    assert attributes["exit_time"] == "2 min"
    assert attributes["entry_time_2"] == "5 min"
    assert attributes["timer_program"] == 3


# --------------------------------------------------------------------- API


@pytest.mark.parametrize(("ten_thousand", "count"), [(False, 10), (True, 15)])
async def test_read_with_the_configuration(ten_thousand: bool, count: int) -> None:
    """One more read, at the model's address, A x 7 bytes."""
    fake = FakePanel(panel_10100()) if ten_thousand else FakePanel()
    fake.state.area_records[1] = GARAGE
    await fake.start()
    panel = SmartLivingPanel(SmartLanSiTransport(fake.host, fake.port), "1234")
    try:
        config = await panel.read_configuration()
    finally:
        await panel.close()
        await fake.stop()
    assert len(config.area_records) == count
    assert len(config.area_records_raw) == count * 7
    assert config.area_records[1].exit_time.value == 120
    assert config.area_records[0].pre_arm_warning == 3
    model = SMARTLIVING_10100_FW6 if ten_thousand else SMARTLIVING_1050_FW6
    assert config.model is model


# --------------------------------------------------------------- selection


def stored(model_string: bytes, records: bytes | None = REAL_1050) -> dict:
    config: dict = {"system_info": {"raw_hex": model_string.hex()}}
    if records is not None:
        config["area_records"] = {"raw_hex": records.hex()}
    return config


def test_stored_area_records() -> None:
    """Decoded from the stored block; none when missing or not fitting."""
    assert area_records(stored(b"6.07 01050  "))[5].exit_time.value == 120
    assert area_records(stored(b"6.07 01050  ", None)) is None
    assert area_records(stored(b"6.07 01050  ", REAL_1050[:-7])) is None
    assert area_records({}) is None


@pytest.mark.parametrize("model_string", [b"6.07 00999  ", b"5.10 01050  "])
def test_fallback_models_show_no_delays(model_string: bytes) -> None:
    """Handled with the fallback layout: the bytes are stored, not shown."""
    assert area_records(stored(model_string)) is None


# ------------------------------------------------------------------ entity


async def test_select_attributes(hass: HomeAssistant, panel: FakePanel) -> None:
    """The real panel's areas on the fake 1050."""
    for index in range(10):
        panel.state.area_records[index] = REAL_1050[index * 7 : index * 7 + 7]
    entry = await add_entry(hass, panel)
    garage = hass.states.get(
        entity_id_for(hass, entry, ("select", "_area_mode_select_6"))
    )
    assert garage.attributes["exit_time"] == "120 s"
    assert garage.attributes["entry_time"] == "120 s"
    assert garage.attributes["entry_time_2"] == "not used"
    assert garage.attributes["area_id"] == 6
    area_2 = hass.states.get(
        entity_id_for(hass, entry, ("select", "_area_mode_select_2"))
    )
    assert area_2.attributes["exit_time"] == "30 s"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def old_entry(hass: HomeAssistant, panel: FakePanel) -> MockConfigEntry:
    """An entry stored by an earlier version, without the area records."""
    config = await stored_configuration(panel)
    del config["area_records"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=5,
        title="Old",
        data={
            "host": panel.host,
            "port": panel.port,
            "pin": "1234",
            "board": "smartlan_si",
            "panel_name": "Old",
            DATA_INITIAL_PANEL_CONFIG: config,
        },
        options={"areas": [6], "zones": [1], "scenarios": [1]},
    )
    entry.add_to_hass(hass)
    return entry


async def test_old_entry_is_read_again(hass: HomeAssistant, panel: FakePanel) -> None:
    """Without the area records the configuration is read again once."""
    panel.state.area_records[5] = GARAGE
    entry = await old_entry(hass, panel)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "area_records" in entry.data[DATA_INITIAL_PANEL_CONFIG]
    state = hass.states.get(
        entity_id_for(hass, entry, ("select", "_area_mode_select_6"))
    )
    assert state.attributes["exit_time"] == "120 s"
    await unload_all(hass)


async def test_old_entry_without_a_re_read(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The re-read fails: the select shows no delays, nothing breaks."""
    entry = await old_entry(hass, panel)
    with patch.object(
        integration,
        "async_fetch_panel_configuration",
        side_effect=ConnectionError("unreachable"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(
        entity_id_for(hass, entry, ("select", "_area_mode_select_6"))
    )
    assert "exit_time" not in state.attributes
    assert state.attributes["area_name"] == "Area 6"
    await unload_all(hass)
