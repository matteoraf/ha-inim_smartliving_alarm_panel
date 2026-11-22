"""What the integration reads from a stored panel configuration.

For each fake panel profile (the configuration a fresh setup stores) and
for each entry an earlier release stored (tests/fixtures/entries, after the
update's migration), everything the integration takes from the stored
configuration is compared with tests/baselines/stored_config_view_<name>.json:
the decoded areas, zones, scenarios, outputs and names, which items exist and
are offered, each zone's terminal and physical slot, what a new setup
proposes, and the system information. However the configuration is stored,
these must stay the same unless a change is intended. After an intended
change, regenerate the files with:

    INIM_UPDATE_BASELINE=1 pytest tests/test_stored_config_view.py

and explain each changed line in the pull request.
"""

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_smartliving_alarm import async_migrate_entry
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.legacy import zone_exists
from custom_components.inim_smartliving_alarm.panel_view import configuration_view
from custom_components.inim_smartliving_alarm.selection import (
    available_areas,
    available_outputs,
    available_scenarios,
    available_zones,
    output_items,
    scenario_area_modes,
    suggested_mappings,
    suggested_selection,
    zone_details,
)

from .fake_panel import (
    FakePanel,
    PanelState,
    const as c,
    panel_505,
    panel_505_fw51,
    panel_515,
    panel_1050,
    panel_1050_fw51,
    panel_10100,
)
from .test_outputs import with_outputs
from .test_zones import stored_configuration

BASELINES = Path(__file__).parent / "baselines"
ENTRIES = Path(__file__).parent / "fixtures" / "entries"


def reader_view(config: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the integration reads from a stored configuration."""
    zones = []
    for detail in zone_details(config):
        if "zone_index" not in detail:
            continue
        zones.append(
            {
                "number": detail["zone_index"] + 1,
                "exists": zone_exists(detail),
                "areas": detail.get("assigned_areas"),
                "terminal": detail.get("terminal_index"),
                "terminal_type": detail.get("terminal_type_val"),
                "second_zone": detail.get("second_zone"),
                "slot": detail.get("internal_index"),
            }
        )
    system_info = config.get("system_info") or {}
    proposed = suggested_selection(config)
    return {
        "configuration": configuration_view(config),
        "system_info": {key: system_info.get(key) for key in ("version", "type")},
        "available": {
            "areas": available_areas(config),
            "zones": available_zones(config),
            "scenarios": available_scenarios(config),
            "outputs": available_outputs(config),
        },
        "suggested_selection": proposed,
        "suggested_mappings": suggested_mappings(config, proposed),
        "zones": zones,
        "scenario_modes": {
            str(number): {str(area): int(mode) for area, mode in modes.items()}
            for number, modes in scenario_area_modes(config).items()
        },
        "output_addresses": {
            str(item.number): item.address for item in output_items(config)
        },
    }


def check(view: dict[str, Any], name: str) -> None:
    """Compare the view with its file (or write it, to regenerate)."""
    current = json.loads(json.dumps(view))
    path = BASELINES / f"stored_config_view_{name}.json"
    if os.environ.get("INIM_UPDATE_BASELINE"):
        path.write_text(
            json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert current == json.loads(path.read_text(encoding="utf-8"))


def physical_slots(state: PanelState) -> PanelState:
    """Terminal 38 only in the last physical slot; terminal 6 in two slots."""
    state.terminal_types[37] = c.TERMINAL_INPUT
    state.zone_areas[37] = 1 << 2
    state.physical_map[129] = 37
    state.physical_map[120] = 5
    return state


PROFILES: dict[str, Callable[[], PanelState]] = {
    "1050": panel_1050,
    "1050_outputs": lambda: with_outputs(panel_1050()),
    "1050_physical_slots": lambda: physical_slots(panel_1050()),
    "10100": panel_10100,
    "505": panel_505,
    "515": panel_515,
    "1050_fw51": panel_1050_fw51,
    "505_fw51": panel_505_fw51,
}


@pytest.mark.parametrize("profile", PROFILES)
async def test_fresh_setup(socket_enabled: None, profile: str) -> None:
    """The configuration a fresh setup stores for each fake panel profile."""
    panel = FakePanel(PROFILES[profile]())
    await panel.start()
    try:
        config = await stored_configuration(panel)
    finally:
        await panel.stop()
    check(reader_view(config), profile)


@pytest.mark.parametrize(
    "release", sorted(path.stem for path in ENTRIES.glob("*.json"))
)
async def test_entry_stored_by_a_release(hass: HomeAssistant, release: str) -> None:
    """An entry a release stored, after the update's migration (no panel)."""
    fixture = json.loads((ENTRIES / f"{release}.json").read_text(encoding="utf-8"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=fixture["version"],
        minor_version=fixture["minor_version"],
        title=fixture["title"],
        data=fixture["data"],
        options=fixture["options"],
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    check(
        reader_view(entry.data[DATA_INITIAL_PANEL_CONFIG]),
        f"release_{release.replace('.', '_')}",
    )
