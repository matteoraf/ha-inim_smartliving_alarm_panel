"""SmartLiving panels on firmware 5.1x.

The four profiles are checked against the panel's addresses for firmware
5.1x; the fake panels on firmware 5.12 are made up.
"""

from collections.abc import AsyncGenerator
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.inim_smartliving_alarm.api import create_panel
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.event_log import event_attributes
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.config import (
    decode_peripherals,
    refuses_arming_when_not_ready,
)
from custom_components.inim_smartliving_alarm.protocol.errors import (
    ProtocolError,
    UnsupportedPanelError,
    UnsupportedReason,
)
from custom_components.inim_smartliving_alarm.protocol.events import (
    decode_event_record,
    fw6_event_code,
    normalized_event_record,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW6,
    SMARTLIVING_515_FW51,
    SMARTLIVING_1050_FW6,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
    SMARTLIVING_10100_FW51,
    model_for,
    parse_model_string,
)
from custom_components.inim_smartliving_alarm.readiness import (
    panel_refuses_not_ready,
)
from custom_components.inim_smartliving_alarm.stored_config import (
    area_names,
    area_records,
    name_table,
    peripherals,
    raw_block,
    stored_layout_fits,
    stored_model,
    zone_names,
    zone_records,
)

from .conftest import add_entry
from .fake_panel import FakePanel, panel_505_fw51, panel_1050_fw51
from .test_config_flow import unload_all
from .test_model_profiles import entry_for
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

FW51 = (
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW51,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW51,
)
FW6 = (
    SMARTLIVING_505_FW6,
    SMARTLIVING_515_FW6,
    SMARTLIVING_1050_FW6,
    SMARTLIVING_10100_FW6,
)

# --------------------------------------------------------------- profiles


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            SMARTLIVING_505_FW51,
            {
                "name": "SmartLiving 505 (firmware 5.1)",
                "pin_table": 0x029DB,
                "code_records": 0x02A9B,
                "zone_records": 0x0217A,
                "terminal_records": 0x0222E,
                "physical_map": 0x03689,
                "scenario_records": 0x033B3,
                "area_records": 0x0298C,
                "event_log": 0x0EE5C,
                "event_ring_size": 500,
                "label_bank": 0,
            },
        ),
        (
            SMARTLIVING_515_FW51,
            {
                "name": "SmartLiving 515 (firmware 5.1)",
                "pin_table": 0x03058,
                "code_records": 0x03118,
                "zone_records": 0x026AC,
                "terminal_records": 0x02814,
                "physical_map": 0x03D26,
                "scenario_records": 0x03A50,
                "area_records": 0x02FEA,
                "event_log": 0x0EE5C,
                "event_ring_size": 500,
                "label_bank": 0,
            },
        ),
        (
            SMARTLIVING_1050_FW51,
            {
                "name": "SmartLiving 1050 (firmware 5.1)",
                "pin_table": 0x052AA,
                "code_records": 0x053E2,
                "zone_records": 0x0451A,
                "terminal_records": 0x0489E,
                "physical_map": 0x069B3,
                "scenario_records": 0x0648E,
                "area_records": 0x051DC,
                "event_log": 0x0EE5C,
                "event_ring_size": 500,
                "label_bank": 0,
            },
        ),
        (
            SMARTLIVING_10100_FW51,
            {
                "name": "SmartLiving 10100 (firmware 5.1)",
                "pin_table": 0x08BC4,
                "code_records": 0x08E28,
                "zone_records": 0x070E4,
                "terminal_records": 0x077EC,
                "physical_map": 0x0B66C,
                "scenario_records": 0x0AF16,
                "area_records": 0x08A62,
                "event_log": 0x19004,
                "event_ring_size": 1000,
                "label_bank": 0x10000,
            },
        ),
    ],
)
def test_profile_values(model, expected: dict) -> None:
    """Addresses, event log and label bank of each firmware 5.1 profile."""
    assert {key: getattr(model, key) for key in expected} == expected
    assert model.experimental
    assert model.peripherals_length == 20
    assert model.general_options_address == 0x00015
    assert model.setup_block_length == 27


@pytest.mark.parametrize(("fw51", "fw6"), zip(FW51, FW6, strict=True))
def test_counts_as_on_firmware_6(fw51, fw6) -> None:
    """Object counts and record sizes are the firmware 6 ones."""
    fields = (
        "areas",
        "terminals",
        "physical_slots",
        "codes",
        "keypads",
        "readers",
        "expanders",
        "keys",
        "timers",
        "sirens",
        "scenario_record_length",
        "zone_record_length",
        "code_record_length",
        "code_output_mask_length",
    )
    assert {f: getattr(fw51, f) for f in fields} == {f: getattr(fw6, f) for f in fields}
    assert (fw6.peripherals_length, fw6.setup_block_length) == (22, 29)
    assert fw6.event_codes is None


@pytest.mark.parametrize(
    ("text", "model"),
    [
        (b"5.10 00505  ", SMARTLIVING_505_FW51),
        (b"5.12 00515  ", SMARTLIVING_515_FW51),
        (b"5.19 01050  ", SMARTLIVING_1050_FW51),
        (b"5.12 10100  ", SMARTLIVING_10100_FW51),
        (b"6.07 01050  ", SMARTLIVING_1050_FW6),
    ],
)
def test_model_for(text: bytes, model) -> None:
    """The firmware family picks the profile."""
    assert model_for(parse_model_string(text)) is model


@pytest.mark.parametrize(
    ("version", "family"),
    [("6.07", "6"), ("6.10", "6"), ("5.12", "5.1"), ("5.07", None), ("4.20", None)],
)
def test_firmware_family(version: str, family: str | None) -> None:
    """6.x and 5.1x have a layout of their own; older firmware has none."""
    assert parse_model_string(f"{version} 01050  ".encode()).family == family


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"5.07 01050  ", UnsupportedReason.FIRMWARE),
        (b"5.00 00505  ", UnsupportedReason.FIRMWARE),
        (b"5.20 01050  ", UnsupportedReason.FIRMWARE),
        (b"5.12 00999  ", UnsupportedReason.MODEL),
    ],
)
def test_unsupported(text: bytes, reason) -> None:
    """5.0x stays refused; an unknown model is refused for its model."""
    with pytest.raises(UnsupportedPanelError) as err:
        model_for(parse_model_string(text))
    assert err.value.reason is reason


# ----------------------------------------------------------------- decoders


@pytest.mark.parametrize(
    ("code", "fw6"),
    [
        (0, 0),
        (10, 10),  # armed
        (11, 11),
        (12, 13),  # reset (firmware 6 has "area disarmed" at 12)
        (21, 22),  # valid key
        (29, 30),  # scenario activated
        (45, 46),
        (46, 48),
        (51, 53),
        (52, 55),
        (96, 99),
        (97, None),  # free events 1-14: none on firmware 6
        (107, None),
        (108, None),
        (110, None),
        (111, None),
    ],
)
def test_event_codes(code: int, fw6: int | None) -> None:
    """Firmware 5.1 event codes in firmware 6 numbering."""
    assert fw6_event_code(code, SMARTLIVING_1050_FW51) == fw6
    assert fw6_event_code(code, SMARTLIVING_1050_FW6) == code


def test_normalized_event_record() -> None:
    """The code byte is translated, the activation bit and the rest kept."""
    record = bytes.fromhex("97358832100000fe9d")  # scenario activated on 5.1
    assert normalized_event_record(record, SMARTLIVING_505_FW51) == (
        record[:8] + b"\x9e"
    )
    assert normalized_event_record(record, SMARTLIVING_505_FW6) == record
    free = record[:8] + bytes((100,))  # a free event that ended
    assert normalized_event_record(free, SMARTLIVING_505_FW51)[8] == 0x7F
    event = decode_event_record(free, SMARTLIVING_505_FW51)
    assert event.code is None
    assert not event.activation
    assert decode_event_record(record, SMARTLIVING_505_FW51).code == 30
    with pytest.raises(ProtocolError):
        normalized_event_record(record[:8], SMARTLIVING_505_FW51)


@pytest.mark.parametrize("model", [SMARTLIVING_1050_FW51, SMARTLIVING_1050_FW6])
@pytest.mark.parametrize("code", range(0, 128, 3))
def test_decoded_code_matches_normalized_record(model, code: int) -> None:
    """Decoding the raw record gives the code of the normalized record."""
    raw = bytes.fromhex("97358832100000fe") + bytes((0x80 | code,))
    normalized = normalized_event_record(raw, model)[8] & 0x7F
    decoded = decode_event_record(raw, model).code
    assert normalized == (0x7F if decoded is None else decoded)


def test_setup_block() -> None:
    """20 bytes of peripherals, then the options, 2 bytes lower than on 6."""
    block = bytearray(27)
    block[10] = 0b101  # keypads 1 and 3
    block[0x15 + 1] = 0x20  # refuse arming with zones not ready
    model = SMARTLIVING_1050_FW51
    peripherals = decode_peripherals(bytes(block[:20]), model)
    assert peripherals.keypads == (0, 2)
    options = block[model.general_options_address :][:6]
    assert refuses_arming_when_not_ready(bytes(options))
    with pytest.raises(ProtocolError):
        decode_peripherals(bytes(22), model)
    with pytest.raises(ProtocolError):
        decode_peripherals(bytes(20), SMARTLIVING_1050_FW6)


# ------------------------------------------------------------- fake panels


@pytest.fixture(params=["1050", "505"])
async def fw51_panel(request, socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake 1050 or 505 on firmware 5.12."""
    state = panel_1050_fw51() if request.param == "1050" else panel_505_fw51()
    state.block_arming_when_not_ready = True
    fake = FakePanel(state)
    await fake.start()
    yield fake
    await fake.stop()


async def test_configuration_read(fw51_panel: FakePanel) -> None:
    """Labels from bank 0, 20 bytes of peripherals, options at 0x0015."""
    config = await stored_configuration(fw51_panel)
    layout = fw51_panel.state.layout
    assert area_names(config)[:2] == ["Area 1", "Area 2"]
    assert len(zone_names(config)) == layout.zones
    assert name_table(config, "codes")[0] == "Master"
    assert len(raw_block(config, "peripherals")) == 20
    assert list(peripherals(config).keypads) == list(
        fw51_panel.state.peripherals["keypads"]
    )
    assert panel_refuses_not_ready(config) is True
    assert stored_model(config).name.endswith("(firmware 5.1)")
    assert area_records(config) is not None
    assert len(zone_records(config)) == layout.zones


async def test_events_in_firmware_6_numbering(fw51_panel: FakePanel) -> None:
    """Events the panel logs in its own numbering are described as on 6."""
    fw51_panel.state.block_arming_when_not_ready = False
    connection = create_panel(fw51_panel.host, fw51_panel.port, "1234")
    try:
        assert (await connection.arm_areas({1: ArmMode.AWAY})).ok
        assert (await connection.arm_areas({1: ArmMode.DISARM})).ok
        assert (await connection.activate_scenario(0)).ok
        events = (await connection.read_events(count=3)).events
    finally:
        await connection.close()
    shown = [event_attributes(logged, {}, None) for logged in events]
    assert [item["description"] for item in shown] == [
        "Scenario activated",
        "Area armed in away mode (ended)",  # 5.1 has no "area disarmed" event
        "Area armed in away mode",
    ]
    # Decoded in firmware 6 numbering, the raw bytes as the panel stored them
    assert events[0].event.action == 0x9E
    assert shown[0]["raw_hex_data"].endswith("9d")


async def test_event_record_read(fw51_panel: FakePanel, panel: FakePanel) -> None:
    """The API gives each record as read and in firmware 6 numbering."""
    for fake, translated in ((fw51_panel, True), (panel, False)):
        fake.state.block_arming_when_not_ready = False
        connection = create_panel(fake.host, fake.port, "1234")
        try:
            assert (await connection.activate_scenario(0)).ok
            head = fake.state.event_head
            read = await connection.read_event_record(head - 1)
        finally:
            await connection.close()
        assert read.raw[:8] == read.record[:8]
        assert read.record[8] == 0x9E  # scenario activated
        assert read.raw[8] == (0x9D if translated else 0x9E)


async def test_setup(
    hass: HomeAssistant, fw51_panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """Set up end to end, as experimental, without a repair issue."""
    with caplog.at_level(logging.WARNING):
        entry = await add_entry(hass, fw51_panel, zones=range(1, 101))
    layout = fw51_panel.state.layout
    assert entry.state is ConfigEntryState.LOADED
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    assert len(area_names(config)) == layout.areas
    assert len(entry.options["zones"]) == min(layout.terminals, 37)
    assert "(firmware 5.1) is experimental" in caplog.text
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_panel_{entry.entry_id}"
    )
    refuses = [
        state
        for state in hass.states.async_all("binary_sensor")
        if state.entity_id.endswith("_refuses_arming_with_zones_not_ready")
    ]
    assert [state.state for state in refuses] == ["on"]
    await unload_all(hass)


# --------------------------------------------------------- existing entries


def as_on_firmware_51(config: dict, model_string: bytes) -> dict:
    """A configuration read with the 1050 firmware 6 layout from a 5.1 panel."""
    return {**config, "model_string": model_string.hex()}


async def test_blocks_read_with_another_layout_are_not_decoded(
    panel: FakePanel,
) -> None:
    """Until it is read again, a 1050 on 5.1 stored with the 6 layout."""
    stored = await stored_configuration(panel)  # read with the 1050 FW 6 layout
    assert stored_layout_fits(stored, SMARTLIVING_1050_FW6)
    assert not stored_layout_fits(stored, SMARTLIVING_1050_FW51)
    old = as_on_firmware_51(stored, b"5.12 01050  ")
    assert stored_model(old) is None
    assert area_records(old) is None
    assert zone_records(old) is None
    assert panel_refuses_not_ready(old) is None


@pytest.mark.parametrize("builder", [panel_1050_fw51, panel_505_fw51])
async def test_existing_entry_is_read_again(
    hass: HomeAssistant,
    panel: FakePanel,
    builder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 5.1 panel set up with the 1050 firmware 6 layout: read again once.

    The 1050 has the same number of terminals as the layout it was read
    with: the peripherals block tells them apart. The repair issue the
    earlier version raised goes away.
    """
    fake = FakePanel(builder())
    await fake.start()
    try:
        stored = as_on_firmware_51(
            await stored_configuration(panel), fake.state.firmware.encode()
        )
        entry = entry_for(fake, stored)
        entry.add_to_hass(hass)
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"unsupported_panel_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unsupported_panel",
            translation_placeholders={"title": "Old", "panel": "x"},
        )
        with caplog.at_level(logging.WARNING):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert "(firmware 5.1) (it was read with another layout)" in caplog.text
        config = entry.data[DATA_INITIAL_PANEL_CONFIG]
        assert len(raw_block(config, "peripherals")) == 20
        assert len(zone_names(config)) == fake.state.layout.zones
        assert stored_model(config) is not None
        assert not ir.async_get(hass).async_get_issue(
            DOMAIN, f"unsupported_panel_{entry.entry_id}"
        )
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_firmware_6_entry_is_not_read_again(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """The peripherals check leaves a 1050 on firmware 6 alone."""
    entry = entry_for(panel, await stored_configuration(panel))
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "reading it again" not in caplog.text
    assert "configuration read again" not in caplog.text
    await unload_all(hass)
