"""The check for zones that are not ready, before arming and scenarios.

The fake 1050's zone 37 belongs to area 3 and zone 20 to area 1; scenario 1
arms areas 1-7, scenario 2 disarms them.
"""

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.inim_smartliving_alarm import readiness
from custom_components.inim_smartliving_alarm.api import (
    NotReadyError,
    SmartLivingPanel,
)
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.errors import (
    CommandRefusedError,
    ZonesNotReadyError,
)
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.config import (
    refuses_arming_when_not_ready,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.frames import (
    not_ready_area_request,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    FALLBACK_MODEL,
    SMARTLIVING_1050_FW6,
)
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .fake_panel import FakePanel, const as c
from .test_init_baseline import AREA_3_SWITCH, call, entity_id_for
from .test_item_selection import SERVICES, live_options

pytestmark = pytest.mark.usefixtures("socket_enabled")

EVENT = "inim_smartliving_alarm_zones_not_ready"
ZONE_37 = {"zone_id": 37, "zone_name": "Zone 37"}


async def set_option(hass: HomeAssistant, entry: ConfigEntry, value: str) -> None:
    """Set "When zones are not ready"."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "zones_not_ready": value}
    )
    await hass.async_block_till_done()


async def arm_area(
    hass: HomeAssistant, entry: ConfigEntry, areas: list[int], **extra: Any
) -> None:
    """Arm areas in total mode with the arm_area action."""
    await hass.services.async_call(
        DOMAIN,
        "arm_area",
        {"config_entry_id": entry.entry_id, "areas": areas, "mode": "total", **extra},
        blocking=True,
    )


async def activate(
    hass: HomeAssistant, entry: ConfigEntry, scenario: int, **extra: Any
) -> None:
    """Call the activate_scenario action."""
    await hass.services.async_call(
        DOMAIN,
        "activate_scenario",
        {"config_entry_id": entry.entry_id, "scenario": scenario, **extra},
        blocking=True,
    )


# ---------------------------------------------------------------- protocol


@pytest.mark.parametrize(
    ("area", "mode", "selector"),
    [(1, 1, 0x00), (3, 2, 0x22), (10, 3, 0x49)],
)
def test_area_request(area: int, mode: int, selector: int) -> None:
    """The 0-based area, and the mode less one in bits 5-6."""
    frame = not_ready_area_request(area, mode, SMARTLIVING_1050_FW6)
    assert frame[5] == selector  # high length byte
    assert frame[6] == SMARTLIVING_1050_FW6.zone_bitmap_length


@pytest.mark.parametrize(("area", "mode"), [(0, 1), (11, 1), (1, 0), (1, 4)])
def test_area_request_range(area: int, mode: int) -> None:
    """Areas of the model, and arming modes only."""
    with pytest.raises(ValueError):
        not_ready_area_request(area, mode, SMARTLIVING_1050_FW6)


def test_refuses_arming_when_not_ready() -> None:
    """Byte 1 bit 5: the panel refuses arming with zones not ready."""
    assert refuses_arming_when_not_ready(b"\x00\x20")
    assert refuses_arming_when_not_ready(b"\x30\x20\x00\x00\x00\x00")  # as read
    assert not refuses_arming_when_not_ready(b"\xff\xdf\xff\xff\xff\xff")
    with pytest.raises(ProtocolError):
        refuses_arming_when_not_ready(b"\x20")


# --------------------------------------------------------------------- API


def make_panel(fake: FakePanel) -> SmartLivingPanel:
    return SmartLivingPanel(SmartLanSiTransport(fake.host, fake.port), "1234")


async def test_api_area_check(fake_panel: FakePanel) -> None:
    """The policy gets the zones; False refuses, True sends."""
    fake_panel.state.zone_states[36] = c.ZONE_ALARM
    panel = make_panel(fake_panel)
    seen: list[frozenset[int]] = []
    try:
        with pytest.raises(NotReadyError) as err:
            await panel.arm_areas(
                {3: ArmMode.AWAY}, on_not_ready=lambda z: seen.append(z) or False
            )
        assert err.value.zones == {36}
        assert err.value.areas == {3}
        assert fake_panel.state.armed_areas() == []

        assert (await panel.arm_areas({3: ArmMode.AWAY}, on_not_ready=bool)).ok
        assert fake_panel.state.armed_areas() == [3]
        # Disarming is not checked
        assert (
            await panel.arm_areas(
                {3: ArmMode.DISARM}, on_not_ready=lambda z: pytest.fail("checked")
            )
        ).ok
    finally:
        await panel.close()
    assert seen == [frozenset({36})]


async def test_api_area_without_open_zones(fake_panel: FakePanel) -> None:
    """Nothing open: the policy is not asked."""
    panel = make_panel(fake_panel)
    try:
        result = await panel.arm_areas(
            {3: ArmMode.AWAY, 4: ArmMode.STAY}, on_not_ready=lambda z: False
        )
    finally:
        await panel.close()
    assert result.ok


@pytest.mark.parametrize("block", [False, True])
async def test_installer_option_stored(
    hass: HomeAssistant, panel: FakePanel, block: bool
) -> None:
    """Read with the configuration and stored."""
    from .conftest import add_entry

    panel.state.block_arming_when_not_ready = block
    entry = await add_entry(hass, panel)
    stored = entry.data[DATA_INITIAL_PANEL_CONFIG]
    # Raw bytes only, decoded where they are used
    assert stored["general_options"] == {
        "raw_hex": "002000000000" if block else "000000000000"
    }
    assert readiness.panel_refuses_not_ready(stored) is block
    assert await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------------------ refuse


async def test_switch_refused(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """By default: nothing is sent, the error and the event list the zones."""
    events = async_capture_events(hass, EVENT)
    panel.state.zone_states[36] = c.ZONE_ALARM
    with pytest.raises(ZonesNotReadyError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.zones == (37,)
    assert panel.state.armed_areas() == []
    assert len(events) == 1
    assert events[0].data == {
        "config_entry_id": entry.entry_id,
        "target": "Area 3",
        "action": "refused",
        "zones": [ZONE_37],
        "panel_refuses_not_ready": False,
    }


async def test_select_refused_but_disarm_allowed(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """The arming mode select is checked when arming, not when disarming."""
    select = entity_id_for(hass, entry, ("select", "_area_mode_select_3"))
    panel.state.area_modes[2] = c.MODE_AWAY
    panel.state.zone_states[36] = c.ZONE_ALARM
    with pytest.raises(ZonesNotReadyError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": select, "option": "armed_partial"},
            blocking=True,
        )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "disarmed"},
        blocking=True,
    )
    assert panel.state.armed_areas() == []


async def test_arm_area_force(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """force arms anyway, with the warning."""
    events = async_capture_events(hass, EVENT)
    panel.state.zone_states[36] = c.ZONE_ALARM
    with pytest.raises(ZonesNotReadyError):
        await arm_area(hass, entry, [3])
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await arm_area(hass, entry, [3], force=True)
    assert panel.state.armed_areas() == [3]
    assert [event.data["action"] for event in events] == ["refused", "armed_anyway"]
    assert "Zone 37 (37)" in note.call_args.args[1]


async def test_panel_refusal_stands(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Forced, the panel set to refuse still refuses."""
    panel.state.zone_states[36] = c.ZONE_ALARM
    panel.state.block_arming_when_not_ready = True
    with pytest.raises(CommandRefusedError) as err:
        await arm_area(hass, entry, [3], force=True)
    assert err.value.reason == "open_zones"
    assert panel.state.armed_areas() == []


# --------------------------------------------------------------------- warn


async def test_switch_warns(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Arm anyway and warn: armed, notification and event."""
    await set_option(hass, entry, "warn")
    events = async_capture_events(hass, EVENT)
    panel.state.zone_states[36] = c.ZONE_ALARM
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.armed_areas() == [3]
    assert events[0].data["action"] == "armed_anyway"
    message = note.call_args.args[1]
    assert "Zone 37 (37)" in message
    assert "set to arm with zones not ready" in message  # the panel's option


async def test_no_warning_when_ready(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Nothing open: no event, no notification."""
    await set_option(hass, entry, "warn")
    events = async_capture_events(hass, EVENT)
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert panel.state.armed_areas() == [3]
    assert not events
    note.assert_not_called()


async def test_scenario_button_warns(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Scenarios follow the same option."""
    await set_option(hass, entry, "warn")
    panel.state.zone_states[19] = c.ZONE_ALARM  # zone 20, area 1
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id_for(hass, entry, ("button", "_scenario_button_0"))},
            blocking=True,
        )
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]
    assert "Zone 20 (20)" in note.call_args.args[1]


# --------------------------------------------------------- activate_scenario


async def test_activate_scenario_action(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Refused with a zone open, activated with force."""
    panel.state.zone_states[19] = c.ZONE_ALARM
    with pytest.raises(ZonesNotReadyError):
        await activate(hass, entry, 1)
    assert panel.state.armed_areas() == []
    with patch.object(readiness.persistent_notification, "async_create"):
        await activate(hass, entry, 1, force=True)
    assert panel.state.armed_areas() == [1, 2, 3, 4, 5, 6, 7]
    await activate(hass, entry, 2)  # disarms
    assert panel.state.armed_areas() == []


async def test_activate_scenario_not_imported(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """A scenario not imported is named and nothing is sent."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "scenarios": [1, 2]}
    )
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await activate(hass, entry, 5)
    assert err.value.translation_key == "scenario_not_imported"


async def test_activate_scenario_lists_imported(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The action's form offers the imported scenarios by name."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "scenarios": [1, 3]}
    )
    await hass.async_block_till_done()
    assert live_options(hass, "activate_scenario", "scenario") == [
        {"value": "1", "label": "Scenario 1"},
        {"value": "3", "label": "Scenario 3"},
    ]
    live_options(hass, "arm_area", "areas")  # the rest as in services.yaml
    assert "force" in SERVICES["activate_scenario"]["fields"]
    assert "force" in SERVICES["arm_area"]["fields"]


async def test_option_in_general_settings(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Refuse by default; saved as chosen."""
    from .test_config_flow import form_defaults, open_options

    result = await open_options(hass, entry)
    assert form_defaults(result)["zones_not_ready"] == "refuse"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zones_not_ready": "warn"}
    )
    await hass.async_block_till_done()
    assert entry.options["zones_not_ready"] == "warn"


# ------------------------------------------------- panel refusal and zones


async def test_panel_refusal_event(
    hass: HomeAssistant, entry: ConfigEntry, panel: FakePanel
) -> None:
    """Sent anyway and refused by the panel: the event says so."""
    events = async_capture_events(hass, EVENT)
    panel.state.zone_states[36] = c.ZONE_ALARM
    panel.state.block_arming_when_not_ready = True
    with pytest.raises(CommandRefusedError):
        await arm_area(hass, entry, [3], force=True)
    assert [event.data["action"] for event in events] == ["refused_by_panel"]
    assert events[0].data["zones"] == [ZONE_37]


@pytest.mark.parametrize(
    ("label", "zone_name", "text"),
    [("Garage door", "Garage door", "Garage door (37)"), ("", "Zone 37", "37")],
)
async def test_zone_names(
    hass: HomeAssistant, panel: FakePanel, label: str, zone_name: str, text: str
) -> None:
    """The event gives the zone's plain name, as the zone entities do.

    The error message and the notification keep the zone number.
    """
    from .conftest import add_entry

    panel.state.labels[c.RAM_LABELS_ZONES][36] = label
    entry = await add_entry(hass, panel)
    events = async_capture_events(hass, EVENT)
    panel.state.zone_states[36] = c.ZONE_ALARM
    with pytest.raises(ZonesNotReadyError) as err:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert err.value.translation_placeholders["zones"] == text
    await set_option(hass, entry, "warn")
    with patch.object(readiness.persistent_notification, "async_create") as note:
        await call(hass, entry, "switch", "turn_on", AREA_3_SWITCH)
    assert [event.data["zones"] for event in events] == [
        [{"zone_id": 37, "zone_name": zone_name}]
    ] * 2
    assert f"- {text}\n" in note.call_args.args[1]
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("block", [False, True])
async def test_master_setting_sensor(
    hass: HomeAssistant, panel: FakePanel, block: bool
) -> None:
    """A diagnostic sensor shows the panel's own setting."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    from .conftest import add_entry

    panel.state.block_arming_when_not_ready = block
    entry = await add_entry(hass, panel)
    entity_id = entity_id_for(
        hass, entry, ("binary_sensor", "_refuses_arming_not_ready")
    )
    assert hass.states.get(entity_id).state == ("on" if block else "off")
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zone_option_attributes(hass: HomeAssistant, panel: FakePanel) -> None:
    """The zone programming flags are attributes of the zone sensor."""
    from .conftest import add_entry

    # Zone 1: interior and autobypass; options 2: no arming if not ready,
    # hold-up. Wireless-only bits (options 0xE0, options 2 0x80) not shown.
    panel.state.zone_options[0] = (0x03 | 0xE0, 0x02 | 0x20 | 0x80)
    entry = await add_entry(hass, panel)
    attributes = hass.states.get(
        entity_id_for(hass, entry, ("binary_sensor", "_zone_1"))
    ).attributes
    flags = {
        "interior": True,
        "autobypass": True,
        "unbypassable": False,
        "chime": False,
        "test_programmed": False,
        "autobypass_no_reinclude": False,
        "no_arming_if_not_ready": True,
        "entry_time_2": False,
        "last_exit_zone": False,
        "reinclude_on_disarm": False,
        "hold_up": True,
        "fault_zone": False,
    }
    assert {key: attributes[key] for key in flags} == flags
    zone_2 = hass.states.get(entity_id_for(hass, entry, ("binary_sensor", "_zone_2")))
    assert not any(zone_2.attributes[key] for key in flags)
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {"general_options": {}},
        {"general_options": {"raw_hex": "zz"}},
        {"general_options": {"raw_hex": "00"}},
    ],
)
def test_general_options_unknown(stored: dict[str, Any]) -> None:
    """Not stored (an earlier version) or not decodable: unknown."""
    assert readiness.panel_refuses_not_ready(stored) is None


def test_zone_flags_decoded_from_stored_records() -> None:
    """The stored model string gives the layout of the stored zone records."""
    from custom_components.inim_smartliving_alarm.selection import (
        stored_model,
        zone_option_flags,
    )

    model = SMARTLIVING_1050_FW6
    records = bytearray(model.zones * model.zone_record_length)
    records[3:5] = b"\x02\x02"  # zone 1: autobypass, no arming if not ready
    records[9 + 3] = 0x01  # zone 2: interior
    config = {
        "system_info": {"raw_hex": b"6.07 01050  ".hex()},
        "zones_config": {"raw_hex_part1_full_response": records.hex()},
    }
    assert stored_model(config) is model
    flags = zone_option_flags(config)
    assert len(flags) == model.zones
    assert flags[1]["autobypass"] and flags[1]["no_arming_if_not_ready"]
    assert not flags[1]["interior"]
    assert flags[2]["interior"] and not flags[2]["autobypass"]

    # Records that do not fit the model, an unknown model, nothing stored
    config["zones_config"]["raw_hex_part1_full_response"] = records[:-9].hex()
    assert zone_option_flags(config) == {}
    # A model without its own profile, or firmware 5: the fallback layout,
    # as the configuration was read with it
    for model_string in (b"6.07 00999  ", b"5.07 01050  "):
        config["system_info"]["raw_hex"] = model_string.hex()
        assert stored_model(config) is FALLBACK_MODEL
    config["system_info"]["raw_hex"] = b"not a model ".hex()
    assert stored_model(config) is None
    assert zone_option_flags({}) == {}
