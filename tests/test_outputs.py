"""The panel's outputs: terminal outputs, O1-O3 and dimmers.

Switching is only tested on the fake panel.
"""

from collections.abc import AsyncGenerator, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.protocol.commands import set_output
from custom_components.inim_smartliving_alarm.protocol.config import (
    OutputConfig,
    OutputKind,
    PhysicalMap,
    TerminalType,
    code_output_mask,
    decode_fixed_outputs,
    decode_home_automation,
    decode_output_mask,
    decode_terminals,
    fixed_output_number,
    output_address,
    terminal_output_number,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.frames import (
    output_detail_request,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW6,
    SMARTLIVING_1050_FW6 as SL1050,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    decode_output_level,
)
from custom_components.inim_smartliving_alarm.selection import (
    available_outputs,
    output_items,
    output_label,
)

from .conftest import add_entry, items_form, offered, proposed
from .fake_panel import (
    FakePanel,
    PanelState,
    captures as cap,
    const as c,
    panel_1050,
    panel_1050_fw51,
)
from .test_config_flow import board_form, open_options, si_input, submit, unload_all
from .test_model_profiles import entry_for
from .test_protocol_decoders import data_of
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

GATE, STRIKE, DIMMER, LOW_SLOT, NOT_ALLOWED = 40, 41, 42, 43, 44  # terminals

# ----------------------------------------------------------------- decoders


@pytest.mark.parametrize(
    ("time", "options", "dimmer_type", "kind", "monostable_time"),
    [
        (0x00, 0x00, False, OutputKind.ON_OFF, None),
        (0x1E, 0x03, False, OutputKind.MONOSTABLE, (30, "seconds")),
        (0x82, 0x02, False, OutputKind.MONOSTABLE, (2, "minutes")),
        (0x00, 0x40, False, OutputKind.DIMMER, None),
        (0x00, 0x00, True, OutputKind.DIMMER, None),
        (0x05, 0x42, False, OutputKind.DIMMER, None),  # dimmer wins
    ],
)
def test_output_kinds(time, options, dimmer_type, kind, monostable_time) -> None:
    """Kind and monostable time from the time and options bytes."""
    config = OutputConfig(time=time, options=options, dimmer_type=dimmer_type)
    assert config.kind is kind
    found = config.monostable_time
    assert (
        None if found is None else (found.value, found.unit.value)
    ) == monostable_time


def test_fixed_outputs_as_read_on_a_1050() -> None:
    """O1 a 30 s normally closed monostable relay, O2 and O3 plain."""
    o1, o2, o3 = decode_fixed_outputs(bytes.fromhex("1e0300000000"))
    assert o1.kind is OutputKind.MONOSTABLE
    assert o1.flags["normally_closed"]
    assert o1.monostable_time.value == 30
    assert o2.kind is o3.kind is OutputKind.ON_OFF
    with pytest.raises(ProtocolError):
        decode_fixed_outputs(bytes(5))


@pytest.mark.parametrize(
    ("terminal_type", "switched"),
    [(t, t in (1, 2, 5)) for t in range(9)],
)
def test_terminal_outputs(terminal_type: int, switched: bool) -> None:
    """Only on/off, input/output and dimmer terminals are switched outputs."""
    record = bytearray(12)
    record[9], record[10], record[11] = 0x05, 0x02, terminal_type
    terminals = decode_terminals(bytes(record) * SL1050.terminals, SL1050)
    output = terminals[0].output
    assert (output is not None) is switched
    if switched:
        assert output.time == 0x05
        assert output.kind is (
            OutputKind.DIMMER
            if terminal_type == TerminalType.OUT_DIMMING
            else OutputKind.MONOSTABLE
        )


@pytest.mark.parametrize(
    "model",
    [
        SMARTLIVING_505_FW6,
        SMARTLIVING_515_FW6,
        SL1050,
        SMARTLIVING_10100_FW6,
        SMARTLIVING_505_FW51,
        SMARTLIVING_1050_FW51,
    ],
)
def test_output_numbers(model) -> None:
    """Terminals 1-L, then O1-O3; O1-O3 are always OUTPUT 0-2."""
    terminals = model.terminals
    by_slot = [None] * model.physical_slots
    by_slot[1] = 4  # terminal 5 only in slot 1: cannot be switched
    by_slot[7] = 6  # terminal 7 in slot 7
    physical = PhysicalMap(tuple(by_slot))
    assert terminal_output_number(6, model) == 7
    for fixed in (1, 2, 3):
        number = fixed_output_number(fixed, model)
        assert number == terminals + fixed
        assert output_address(number, physical, model) == fixed - 1
    assert output_address(7, physical, model) == 7
    for number in (5, 9, 0, terminals + 4):  # slot < 3, no slot, out of range
        with pytest.raises(ValueError):
            output_address(number, physical, model)


def test_output_command_frames() -> None:
    """OUTPUT and VALUE bytes of the command."""
    assert set_output("1234", 1, 1, SL1050).payload[6:] == bytes((1, 1))
    assert set_output("1234", 40, 0, SL1050).payload[6:] == bytes((40, 0))
    assert set_output("1234", 42, 75, SL1050).payload[6:] == bytes((42, 75))


def test_output_detail() -> None:
    """The request carries the code index and terminal; FW 6 only."""
    assert output_detail_request(2, 28, SL1050).hex() == "0002001ffd001c3a"
    with pytest.raises(ValueError):
        output_detail_request(0, 1, SMARTLIVING_1050_FW51)
    assert decode_output_level(bytes((7, 0, 0, 0, 0, 0, 50, 1))) == 50
    assert decode_output_level(bytes((7, 0, 0, 0, 0, 0, 0xB2, 1))) == 50  # bit 7
    assert decode_output_level(b"\xff" * 8) is None  # no detail: not 0
    assert decode_output_level(bytes((7, 0, 0, 0, 0, 0, 101, 1))) is None
    with pytest.raises(ProtocolError):
        decode_output_level(bytes(7))


def test_captured_output_mask() -> None:
    """The master code of the captures may drive O1-O3 and no terminal."""
    record = data_of(cap.CODE_RECORD_0_REPLY)
    mask = code_output_mask(record, SL1050)
    assert decode_output_mask(mask, SL1050) == (frozenset(), frozenset({1, 2, 3}))


# --------------------------------------------------------------- fake panel


def with_outputs(state: PanelState) -> PanelState:
    """Terminal outputs 41-45 and O1-O3, as a test panel.

    Terminal 41 on/off (Gate), 42 a 2 s monostable (Door strike), 43 a
    dimmer (Lounge), 44 on physical slot 2 only (cannot be switched), 45 not
    allowed to the code; O1 a 30 s normally-closed monostable (Siren relay),
    O2 on/off (Garden), O3 unnamed. Code 0 may drive O1-O3 and 41-44.
    """
    for terminal in (GATE, STRIKE, NOT_ALLOWED, LOW_SLOT):
        state.terminal_types[terminal] = c.TERMINAL_OUTPUT
    state.terminal_types[DIMMER] = c.TERMINAL_OUT_DIMMING
    state.output_programming[STRIKE] = (0x02, 0x02)  # 2 s, monostable
    for terminal in (GATE, STRIKE, DIMMER, NOT_ALLOWED):
        state.physical_map[terminal] = terminal
    state.physical_map[2] = LOW_SLOT
    zones = state.labels.setdefault(c.RAM_LABELS_ZONES, [])
    zones += [""] * (c.NUM_ZONES - len(zones))
    zones[GATE], zones[STRIKE], zones[DIMMER] = "Gate", "Door strike", "Lounge"
    state.labels[c.RAM_LABELS_OUTPUTS] = ["Siren relay", "Garden", ""]
    state.fixed_output_records = bytes.fromhex("1e0300000000")
    record = bytearray(state.code_records[0])
    record[c.CODE_OUTPUT_MASK_OFFSET] = 0x07  # O1-O3
    record[c.CODE_OUTPUT_MASK_OFFSET + 1 + GATE // 8] = 0x0F  # terminals 40-43
    state.code_records[0] = bytes(record)
    return state


@pytest.fixture
async def outputs_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """The fake 1050 with outputs."""
    fake = FakePanel(with_outputs(panel_1050()))
    await fake.start()
    yield fake
    await fake.stop()


async def import_outputs(
    hass: HomeAssistant, entry: ConfigEntry, outputs: list[int]
) -> dict:
    """Tick these outputs in the options (the rest as it is)."""
    result = await open_options(hass, entry, "items")
    chosen = {"areas": None, "zones": None, "scenarios": None, "outputs": outputs}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], items_form(result, chosen)
    )
    await hass.async_block_till_done()
    return result


def unique(entry: ConfigEntry, suffix: str) -> str:
    return f"{entry.entry_id}_{suffix}"


def entity_of(hass: HomeAssistant, platform: str, entry: ConfigEntry, suffix: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, unique(entry, suffix)
    )
    return None if entity_id is None else hass.states.get(entity_id)


def output_commands(fake: FakePanel) -> list[bytes]:
    """OUTPUT and VALUE of every output command the panel received."""
    return [
        request.payload[6:8]
        for request in fake.requests
        if request.write and request.address == 0x2007
    ]


def detail_requests(fake: FakePanel) -> list[int]:
    """Terminals of the output detail requests the panel received."""
    return [
        request.length & 0xFF
        for request in fake.requests
        if not request.write and request.address == c.RAM_OUTPUT_DETAIL
    ]


# ------------------------------------------------------------------ offering


async def test_offered_and_not_proposed(
    hass: HomeAssistant, outputs_panel: FakePanel
) -> None:
    """Switchable outputs the code may drive are offered; none is ticked."""
    config = await stored_configuration(outputs_panel)
    assert available_outputs(config) == [41, 42, 43, 51, 52, 53]
    assert [item.number for item in output_items(config)] == [
        41,
        42,
        43,
        44,
        45,
        51,
        52,
        53,
    ]
    assert output_label(config, 42) == "42 - Door strike (monostable 2 s)"
    assert output_label(config, 43) == "43 - Lounge (dimmer)"
    assert output_label(config, 51) == "O1 - Siren relay (monostable 30 s)"
    assert output_label(config, 53) == "O3 (on/off)"

    entry = await add_entry(hass, outputs_panel)
    assert entry.options["outputs"] == []
    assert not hass.states.async_all("light")
    assert not [s for s in hass.states.async_all("switch") if "output" in s.entity_id]
    await unload_all(hass)


async def test_captured_panel_offers_o1_to_o3(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The captured 1050 has no terminal output; its master may drive O1-O3."""
    config = await stored_configuration(panel)
    assert available_outputs(config) == [51, 52, 53]  # the master may drive them
    old = {k: v for k, v in config.items() if k != "user_code_outputs"}
    assert available_outputs(old) == []  # stored by an earlier version


# ------------------------------------------------------------------ entities


async def test_entities_per_kind(hass: HomeAssistant, outputs_panel: FakePanel) -> None:
    """Switches for on/off and monostable outputs, a light for the dimmer."""
    entry = await add_entry(hass, outputs_panel)
    await import_outputs(hass, entry, [41, 42, 43, 51, 52, 53])
    gate = entity_of(hass, "switch", entry, "terminal_output_41")
    strike = entity_of(hass, "switch", entry, "terminal_output_42")
    lounge = entity_of(hass, "light", entry, "terminal_output_43")
    siren = entity_of(hass, "switch", entry, "fixed_output_1")
    o3 = entity_of(hass, "switch", entry, "fixed_output_3")
    assert gate.state == "off"
    assert gate.attributes["friendly_name"].endswith("Gate")
    assert gate.attributes["kind"] == "on_off"
    assert gate.attributes["terminal_index"] == GATE
    assert strike.attributes["kind"] == "monostable"
    assert strike.attributes["monostable_time"] == "2 s"
    assert lounge.attributes["kind"] == "dimmer"
    assert siren.attributes["fixed_output"] == 1
    assert siren.attributes["normally_closed"] is True
    assert siren.attributes["monostable_time"] == "30 s"
    assert o3.attributes["friendly_name"].endswith("O3")
    assert output_commands(outputs_panel) == []  # nothing switched by itself

    # Unticked: their entities go, including the fixed outputs'
    await import_outputs(hass, entry, [41])
    assert entity_of(hass, "switch", entry, "fixed_output_1") is None
    assert entity_of(hass, "light", entry, "terminal_output_43") is None
    assert entity_of(hass, "switch", entry, "terminal_output_41") is not None
    await unload_all(hass)


async def test_switching(hass: HomeAssistant, outputs_panel: FakePanel) -> None:
    """Terminal outputs by physical slot, O1-O3 as 0-2; state from the poll."""
    entry = await add_entry(hass, outputs_panel)
    await import_outputs(hass, entry, [41, 42, 52])
    gate = entity_of(hass, "switch", entry, "terminal_output_41").entity_id
    strike = entity_of(hass, "switch", entry, "terminal_output_42").entity_id
    garden = entity_of(hass, "switch", entry, "fixed_output_2").entity_id

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": gate}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": garden}, blocking=True
    )
    await hass.async_block_till_done()
    assert outputs_panel.state.outputs[GATE] == 1
    assert outputs_panel.state.fixed_outputs == [False, True, False]
    assert output_commands(outputs_panel) == [bytes((GATE, 1)), bytes((1, 1))]
    assert hass.states.get(gate).state == "on"
    assert hass.states.get(garden).state == "on"

    # A monostable output switches itself off: the next poll shows it off
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": strike}, blocking=True
    )
    assert hass.states.get(strike).state == "on"
    outputs_panel.state.outputs[STRIKE] = 0
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(strike).state == "off"

    # Already off: no error
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": strike}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": gate}, blocking=True
    )
    assert outputs_panel.state.outputs[GATE] == 0
    assert hass.states.get(gate).state == "off"
    await unload_all(hass)


@pytest.mark.parametrize(
    ("change", "key"),
    [
        (lambda s: setattr(s, "outputs_locked", True), "refused_areas_armed"),
        (lambda s: drop_from_mask(s, GATE), "refused_output_not_enabled"),
        (lambda s: setattr(s, "in_programming", True), "panel_programming"),
    ],
)
async def test_refusals(
    hass: HomeAssistant,
    outputs_panel: FakePanel,
    change: Callable[[PanelState], None],
    key: str,
) -> None:
    """Refusals and programming mode give their messages."""
    entry = await add_entry(hass, outputs_panel)
    await import_outputs(hass, entry, [41])
    gate = entity_of(hass, "switch", entry, "terminal_output_41").entity_id
    change(outputs_panel.state)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": gate}, blocking=True
        )
    assert err.value.translation_key.startswith(key)
    assert outputs_panel.state.outputs.get(GATE, 0) == 0
    await unload_all(hass)


def drop_from_mask(state: PanelState, terminal: int) -> None:
    record = bytearray(state.code_records[0])
    record[c.CODE_OUTPUT_MASK_OFFSET + 1 + terminal // 8] &= ~(1 << terminal % 8)
    state.code_records[0] = bytes(record)


# ------------------------------------------------------------------ dimmers


async def test_dimmer_level_from_the_panel(
    hass: HomeAssistant, outputs_panel: FakePanel
) -> None:
    """Level sent as 0-100, read back at every poll and after the command."""
    entry = await add_entry(hass, outputs_panel)
    assert detail_requests(outputs_panel) == []  # no dimmer imported
    await import_outputs(hass, entry, [41, 43])
    lounge = entity_of(hass, "light", entry, "terminal_output_43").entity_id
    assert hass.states.get(lounge).state == "off"
    outputs_panel.requests.clear()

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": lounge, "brightness": 128}, blocking=True
    )
    await hass.async_block_till_done()
    assert outputs_panel.state.outputs[DIMMER] == 50
    assert bytes((DIMMER, 50)) in output_commands(outputs_panel)
    assert set(detail_requests(outputs_panel)) == {DIMMER}  # only the dimmer
    state = hass.states.get(lounge)
    assert state.state == "on"
    assert state.attributes["brightness"] == 128

    # Changed at a keypad: the next poll shows the new level
    outputs_panel.state.outputs[DIMMER] = 30
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(lounge).attributes["brightness"] == 76

    # The panel gives no detail (0xFF): unknown, the last level set is shown
    drop_from_mask(outputs_panel.state, DIMMER)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(lounge).attributes["brightness"] == 128

    await unload_all(hass)


async def test_dimmer_on_firmware_51(hass: HomeAssistant, socket_enabled) -> None:
    """No level read on firmware 5.1: the brightness is the last one set."""
    fake = FakePanel(with_outputs(panel_1050_fw51()))
    await fake.start()
    try:
        entry = await add_entry(hass, fake)
        await import_outputs(hass, entry, [43])
        lounge = entity_of(hass, "light", entry, "terminal_output_43").entity_id
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": lounge, "brightness": 64}, blocking=True
        )
        await hass.async_block_till_done()
        assert fake.state.outputs[DIMMER] == 25
        assert detail_requests(fake) == []
        assert hass.states.get(lounge).attributes["brightness"] == 64
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": lounge}, blocking=True
        )
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": lounge}, blocking=True
        )  # the last level again
        assert fake.state.outputs[DIMMER] == 25
        await unload_all(hass)
    finally:
        await fake.stop()


# ------------------------------------------------------------ existing entry


async def test_existing_entry_is_read_again_and_imports_none(
    hass: HomeAssistant, outputs_panel: FakePanel
) -> None:
    """A configuration from an earlier version: read again once, no output."""
    stored = await stored_configuration(outputs_panel)
    for key in (
        "fixed_output_records",
        "fixed_output_labels",
        "physical_map",
        "user_code_outputs",
        "extra_output_options",
    ):
        del stored[key]
    entry = entry_for(outputs_panel, stored)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    config = entry.data[DATA_INITIAL_PANEL_CONFIG]
    assert config["fixed_output_records"] == "1e0300000000"
    assert available_outputs(config) == [41, 42, 43, 51, 52, 53]
    assert "outputs" not in entry.options
    assert not hass.states.async_all("light")
    assert output_commands(outputs_panel) == []
    await unload_all(hass)


# ---------------------------------------------------------- home automation


def test_home_automation_flags() -> None:
    """One byte per output (terminals, then O1-O3), bit 0; FW 6 only."""
    data = bytes([1, 0, 0x81] + [0] * (SL1050.terminals - 3) + [0, 1, 0])
    flags = decode_home_automation(data, SL1050)
    assert flags[:3] == (True, False, True)
    assert flags[SL1050.terminals :] == (False, True, False)
    with pytest.raises(ProtocolError):
        decode_home_automation(data[:-1], SL1050)
    assert (
        SMARTLIVING_505_FW6.extra_output_options,
        SMARTLIVING_515_FW6.extra_output_options,
        SL1050.extra_output_options,
        SMARTLIVING_10100_FW6.extra_output_options,
    ) == (0x04CCE, 0x054BF, 0x08C7D, 0x0E7B9)
    assert SMARTLIVING_1050_FW51.extra_output_options is None
    assert SMARTLIVING_505_FW51.extra_output_options is None


def flagged(state: PanelState) -> PanelState:
    """Home automation: Gate, the low-slot and the not-allowed terminals, O2."""
    state.home_automation = {GATE, LOW_SLOT, NOT_ALLOWED, state.layout.terminals + 1}
    return state


async def test_home_automation_outputs_are_proposed(
    hass: HomeAssistant, socket_enabled
) -> None:
    """Only flagged outputs that are offered: Gate and O2."""
    fake = FakePanel(flagged(with_outputs(panel_1050())))
    await fake.start()
    try:
        config = await stored_configuration(fake)
        assert output_label(config, 41) == "41 - Gate (on/off, home automation)"
        assert output_label(config, 52) == "O2 - Garden (on/off, home automation)"

        flow_id = await board_form(hass, "smartlan_si")
        result = await submit(hass, flow_id, si_input(fake))
        assert result["step_id"] == "items"
        assert offered(result, "outputs") == [41, 42, 43, 51, 52, 53]
        assert proposed(result, "outputs") == [41, 52]
        chosen = {"areas": None, "zones": None, "scenarios": None, "outputs": None}
        result = await submit(hass, flow_id, items_form(result, chosen))
        result = await submit(hass, flow_id, {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.options["outputs"] == [41, 52]
        gate = entity_of(hass, "switch", entry, "terminal_output_41")
        assert gate.attributes["home_automation"] is True
        o2 = entity_of(hass, "switch", entry, "fixed_output_2")
        assert o2.attributes["home_automation"] is True
        assert output_commands(fake) == []  # proposed, never switched
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_options_propose_them_the_first_time(
    hass: HomeAssistant, socket_enabled
) -> None:
    """An entry without saved outputs: proposed once; then the saved choice."""
    fake = FakePanel(flagged(with_outputs(panel_1050())))
    await fake.start()
    try:
        entry = entry_for(fake, await stored_configuration(fake))
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert not hass.states.async_all("light")
        result = await open_options(hass, entry, "items")
        assert proposed(result, "outputs") == [41, 52]
        hass.config_entries.options.async_abort(result["flow_id"])

        await import_outputs(hass, entry, [])  # the user unticks them
        result = await open_options(hass, entry, "items")
        assert proposed(result, "outputs") == []
        hass.config_entries.options.async_abort(result["flow_id"])
        await unload_all(hass)
    finally:
        await fake.stop()


async def test_no_home_automation_on_firmware_51(
    hass: HomeAssistant, socket_enabled
) -> None:
    """The flag is not known on 5.1: nothing proposed, no attribute."""
    fake = FakePanel(flagged(with_outputs(panel_1050_fw51())))
    await fake.start()
    try:
        config = await stored_configuration(fake)
        assert config["extra_output_options"] == ""
        assert available_outputs(config) == [41, 42, 43, 51, 52, 53]
        entry = await add_entry(hass, fake)
        await import_outputs(hass, entry, [41])
        gate = entity_of(hass, "switch", entry, "terminal_output_41")
        assert "home_automation" not in gate.attributes
        assert "home automation" not in output_label(config, 41)
        await unload_all(hass)
    finally:
        await fake.stop()
