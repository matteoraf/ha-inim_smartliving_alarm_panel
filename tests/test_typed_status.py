"""The typed live status and events the entities read: the protocol and API
additions, the event log items, the attribute texts and the coordinator."""

from collections.abc import Callable
import json
import logging
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
import pytest

from custom_components.inim_smartliving_alarm.api import (
    LoggedEvent,
    SmartLivingPanel,
)
from custom_components.inim_smartliving_alarm.attribute_values import (
    area_mode_text,
    area_status_text,
    zone_state_text,
)
from custom_components.inim_smartliving_alarm.const import DATA_COORDINATOR, DOMAIN
from custom_components.inim_smartliving_alarm.event_log import event_attributes
from custom_components.inim_smartliving_alarm.protocol.commands import ArmMode
from custom_components.inim_smartliving_alarm.protocol.events import (
    NO_FW6_CODE,
    EventPointers,
    PanelEvent,
    decode_event_record,
    indexes_to_read,
)
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6,
    SMARTLIVING_1050_FW51,
    PanelModel,
)
from custom_components.inim_smartliving_alarm.protocol.status import (
    AreaMode,
    ZoneMemory,
    ZoneState,
)
from custom_components.inim_smartliving_alarm.transport.base import TransportError

from .conftest import add_entry
from .fake_panel import FakePanel, PanelState, const as c, panel_1050, panel_1050_fw51
from .test_config_flow import unload_all
from .test_zones import stored_configuration

pytestmark = pytest.mark.usefixtures("socket_enabled")

BASELINES = Path(__file__).parent / "baselines"
ROME = ZoneInfo("Europe/Rome")
MODEL = SMARTLIVING_1050_FW6


# ---------------------------------------------------------------- protocol


def test_indexes_to_read() -> None:
    """The newest count events, or those written after an index; at most limit."""
    size = MODEL.event_ring_size
    fresh = EventPointers(head=5, tail=0)
    wrapped = EventPointers(head=2, tail=3)
    assert indexes_to_read(fresh, MODEL, count=3, limit=50) == [4, 3, 2]
    assert indexes_to_read(fresh, MODEL, count=0, limit=50) == [4]  # at least 1
    assert indexes_to_read(fresh, MODEL, count=99, limit=50) == [4, 3, 2, 1, 0]
    assert indexes_to_read(fresh, MODEL, after=2, limit=50) == [4, 3]
    assert indexes_to_read(fresh, MODEL, after=4, limit=50) == []
    assert indexes_to_read(fresh, MODEL, after=size, limit=50) == []  # not an index
    assert indexes_to_read(EventPointers(0, 0), MODEL, after=0, limit=50) == []
    assert indexes_to_read(wrapped, MODEL, after=size - 1, limit=50) == [1, 0]
    assert len(indexes_to_read(wrapped, MODEL, count=200, limit=50)) == 50
    with pytest.raises(ValueError):
        indexes_to_read(fresh, MODEL, limit=50)


def test_event_action_in_firmware_6_numbering() -> None:
    """The code byte with its activation bit; NO_FW6_CODE for a code firmware 6
    does not have."""
    record = bytes(4) + bytes((1, 0, 0xFF, 0xFE, 0x8A))
    assert decode_event_record(record, MODEL).action == 0x8A
    restoral = bytes(4) + bytes((1, 0, 0xFF, 0xFE, 0x0A))
    assert decode_event_record(restoral, MODEL).action == 0x0A
    event = PanelEvent(
        timestamp=decode_event_record(record, MODEL).timestamp,
        areas=frozenset(),
        agent=None,
        location=None,
        code=None,
        activation=True,
    )
    assert event.action == 0x80 | NO_FW6_CODE


def test_zone_tamper_memory_per_terminal() -> None:
    """Both zones of a terminal share its tamper memory."""
    memory = ZoneMemory(
        alarm_memory=(False,) * 4,
        terminal_tamper_memory=(False, True),
        outputs=(False, False),
        fixed_outputs=(False,) * 3,
    )
    assert [memory.zone_tamper_memory(zone) for zone in range(4)] == [
        False,
        True,
        False,
        True,
    ]


# ---------------------------------------------------------- attribute texts


def test_attribute_texts() -> None:
    """The area and zone texts the entities have always shown."""
    assert [area_mode_text(mode) for mode in AreaMode] == [
        "unknown",
        "total",
        "partial",
        "instant",
        "disarmed",
    ]
    assert [area_status_text(mode) for mode in AreaMode] == [
        "unknown",
        "armed",
        "armed",
        "armed",
        "disarmed",
    ]
    assert [zone_state_text(state) for state in ZoneState] == [
        "short",
        "clear",
        "alarmed",
        "tamper",
    ]


# ---------------------------------------------------------------- event log


def snapshot_events(state: str) -> list[dict]:
    """The event log items a snapshot recorded (made before this module)."""
    data = json.loads((BASELINES / f"entity_snapshot_{state}.json").read_text())
    sensor = next(e for e in data["entities"] if e["unique_id"].endswith("event_log"))
    return sensor["attributes"]["event_log"]


@pytest.mark.parametrize(
    ("state", "profile", "model"),
    [
        ("d_event_log", panel_1050, SMARTLIVING_1050_FW6),
        ("h_firmware_51", panel_1050_fw51, SMARTLIVING_1050_FW51),
    ],
)
async def test_event_items_as_recorded(
    state: str, profile: Callable[[], PanelState], model: PanelModel
) -> None:
    """Every recorded item is rebuilt key for key from its raw record."""
    fake = FakePanel(profile())
    await fake.start()
    try:
        stored = await stored_configuration(fake)
    finally:
        await fake.stop()
    items = snapshot_events(state)
    assert items
    for item in items:
        raw = bytes.fromhex(item["raw_hex_data"])
        logged = LoggedEvent(0, raw, decode_event_record(raw, model))
        assert event_attributes(logged, stored, ROME) == item


# -------------------------------------------------------------- coordinator


async def test_failed_status_read(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """A status read that fails fails the poll, with no error of its own."""
    entry = await add_entry(hass, panel)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    caplog.clear()
    with (
        caplog.at_level(logging.ERROR),
        patch.object(
            SmartLivingPanel, "read_status", side_effect=TransportError("gone")
        ),
    ):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert "Cannot read the panel status" not in caplog.text
    await unload_all(hass)


async def test_failed_event_read_keeps_the_poll(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """An event read that fails leaves the events out and keeps the status;
    it is logged once as a warning, then at debug, and the recovery once."""
    entry = await add_entry(hass, panel)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    index = coordinator.data.latest_event_index
    assert index is not None
    caplog.clear()
    with (
        caplog.at_level(logging.DEBUG, "custom_components.inim_smartliving_alarm"),
        patch.object(
            SmartLivingPanel, "read_events", side_effect=TransportError("gone")
        ),
    ):
        for _ in range(3):
            await coordinator.async_refresh()
            assert coordinator.last_update_success
            assert coordinator.data.new_events == ()
            assert coordinator.data.latest_event_index == index
    failures = [r for r in caplog.records if "Cannot read the event pointers" in r.msg]
    assert [record.levelno for record in failures] == [
        logging.WARNING,
        logging.DEBUG,
        logging.DEBUG,
    ]
    caplog.clear()
    with caplog.at_level(logging.INFO):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    assert caplog.text.count("Event pointers of") == 1
    await unload_all(hass)


async def test_events_are_shown_once(hass: HomeAssistant, panel: FakePanel) -> None:
    """A failed poll after a good one does not add that poll's events again."""
    entry = await add_entry(hass, panel)
    hass.config_entries.async_update_entry(  # reloads; more room than one read
        entry, options={**entry.options, "event_log_size": 100}
    )
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    log = next(
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_event_log")
    )

    def shown() -> int:
        return hass.states.get(log).attributes["event_log_count_current"]

    count = shown()
    assert count > 0
    await coordinator.panel.arm_areas({3: ArmMode.AWAY})
    await coordinator.async_refresh()  # one new event
    await hass.async_block_till_done()
    assert shown() == count + 1
    with patch.object(
        SmartLivingPanel, "read_status", side_effect=TransportError("gone")
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert shown() == count + 1
    await coordinator.async_refresh()  # back, with nothing new
    await hass.async_block_till_done()
    assert shown() == count + 1
    await unload_all(hass)


async def test_events_after_the_first_poll(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The first poll reads the log; later polls only what was added."""
    entry = await add_entry(hass, panel)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_refresh()
    assert coordinator.data.new_events == ()
    await coordinator.panel.arm_areas({3: ArmMode.AWAY})
    await coordinator.async_refresh()
    assert [logged.event.action for logged in coordinator.data.new_events] == [0x8A]
    await unload_all(hass)


# -------------------------------------------------------------- alarm panel


async def test_no_matching_scenario_is_custom_bypass(
    hass: HomeAssistant, panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """With a Disarm scenario mapped, no matching scenario is Armed custom
    bypass, logged once by its case; the sensor says so."""
    entry = await add_entry(hass, panel)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    panel.state.area_modes[2] = c.MODE_AWAY  # area 3 alone: no scenario
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert caplog.text.count("no scenario matches the areas' state") == 1
    states = {state.entity_id: state for state in hass.states.async_all()}
    alarm = next(s for e, s in states.items() if e.startswith("alarm_control_panel."))
    assert alarm.state == "armed_custom_bypass"
    sensor = next(s for e, s in states.items() if e.endswith("_active_scenario"))
    assert sensor.state == "No scenario matches"
    assert sensor.attributes["active_scenario_index"] is None
    assert sensor.attributes["raw_active_scenario_hex"] == f"{c.NO_SCENARIO:02x}"
    await unload_all(hass)
