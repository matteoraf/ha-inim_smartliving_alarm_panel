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
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Mapping
import contextlib
from dataclasses import dataclass, field, replace
import logging
import time

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
    set_output,
)
from .protocol.config import (
    FIXED_OUTPUT_RECORD_LENGTH,
    AreaConfig,
    OutputConfig,
    Peripherals,
    PhysicalMap,
    ScenarioConfig,
    Terminal,
    UserCode,
    ZoneConfig,
    area_mask,
    code_output_mask,
    decode_area_records,
    decode_code_record,
    decode_fixed_outputs,
    decode_labels,
    decode_peripherals,
    decode_physical_map,
    decode_scenario_records,
    decode_terminals,
    decode_zone_records,
    find_code_index,
    label_address,
    refuses_arming_when_not_ready,
)
from .protocol.errors import ProtocolError, UnsupportedPanelError, UnsupportedReason
from .protocol.events import (
    EVENT_RECORD_LENGTH,
    PanelEvent,
    decode_event_pointers,
    decode_event_record,
    event_address,
    indexes_to_read,
    newest_indexes,
    normalized_event_record,
)
from .protocol.frames import (
    active_scenario_request,
    not_ready_area_request,
    not_ready_request,
    output_detail_request,
    read_eeprom as read_eeprom_frame,
    read_ram,
    split,
)
from .protocol.masking import Privacy
from .protocol.model import (
    FIXED_OUTPUTS,
    GENERAL_OPTIONS_READ_LENGTH,
    LABEL_LENGTH,
    PERIPHERALS_ADDRESS,
    SCENARIOS,
    SMARTLIVING_1050_FW6,
    FirmwareInfo,
    PanelModel,
    model_for_or_fallback,
    parse_model_string,
)
from .protocol.report_layout import ReportBlock, ReportTable, Space, table_for
from .protocol.smartlan import BOARD_SMARTLAN_G, BOARD_SMARTLAN_SI, decode_si_reply
from .protocol.status import (
    OUTPUT_DETAIL_LENGTH,
    AreaStatus,
    PanelState,
    ZoneInclusion,
    ZoneMemory,
    ZoneState,
    decode_active_scenario,
    decode_area_status,
    decode_not_ready_zones,
    decode_output_level,
    decode_panel_state,
    decode_zone_inclusion,
    decode_zone_memory,
    decode_zone_states,
)
from .protocol.thermostat import (
    HEAD_LENGTH as THERMOSTAT_HEAD_LENGTH,
    KEYPAD_STATUS,
    KEYPAD_STATUS_LENGTH,
    TEMPERATURE_LENGTH,
    ThermostatHead,
    ThermostatMode,
    ThermostatSetting,
    decode_head,
    decode_setpoint,
    decode_temperature,
    decode_thermostats_on,
    encode_setpoint,
    setting_write,
    temperature_request,
    thermostat_address,
    weekly_offset,
)
from .transport.base import (
    PanelTransport,
    Priority,
    Session,
    TransportError,
)
from .transport.smartlan_g import SmartLanGTransport
from .transport.smartlan_si import SmartLanSiTransport

_LOGGER = logging.getLogger(__name__)

# Errors of a read or command that did not reach a result: the panel not
# reachable, a reply not understood, a panel without a profile, a value out
# of range
PANEL_ERRORS = (TransportError, ProtocolError, UnsupportedPanelError, ValueError)
# Events read at most in one go
MAX_EVENTS_PER_READ = 50

# After accepting a command the panel needs a moment before its status shows
# the change (measured on a 1050: still old after 88 ms, new after 218 ms)
COMMAND_SETTLE = 0.5
# Label pointers read in one request: areas (0x4014) up to scenarios (0x4024)
_LABEL_POINTERS_LENGTH = reg.LABELS_SCENARIOS + 2 - reg.LABELS_AREAS


# Decides what happens when zones are not ready: given the zones (0-based),
# True sends the command anyway, False refuses it with NotReadyError
NotReadyPolicy = Callable[[frozenset[int]], bool]


class NotReadyError(Exception):
    """Not sent: zones are not ready for a scenario or for arming areas."""

    def __init__(
        self,
        zones: frozenset[int],
        *,
        scenario: int | None = None,
        areas: frozenset[int] = frozenset(),
    ) -> None:
        """Record the zones (0-based) and the scenario (0-based) or areas."""
        what = (
            f"scenario {scenario}" if scenario is not None else f"areas {sorted(areas)}"
        )
        super().__init__(f"{what} not ready: zones {sorted(zones)} are not at rest")
        self.zones = zones
        self.scenario = scenario
        self.areas = areas


class ThermostatNotSetError(Exception):
    """The panel did not keep a thermostat setting that was written."""

    def __init__(self, keypad: int) -> None:
        """keypad is 0-based."""
        super().__init__(f"the thermostat of keypad {keypad + 1} kept another value")
        self.keypad = keypad


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
class EventRecord:
    """One event-log record (9 bytes)."""

    raw: bytes  # as read from the panel
    record: bytes  # with the event code in firmware 6 numbering


@dataclass(frozen=True, slots=True)
class LoggedEvent:
    """One event of the panel's log, at its ring index."""

    index: int
    raw: bytes  # as read from the panel
    event: PanelEvent


@dataclass(frozen=True, slots=True)
class EventBatch:
    """Events read in one go, newest first, and the newest event's index."""

    events: tuple[LoggedEvent, ...]
    latest_index: int | None  # None when the log is empty


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
    phone_labels: Labels  # the names of the phone numbers, never the numbers
    peripherals: Peripherals
    peripherals_raw: bytes
    general_options_raw: bytes
    refuses_arming_when_not_ready: bool
    physical_map: PhysicalMap
    physical_map_raw: bytes
    terminals: tuple[Terminal, ...]
    terminal_records: bytes
    zones: tuple[ZoneConfig, ...]
    zone_records: bytes
    scenarios: tuple[ScenarioConfig, ...]
    scenario_records: bytes
    area_records: tuple[AreaConfig, ...]
    area_records_raw: bytes
    fixed_output_labels: Labels  # O1-O3
    fixed_outputs: tuple[OutputConfig, ...]  # O1-O3
    fixed_output_records: bytes
    # The output mask of the PIN's user code (empty if the PIN matches none)
    user_output_mask: bytes
    # Extra output options (L + 3 bytes); empty where the address is not known
    extra_output_options: bytes


@dataclass(slots=True)
class BlockRead:
    """One block of a report table, as read (or why it was not)."""

    block: ReportBlock
    data: bytes | None = None
    address: int | None = None  # where it was read; for labels pointer + bank
    error: str | None = None  # the read failed: the error's kind
    not_read: str | None = None  # left out on purpose: why


@dataclass(slots=True)
class ReportReads:
    """What a system report read from the panel, block by block."""

    firmware: FirmwareInfo
    model_string: bytes
    model: PanelModel  # the profile in use (sizes basic mode)
    table: ReportTable
    reads: list[BlockRead] = field(default_factory=list)
    # The profile in use is a fallback for a model or firmware without one
    fallback: bool = False
    # "before" or "after" when the panel was in programming mode
    programming_mode: str | None = None
    seconds: float = 0.0
    requests: int = 0


# A system report reads in short sessions, so that a command waits for at
# most one of them, and stops after a time budget
REPORT_SESSION_REQUESTS = 10
REPORT_TIME_BUDGET = 120.0


class _ReportReader:
    """Reads a report's blocks in short sessions at poll priority."""

    def __init__(
        self,
        panel: "SmartLivingPanel",
        reads: ReportReads,
        session_size: int,
        time_budget: float,
        reads_panel_state: bool,
    ) -> None:
        self.panel = panel
        self.reads_panel_state = reads_panel_state
        self.reads = reads
        self.session_size = session_size
        self.deadline = time.monotonic() + time_budget
        self._session: Session | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._count = 0
        self.unreachable = False

    @property
    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline

    async def _next_session(self) -> Session:
        if self._session is not None and self._count < self.session_size:
            return self._session
        await self.close()
        self._stack = contextlib.AsyncExitStack()
        self._session = await self._stack.enter_async_context(
            self.panel.transport.session(Priority.POLL)
        )
        self._count = 0
        return self._session

    async def close(self) -> None:
        """Release the current session, if any."""
        if self._stack is not None:
            stack, self._stack, self._session = self._stack, None, None
            await stack.aclose()

    async def request(self, frame: bytes, length: int, *, sensitive: bool) -> bytes:
        session = await self._next_session()
        self._count += 1
        self.reads.requests += 1
        return await session.read(frame, length, sensitive=sensitive)

    async def read(
        self, space: Space, address: int, length: int, *, sensitive: bool
    ) -> bytes:
        """Read a block in parts, one request each."""
        dialect = self.panel.transport.dialect
        data = b""
        for part_address, part_length in split(address, length, dialect):
            if space is Space.EEPROM:
                frame = read_eeprom_frame(part_address, part_length, dialect)
            else:
                frame = read_ram(part_address, part_length, dialect)
            data += await self.request(frame, part_length, sensitive=sensitive)
        return data

    async def programming(self) -> bool:
        if not self.reads_panel_state:
            return False
        faults = await self.read(
            Space.RAM, reg.FAULTS, reg.FAULTS_LENGTH, sensitive=False
        )
        return _panel_state(faults).programming


@dataclass(frozen=True, slots=True)
class PanelStatus:
    """One poll of the live status registers."""

    areas: AreaStatus
    zone_states: tuple[ZoneState, ...]
    zone_inclusion: ZoneInclusion
    zone_memory: ZoneMemory
    # 0-based; None when no scenario matches the areas' state (the panel
    # reports a value that is not a scenario), and only then
    active_scenario: int | None
    panel_state: PanelState | None  # None when the panel's model is not known
    raw: Mapping[int, bytes]  # reply data by register
    raw_active_scenario: bytes = b""  # the active scenario as reported
    # The selected keypads' thermostats, by 0-based keypad
    thermostats: Mapping[int, "ThermostatState"] = field(default_factory=dict)

    @property
    def scenario_matches(self) -> bool:
        """Whether the areas' state matches a scenario."""
        return self.active_scenario is not None


# The live status read in parts, one per polling group. Each part replaces
# its fields (and its registers' bytes) in an earlier PanelStatus.


@dataclass(frozen=True, slots=True)
class ZoneReading:
    """Zone states and zone memory (alarm and tamper memories, outputs)."""

    zone_states: tuple[ZoneState, ...]
    zone_memory: ZoneMemory
    raw: Mapping[int, bytes]

    def apply(self, status: PanelStatus) -> PanelStatus:
        """The status with these zone states and memory."""
        return replace(
            status,
            zone_states=self.zone_states,
            zone_memory=self.zone_memory,
            raw={**status.raw, **self.raw},
        )


@dataclass(frozen=True, slots=True)
class AreaReading:
    """Area status and the active scenario, read together."""

    areas: AreaStatus
    active_scenario: int | None
    raw_active_scenario: bytes
    raw: Mapping[int, bytes]

    def apply(self, status: PanelStatus) -> PanelStatus:
        """The status with these areas and active scenario."""
        return replace(
            status,
            areas=self.areas,
            active_scenario=self.active_scenario,
            raw_active_scenario=self.raw_active_scenario,
            raw={**status.raw, **self.raw},
        )


@dataclass(frozen=True, slots=True)
class InclusionReading:
    """Zone inclusion and test."""

    zone_inclusion: ZoneInclusion
    raw: Mapping[int, bytes]

    def apply(self, status: PanelStatus) -> PanelStatus:
        """The status with this zone inclusion."""
        return replace(
            status,
            zone_inclusion=self.zone_inclusion,
            raw={**status.raw, **self.raw},
        )


@dataclass(frozen=True, slots=True)
class PanelStateReading:
    """The panel state flags; None when the panel's model is not known."""

    panel_state: PanelState | None
    raw: Mapping[int, bytes]

    def apply(self, status: PanelStatus) -> PanelStatus:
        """The status with these panel state flags."""
        return replace(
            status, panel_state=self.panel_state, raw={**status.raw, **self.raw}
        )


@dataclass(frozen=True, slots=True)
class ThermostatState:
    """A keypad's thermostat as last read."""

    head: ThermostatHead  # mode, season, manual setpoint, daily program
    # This hour's weekly setpoint, read only in the weekly program
    weekly_setpoint: float | None
    # Whether it is heating or cooling now; None where the panel cannot tell
    on: bool | None
    # The room temperature; None when not read or not a room temperature
    temperature: float | None


@dataclass(frozen=True, slots=True)
class ThermostatReading:
    """The selected keypads' thermostats.

    temperature_failed: the keypads (0-based) whose temperature read got
    no answer, or none that could be read, this time.
    """

    thermostats: Mapping[int, ThermostatState]
    temperature_failed: frozenset[int] = frozenset()

    def apply(self, status: PanelStatus) -> PanelStatus:
        """The status with these thermostats."""
        return replace(status, thermostats=self.thermostats)


StatusReading = (
    ZoneReading | AreaReading | InclusionReading | PanelStateReading | ThermostatReading
)


def _zone_reading(raw: Mapping[int, bytes], model: PanelModel) -> ZoneReading:
    return ZoneReading(
        zone_states=decode_zone_states(raw[reg.ZONE_STATE], model),
        zone_memory=decode_zone_memory(raw[reg.ZONE_MEMORY], model),
        raw={key: raw[key] for key in (reg.ZONE_STATE, reg.ZONE_MEMORY)},
    )


def _area_reading(raw: Mapping[int, bytes], model: PanelModel) -> AreaReading:
    return AreaReading(
        areas=decode_area_status(raw[reg.AREA_STATUS], model),
        active_scenario=decode_active_scenario(raw[reg.ACTIVE_SCENARIO], model),
        raw_active_scenario=raw[reg.ACTIVE_SCENARIO],
        raw={key: raw[key] for key in (reg.AREA_STATUS, reg.ACTIVE_SCENARIO)},
    )


def _inclusion_reading(raw: Mapping[int, bytes], model: PanelModel) -> InclusionReading:
    return InclusionReading(
        zone_inclusion=decode_zone_inclusion(raw[reg.ZONE_INCLUSION], model),
        raw={reg.ZONE_INCLUSION: raw[reg.ZONE_INCLUSION]},
    )


def _panel_state_reading(raw: Mapping[int, bytes]) -> PanelStateReading:
    faults = raw.get(reg.FAULTS)
    return PanelStateReading(
        panel_state=None if faults is None else _panel_state(faults),
        raw={} if faults is None else {reg.FAULTS: faults},
    )


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
        self._user_output_mask = b""
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

    async def board_reply(self) -> bytes | None:
        """The SmartLAN/SI board's reply to FIND, read over TCP.

        None for another board, when the board cannot be reached or is in
        use, or when the reply is not a FIND reply. The reply holds the
        board's credentials when they are set: never log or store it.
        """
        if not isinstance(self.transport, SmartLanSiTransport):
            return None
        try:
            reply = await self.transport.board_reply()
        except TransportError:
            return None
        return reply if decode_si_reply(reply) is not None else None

    async def release(self) -> None:
        """Close the connection once the current request is done.

        For a board that accepts one client at a time: another app can
        connect until the next request, which connects again.
        """
        await self.transport.close()

    def set_shared(self, shared: bool) -> None:
        """Share the board with other apps, or hold its connection as usual.

        Shared: one connection attempt with a short timeout (a board in use
        ignores a new connection, so a request fails in seconds), and the
        connection is closed shortly after the last request, whatever it
        was.
        """
        self.transport.shared = shared

    @contextlib.asynccontextmanager
    async def released(self) -> AsyncIterator[None]:
        """Hold this panel's requests and free its connection meanwhile.

        For another client that must reach the same board, which may accept
        only one connection; the panel reconnects on its next request.
        """
        async with self.transport.released():
            yield

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
        model, unsupported = model_for_or_fallback(firmware, self._fallback_model)
        known = unsupported is None
        if unsupported is not None:
            _LOGGER.warning("%s; handling the panel as a %s", unsupported, model.name)
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
            self._user_output_mask = code_output_mask(record, model)
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
            phone_labels = await labels(reg.LABELS_PHONES, model.phone_numbers)
            fixed_output_labels = await labels(reg.LABELS_FIXED_OUTPUTS, FIXED_OUTPUTS)
            # The enabled peripherals and the general options, in one go
            setup_block = await session.read_eeprom(
                PERIPHERALS_ADDRESS, model.setup_block_length
            )
            peripherals = setup_block[: model.peripherals_length]
            general_options = setup_block[
                model.general_options_address : model.general_options_address
                + GENERAL_OPTIONS_READ_LENGTH
            ]
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
            areas = await session.read_eeprom(
                model.area_records, model.areas * model.area_record_length
            )
            fixed_outputs = await session.read_eeprom(
                model.fixed_output_records, FIXED_OUTPUTS * FIXED_OUTPUT_RECORD_LENGTH
            )
            extra_output_options = (
                b""
                if model.extra_output_options is None
                else await session.read_eeprom(
                    model.extra_output_options, model.terminals + FIXED_OUTPUTS
                )
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
            phone_labels=phone_labels,
            peripherals=decode_peripherals(peripherals, model),
            peripherals_raw=peripherals,
            general_options_raw=general_options,
            refuses_arming_when_not_ready=refuses_arming_when_not_ready(
                general_options
            ),
            physical_map=decode_physical_map(physical_map, model),
            physical_map_raw=physical_map,
            terminals=tuple(decode_terminals(terminals, model)),
            terminal_records=terminals,
            zones=tuple(decode_zone_records(zones, model)),
            zone_records=zones,
            scenarios=tuple(decode_scenario_records(scenarios, model)),
            scenario_records=scenarios,
            area_records=tuple(decode_area_records(areas, model)),
            area_records_raw=areas,
            fixed_output_labels=fixed_output_labels,
            fixed_outputs=tuple(decode_fixed_outputs(fixed_outputs)),
            fixed_output_records=fixed_outputs,
            user_output_mask=self._user_output_mask,
            extra_output_options=extra_output_options,
        )

    # ------------------------------------------------------------------ status

    async def read_status(self) -> PanelStatus:
        """Read areas, zones, the active scenario and the state flags in one go."""
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw = await self._read_registers(
                session,
                model,
                (reg.AREA_STATUS, reg.ZONE_STATE, reg.ZONE_INCLUSION, reg.ZONE_MEMORY),
            )
            raw.update(await self._read_active_scenario(session))
            raw.update(await self._read_faults(session))
        areas = _area_reading(raw, model)
        zones = _zone_reading(raw, model)
        return PanelStatus(
            areas=areas.areas,
            zone_states=zones.zone_states,
            zone_inclusion=_inclusion_reading(raw, model).zone_inclusion,
            zone_memory=zones.zone_memory,
            active_scenario=areas.active_scenario,
            panel_state=_panel_state_reading(raw).panel_state,
            raw=raw,
            raw_active_scenario=areas.raw_active_scenario,
        )

    async def read_zones(self) -> ZoneReading:
        """Read the zone states and the zone memory."""
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw = await self._read_registers(
                session, model, (reg.ZONE_STATE, reg.ZONE_MEMORY)
            )
        return _zone_reading(raw, model)

    async def read_areas(self) -> AreaReading:
        """Read the area status and the active scenario."""
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw = await self._read_registers(session, model, (reg.AREA_STATUS,))
            raw.update(await self._read_active_scenario(session))
        return _area_reading(raw, model)

    async def read_zone_inclusion(self) -> InclusionReading:
        """Read the zone inclusion and test flags."""
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw = await self._read_registers(session, model, (reg.ZONE_INCLUSION,))
        return _inclusion_reading(raw, model)

    async def read_panel_state(self) -> PanelStateReading:
        """Read the panel state flags (nothing on a panel without a profile)."""
        async with self.transport.session() as session:
            await self._identify(session)
            raw = await self._read_faults(session)
        return _panel_state_reading(raw)

    async def _read_registers(
        self, session: Session, model: PanelModel, registers: Iterable[int]
    ) -> dict[int, bytes]:
        """Read status registers, each with the model's length."""
        lengths = {
            reg.AREA_STATUS: model.area_status_length,
            reg.ZONE_STATE: model.zone_state_length,
            reg.ZONE_INCLUSION: model.zone_inclusion_length,
            reg.ZONE_MEMORY: model.zone_memory_length,
        }
        raw: dict[int, bytes] = {}
        for register in registers:
            length = lengths[register]
            raw[register] = await session.read(read_ram(register, length), length)
        return raw

    async def _read_active_scenario(self, session: Session) -> dict[int, bytes]:
        """The active scenario for the user code's areas (1 byte)."""
        data = await session.read(
            active_scenario_request(area_mask(self._user_areas)), 1
        )
        return {reg.ACTIVE_SCENARIO: data}

    async def _read_faults(self, session: Session) -> dict[int, bytes]:
        """The faults register, only on a panel with a profile of its own."""
        if not self.reads_panel_state:
            return {}
        return {reg.FAULTS: await session.read_ram(reg.FAULTS, reg.FAULTS_LENGTH)}

    async def _not_ready_for_areas(
        self, session: Session, modes: Mapping[int, ArmMode]
    ) -> frozenset[int]:
        """Zones (0-based) that would prevent arming these areas, one read each.

        Areas disarmed or kept are not checked.
        """
        model = await self._identify(session)
        zones: set[int] = set()
        for area, mode in sorted(modes.items()):
            if mode in (ArmMode.AWAY, ArmMode.STAY, ArmMode.INSTANT):
                data = await session.read(
                    not_ready_area_request(area, int(mode), model),
                    model.zone_bitmap_length,
                )
                zones |= decode_not_ready_zones(data, model)
        return frozenset(zones)

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

    # ------------------------------------------------------------ thermostats

    async def probe_thermostats(self, keypads: Iterable[int]) -> frozenset[int]:
        """The keypads (0-based) whose thermostat can be used.

        A keypad counts when its room temperature read answers with a room
        temperature; a keypad whose read fails (no answer, or one that
        cannot be read) does not. Each keypad is read in a session of its
        own, so that a keypad that does not answer holds the connection for
        that one request only. Nothing on a panel without a profile of
        its own.
        """
        found: set[int] = set()
        for keypad in sorted(set(keypads)):
            async with self.transport.session() as session:
                model = await self._identify(session)
                if not self.reads_panel_state:
                    return frozenset()
                if not 0 <= keypad < model.keypads:
                    continue
                try:
                    data = await session.read(
                        temperature_request(keypad, model, self.transport.dialect),
                        TEMPERATURE_LENGTH,
                    )
                except PANEL_ERRORS as err:
                    # No answer, or not one that can be read: no thermostat
                    _LOGGER.debug("Keypad %s: no temperature (%s)", keypad + 1, err)
                    continue
            if decode_temperature(data) is not None:
                found.add(keypad)
        return frozenset(found)

    async def read_thermostats(
        self,
        keypads: Iterable[int],
        *,
        hour: int,
        weekday: int,
        skip_temperature: Collection[int] = (),
    ) -> ThermostatReading:
        """Read the thermostats of these keypads (0-based).

        Per keypad: the first 27 bytes of its block, the weekly setpoint
        for this hour and weekday (0 = Sunday) when it follows the weekly
        program, and its room temperature unless in skip_temperature. A
        temperature read that fails (no answer, or one that cannot be read)
        leaves the temperature unknown and does not fail the reading. On
        firmware 6 the keypad status block also says which thermostats are
        calling now. Nothing on a panel without a profile of its own.

        The keypad status block is read in a short session of its own, then
        each keypad in one of its own, so that a keypad that does not answer
        keeps a waiting command out for that keypad only.
        """
        wanted = sorted(set(keypads))
        failed: set[int] = set()
        states: dict[int, ThermostatState] = {}
        async with self.transport.session() as session:
            model = await self._identify(session)
            if not self.reads_panel_state or not wanted:
                return ThermostatReading({})
            on: frozenset[int] | None = None
            if model.keypad_status:
                on = decode_thermostats_on(
                    await session.read_ram(KEYPAD_STATUS, KEYPAD_STATUS_LENGTH), model
                )
        dialect = self.transport.dialect
        for keypad in wanted:
            async with self.transport.session() as session:
                address = thermostat_address(model, keypad)
                head = decode_head(
                    await session.read_eeprom(address, THERMOSTAT_HEAD_LENGTH)
                )
                weekly = None
                if head.mode is ThermostatMode.WEEKLY:
                    raw = await session.read_eeprom(
                        address + weekly_offset(hour, weekday), 1
                    )
                    weekly = decode_setpoint(raw[0])
                temperature = None
                if keypad not in skip_temperature:
                    try:
                        temperature = decode_temperature(
                            await session.read(
                                temperature_request(keypad, model, dialect),
                                TEMPERATURE_LENGTH,
                            )
                        )
                    except PANEL_ERRORS as err:
                        # Unanswered or unreadable: unknown, the rest is read
                        _LOGGER.debug(
                            "Keypad %s: temperature not read (%s)", keypad + 1, err
                        )
                        failed.add(keypad)
                states[keypad] = ThermostatState(
                    head=head,
                    weekly_setpoint=weekly,
                    on=None if on is None else keypad in on,
                    temperature=temperature,
                )
        return ThermostatReading(states, frozenset(failed))

    async def set_thermostat(
        self,
        keypad: int,
        *,
        season: int | None = None,
        setpoint: float | None = None,
        mode: int | None = None,
    ) -> ThermostatHead:
        """Change a keypad's (0-based) thermostat: season, manual setpoint
        (degrees Celsius) and regulation mode, each when given.

        The settings that already hold the value asked for are not written.
        The others are written one byte each, season first and mode last,
        then the block is read back: ThermostatNotSetError if the panel did
        not keep a value. Refused (ProgrammingModeError) while the panel is
        in programming mode, and on a panel without a profile of its own.
        Returns the first 27 bytes of the block as read back.
        """
        raw_setpoint = None if setpoint is None else encode_setpoint(setpoint)
        async with self.transport.session(Priority.COMMAND) as session:
            model = await self._identify(session)
            if not self.reads_panel_state:
                raise UnsupportedPanelError(
                    "thermostats need the panel's own model profile",
                    UnsupportedReason.FIRMWARE,
                )
            address = thermostat_address(model, keypad)
            await self._check_not_programming(session)
            current = await session.read_eeprom(address, len(ThermostatSetting))
            asked = (
                (ThermostatSetting.SEASON, season),
                (ThermostatSetting.SETPOINT, raw_setpoint),
                (ThermostatSetting.MODE, mode),
            )
            writes = [
                (setting, int(value))
                for setting, value in asked
                if value is not None and current[setting] != int(value)
            ]
            for setting, value in writes:
                frame, payload = setting_write(
                    model, keypad, setting, value, self.transport.dialect
                )
                await session.write(frame, payload)
            data = await session.read_eeprom(address, THERMOSTAT_HEAD_LENGTH)
        if any(data[setting] != value for setting, value in writes):
            raise ThermostatNotSetError(keypad)
        return decode_head(data)

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

    async def read_event_record(self, index: int) -> EventRecord:
        """Read the event-log record at a ring index.

        Panels on other firmware number their events differently; the
        record is also given with the code in firmware 6 numbering.
        """
        async with self.transport.session() as session:
            model = await self._identify(session)
            raw = await session.read_eeprom(
                event_address(index, model), EVENT_RECORD_LENGTH
            )
        return EventRecord(raw, normalized_event_record(raw, model))

    async def read_events(
        self,
        *,
        count: int | None = None,
        after: int | None = None,
        limit: int = MAX_EVENTS_PER_READ,
    ) -> EventBatch:
        """The newest count events, or those written after a ring index.

        At most limit events, newest first. A record that cannot be read
        ends the read: the events read until then are returned. Raises if
        the ring pointers cannot be read.
        """
        data = await self.read_ram(reg.EVENT_POINTERS, reg.EVENT_POINTERS_LENGTH)
        model = self.model
        pointers = decode_event_pointers(data, model)
        newest = newest_indexes(pointers, model, 1)
        events: list[LoggedEvent] = []
        indexes = indexes_to_read(
            pointers, model, count=count, after=after, limit=limit
        )
        for index in indexes:
            try:
                read = await self.read_event_record(index)
            except PANEL_ERRORS as err:
                _LOGGER.info("Stopped reading events at %s: %s", index, err)
                break
            events.append(
                LoggedEvent(index, read.raw, decode_event_record(read.raw, model))
            )
        return EventBatch(tuple(events), newest[0] if newest else None)

    # ---------------------------------------------------------- system report

    async def read_report(
        self,
        *,
        session_size: int = REPORT_SESSION_REQUESTS,
        time_budget: float = REPORT_TIME_BUDGET,
    ) -> ReportReads:
        """Read the blocks of the panel's report table.

        Only the blocks of the table that fits the panel's own model and
        firmware are read (identification and status registers only, when
        none fits). The reads go in sessions of a few requests at poll
        priority, so commands are not held up; blocks not read within the
        time budget, or while the panel is in programming mode, are marked
        as such. A failed block is marked with its error and the next one
        is tried; once the panel cannot be reached, the rest are left out.
        """
        started = time.monotonic()
        firmware, model_string, model, known = await self._report_identification()
        table = table_for(firmware, model)
        reads = ReportReads(firmware, model_string, model, table, fallback=not known)
        reader = _ReportReader(self, reads, session_size, time_budget, known)
        blocks = _report_order(table)
        try:
            if await self._report_guard(reader, reads, "before"):
                blocks = [b for b in blocks if b.space not in _PROGRAMMING]
            pointers: bytes | None = None
            for block in blocks:
                read = BlockRead(block)
                reads.reads.append(read)
                if reader.unreachable:
                    read.not_read = "panel not reachable"
                    continue
                if reader.out_of_time:
                    read.not_read = "time limit"
                    continue
                try:
                    await self._read_report_block(reader, table, read, pointers)
                except (TransportError, ProtocolError) as err:
                    read.data = None
                    read.error = _error_kind(err)
                    if read.error == "connect_error":
                        reader.unreachable = True
                if block.name == "label_pointers":
                    pointers = read.data
            if reads.programming_mode is None and not reader.unreachable:
                await self._report_guard(reader, reads, "after")
        finally:
            await reader.close()
        # The programming, left out because the panel was in programming mode
        present = {read.block.name for read in reads.reads}
        reads.reads += [
            BlockRead(b, not_read="programming mode")
            for b in table.blocks
            if b.name not in present
        ]
        reads.seconds = round(time.monotonic() - started, 3)
        return reads

    async def _report_identification(
        self,
    ) -> tuple[FirmwareInfo, bytes, PanelModel, bool]:
        """The model as identified, or read now without the PIN table.

        The last item tells whether the model has a profile of its own (its
        state flags, and so programming mode, can be read).
        """
        if self._model is not None:
            return (
                self.firmware,
                self._model_string,
                self._model,
                self.reads_panel_state,
            )
        async with self.transport.session() as session:
            data = await session.read_ram(reg.MODEL_STRING, reg.MODEL_STRING_LENGTH)
        firmware = parse_model_string(data)
        model, unsupported = model_for_or_fallback(firmware, self._fallback_model)
        return firmware, data, model, unsupported is None

    async def _report_guard(
        self, reader: _ReportReader, reads: ReportReads, when: str
    ) -> bool:
        """Note programming mode; True if the panel is in it."""
        try:
            if await reader.programming():
                reads.programming_mode = when
                return True
        except (TransportError, ProtocolError) as err:
            if _error_kind(err) == "connect_error":
                reader.unreachable = True
        return False

    async def _read_report_block(
        self,
        reader: _ReportReader,
        table: ReportTable,
        read: BlockRead,
        pointers: bytes | None,
    ) -> None:
        block = read.block
        sensitive = block.privacy is not Privacy.PUBLIC
        if block.space is Space.QUERY:
            parts = [
                await reader.request(
                    not_ready_request(scenario, reader.reads.model),
                    block.length,
                    sensitive=False,
                )
                for scenario in range(SCENARIOS)
            ]
            read.data, read.address = b"".join(parts), block.address
            return
        if block.space is Space.LABELS:
            offset = block.address - reg.LABELS_AREAS
            if pointers is not None and 0 <= offset + 2 <= len(pointers):
                pointer = pointers[offset : offset + 2]
            else:
                pointer = await reader.read(
                    Space.RAM, block.address, 2, sensitive=False
                )
            address = int.from_bytes(pointer, "little") + table.label_bank
            read.address = address
            if not table.labels_fit(address, block.length):
                read.not_read = "outside the label area"
                return
            read.data = await reader.read(
                Space.EEPROM, address, block.length, sensitive=True
            )
            return
        read.address = block.address
        read.data = await reader.read(
            block.space, block.address, block.length, sensitive=sensitive
        )

    # ---------------------------------------------------------------- commands

    async def arm_areas(
        self,
        modes: Mapping[int, ArmMode],
        *,
        on_not_ready: NotReadyPolicy | None = None,
    ) -> CommandResult:
        """Arm or disarm areas (1-based); areas not given are kept.

        With on_not_ready, the zones that are not ready for the areas armed
        are read first in the same session; if there are any, on_not_ready
        decides whether to send the command or raise NotReadyError.
        """
        async with self.transport.session(Priority.COMMAND) as session:
            model = await self._identify(session)
            command = arm_areas(self._pin, modes, model)
            if on_not_ready is not None:
                zones = await self._not_ready_for_areas(session, modes)
                if zones and not on_not_ready(zones):
                    raise NotReadyError(zones, areas=frozenset(modes))
            return await self._send(session, command)

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

    async def set_output(self, output: int, value: int) -> CommandResult:
        """Switch an output: 0/1 off/on, 0-100 for a dimmer.

        output is the command's OUTPUT byte (see protocol.config.output_address).
        """
        return await self._command(
            lambda model: set_output(self._pin, output, value, model)
        )

    async def read_output_levels(
        self, terminals: Iterable[int]
    ) -> dict[int, int | None]:
        """The level (0-100) of dimmer output terminals (0-based).

        Empty when the panel cannot tell (no output detail on its firmware,
        or the PIN matches no user code); None for a terminal it gave no
        detail for.
        """
        wanted = sorted(set(terminals))
        if not wanted:
            return {}
        async with self.transport.session() as session:
            model = await self._identify(session)
            code = self.user_code_index
            if not model.output_detail or code is None:
                return {}
            levels: dict[int, int | None] = {}
            for terminal in wanted:
                reply = await session.read(
                    output_detail_request(code, terminal, model),
                    OUTPUT_DETAIL_LENGTH,
                )
                levels[terminal] = decode_output_level(reply)
        return levels

    async def activate_scenario(
        self,
        scenario: int,
        *,
        check_ready: bool = True,
        on_not_ready: NotReadyPolicy | None = None,
    ) -> CommandResult:
        """Activate a scenario (0-based).

        With check_ready, the not-ready zones are read first in the same
        session; if there are any, on_not_ready decides whether to send the
        command anyway (without it, NotReadyError is raised).
        """
        async with self.transport.session(Priority.COMMAND) as session:
            model = await self._identify(session)
            command = activate_scenario(self._pin, scenario, model)
            if check_ready:
                zones = await self._not_ready_zones(session, scenario)
                if zones and (on_not_ready is None or not on_not_ready(zones)):
                    raise NotReadyError(zones, scenario=scenario)
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


# Blocks of the programming, not read while the panel is in programming mode
_PROGRAMMING = (Space.EEPROM, Space.LABELS)


def _report_order(table: ReportTable) -> list[ReportBlock]:
    """Registers first, then EEPROM by address with the event log last, then
    labels (the label pointers come with the registers)."""
    registers = [b for b in table.blocks if b.space in (Space.RAM, Space.QUERY)]
    eeprom = sorted(
        (b for b in table.blocks if b.space is Space.EEPROM),
        key=lambda b: (b.name == "event_log", b.address),
    )
    labels = [b for b in table.blocks if b.space is Space.LABELS]
    return registers + eeprom + labels


def _error_kind(err: Exception) -> str:
    """An error's kind for a report: no address, no data."""
    name = type(err).__name__
    return "".join(
        "_" + c.lower() if c.isupper() and i else c.lower() for i, c in enumerate(name)
    )


def _panel_state(faults: bytes) -> PanelState:
    """The panel state flags, read with the faults register."""
    start = reg.PANEL_STATE_OFFSET
    return decode_panel_state(faults[start : start + reg.PANEL_STATE_LENGTH])


def create_panel(
    host: str,
    port: int,
    pin: str,
    *,
    board: str = BOARD_SMARTLAN_SI,
    username: str = "",
    password: str = "",
    **options: float,
) -> SmartLivingPanel:
    """A panel behind a SmartLAN/SI or SmartLAN/G board.

    User name and password are the SmartLAN/G board's (they derive its
    encryption key). Options are the transport's (timeouts, pauses). Panels
    without a profile of their own (models other than those supported,
    firmware below 6) are handled with the SmartLiving 1050's layout, with a
    warning.
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
    return SmartLivingPanel(transport, pin, fallback_model=SMARTLIVING_1050_FW6)
