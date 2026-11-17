"""The integration's view of the panel, in the dictionaries it has always used.

The entities, the stored panel configuration (entry.data) and the
coordinator data keep their shapes while the panel is accessed through the
asynchronous API. Values are derived from the raw replies with the same
rules as before, unless a later change says otherwise.

Errors are reported the old way: None, False or a dictionary marking the
failure, never an exception.
"""

from collections.abc import AsyncIterator, Collection
import contextlib
from datetime import tzinfo
import logging
from typing import Any

from .api import (
    NotReadyError,
    PanelConfiguration,
    PanelStatus,
    ProgrammingModeError,
    SmartLivingPanel,
)
from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
    ZONE_STATE_ALARMED,
    ZONE_STATE_CLEAR,
    ZONE_STATE_SHORT,
    ZONE_STATE_TAMPER,
)
from .protocol import registers as reg
from .protocol.commands import ArmMode
from .protocol.config import ZONE_TYPES, TerminalType
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .protocol.events import (
    EVENT_RECORD_LENGTH,
    decode_event_pointers,
    decode_timestamp,
    event_address,
    newest_indexes,
)
from .protocol.model import SMARTLIVING_1050_FW6, PanelModel
from .protocol.status import AreaMode, AreaStatus, ZoneState
from .transport.base import PanelTransport, Priority, TransportError
from .transport.smartlan_g import SmartLanGTransport
from .transport.smartlan_si import SmartLanSiTransport

_LOGGER = logging.getLogger(__name__)

LABEL_LENGTH = 16
MAX_EVENTS_PER_FETCH = 50

ACTIVATION_TYPES = {0x00: "Immediate", 0x01: "Delayed", 0x04: "24 hours"}
BALANCING_TYPES = {
    0x00: "Normally Open",
    0x01: "Normally Closed",
    0x03: "Double Balancing",
}
SENSOR_TYPES = {0x00: "Generic zone", 0x80: "Shutter type"}
TERMINAL_TYPES = {
    TerminalType.INPUT: "Input",
    TerminalType.OUTPUT: "Output",
    TerminalType.IN_OUT: "Input/output",
    TerminalType.DOUBLE: "Double zone",
    TerminalType.NULL: "Not used",
    TerminalType.OUT_DIMMING: "Dimmer output",
    TerminalType.OUT_DAC: "DAC output",
    TerminalType.OUT_ANALOG: "Analog output",
    TerminalType.OUT_NOT_ENABLED: "Output not enabled",
}
TERMINAL_TYPE_OFFSET = 11  # in the terminal record
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
_ZONE_STATES = {
    ZoneState.SHORT: ZONE_STATE_SHORT,
    ZoneState.STANDBY: ZONE_STATE_CLEAR,
    ZoneState.ALARM: ZONE_STATE_ALARMED,
    ZoneState.TAMPER: ZONE_STATE_TAMPER,
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
    without a profile of their own (models other than the 1050 and 10100,
    firmware below 6) are handled with the 1050's layout, with a warning, as
    the integration always did.
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
                data = await self.panel.read_eeprom(
                    event_address(index, model), EVENT_RECORD_LENGTH
                )
            except _ERRORS as err:
                _LOGGER.info("Stopped reading events at %s: %s", index, err)
                break
            event = event_dict(data, model, time_zone)
            event["query_index_hex"] = f"{index:04x}"
            event["query_index_val"] = index
            events.append(event)
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
        self,
        zone_internal_index: int,
        excluded_status: bool,
        *,
        second_zone: bool = False,
    ) -> bool:
        """Bypass (True) or include the zone at a physical slot; True if accepted.

        second_zone selects the second zone of a double terminal.
        """
        return await self._run(
            f"Bypass of slot {zone_internal_index}"
            + (" (second zone)" if second_zone else ""),
            self.panel.bypass_zone(
                zone_internal_index, bypass=excluded_status, second_zone=second_zone
            ),
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
        except (*_ERRORS, ProgrammingModeError) as err:
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
            "zone_names": _names(config.zone_labels.raw)[: model.zones],
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
        # Names of the other objects events refer to
        "code_names": {"names": config.code_labels.names},
        "key_names": {"names": config.key_labels.names},
        "reader_names": {"names": config.reader_labels.names},
        "expander_names": {"names": config.expander_labels.names},
        "timer_names": {"names": config.timer_labels.names},
        "siren_names": {"names": config.siren_labels.names},
        "peripherals": _peripherals(config),
    }


def _peripherals(config: PanelConfiguration) -> dict[str, Any]:
    """The enabled devices of each kind, as 1-based numbers."""
    found = config.peripherals
    return {
        "raw_hex": config.peripherals_raw.hex(),
        **{
            kind: [index + 1 for index in getattr(found, kind)]
            for kind in ("keypads", "readers", "expanders", "sirens")
        },
    }


def _names(raw: bytes) -> list[str]:
    return [
        raw[i : i + LABEL_LENGTH].decode("ascii", errors="ignore").strip()
        for i in range(0, len(raw), LABEL_LENGTH)
    ]


def _zones(config: PanelConfiguration) -> list[dict[str, Any]]:
    # logical zone -> physical slot; the last slot is not looked at and a
    # later slot wins, as before
    slots = {
        terminal: slot
        for slot, terminal in enumerate(config.physical_map_raw[:-1])
        if terminal != 0xFF
    }
    return zone_details(
        config.zone_records, config.terminal_records, slots, config.model
    )


def zone_details(
    zone_records: bytes,
    terminal_records: bytes,
    slots: dict[int, int],
    model: PanelModel,
) -> list[dict[str, Any]]:
    """One entry per zone number (2L), from the raw zone and terminal records.

    Zone L+n is the second zone of terminal n; it has its own zone record
    and shares the terminal record and physical slot (by terminal index in
    slots) with zone n. Whether a zone exists is told by zone_exists().
    """
    zones = []
    for index in range(model.zones):
        terminal_index = index % model.terminals
        record = zone_records[
            index * model.zone_record_length : (index + 1) * model.zone_record_length
        ]
        terminal = terminal_records[
            terminal_index * model.terminal_record_length : (terminal_index + 1)
            * model.terminal_record_length
        ]
        activation, balancing, sensor = record[2], terminal[5], terminal[6]
        terminal_type = terminal[TERMINAL_TYPE_OFFSET]
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
            "terminal_index": terminal_index,
            "terminal_type_val": terminal_type,
            "terminal_type_desc": TERMINAL_TYPES.get(
                terminal_type, f"Unknown (0x{terminal_type:02X})"
            ),
            "second_zone": index >= model.terminals,
        }
        if terminal_index in slots:
            zone["internal_index"] = slots[terminal_index]
        zones.append(zone)
    return zones


def zone_exists(zone: dict[str, Any]) -> bool:
    """Whether a zone entry is a zone on the panel.

    A zone of an input, input/output or double terminal, or the second zone
    of a double terminal. Entries stored without the terminal type (older
    configuration) are taken as zones, as they always were.
    """
    if "terminal_type_val" not in zone:
        return True
    terminal_type = zone["terminal_type_val"]
    if zone.get("second_zone"):
        return terminal_type == TerminalType.DOUBLE
    return terminal_type in ZONE_TYPES


def zones_to_create(
    zone_names: list[str],
    zone_details: list[dict[str, Any]],
    selected: Collection[int],
) -> list[tuple[int, str, dict[str, Any] | None]]:
    """(0-based index, name, detail) of the zones that get entities.

    The selected zone numbers (1-based) that are zones on the panel
    (zone_exists); without stored zone details, the selected zone numbers.
    """
    if not zone_details:
        return [
            (index, name or f"Zone {index + 1}", None)
            for index, name in enumerate(zone_names)
            if index + 1 in selected
        ]
    zones = []
    for detail in zone_details:
        index = detail.get("zone_index")
        if index is None or index + 1 not in selected or not zone_exists(detail):
            continue
        name = zone_names[index] if index < len(zone_names) else ""
        zones.append((index, name or f"Zone {index + 1}", detail))
    return zones


def upgrade_zone_configuration(config: dict[str, Any]) -> dict[str, Any] | None:
    """A stored configuration with the zone list of every zone number.

    Rebuilt from the raw records and labels the configuration already
    holds; physical slots are taken from the stored zone list. None when the
    raw data is missing or too short (the configuration is then kept).

    Every configuration stored before this version was read with the 1050's
    layout, so it is rebuilt with that layout; a panel with another profile
    has its configuration read again at setup.
    """
    model = SMARTLIVING_1050_FW6
    try:
        zones_config = config["zones_config"]
        stored = zones_config["zones_config_detailed"]
        zone_records = bytes.fromhex(zones_config["raw_hex_part1_full_response"])
        terminal_records = bytes.fromhex(zones_config["raw_hex_part2_full_response"])
        labels = bytes.fromhex(config["zones"]["raw_hex_full"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        len(zone_records) < model.zones * model.zone_record_length
        or len(terminal_records) < model.terminals * model.terminal_record_length
        or len(labels) < model.zones * LABEL_LENGTH
    ):
        return None
    slots = {
        zone["zone_index"]: zone["internal_index"]
        for zone in stored
        if "internal_index" in zone and zone.get("zone_index", -1) < model.terminals
    }
    return {
        **config,
        "zones": {**config["zones"], "zone_names": _names(labels)[: model.zones]},
        "zones_config": {
            **zones_config,
            "zones_config_detailed": zone_details(
                zone_records, terminal_records, slots, model
            ),
        },
    }


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
        area_bytes = (model.areas + 1) // 2  # one nibble per area
        for byte_index in range(area_bytes):
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
                "unknown_trailing_bytes_hex": record[area_bytes:].hex(),
            }
        )
    return activations


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
