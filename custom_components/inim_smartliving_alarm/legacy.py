"""The panel's status and events, in the dictionaries the entities use.

The coordinator data keeps its shape while the panel is accessed through
the asynchronous API. Values are derived from the raw replies with the same
rules as before, unless a later change says otherwise.

Errors are reported the old way: a dictionary marking the failure, never an
exception.
"""

from datetime import tzinfo
import logging
from typing import Any

from .api import (
    PanelStatus,
    SmartLivingPanel,
)
from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    ZONE_STATE_ALARMED,
    ZONE_STATE_CLEAR,
    ZONE_STATE_SHORT,
    ZONE_STATE_TAMPER,
)
from .protocol import registers as reg
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .protocol.events import (
    decode_event_pointers,
    decode_timestamp,
    newest_indexes,
)
from .protocol.model import PanelModel
from .protocol.status import AreaMode, AreaStatus, ZoneState
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)

MAX_EVENTS_PER_FETCH = 50

EVENT_ACTIONS = {
    0x9E: "Scenario Activated",
    0x80: "Area/Zone Alarm",
    0x00: "Area/Zone Alarm End",
    0x8A: "Area(s) Armed",
    0x8C: "Area(s) Disarmed",
    0x8D: "Area(s) Reset",
    0x85: "Zone Excluded",
    0x05: "End of Zone Excluded",
    0x0A: "End of Area(s) Armed",
    0x0C: "End of Area(s) Disarmed",
    0xBA: "Installer Code Inserted",
    0xC2: "Programming Started",
    0x42: "Programming Ended",
    0x9B: "Failed Call",
    0x1B: "End of Failed Call",
    0x95: "Valid Code",
    0x96: "Valid Key",
}
_AREA_EVENTS = (0x8A, 0x8C, 0x8D, 0x0A, 0x0C, 0x95, 0x96)
_ZONE_EVENTS = (0x80, 0x00, 0x85, 0x05)
_AREA_MODES = {
    AreaMode.AWAY: AREA_MODE_TOTAL,
    AreaMode.STAY: AREA_MODE_PARTIAL,
    AreaMode.INSTANT: AREA_MODE_INSTANT,
    AreaMode.DISARMED: AREA_MODE_DISARMED,
}
_ZONE_STATES = {
    ZoneState.SHORT: ZONE_STATE_SHORT,
    ZoneState.STANDBY: ZONE_STATE_CLEAR,
    ZoneState.ALARM: ZONE_STATE_ALARMED,
    ZoneState.TAMPER: ZONE_STATE_TAMPER,
}

_ERRORS = (TransportError, ProtocolError, UnsupportedPanelError, ValueError)


class LegacyPanelClient:
    """Status and event reads in the integration's dictionaries; async."""

    def __init__(self, panel: SmartLivingPanel) -> None:
        """Read from this panel."""
        self.panel = panel

    # ------------------------------------------------------------------ status

    async def get_live_status_snapshot(self) -> dict[str, Any]:
        """Areas, zones and active scenario; marks a failure as before."""
        try:
            status = await self.panel.read_status()
        except _ERRORS as err:
            _LOGGER.error("Cannot read the panel status: %s", err)
            return {
                "error": "Connection failure",
                "areas_status": None,
                "zones_status": None,
                "zones_excluded_status": None,
                "zones_triggered_status": None,
                "active_scenario": None,
            }
        return status_dict(status, self.panel.model)

    # ------------------------------------------------------------------ events

    async def execute_get_compact_events(
        self,
        count: int | None = None,
        last_processed_compact_event_index_val: int | None = None,
        time_zone: tzinfo | None = None,
    ) -> dict[str, Any]:
        """The newest events (count) or those after a known index, newest first.

        Indexes are positions in the panel's event ring (0 to ring size - 1,
        wrapping around); latest_event_index_val is the newest event's. Event
        times are the panel's local time in time_zone (without one, they
        carry no offset).
        """
        try:
            pointers_data = await self.panel.read_ram(
                reg.EVENT_POINTERS, reg.EVENT_POINTERS_LENGTH
            )
            model = self.panel.model
            pointers = decode_event_pointers(pointers_data, model)
        except _ERRORS as err:
            _LOGGER.error("Cannot read the event pointers: %s", err)
            return {"events": [], "latest_event_index_val": None}

        size = model.event_ring_size
        newest = newest_indexes(pointers, model, 1)
        latest = newest[0] if newest else None
        last = last_processed_compact_event_index_val
        if count is not None:
            wanted = min(max(1, count), MAX_EVENTS_PER_FETCH)
        elif last is not None:
            if latest is None or not 0 <= last < size:
                return {"events": [], "latest_event_index_val": latest}
            # events written since the last poll; the ring wraps around
            wanted = min((latest - last) % size, MAX_EVENTS_PER_FETCH)
        else:
            _LOGGER.error("Either count or the last processed index is needed")
            return {"events": [], "latest_event_index_val": latest}

        events: list[dict[str, Any]] = []
        for index in newest_indexes(pointers, model, wanted):
            try:
                read = await self.panel.read_event_record(index)
            except _ERRORS as err:
                _LOGGER.info("Stopped reading events at %s: %s", index, err)
                break
            event = event_dict(read.record, model, time_zone)
            event["raw_hex_data"] = read.raw.hex()  # as the panel stored it
            event["query_index_hex"] = f"{index:04x}"
            event["query_index_val"] = index
            events.append(event)
        return {"events": events, "latest_event_index_val": latest}


def _areas(mask: int, model: PanelModel) -> list[int]:
    return [area for area in range(1, model.areas + 1) if mask >> (area - 1) & 1]


# -------------------------------------------------------------------- status


def status_dict(status: PanelStatus, model: PanelModel) -> dict[str, Any]:
    """The live status in the coordinator's dictionaries.

    Zones are numbered 1-2L: L+n is the second zone of terminal n when that
    terminal is a double one. Which zones exist is decided by the panel
    configuration, not here: every zone number is reported.
    """
    raw = status.raw
    memory = status.zone_memory
    terminals = model.terminals
    return {
        "areas_status": _area_status(raw[reg.AREA_STATUS], status.areas),
        "zones_status": {
            "raw_hex_data": raw[reg.ZONE_STATE].hex(),
            "zone_statuses": {
                zone + 1: _ZONE_STATES[state]
                for zone, state in enumerate(status.zone_states)
            },
        },
        "zones_excluded_status": {
            "raw_hex_data": raw[reg.ZONE_INCLUSION].hex(),
            "zone_excluded_statuses": _flags(
                status.zone_inclusion.included, "enabled", "disabled"
            ),
            "zone_test_statuses": _flags(status.zone_inclusion.test, True, False),
        },
        "zones_triggered_status": {
            "raw_hex_data": raw[reg.ZONE_MEMORY].hex(),
            "zone_triggered_statuses": _flags(
                memory.alarm_memory, "triggered", "clear"
            ),
            # per terminal; both zones of a double terminal share it
            "zone_tamper_memory_statuses": {
                zone + 1: memory.terminal_tamper_memory[zone % terminals]
                for zone in range(model.zones)
            },
        },
        "active_scenario": {
            "raw_hex": raw[reg.ACTIVE_SCENARIO].hex(),
            "active_scenario_number": raw[reg.ACTIVE_SCENARIO][0],
        },
        # None when the panel's state flags are not read (model not known)
        "panel_programming": (
            None if status.panel_state is None else status.panel_state.programming
        ),
        "errors": [],
    }


def _flags(bits: tuple[bool, ...], set_value: Any, clear_value: Any) -> dict[int, Any]:
    return {
        zone + 1: set_value if bit else clear_value for zone, bit in enumerate(bits)
    }


def _area_status(data: bytes, areas: AreaStatus) -> dict[str, Any]:
    """Modes and masks of every area; "armed" stands for any armed mode."""
    statuses = {}
    modes = {}
    for area, mode in areas.modes.items():
        modes[area] = _AREA_MODES.get(mode, "unknown")
        statuses[area] = (
            "armed"
            if mode.armed
            else "disarmed"
            if mode is AreaMode.DISARMED
            else "unknown"
        )
    return {
        "raw_hex": data.hex(),
        "area_statuses": statuses,
        "area_modes": modes,
        "alarm_areas": sorted(areas.alarm),
        "tamper_areas": sorted(areas.tamper),
        "triggered_areas": sorted(areas.alarm_memory),
        "tamper_memory_areas": sorted(areas.tamper_memory),
        "auto_arm_areas": sorted(areas.auto_arm),
    }


# -------------------------------------------------------------------- events


def event_dict(
    data: bytes, model: PanelModel, time_zone: tzinfo | None = None
) -> dict[str, Any]:
    """One event record as the event sensor expects it."""
    d1, d2, d3, d4, action = data[4:9]
    seconds = int.from_bytes(data[0:4], "little")
    event: dict[str, Any] = {
        "raw_hex_data": data.hex(),
        # panel local time; for a repeated hour (clocks going back) the
        # first occurrence is taken
        "timestamp_iso": decode_timestamp(seconds)
        .replace(tzinfo=time_zone)
        .isoformat(),
        "action_code": action,
        "action_description": EVENT_ACTIONS.get(
            action, f"Unknown Action (0x{action:02X})"
        ),
    }
    if action in _AREA_EVENTS:
        event["affected_areas"] = _areas(d1 | d2 << 8, model)
        event["device_id_hex"] = f"{d4:02x}"
        event["authorized_id_hex"] = f"{d3:02x}"
    elif action == 0x9E:
        event["scenario_number_0_indexed"] = d3
    elif action in _ZONE_EVENTS:
        event["affected_areas"] = _areas(d1 | d2 << 8, model)
        event["zone_number_0_indexed"] = d3
        event["zone_number_1_indexed_for_display"] = d3 + 1
    event["details_byte1_area_lsb_or_direct_area"] = f"{d1:02x}"
    event["details_byte2_area_msb"] = f"{d2:02x}"
    event["details_byte3_scenario_or_zone"] = f"{d3:02x}"
    event["details_byte4_device_id"] = f"{d4:02x}"
    event["details_byte5_action_code"] = f"{action:02x}"
    return event
