"""The integration's view of the panel, in the dictionaries it has always used.

The entities, the stored panel configuration (entry.data) and the
coordinator data keep their shapes while the panel is accessed through the
asynchronous API. Values are derived from the raw replies with the same
rules as before, including their known limits (zone values other than
rest/alarm reported as unknown, primary zones only, event indexes not
wrapping around the log, event times in UTC+1); each of those is lifted by
its own change later.

Errors are reported the old way: None, False or a dictionary marking the
failure, never an exception.
"""

from collections.abc import AsyncIterator
import contextlib
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from .api import NotReadyError, PanelConfiguration, PanelStatus, SmartLivingPanel
from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
)
from .protocol import registers as reg
from .protocol.commands import ArmMode
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .protocol.model import SMARTLIVING_1050_FW6, PanelModel
from .protocol.status import AreaMode, AreaStatus
from .transport.base import PanelTransport, Priority, TransportError
from .transport.smartlan_g import SmartLanGTransport
from .transport.smartlan_si import SmartLanSiTransport

_LOGGER = logging.getLogger(__name__)

LABEL_LENGTH = 16
MAX_EVENTS_PER_FETCH = 50
EVENT_RECORD_LENGTH = 9
EVENT_TIME_ZONE = timezone(timedelta(hours=1))
EVENT_EPOCH = datetime(2000, 1, 1, tzinfo=EVENT_TIME_ZONE)

ACTIVATION_TYPES = {0x00: "Immediate", 0x01: "Delayed", 0x04: "24 hours"}
BALANCING_TYPES = {
    0x00: "Normally Open",
    0x01: "Normally Closed",
    0x03: "Double Balancing",
}
SENSOR_TYPES = {0x00: "Generic zone", 0x80: "Shutter type"}
SCENARIO_ACTIONS = {0x1: "arm", 0x4: "disarm", 0x0: "keep"}
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
_ZONE_PAIRS = {  # 2-bit zone pair value -> (lower zone, higher zone)
    0x5: ("clear", "clear"),
    0x6: ("alarmed", "clear"),
    0x9: ("clear", "alarmed"),
    0xA: ("alarmed", "alarmed"),
}

_ERRORS = (TransportError, ProtocolError, UnsupportedPanelError, ValueError)


def create_client(
    host: str,
    port: int,
    pin: str,
    *,
    board: str = BOARD_SMARTLAN_SI,
    username: str = "",
    password: str = "",
    **options: float,
) -> "LegacyPanelClient":
    """A client for a panel behind a SmartLAN/SI or SmartLAN/G board.

    User name and password are the SmartLAN/G board's (they derive its
    encryption key). Options are the transport's (timeouts, pauses). Panels
    other than the 1050 on firmware 6 are handled with the 1050's layout,
    with a warning, as the integration always did.
    """
    transport: PanelTransport
    if board == BOARD_SMARTLAN_G:
        transport = SmartLanGTransport(
            host, port, username=username, password=password, **options
        )
    elif board == BOARD_SMARTLAN_SI:
        transport = SmartLanSiTransport(host, port, **options)
    else:
        raise ValueError(f"unknown board type: {board}")
    panel = SmartLivingPanel(transport, pin, fallback_model=SMARTLIVING_1050_FW6)
    return LegacyPanelClient(panel, host, port)


class LegacyPanelClient:
    """The panel API in the integration's dictionaries; all methods async."""

    def __init__(self, panel: SmartLivingPanel, host: str, port: int) -> None:
        """Wrap a panel; host and port are for messages only."""
        self.panel = panel
        self.host = host
        self.port = port

    async def close(self) -> None:
        """Close the connection to the panel."""
        await self.panel.close()

    @contextlib.asynccontextmanager
    async def released(self) -> AsyncIterator[None]:
        """Hold this client's requests and free its connection meanwhile.

        For another client that must reach the same board, which may accept
        only one connection; this client reconnects on its next request.
        """
        transport = self.panel.transport
        async with transport.session(Priority.COMMAND):
            await transport.disconnect()
            yield

    # ----------------------------------------------------------- configuration

    async def get_initial_panel_configuration(self) -> dict[str, Any] | None:
        """Names, zone setup and scenarios; None if the panel is unreachable."""
        try:
            config = await self.panel.read_configuration()
        except TransportError as err:
            _LOGGER.error("Cannot read the panel configuration: %s", err)
            return None
        except (ProtocolError, UnsupportedPanelError) as err:
            _LOGGER.error("Invalid panel configuration: %s", err)
            return {
                "errors": [str(err)],
                **dict.fromkeys(
                    (
                        "system_info",
                        "areas",
                        "zones",
                        "zones_config",
                        "scenarios",
                        "scenario_activations",
                        "keyboard_names",
                    )
                ),
            }
        return configuration_dict(config)

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
    ) -> dict[str, Any]:
        """The newest events (count) or those after a known index, newest first.

        Indexes are the low 16 bits of the record address, as before.
        """
        try:
            pointers = await self.panel.read_ram(
                reg.EVENT_POINTERS, reg.EVENT_POINTERS_LENGTH
            )
            model = self.panel.model
        except _ERRORS as err:
            _LOGGER.error("Cannot read the event pointers: %s", err)
            return {"events": [], "latest_event_index_val": None}

        head = int.from_bytes(pointers[0:2], "little")
        bank = model.event_log & 0xFF0000
        latest = (
            (head - 1) * EVENT_RECORD_LENGTH + (model.event_log & 0xFFFF)
        ) & 0xFFFF
        last = last_processed_compact_event_index_val
        if count is not None:
            wanted = min(max(1, count), MAX_EVENTS_PER_FETCH)
        elif last is not None:
            if latest <= last:
                return {"events": [], "latest_event_index_val": latest}
            wanted = MAX_EVENTS_PER_FETCH
        else:
            _LOGGER.error("Either count or the last processed index is needed")
            return {"events": [], "latest_event_index_val": latest}

        events: list[dict[str, Any]] = []
        index = latest
        for _ in range(wanted):
            if index < 0 or (count is None and last is not None and index <= last):
                break
            try:
                data = await self.panel.read_eeprom(bank | index, EVENT_RECORD_LENGTH)
            except _ERRORS as err:
                _LOGGER.info("Stopped reading events at %04x: %s", index, err)
                break
            event = event_dict(data, model)
            event["query_index_hex"] = f"{index:04x}"
            event["query_index_val"] = index
            events.append(event)
            index -= EVENT_RECORD_LENGTH
        return {"events": events, "latest_event_index_val": latest}

    # ---------------------------------------------------------------- commands

    async def execute_arm_disarm_areas(
        self,
        areas_to_arm: list[int] | None = None,
        areas_to_disarm: list[int] | None = None,
    ) -> bool:
        """Arm (total) and/or disarm areas (1-based); True if accepted."""
        modes = dict.fromkeys(areas_to_disarm or (), ArmMode.DISARM)
        modes.update(dict.fromkeys(areas_to_arm or (), ArmMode.AWAY))
        return await self._run("Arm/disarm", self.panel.arm_areas(modes))

    async def execute_reset_area_alarm(self, area_number_1_indexed: int) -> bool:
        """Reset the alarm memory of one area (1-based); True if accepted."""
        return await self._run(
            f"Reset of area {area_number_1_indexed}",
            self.panel.reset_alarm_memory(frozenset({area_number_1_indexed})),
        )

    async def execute_set_zone_excluded_status(
        self, zone_internal_index: int, excluded_status: bool
    ) -> bool:
        """Bypass (True) or include the zone at a physical slot; True if accepted."""
        return await self._run(
            f"Bypass of slot {zone_internal_index}",
            self.panel.bypass_zone(zone_internal_index, bypass=excluded_status),
        )

    async def execute_activate_scenario(self, scenario_number_0_indexed: int) -> bool:
        """Activate a scenario (0-based) if no zone prevents it; True if done."""
        try:
            return await self._run(
                f"Scenario {scenario_number_0_indexed}",
                self.panel.activate_scenario(scenario_number_0_indexed),
            )
        except NotReadyError as err:
            _LOGGER.warning("%s", err)
            return False

    async def execute_check_scenario_activation_allowed(
        self, scenario_number_0_indexed: int
    ) -> bool | None:
        """Whether a scenario (0-based) can be activated; None on error."""
        try:
            zones = await self.panel.not_ready_zones(scenario_number_0_indexed)
        except ValueError as err:  # scenario out of range
            _LOGGER.error("%s", err)
            return False
        except _ERRORS as err:
            _LOGGER.error(
                "Cannot check scenario %s: %s", scenario_number_0_indexed, err
            )
            return None
        return not zones

    async def _run(self, what: str, command) -> bool:
        try:
            result = await command
        except _ERRORS as err:
            _LOGGER.error("%s failed: %s", what, err)
            return False
        if not result.ok:
            _LOGGER.error("%s refused by the panel: %s", what, result.reason)
        return result.ok


# ------------------------------------------------------------- configuration


def configuration_dict(config: PanelConfiguration) -> dict[str, Any]:
    """The configuration as stored in entry.data."""
    model = config.model
    ascii_text = config.model_string.decode("ascii", errors="ignore")
    parts = ascii_text.strip().split(" ")
    return {
        "errors": [],
        "system_info": {
            "raw_hex": config.model_string.hex(),
            "ascii": ascii_text,
            "version": parts[0] if parts else "Unknown",
            "type": parts[1] if len(parts) > 1 else "Unknown",
        },
        "areas": {
            "raw_hex": config.area_labels.raw.hex(),
            "names": _names(config.area_labels.raw),
        },
        "zones": {
            "raw_hex_full": config.zone_labels.raw.hex(),
            "zone_names": _names(config.zone_labels.raw)[: model.terminals],
        },
        "zones_config": {
            "raw_hex_part1_full_response": config.zone_records.hex(),
            "raw_hex_part2_full_response": config.terminal_records.hex(),
            "zones_config_detailed": _zones(config),
        },
        "scenarios": {
            "raw_hex": config.scenario_labels.raw.hex(),
            "names": _names(config.scenario_labels.raw),
        },
        "scenario_activations": _scenario_activations(config),
        "keyboard_names": {
            "raw_hex": config.keypad_labels.raw.hex(),
            "names": _names(config.keypad_labels.raw),
        },
    }


def _names(raw: bytes) -> list[str]:
    return [
        raw[i : i + LABEL_LENGTH].decode("ascii", errors="ignore").strip()
        for i in range(0, len(raw), LABEL_LENGTH)
    ]


def _zones(config: PanelConfiguration) -> list[dict[str, Any]]:
    model = config.model
    # logical zone -> physical slot; the last slot is not looked at and a
    # later slot wins, as before
    slots = {
        terminal: slot
        for slot, terminal in enumerate(config.physical_map_raw[:-1])
        if terminal != 0xFF
    }
    zones = []
    for index in range(model.terminals):
        record = config.zone_records[
            index * model.zone_record_length : (index + 1) * model.zone_record_length
        ]
        terminal = config.terminal_records[
            index * model.terminal_record_length : (index + 1)
            * model.terminal_record_length
        ]
        activation, balancing, sensor = record[2], terminal[5], terminal[6]
        zone: dict[str, Any] = {
            "zone_index": index,
            "assigned_areas": _areas(record[0] | record[1] << 8, model),
            "activation_type_val": activation,
            "activation_type_desc": ACTIVATION_TYPES.get(
                activation, f"Unknown (0x{activation:02X})"
            ),
            "duration_ms": record[6],
            "time": _time(record[7]),
            "pulses": record[8],
            "balancing_type_val": balancing,
            "balancing_type_desc": BALANCING_TYPES.get(
                balancing, f"Unknown (0x{balancing:02X})"
            ),
            "sensor_type_val": sensor,
            "sensor_type_desc": SENSOR_TYPES.get(sensor, f"Unknown (0x{sensor:02X})"),
        }
        if index in slots:
            zone["internal_index"] = slots[index]
        zones.append(zone)
    return zones


def _time(value: int) -> str:
    if value <= 0x3B:
        return f"{value} seconds"
    if value >= 0x80:
        return f"{value - 0x80 + (1 if value > 0x80 else 0)} minutes"
    return f"Raw value 0x{value:02X}"


def _scenario_activations(config: PanelConfiguration) -> list[dict[str, Any]]:
    model = config.model
    size = model.scenario_record_length
    activations = []
    for index in range(model.scenarios):
        record = config.scenario_records[index * size : (index + 1) * size]
        actions = {}
        for byte_index in range(5):
            for area, nibble in (
                (2 * byte_index + 1, record[byte_index] & 0x0F),
                (2 * byte_index + 2, record[byte_index] >> 4),
            ):
                if area <= model.areas:
                    actions[area] = SCENARIO_ACTIONS.get(nibble, "unknown")
        activations.append(
            {
                "scenario_index": index,
                "raw_activation_hex": record.hex(),
                "area_actions": actions,
                "unknown_trailing_bytes_hex": record[5:].hex(),
            }
        )
    return activations


def _areas(mask: int, model: PanelModel) -> list[int]:
    return [area for area in range(1, model.areas + 1) if mask >> (area - 1) & 1]


# -------------------------------------------------------------------- status


def status_dict(status: PanelStatus, model: PanelModel) -> dict[str, Any]:
    """The live status as the coordinator has always provided it."""
    raw = status.raw
    return {
        "areas_status": _area_status(raw[reg.AREA_STATUS], status.areas),
        "zones_status": _zone_status(raw[reg.ZONE_STATE], model),
        "zones_excluded_status": {
            "raw_hex_data": raw[reg.ZONE_INCLUSION].hex(),
            "zone_excluded_statuses": _zone_bits(
                raw[reg.ZONE_INCLUSION], model, "enabled", "disabled"
            ),
        },
        "zones_triggered_status": {
            "raw_hex_data": raw[reg.ZONE_MEMORY].hex(),
            "zone_triggered_statuses": _zone_bits(
                raw[reg.ZONE_MEMORY], model, "triggered", "clear"
            ),
        },
        "active_scenario": {
            "raw_hex": raw[reg.ACTIVE_SCENARIO].hex(),
            "active_scenario_number": raw[reg.ACTIVE_SCENARIO][0],
        },
        "errors": [],
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


def _zone_status(data: bytes, model: PanelModel) -> dict[str, Any]:
    statuses: dict[int, str] = {}
    for zone_pair in range(model.terminals // 2):  # pairs of the first L zones
        byte = data[zone_pair // 2]
        value = byte >> 4 if zone_pair % 2 else byte & 0x0F
        lower, higher = _ZONE_PAIRS.get(value, ("unknown", "unknown"))
        if value not in _ZONE_PAIRS:
            _LOGGER.warning(
                "Unknown status %x for zones %s and %s",
                value,
                2 * zone_pair + 1,
                2 * zone_pair + 2,
            )
        statuses[2 * zone_pair + 1] = lower
        statuses[2 * zone_pair + 2] = higher
    return {
        "raw_hex_data": data.hex(),
        "zone_statuses": statuses,
        "unknown_byte_after_status_data": data[-1:].hex(),
    }


def _zone_bits(
    data: bytes, model: PanelModel, set_text: str, clear_text: str
) -> dict[int, str]:
    return {
        zone: set_text if data[(zone - 1) // 8] >> ((zone - 1) % 8) & 1 else clear_text
        for zone in range(1, model.terminals + 1)
    }


# -------------------------------------------------------------------- events


def event_dict(data: bytes, model: PanelModel) -> dict[str, Any]:
    """One event record as the event sensor expects it."""
    d1, d2, d3, d4, action = data[4:9]
    seconds = int.from_bytes(data[0:4], "little")
    event: dict[str, Any] = {
        "raw_hex_data": data.hex(),
        "timestamp_iso": (EVENT_EPOCH + timedelta(seconds=seconds)).isoformat(),
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
