"""SmartLiving 505 and 515 on firmware 6, and panels without a profile.

The 505 and 515 profiles are checked against the protocol's capacity,
request-length and address tables; their fake panels are made up.
"""

from collections.abc import AsyncGenerator
import logging

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.protocol.errors import (
    UnsupportedPanelError,
    UnsupportedReason,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6 as SL505,
    SMARTLIVING_515_FW6 as SL515,
    model_for,
    parse_model_string,
)

from .conftest import add_entry
from .fake_panel import FakePanel, panel_505, panel_515
from .test_config_flow import unload_all
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")


# --------------------------------------------------------------- profiles


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            SL505,
            {
                "areas": 5,
                "terminals": 10,
                "zones": 20,
                "physical_slots": 40,
                "codes": 30,
                "keypads": 5,
                "readers": 10,
                "expanders": 4,
                "keys": 50,
                "timers": 10,
                "sirens": 10,
                "scenario_record_length": 5,
                "zone_record_length": 9,
                "code_record_length": 54,
                "code_output_mask_length": 3,
                "event_ring_size": 500,
                "pin_table": 0x03132,
                "code_records": 0x031F2,
                "zone_records": 0x028BD,
                "terminal_records": 0x02971,
                "physical_map": 0x03DE0,
                "scenario_records": 0x03B0A,
                "event_log": 0x0EE5C,
                "label_bank": 0,
                "area_records": 0x030E3,
            },
        ),
        (
            SL515,
            {
                "areas": 5,
                "terminals": 20,
                "zones": 40,
                "physical_slots": 70,
                "codes": 30,
                "keypads": 5,
                "readers": 10,
                "expanders": 10,
                "keys": 50,
                "timers": 10,
                "sirens": 10,
                "scenario_record_length": 5,
                "zone_record_length": 9,
                "code_record_length": 55,
                "code_output_mask_length": 4,
                "event_ring_size": 500,
                "pin_table": 0x038C7,
                "code_records": 0x03987,
                "zone_records": 0x02F07,
                "terminal_records": 0x0306F,
                "physical_map": 0x04595,
                "scenario_records": 0x042BF,
                "event_log": 0x0EE5C,
                "label_bank": 0,
                "area_records": 0x03859,
            },
        ),
    ],
)
def test_profile_values(model, expected: dict) -> None:
    """Counts, record sizes and addresses as the protocol tables give them."""
    assert {key: getattr(model, key) for key in expected} == expected
    assert model.experimental


@pytest.mark.parametrize(
    ("model", "lengths"),
    [
        (SL505, (13, 6, 6, 8, 3, 180)),
        (SL515, (13, 11, 12, 13, 6, 180)),
    ],
)
def test_request_lengths(model, lengths: tuple) -> None:
    """Area status, terminal state, inclusion, memory, not-ready, PIN table."""
    assert (
        model.area_status_length,
        model.zone_state_length,
        model.zone_inclusion_length,
        model.zone_memory_length,
        model.zone_bitmap_length,
        model.pin_table_length,
    ) == lengths


@pytest.mark.parametrize(
    ("text", "model"), [(b"6.07 00505  ", SL505), (b"6.12 00515  ", SL515)]
)
def test_model_for(text: bytes, model) -> None:
    """Firmware 6 505 and 515 have their own profile."""
    assert model_for(parse_model_string(text)) is model


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"5.11 00515  ", UnsupportedReason.FIRMWARE),
        (b"5.07 01050  ", UnsupportedReason.FIRMWARE),
        (b"4.20 01050  ", UnsupportedReason.FIRMWARE),
        (b"6.07 00999  ", UnsupportedReason.MODEL),
        (b"5.11 00999  ", UnsupportedReason.MODEL),  # the model is checked first
    ],
)
def test_unsupported_panels(text: bytes, reason) -> None:
    """Firmware below 6 and other models, with the reason."""
    with pytest.raises(UnsupportedPanelError) as err:
        model_for(parse_model_string(text))
    assert err.value.reason is reason


# ------------------------------------------------------------ fake panels


@pytest.fixture(params=["505", "515"])
async def small_panel(request, socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake 505 or 515."""
    fake = FakePanel(panel_505() if request.param == "505" else panel_515())
    await fake.start()
    yield fake
    await fake.stop()


async def test_setup(
    hass: HomeAssistant, small_panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """Set up end to end: 5 areas, the zones of the terminals, status polls."""
    with caplog.at_level(logging.WARNING):
        entry = await add_entry(hass, small_panel, zones=range(1, 41))
    terminals = small_panel.state.layout.terminals
    assert entry.state is ConfigEntryState.LOADED
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    assert len(config["areas"]["names"]) == 5
    assert len(config["zones"]["zone_names"]) == 2 * terminals  # 2L labels
    assert entry.options["areas"] == [1, 2, 3, 4, 5]
    # Every terminal is an input, none double: zones 1-L only
    assert entry.options["zones"] == list(range(1, terminals + 1))
    assert "is experimental" in caplog.text
    states = hass.states.async_all("binary_sensor")
    zone_sensors = [
        s
        for s in states
        if s.entity_id.endswith(tuple(f"_zone_{n}" for n in range(1, 101)))
    ]
    assert len(zone_sensors) == terminals
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_panel_{entry.entry_id}"
    )
    await unload_all(hass)


# ------------------------------------------------------------- unsupported


@pytest.mark.parametrize(
    ("firmware", "error"),
    [
        ("6.07 00999  ", "unsupported_model"),
        ("5.11 01050  ", "unsupported_firmware"),  # a known model on firmware 5
        ("5.11 00515  ", "unsupported_firmware"),
    ],
)
async def test_reconfigure_refuses_unsupported_panels(
    hass: HomeAssistant, panel: FakePanel, firmware: str, error: str
) -> None:
    """Reconfigure checks the panel as the setup does."""
    entry = await add_entry(hass, panel)
    panel.state.firmware = firmware
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": panel.host, "port": panel.port}
    )
    assert result["errors"] == {"base": error}
    await unload_all(hass)


def entry_for(panel: FakePanel, config: dict) -> MockConfigEntry:
    return MockConfigEntry(
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
        options={"areas": [1, 2], "zones": [1, 2, 45], "scenarios": [1]},
    )


async def test_existing_515_entry_is_read_again(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A 515 set up with the 1050 layout: read again once with its own."""
    stored = await stored_configuration(panel)  # read with the 1050 layout
    fake = FakePanel(panel_515())
    await fake.start()
    try:
        entry = entry_for(fake, stored)
        entry.add_to_hass(hass)
        with caplog.at_level(logging.WARNING):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert "does not fit the SmartLiving 515" in caplog.text
        config = entry.data[DATA_INITIAL_PANEL_CONFIG]
        assert len(config["zones"]["zone_names"]) == 40
        assert len(config["areas"]["names"]) == 5
        assert entry.options["zones"] == [1, 2, 45]  # the selection is kept
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_existing_unsupported_entry_keeps_running(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """Firmware 5 or another model set up earlier: runs, with a repair issue."""
    stored = await stored_configuration(panel)
    panel.state.firmware = "6.07 00999  "
    entry = entry_for(panel, stored)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_panel_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "unsupported_panel"
    assert issue.translation_placeholders == {
        "title": "Old",
        "panel": "6.07 00999",
    }
    await unload_all(hass)

    # Once the panel is supported the issue goes away
    panel.state.firmware = "6.07 01050  "
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_panel_{entry.entry_id}"
    )
    await unload_all(hass)
