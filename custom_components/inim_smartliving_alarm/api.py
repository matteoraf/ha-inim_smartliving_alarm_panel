"""Asynchronous access to a SmartLiving panel through a SmartLAN transport.

Builds on the protocol package (frames and decoding) and the transport
package (one persistent connection, commands ahead of polls). No Home
Assistant imports.

The panel has no login: the PIN travels with every command and the panel
checks it there. While the panel is in programming mode it ignores commands
and keeps the result of the previous one, so commands are not sent then. The PIN table is read once to find the user code, whose
areas are needed to ask which scenario is active; the table itself is
never logged or kept.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import logging

from .protocol import registers as reg
from .protocol.commands import (
    ArmMode,
    Command,
    CommandResult,
    activate_scenario,
    arm_areas,
    bypass_zone,
    encode_pin,
    reset_alarm_memory,
)
from .protocol.config import (
    Peripherals,
    PhysicalMap,
    ScenarioConfig,
    Terminal,
    UserCode,
    ZoneConfig,
    area_mask,
    decode_code_record,
    decode_labels,
    decode_peripherals,
    decode_physical_map,
    decode_scenario_records,
    decode_terminals,
    decode_zone_records,
    find_code_index,
    label_address,
)
from .protocol.errors import UnsupportedPanelError
from .protocol.frames import active_scenario_request, not_ready_request, read_ram
from .protocol.model import (
    PERIPHERALS_ADDRESS,
    PERIPHERALS_LENGTH,
    FirmwareInfo,
    PanelModel,
    model_for,
    parse_model_string,
)
from .protocol.status import (
    AreaStatus,
    PanelState,
    ZoneInclusion,
    ZoneMemory,
    ZoneState,
    decode_active_scenario,
    decode_area_status,
    decode_not_ready_zones,
    decode_panel_state,
    decode_zone_inclusion,
    decode_zone_memory,
    decode_zone_states,
)
from .transport.base import PanelTransport, Priority, Session

_LOGGER = logging.getLogger(__name__)

LABEL_LENGTH = 16
# After accepting a command the panel needs a moment before its status shows
# the change (measured on a 1050: still old after 88 ms, new after 218 ms)
COMMAND_SETTLE = 0.5
# Label pointers read in one request: areas (0x4014) up to scenarios (0x4024)
_LABEL_POINTERS_LENGTH = reg.LABELS_SCENARIOS + 2 - reg.LABELS_AREAS


class NotReadyError(Exception):
    """A scenario cannot be activated because some zones are not ready."""

    def __init__(self, scenario: int, zones: frozenset[int]) -> None:
        """Record the scenario (0-based) and the zones (0-based) in the way."""
        super().__init__(
            f"scenario {scenario} not ready: zones {sorted(zones)} are not at rest"
        )
        self.scenario = scenario
        self.zones = zones


class ProgrammingModeError(Exception):
    """The panel is in programming mode, so it ignores commands.

    This is keypad programming or a reload of the panel's configuration.
    sent is False when the command was not sent; True when the panel entered
    programming mode while it was sent, so it may not have been carried out
    (the result read back cannot be trusted).
    """

    def __init__(self, *, sent: bool = False) -> None:
        """Say whether the command had been sent."""
        super().__init__(
            "the panel entered programming mode while the command was sent; "
            "it may not have been carried out"
            if sent
            else "the panel is in programming mode"
        )
        self.sent = sent


@dataclass(frozen=True, slots=True)
class Labels:
    """A table of 16-byte labels as read from the panel."""

    raw: bytes
    count: int

    @property
    def names(self) -> list[str]:
        """The labels, decoded with the panel's character set."""
        return decode_labels(self.raw, self.count)


@dataclass(frozen=True, slots=True)
class PanelConfiguration:
    """The panel's programming that the integration needs.

    Raw blocks are kept next to their decoded form for callers that need
    fields the decoders do not cover yet.
    """

    firmware: FirmwareInfo
    model_string: bytes  # as read, untrimmed
    model: PanelModel
    area_labels: Labels
    zone_labels: Labels  # 2L labels: primary zones, then second zones
    keypad_labels: Labels
    scenario_labels: Labels
    code_labels: Labels  # user codes
    key_labels: Labels
    reader_labels: Labels
    expander_labels: Labels
    timer_labels: Labels
    siren_labels: Labels
    peripherals: Peripherals
    peripherals_raw: bytes
    physical_map: PhysicalMap
    physical_map_raw: bytes
    terminals: tuple[Terminal, ...]
    terminal_records: bytes
    zones: tuple[ZoneConfig, ...]
    zone_records: bytes
    scenarios: tuple[ScenarioConfig, ...]
    scenario_records: bytes


@dataclass(frozen=True, slots=True)
class PanelStatus:
    """One poll of the live status registers."""

    areas: AreaStatus
    zone_states: tuple[ZoneState, ...]
    zone_inclusion: ZoneInclusion
    zone_memory: ZoneMemory
    active_scenario: int | None  # 0-based; None when no scenario matches
    panel_state: PanelState | None  # None when the panel's model is not known
    raw: Mapping[int, bytes]  # reply data by register


class SmartLivingPanel:
    """A SmartLiving panel reached through a SmartLAN transport.

    The panel is identified on first use (model string, then the user
    code); every method then holds the connection for its requests.
    """

    def __init__(
        self,
        transport: PanelTransport,
        pin: str,
        *,
        fallback_model: PanelModel | None = None,
        command_settle: float | None = None,
    ) -> None:
        """Wrap a transport; the PIN is checked for format only.

        With a fallback model, a panel whose model or firmware is not
        supported is handled as that model instead of being refused.
        After an accepted command the connection is held for command_settle
        seconds (COMMAND_SETTLE by default), so that the next status read
        sees the change.
        """
        encode_pin(pin)  # raises InvalidPinError
        self.transport = transport
        self._pin = pin
        self._fallback_model = fallback_model
        self.command_settle = (
            COMMAND_SETTLE if command_settle is None else command_settle
        )
        self._firmware: FirmwareInfo | None = None
        self._model_string = b""
        self._model: PanelModel | None = None
        self._user_areas: frozenset[int] = frozenset()
        self.user_code_found = False
        # The user code the PIN belongs to: its 0-based number and record
        self.user_code_index: int | None = None
        self.user_code: UserCode | None = None
        # The state flags are read only from models with a profile: other
        # firmware may lay out the faults register differently
        self.reads_panel_state = False

    @property
    def firmware(self) -> FirmwareInfo:
        """Model and firmware as read from the panel."""
        if self._firmware is None:
            raise RuntimeError("the panel has not been identified yet")
        return self._firmware

    @property
    def model(self) -> PanelModel:
        """The model profile in use."""
        if self._model is None:
            raise RuntimeError("the panel has not been identified yet")
        return self._model

    @property
    def user_areas(self) -> frozenset[int]:
        """Areas of the user code (all areas when the PIN was not found)."""
        return self._user_areas

    async def close(self) -> None:
        """Close the connection."""
        await self.transport.close()

    # ---------------------------------------------------------- identification

    async def identify(self) -> FirmwareInfo:
        """Read the model string and find the user code (once)."""
        async with self.transport.session() as session:
            await self._identify(session)
        return self.firmware

    async def _identify(self, session: Session) -> PanelModel:
        if self._model is not None:
            return self._model
        data = await session.read_ram(reg.MODEL_STRING, reg.MODEL_STRING_LENGTH)
        firmware = parse_model_string(data)
        known = True
        try:
            model = model_for(firmware)
        except UnsupportedPanelError as err:
            known = False
            if self._fallback_model is None:
                raise
            _LOGGER.warning(
                "%s; handling the panel as a %s",
                err,
                self._fallback_model.name,
            )
            model = self._fallback_model
        if model.experimental:
            _LOGGER.warning(
                "Support for the %s is experimental (not yet tested on a real "
                "panel): please report how it works",
                model.name,
            )

        table = await session.read_eeprom(
            model.pin_table, model.pin_table_length, sensitive=True
        )
        index = find_code_index(table, self._pin, model)
        del table
        code = None
        if index is None:
            _LOGGER.warning(
                "The PIN does not match any user code of the panel; "
                "commands will be refused"
            )
            areas = frozenset(range(1, model.areas + 1))
        else:
            record = await session.read_eeprom(
                model.code_records + index * model.code_record_length,
                model.code_record_length,
            )
            code = decode_code_record(record, model)
            areas = code.areas
        self._firmware, self._model = firmware, model
        self._model_string = data
        self._user_areas = areas
        self.user_code_found = index is not None
        self.user_code_index, self.user_code = index, code
        self.reads_panel_state = known
        return model

    # ----------------------------------------------------------- configuration

    async def read_configuration(self) -> PanelConfiguration:
        """Read labels, enabled peripherals, terminals, zones and scenarios.

        Raises ProgrammingModeError if the panel is in programming mode
        before or after the reads, as the configuration may be changing.
        """
        async with self.transport.session() as session:
            model = await self._identify(session)
            await self._check_not_programming(session)
            pointers = await session.read_ram(reg.LABELS_AREAS, _LABEL_POINTERS_LENGTH)

            async def labels(register: int, count: int) -> Labels:
                offset = register - reg.LABELS_AREAS
                if 0 <= offset < _LABEL_POINTERS_LENGTH:
                    pointer = pointers[offset : offset + 2]
                else:  # after the pointers read in one go
                    pointer = await session.read_ram(register, 2)
                address = label_address(pointer, model)
                raw = await session.read_eeprom(address, count * LABEL_LENGTH)
                return Labels(raw, count)

            area_labels = await labels(reg.LABELS_AREAS, model.areas)
            zone_labels = await labels(reg.LABELS_ZONES, model.zones)
            keypad_labels = await labels(reg.LABELS_KEYPADS, model.keypads)
            scenario_labels = await labels(reg.LABELS_SCENARIOS, model.scenarios)
            code_labels = await labels(reg.LABELS_CODES, model.codes)
            key_labels = await labels(reg.LABELS_KEYS, model.keys)
            reader_labels = await labels(reg.LABELS_READERS, model.readers)
            expander_labels = await labels(reg.LABELS_EXPANDERS, model.expanders)
            timer_labels = await labels(reg.LABELS_TIMERS, model.timers)
            siren_labels = await labels(reg.LABELS_SIRENS, model.sirens)
            peripherals = await session.read_eeprom(
                PERIPHERALS_ADDRESS, PERIPHERALS_LENGTH
            )
            physical_map = await session.read_eeprom(
                model.physical_map, model.physical_slots
            )
            terminals = await session.read_eeprom(
                model.terminal_records, model.terminals * model.terminal_record_length
            )
            zones = await session.read_eeprom(
                model.zone_records, model.zones * model.zone_record_length
            )
            scenarios = await session.read_eeprom(
                model.scenario_records, model.scenarios * model.scenario_record_length
            )
            await self._check_not_programming(session)
        return PanelConfiguration(
            firmware=self.firmware,
            model_string=self._model_string,
            model=model,
            area_labels=area_labels,
            zone_labels=zone_labels,
            keypad_labels=keypad_labels,
            scenario_labels=scenario_labels,
            code_labels=code_labels,
            key_labels=key_labels,
            reader_labels=reader_labels,
            expander_labels=expander_labels,
            timer_labels=timer_labels,
            siren_labels=siren_labels,
            peripherals=decode_peripherals(peripherals, model),
            peripherals_raw=peripherals,
            physical_map=decode_physical_map(physical_map, model),
            physical_map_raw=physical_map,
            terminals=tuple(decode_terminals(terminals, model)),
            terminal_records=terminals,
            zones=tuple(decode_zone_records(zones, model)),
            zone_records=zones,
            scenarios=tuple(decode_scenario_records(scenarios, model)),
            scenario_records=scenarios,
        )

    # ------------------------------------------------------------------ status

    async def read_status(self) -> PanelStatus:
        """Read areas, zones, the active scenario and the state flags in one go."""
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw: dict[int, bytes] = {}
            for register, length in (
                (reg.AREA_STATUS, model.area_status_length),
                (reg.ZONE_STATE, model.zone_state_length),
                (reg.ZONE_INCLUSION, model.zone_inclusion_length),
                (reg.ZONE_MEMORY, model.zone_memory_length),
            ):
                raw[register] = await session.read(read_ram(register, length), length)
            raw[reg.ACTIVE_SCENARIO] = await session.read(
                active_scenario_request(area_mask(self._user_areas)), 1
            )
            if self.reads_panel_state:
                raw[reg.FAULTS] = await session.read_ram(reg.FAULTS, reg.FAULTS_LENGTH)
        faults = raw.get(reg.FAULTS)
        return PanelStatus(
            areas=decode_area_status(raw[reg.AREA_STATUS], model),
            zone_states=decode_zone_states(raw[reg.ZONE_STATE], model),
            zone_inclusion=decode_zone_inclusion(raw[reg.ZONE_INCLUSION], model),
            zone_memory=decode_zone_memory(raw[reg.ZONE_MEMORY], model),
            active_scenario=decode_active_scenario(raw[reg.ACTIVE_SCENARIO], model),
            panel_state=None if faults is None else _panel_state(faults),
            raw=raw,
        )

    async def not_ready_zones(self, scenario: int) -> frozenset[int]:
        """Zones (0-based) that would prevent activating a scenario (0-based)."""
        async with self.transport.session() as session:
            return await self._not_ready_zones(session, scenario)

    async def _not_ready_zones(self, session: Session, scenario: int) -> frozenset[int]:
        model = await self._identify(session)
        data = await session.read(
            not_ready_request(scenario, model), model.zone_bitmap_length
        )
        return decode_not_ready_zones(data, model)

    async def read_ram(self, address: int, length: int) -> bytes:
        """Read RAM (in parts if needed)."""
        async with self.transport.session() as session:
            await self._identify(session)
            return await session.read_ram(address, length)

    async def read_eeprom(
        self, address: int, length: int, *, sensitive: bool = False
    ) -> bytes:
        """Read EEPROM (in parts if needed); sensitive replies are not logged."""
        async with self.transport.session() as session:
            await self._identify(session)
            return await session.read_eeprom(address, length, sensitive=sensitive)

    # ---------------------------------------------------------------- commands

    async def arm_areas(self, modes: Mapping[int, ArmMode]) -> CommandResult:
        """Arm or disarm areas (1-based); areas not given are kept."""
        return await self._command(lambda model: arm_areas(self._pin, modes, model))

    async def reset_alarm_memory(self, areas: frozenset[int]) -> CommandResult:
        """Reset the alarm memory of areas (1-based)."""
        return await self._command(
            lambda model: reset_alarm_memory(self._pin, areas, model)
        )

    async def bypass_zone(
        self, slot: int, *, bypass: bool, second_zone: bool = False
    ) -> CommandResult:
        """Bypass or include the zone at a physical terminal slot."""
        return await self._command(
            lambda model: bypass_zone(
                self._pin, slot, model, bypass=bypass, second_zone=second_zone
            )
        )

    async def activate_scenario(
        self, scenario: int, *, check_ready: bool = True
    ) -> CommandResult:
        """Activate a scenario (0-based).

        With check_ready, the not-ready zones are read first in the same
        session and NotReadyError is raised instead of sending the command.
        """
        async with self.transport.session(Priority.COMMAND) as session:
            model = await self._identify(session)
            command = activate_scenario(self._pin, scenario, model)
            if check_ready:
                zones = await self._not_ready_zones(session, scenario)
                if zones:
                    raise NotReadyError(scenario, zones)
            return await self._send(session, command)

    async def _command(self, build) -> CommandResult:
        async with self.transport.session(Priority.COMMAND) as session:
            model = await self._identify(session)
            command: Command = build(model)
            return await self._send(session, command)

    async def _send(self, session: Session, command: Command) -> CommandResult:
        """Send a command unless the panel is in programming mode.

        The state flags are read again after the command: if programming
        began meanwhile, the panel may have ignored it, and its result
        cannot be trusted.
        """
        await self._check_not_programming(session)
        result = await session.command(command)
        await self._check_not_programming(session, sent=True)
        if result.ok and self.command_settle > 0:
            await asyncio.sleep(self.command_settle)  # still holding the session
        return result

    async def _check_not_programming(
        self, session: Session, *, sent: bool = False
    ) -> None:
        """Raise ProgrammingModeError if the panel is in programming mode."""
        if not self.reads_panel_state:
            return
        faults = await session.read_ram(reg.FAULTS, reg.FAULTS_LENGTH)
        if _panel_state(faults).programming:
            raise ProgrammingModeError(sent=sent)


def _panel_state(faults: bytes) -> PanelState:
    """The panel state flags, read with the faults register."""
    start = reg.PANEL_STATE_OFFSET
    return decode_panel_state(faults[start : start + reg.PANEL_STATE_LENGTH])
