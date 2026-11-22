"""Every entity's full state, in a set of panel states, against a stored file.

For each panel state below, the entity ID, unique ID, name, icon, device
class, entity category, disabled and hidden flags, state and all attributes
of every entity, and the panel's device entry, are compared with
tests/baselines/entity_snapshot_<state>.json. A change to what any entity
shows fails here; after an intended change, regenerate the files with:

    INIM_UPDATE_BASELINE=1 pytest tests/test_entity_snapshot.py

and explain each changed line in the pull request. Home Assistant derives
entity IDs and friendly names differently across its versions: they are
compared only on the version the files were generated with (recorded in
each file); everything else is compared on every version.

Entries stored by earlier releases come from tests/fixtures/entries: each
file is the entry a release stored for the fake 1050, captured by running
that release's own configuration read against it. They are inputs and are
never regenerated.
"""

from collections.abc import AsyncGenerator, Callable
import json
import os
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.json import JSONEncoder
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm.const import DOMAIN

from .conftest import ALL, add_entry
from .fake_panel import (
    EventRecord,
    FakePanel,
    PanelState,
    const as c,
    panel_1050,
    panel_1050_fw51,
    panel_10100,
)
from .test_config_flow import unload_all
from .test_init_baseline import (
    ALARM_PANEL,
    AREA_3_SWITCH,
    ZONE_36_SWITCH,
    call,
    refresh,
)
from .test_outputs import DIMMER, GATE, import_outputs, with_outputs

pytestmark = pytest.mark.usefixtures("socket_enabled")

BASELINES = Path(__file__).parent / "baselines"
ENTRIES = Path(__file__).parent / "fixtures" / "entries"
# The Home Assistant release the running tests use, as "year.month"
HA_RELEASE = ".".join(HA_VERSION.split(".")[:2])


# ------------------------------------------------------------------ snapshot


def _scrub(value: Any, replacements: dict[str, str]) -> Any:
    """The value with run-dependent strings (the entry ID) replaced."""
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            _scrub(key, replacements): _scrub(item, replacements)
            for key, item in value.items()
        }
    return value


def snapshot(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Every entity of the entry and the panel's device, as plain JSON values."""
    registry = er.async_get(hass)
    entities = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(entity.entity_id)
        entities.append(
            {
                "platform": entity.domain,
                "unique_id": entity.unique_id.removeprefix(entry.entry_id),
                "entity_id": entity.entity_id,
                "original_name": entity.original_name,
                "original_icon": entity.original_icon,
                "original_device_class": entity.original_device_class,
                "entity_category": entity.entity_category,
                "disabled_by": entity.disabled_by,
                "hidden_by": entity.hidden_by,
                "state": None if state is None else state.state,
                "attributes": None if state is None else dict(state.attributes),
            }
        )
    entities.sort(key=lambda item: (item["platform"], item["unique_id"]))
    devices = [
        {
            "name": device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "sw_version": device.sw_version,
            "hw_version": device.hw_version,
            "identifiers": sorted(
                list(identifier) for identifier in device.identifiers
            ),
        }
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    ]
    devices.sort(key=lambda device: device["identifiers"])
    plain = json.loads(
        json.dumps(
            {"home_assistant": HA_RELEASE, "devices": devices, "entities": entities},
            cls=JSONEncoder,
        )
    )
    return _scrub(plain, {entry.entry_id: "<entry_id>"})


def without_names(snapshot: dict) -> dict:
    """The snapshot without what Home Assistant's version decides."""
    entities = []
    for entity in snapshot["entities"]:
        entity = {key: value for key, value in entity.items() if key != "entity_id"}
        if entity["attributes"] is not None:
            entity["attributes"] = {
                key: value
                for key, value in entity["attributes"].items()
                if key != "friendly_name"
            }
        entities.append(entity)
    return {"devices": snapshot["devices"], "entities": entities}


def check(hass: HomeAssistant, entry: ConfigEntry, name: str) -> None:
    """Compare the snapshot with its file (or write it, to regenerate)."""
    current = snapshot(hass, entry)
    path = BASELINES / f"entity_snapshot_{name}.json"
    if os.environ.get("INIM_UPDATE_BASELINE"):
        path.write_text(
            json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored["home_assistant"] == HA_RELEASE:
        assert current == stored
    else:
        assert without_names(current) == without_names(stored)


@pytest.fixture(autouse=True)
async def rome_time_zone(hass: HomeAssistant) -> None:
    """Event times in a fixed time zone with daylight saving time."""
    await hass.config.async_set_time_zone("Europe/Rome")


async def start(state: PanelState) -> FakePanel:
    """A fake panel with this state, listening."""
    panel = FakePanel(state)
    await panel.start()
    return panel


@pytest.fixture
async def make_panel() -> AsyncGenerator[Callable]:
    """Start fake panels with a given state; stop them after the test."""
    panels: list[FakePanel] = []

    async def make(state: PanelState) -> FakePanel:
        panel = await start(state)
        panels.append(panel)
        return panel

    yield make
    for panel in panels:
        await panel.stop()


def stored_entry(
    hass: HomeAssistant,
    panel: FakePanel,
    release: str,
    change: Callable[[dict], None] | None = None,
) -> MockConfigEntry:
    """An entry as a release stored it, pointed at the fake panel."""
    fixture = json.loads((ENTRIES / f"{release}.json").read_text(encoding="utf-8"))
    data = {**fixture["data"], "host": panel.host, "port": panel.port}
    if change is not None:
        change(data["initial_panel_config"])
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=fixture["version"],
        minor_version=fixture["minor_version"],
        title=fixture["title"],
        data=data,
        options=fixture["options"],
    )
    entry.add_to_hass(hass)
    return entry


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up a stored entry and wait for its platforms."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def code_labels(state: PanelState) -> range:
    """EEPROM addresses of the user code names."""
    offset, count = state.layout.labels[c.RAM_LABELS_CODES]
    start = state.layout.label_bank + offset
    return range(start, start + count * 16)


# -------------------------------------------------------------------- states


async def test_a_fresh_setup(hass: HomeAssistant, make_panel: Callable) -> None:
    """(a) A fresh setup of the 1050, every area, zone and scenario, idle."""
    panel = await make_panel(panel_1050())
    entry = await add_entry(hass, panel)
    check(hass, entry, "a_fresh_setup")
    await unload_all(hass)


async def test_b_live_states(hass: HomeAssistant, make_panel: Callable) -> None:
    """(b) Areas armed in each mode; zones in alarm, tamper, bypassed, in test.

    Terminal 1 is a double terminal: zone 1 in tamper and zone 51 in short
    circuit, with the terminal's tamper memory.
    """
    state = panel_1050()
    state.terminal_types[0] = c.TERMINAL_DOUBLE
    panel = await make_panel(state)
    entry = await add_entry(hass, panel, zones=ALL)

    state.area_modes[0:4] = [c.MODE_AWAY, c.MODE_STAY, c.MODE_INSTANT, c.MODE_NONE]
    state.zone_states[19] = c.ZONE_ALARM
    state.zone_memory[19] = True
    state.zone_states[0] = c.ZONE_TAMPER
    state.zone_states[50] = c.ZONE_SHORT
    state.zone_memory_middle = bytes((0x01,)) + bytes(6)  # terminal 1
    state.zone_included[23] = False
    state.zone_inclusion_tail = bytes((0, 0, 0, 0x20)) + bytes(9)  # zone 30
    state.area_alarm = 1 << 0
    state.area_alarm_memory = 1 << 0 | 1 << 3
    state.area_tamper = 1 << 1
    state.area_tamper_memory = 1 << 1
    await refresh(hass, entry)
    check(hass, entry, "b_live_states")
    await unload_all(hass)


async def test_c_no_scenario_matches(hass: HomeAssistant, make_panel: Callable) -> None:
    """(c) Area 3 armed alone: the state of the areas matches no scenario."""
    state = panel_1050()
    panel = await make_panel(state)
    entry = await add_entry(hass, panel)
    state.area_modes[2] = c.MODE_AWAY
    await refresh(hass, entry)
    assert state.active_scenario_for(0x3FF) == c.NO_SCENARIO
    check(hass, entry, "c_no_scenario_matches")
    await unload_all(hass)


async def test_d_event_log(hass: HomeAssistant, make_panel: Callable) -> None:
    """(d) The captured events and a key on a reader, a remote scenario
    and an unknown event code, all shown (event log size 50).
    """
    state = panel_1050()
    planted = (
        # key 1 on reader 1 (location 10) arms area 3
        EventRecord(
            state.clock - 300, 0x0004, 0, 10, c.EVENT_AREA_ARMED | c.EVENT_ACTIVATION
        ),
        # scenario 4 activated remotely
        EventRecord(
            state.clock - 200,
            0x0000,
            3,
            c.LOCATION_NETWORK,
            c.EVENT_SCENARIO | c.EVENT_ACTIVATION,
        ),
        # a code with no description
        EventRecord(state.clock - 100, 0x0001, 0xFF, 0xFF, 0x7E),
    )
    for event in planted:
        state.events[state.event_head] = event
        state.event_head += 1
    panel = await make_panel(state)
    entry = await add_entry(hass, panel)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "event_log_size": 50}
    )
    await hass.async_block_till_done()
    check(hass, entry, "d_event_log")
    await unload_all(hass)


async def test_e_programming_mode(hass: HomeAssistant, make_panel: Callable) -> None:
    """(e) The panel is being programmed from a keypad."""
    state = panel_1050()
    panel = await make_panel(state)
    entry = await add_entry(hass, panel)
    state.in_programming = True
    await refresh(hass, entry)
    check(hass, entry, "e_programming_mode")
    await unload_all(hass)


async def test_f_unreachable(hass: HomeAssistant, make_panel: Callable) -> None:
    """(f) The panel cannot be reached after a good poll."""
    panel = await make_panel(panel_1050())
    entry = await add_entry(hass, panel)
    panel.refuse_connections = True
    panel.drop_client()
    await refresh(hass, entry)
    check(hass, entry, "f_unreachable")
    panel.refuse_connections = False
    await unload_all(hass)


async def test_g_10100(hass: HomeAssistant, make_panel: Callable) -> None:
    """(g) A 10100: 15 areas, 200 zones."""
    panel = await make_panel(panel_10100())
    entry = await add_entry(hass, panel, zones=ALL)
    check(hass, entry, "g_10100")
    await unload_all(hass)


async def test_h_firmware_51(hass: HomeAssistant, make_panel: Callable) -> None:
    """(h) A 1050 on firmware 5.1, with events in its own numbering.

    The scenario is activated through the alarm panel (Arm Away), not a
    button: a pressed button's state is the time of the press.
    """
    state = panel_1050_fw51()
    panel = await make_panel(state)
    entry = await add_entry(hass, panel)
    await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    await call(hass, entry, "alarm_control_panel", "alarm_arm_away", ALARM_PANEL)
    await call(hass, entry, "switch", "turn_off", ZONE_36_SWITCH)
    await refresh(hass, entry)
    check(hass, entry, "h_firmware_51")
    await unload_all(hass)


async def test_i_outputs(hass: HomeAssistant, make_panel: Callable) -> None:
    """(i) Terminal and fixed outputs on and off, a dimmer at a level."""
    state = with_outputs(panel_1050())
    panel = await make_panel(state)
    entry = await add_entry(hass, panel)
    await import_outputs(hass, entry, [41, 42, 43, 51, 52, 53])
    state.outputs[GATE] = 1
    state.outputs[DIMMER] = 30
    state.fixed_outputs[1] = True
    await refresh(hass, entry)
    check(hass, entry, "i_outputs")
    await unload_all(hass)


async def test_j_no_area_labels(hass: HomeAssistant, make_panel: Callable) -> None:
    """(j) A stored configuration without the area labels."""
    panel = await make_panel(panel_1050())

    def drop_areas(config: dict) -> None:
        del config["areas"]

    entry = stored_entry(hass, panel, "0.4.0b2", drop_areas)
    await set_up(hass, entry)
    check(hass, entry, "j_no_area_labels")
    await unload_all(hass)


@pytest.mark.parametrize("release", ["0.3.2", "0.4.0b2"])
@pytest.mark.parametrize("readable", [True, False], ids=["read", "unreadable"])
async def test_k_stored_by_a_release(
    hass: HomeAssistant, make_panel: Callable, release: str, readable: bool
) -> None:
    """(k) An entry stored by a release, at the first start after the update.

    Unreadable: the panel cannot deliver the user code names, so a
    configuration read at this start fails.
    """
    state = panel_1050()
    panel = await make_panel(state)
    if not readable:
        panel.unreadable_eeprom = [code_labels(state)]
    entry = stored_entry(hass, panel, release)
    await set_up(hass, entry)
    name = f"k_{release.replace('.', '_')}_{'read' if readable else 'unreadable'}"
    check(hass, entry, name)
    await unload_all(hass)


async def test_l_physical_slots(hass: HomeAssistant, make_panel: Callable) -> None:
    """(l) Zone 38's terminal only in the last physical slot (129); terminal 6
    in two slots (5 and 120).
    """
    state = panel_1050()
    state.terminal_types[37] = c.TERMINAL_INPUT
    state.zone_areas[37] = 1 << 2
    state.physical_map[129] = 37
    state.physical_map[120] = 5
    panel = await make_panel(state)
    entry = await add_entry(hass, panel, zones=ALL)
    check(hass, entry, "l_physical_slots")
    await unload_all(hass)
