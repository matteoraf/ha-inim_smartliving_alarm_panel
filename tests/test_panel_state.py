"""Programming mode: commands and configuration reads wait until it has ended.

While the panel is in programming mode (keypad programming, or a reload of
its configuration) it ignores commands and keeps the previous command's
result, so a command sent then would look carried out when it was not.
"""

from collections.abc import AsyncGenerator

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.inim_smartliving_alarm.api import (
    ProgrammingModeError,
    SmartLivingPanel,
)
from custom_components.inim_smartliving_alarm.errors import PanelProgrammingError
from custom_components.inim_smartliving_alarm.protocol import registers as reg
from custom_components.inim_smartliving_alarm.protocol.commands import (
    ArmMode,
    arm_areas,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6 as SL1050,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import FakePanel, const as c
from .test_command_errors import failed_events
from .test_config_flow import board_form, si_input, submit, unload_all
from .test_init_baseline import (
    AREA_3_SWITCH,
    SCENARIO_3_BUTTON,
    ZONE_36_SWITCH,
    call,
    entity_id_for,
    refresh,
)
from .test_reconfigure_refresh import reread

pytestmark = pytest.mark.usefixtures("socket_enabled")

PROGRAMMING_SENSOR = ("binary_sensor", "_programming_mode")
COMMAND_REGISTERS = {
    c.RAM_CMD_ARM,
    c.RAM_CMD_OUTPUT,
    c.RAM_CMD_RESET,
    c.RAM_CMD_BYPASS,
    c.RAM_CMD_SCENARIO,
    c.RAM_CMD_STOP,
}


@pytest.fixture
async def make_panel(fake_panel: FakePanel) -> AsyncGenerator:
    """API objects for the single-client fake panel, closed afterwards."""
    panels: list[SmartLivingPanel] = []

    def make(**kwargs) -> SmartLivingPanel:
        transport = SmartLanSiTransport(
            fake_panel.host, fake_panel.port, reconnect_pause=0.01
        )
        panel = SmartLivingPanel(transport, "1234", command_settle=0, **kwargs)
        panels.append(panel)
        return panel

    yield make
    for panel in panels:
        await panel.close()


def commands_sent(fake: FakePanel) -> int:
    """How many commands reached the panel."""
    return len([r for r in fake.requests if r.write and r.address in COMMAND_REGISTERS])


def state_reads(fake: FakePanel) -> int:
    """How many times the panel state flags were read."""
    return len([r for r in fake.requests if not r.write and r.address == reg.FAULTS])


COMMANDS = {
    "arm": lambda panel: panel.arm_areas({4: ArmMode.AWAY}),
    "reset": lambda panel: panel.reset_alarm_memory(frozenset({4})),
    "bypass": lambda panel: panel.bypass_zone(17, bypass=True),
    "scenario": lambda panel: panel.activate_scenario(2, check_ready=False),
}


# ------------------------------------------------------------------- API


async def test_status_reads_the_flag(make_panel, fake_panel: FakePanel) -> None:
    """Every poll reads the state flags."""
    panel = make_panel()
    assert not (await panel.read_status()).panel_state.programming
    fake_panel.state.in_programming = True
    assert (await panel.read_status()).panel_state.programming


async def test_ignored_command_keeps_the_previous_result(
    make_panel, fake_panel: FakePanel
) -> None:
    """Without the check, an ignored command would look carried out."""
    panel = make_panel()
    await panel.identify()
    fake_panel.state.in_programming = True
    result = await panel.transport.command(arm_areas("1234", {4: ArmMode.AWAY}, SL1050))
    assert result.ok  # the previous command's result
    assert fake_panel.state.armed_areas() == []


@pytest.mark.parametrize("command", COMMANDS)
async def test_commands_not_sent_in_programming(
    make_panel, fake_panel: FakePanel, command: str
) -> None:
    """In programming mode nothing is sent."""
    panel = make_panel()
    await panel.identify()
    fake_panel.state.in_programming = True
    with pytest.raises(ProgrammingModeError) as err:
        await COMMANDS[command](panel)
    assert not err.value.sent
    assert commands_sent(fake_panel) == 0


@pytest.mark.parametrize("command", COMMANDS)
async def test_commands_checked_before_and_after(
    make_panel, fake_panel: FakePanel, command: str
) -> None:
    """Outside programming mode the flags are read before and after a command."""
    panel = make_panel()
    await panel.identify()
    before = state_reads(fake_panel)
    await COMMANDS[command](panel)
    assert commands_sent(fake_panel) == 1
    assert state_reads(fake_panel) - before == 2


async def test_programming_begins_during_a_command(
    make_panel, fake_panel: FakePanel
) -> None:
    """A command the panel may have ignored is not reported as carried out."""
    panel = make_panel()
    await panel.identify()
    fake_panel.state.enter_programming_on_command = True
    with pytest.raises(ProgrammingModeError) as err:
        await panel.arm_areas({4: ArmMode.AWAY})
    assert err.value.sent
    assert commands_sent(fake_panel) == 1
    assert fake_panel.state.armed_areas() == []


async def test_configuration_not_read_in_programming(
    make_panel, fake_panel: FakePanel
) -> None:
    """The configuration is not read in programming mode."""
    fake_panel.state.in_programming = True
    with pytest.raises(ProgrammingModeError):
        await make_panel().read_configuration()


async def test_programming_begins_during_the_configuration_read(
    make_panel, fake_panel: FakePanel
) -> None:
    """A configuration that may have changed while it was read is not used."""
    state = fake_panel.state
    eeprom_read = state.eeprom_read

    def read(address: int, length: int) -> bytes:
        if address == SL1050.scenario_records:  # the last part read
            state.in_programming = True
        return eeprom_read(address, length)

    state.eeprom_read = read
    with pytest.raises(ProgrammingModeError):
        await make_panel().read_configuration()


async def test_other_models_do_not_read_the_flags(
    make_panel, fake_panel: FakePanel
) -> None:
    """A panel handled with the 1050's layout: the flags may be elsewhere."""
    fake_panel.state.firmware = "6.07 00999  "
    fake_panel.state.in_programming = True
    panel = make_panel(fallback_model=SL1050)
    assert (await panel.read_status()).panel_state is None
    await panel.arm_areas({4: ArmMode.AWAY})
    assert commands_sent(fake_panel) == 1
    assert state_reads(fake_panel) == 0


# ------------------------------------------------------------ Home Assistant


async def test_programming_mode_sensor(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A diagnostic sensor shows programming mode."""
    entity_id = entity_id_for(hass, entry, PROGRAMMING_SENSOR)
    assert entity_id.endswith("_programming_mode")
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(entity_id).state == "off"

    panel.state.in_programming = True
    await refresh(hass, entry)
    assert hass.states.get(entity_id).state == "on"

    panel.state.in_programming = False
    await refresh(hass, entry)
    assert hass.states.get(entity_id).state == "off"


@pytest.mark.parametrize(
    ("service", "entity", "target"),
    [
        ("turn_on", AREA_3_SWITCH, "Area 3"),
        ("turn_off", ZONE_36_SWITCH, "Zone 36"),
    ],
)
async def test_switch_in_programming(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel: FakePanel,
    service: str,
    entity: tuple[str, str],
    target: str,
) -> None:
    """Switches say the command was not sent."""
    panel.state.in_programming = True
    with pytest.raises(PanelProgrammingError) as err:
        await call(hass, entry, "switch", service, entity)
    assert err.value.translation_key == "panel_programming"
    message = str(err.value)
    assert message.startswith(f"The command for {target}")
    assert "was not sent: the panel is in programming mode" in message
    assert commands_sent(panel) == 0


async def test_switch_programming_begins_during_the_command(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """A command that may have been ignored says so."""
    panel.state.enter_programming_on_command = True
    with pytest.raises(PanelProgrammingError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.translation_key == "panel_programming_started"
    assert "may not have been carried out" in str(err.value)
    assert panel.state.armed_areas() == []


async def test_scenario_in_programming(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The scenario-failed event and notification give the reason."""
    events = failed_events(hass)
    panel.state.in_programming = True
    with pytest.raises(PanelProgrammingError) as err:
        await call(hass, entry, "button", "press", SCENARIO_3_BUTTON)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0]["reason"] == "panel_programming"
    assert events[0]["panel_result"] is None
    assert events[0]["message"] == str(err.value)
    assert commands_sent(panel) == 0


async def test_reread_in_programming(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The configuration is not re-read in programming mode."""
    data = dict(entry.data)
    panel.state.in_programming = True
    with pytest.raises(HomeAssistantError) as err:
        await reread(hass, entry.entry_id)
    assert err.value.translation_key == "panel_programming_config"
    assert "the panel is in programming mode" in str(err.value).lower()
    assert dict(entry.data) == data


async def test_setup_in_programming(hass: HomeAssistant, panel: FakePanel) -> None:
    """Setup waits until programming has ended."""
    panel.state.in_programming = True
    flow_id = await board_form(hass, "smartlan_si")
    result = await submit(hass, flow_id, si_input(panel))
    assert result["errors"] == {"base": "panel_programming"}

    panel.state.in_programming = False
    result = await submit(hass, flow_id, si_input(panel))
    assert result["step_id"] == "items"
    await unload_all(hass)
