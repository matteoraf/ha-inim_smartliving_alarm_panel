"""DataUpdateCoordinator for the Inim Alarm integration.

The live status is read in polling groups, each at its own interval (see
polling.py). A full read of every group runs at setup, after commands and
on request; between them, the coordinator reads only the groups due and
tells the entities only when something changed.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PANEL_ERRORS,
    LoggedEvent,
    PanelStatus,
    SmartLivingPanel,
    StatusReading,
)
from .const import CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
from .polling import PollGroup, PollSchedule, intervals
from .selection import imported_outputs, is_dimmer, panel_config

_LOGGER = logging.getLogger(__name__)

# A refresh asked for (after a command) runs at once; another one asked for
# within this time runs once at its end
REFRESH_COOLDOWN = 1.0  # seconds


def _now() -> float:
    """The clock of the polling schedule."""
    return time.monotonic()


@dataclass(frozen=True, slots=True)
class PanelData:
    """The panel's live status, as last read."""

    status: PanelStatus  # every group's latest reading
    new_events: tuple[LoggedEvent, ...]  # read in this update, newest first
    latest_event_index: int | None  # newest event in the ring, as last known
    # Imported dimmers' levels: logical terminal (0-based) -> 0-100 or None
    output_levels: Mapping[int, int | None]


class InimDataUpdateCoordinator(DataUpdateCoordinator[PanelData]):
    """Reads the Inim alarm panel's live status, group by group."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        panel: SmartLivingPanel,
        name: str,
    ) -> None:
        """Initialize the data update coordinator."""
        self.panel = panel
        self.panel_host_port = f"{panel.transport.host}:{panel.transport.port}"
        self.intervals = intervals(entry.options)
        self.schedule = PollSchedule(self.intervals)
        self._latest_event_index: int | None = None
        # Whether the last event read failed (it is logged once)
        self._events_failing = False
        self._output_levels: Mapping[int, int | None] = {}
        # Events read but not handed to the entities yet (a later group of
        # the same read failed), newest first
        self._pending_events: tuple[LoggedEvent, ...] = ()
        # Whether an unexpected error was logged with its traceback (once,
        # until a read goes through)
        self._unexpected_logged = False
        # One read at a time: a group read and a full read never overlap
        self._reading = asyncio.Lock()
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._polling = False
        self._tick_job = HassJob(self._async_tick, f"{name} polling")

        # Home Assistant does not poll: the groups have their own schedule
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=name,
            update_interval=None,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        _LOGGER.info(
            "%s reads the panel every %s",
            self.name,
            ", ".join(
                f"{seconds:g} s ({group})" for group, seconds in self.intervals.items()
            ),
        )

    # ------------------------------------------------------------- full read

    async def _async_update_data(self) -> PanelData:
        """Read every group: the status, the dimmer levels and the new events."""
        _LOGGER.debug("Polling Inim panel (%s): every group", self.name)
        async with self._reading:
            started = _now()
            try:
                try:
                    status = await self.panel.read_status()
                except PANEL_ERRORS as err:
                    # Home Assistant logs the first failure and the recovery
                    raise UpdateFailed(
                        f"Failed to connect to Inim panel at {self.panel_host_port} "
                        "for live update"
                    ) from err
                self._output_levels = await self._read_dimmer_levels()
                await self._read_new_events()
            except UpdateFailed:
                self.schedule.failed(_now())
                raise
            except Exception as err:
                self.schedule.failed(_now())
                self._log_unexpected(err)
                raise UpdateFailed(
                    f"Error communicating with API for {self.panel_host_port}: {err}"
                ) from err
            self.schedule.read_all(started)
            self.schedule.succeeded()
            self._unexpected_logged = False
            return self._panel_data(status)

    # ------------------------------------------------------- scheduled reads

    def async_start_polling(self) -> None:
        """Read the groups at their intervals, from now until stopped.

        Nothing is scheduled while the entry's "Enable polling for updates"
        system option is off; refreshes after commands still read.
        """
        if self.config_entry.pref_disable_polling:
            _LOGGER.info("%s: polling is turned off for this entry", self.name)
            return
        self._polling = True
        self._schedule_tick()

    async def async_stop_polling(self) -> None:
        """Stop the scheduled reads, and wait for a read in progress.

        Once it returns, nothing reads the panel on a schedule: its
        connection can be closed without a read opening it again.
        """
        self._polling = False
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        async with self._reading:
            pass

    @property
    def _stopping(self) -> bool:
        """Whether scheduled reads must end: stopped, or Home Assistant or
        the coordinator shutting down."""
        return not self._polling or self.hass.is_stopping or self._shutdown_requested

    def _schedule_tick(self) -> None:
        if self._stopping:
            return
        if self._unsub_tick is not None:
            self._unsub_tick()
        delay = self.schedule.next_wake(_now())
        self._unsub_tick = async_call_later(self.hass, delay, self._tick_job)

    async def _async_tick(self, _fired: datetime) -> None:
        """Read the groups due, then wait for the next one."""
        self._unsub_tick = None
        try:
            async with self._reading:
                if not self._stopping and self.data is not None:
                    await self._read_due_groups()
        finally:
            self._schedule_tick()

    async def _read_due_groups(self) -> None:
        now = _now()
        due = self.schedule.due(now)
        if not due:
            return
        if self.schedule.failures == 0:
            for group in due:
                self._check_pace(group, now)
        _LOGGER.debug("Polling Inim panel (%s): %s", self.name, ", ".join(due))
        status = self.data.status
        try:
            for group in due:
                if self._stopping:
                    return
                started = _now()
                if group is PollGroup.EVENTS:
                    await self._read_new_events()
                else:
                    status = (await self._read_group(group)).apply(status)
                self.schedule.read(group, started)
        except PANEL_ERRORS as err:
            self._read_failed(
                status,
                UpdateFailed(
                    f"Failed to connect to Inim panel at {self.panel_host_port} "
                    "for live update"
                ),
                err,
            )
            return
        except Exception as err:  # noqa: BLE001 - like a full read, it fails the poll
            self._log_unexpected(err)
            self._read_failed(
                status,
                UpdateFailed(
                    f"Error communicating with API for {self.panel_host_port}: {err}"
                ),
                err,
            )
            return
        self.schedule.succeeded()
        self._unexpected_logged = False
        self._publish(self._panel_data(status))

    async def _read_group(self, group: PollGroup) -> StatusReading:
        """Read one group of the status (not the events)."""
        readers: dict[PollGroup, Callable[[], Awaitable[StatusReading]]] = {
            PollGroup.ZONES: self.panel.read_zones,
            PollGroup.AREAS: self.panel.read_areas,
            PollGroup.INCLUSION: self._read_inclusion,
            PollGroup.FAULTS: self.panel.read_panel_state,
        }
        return await readers[group]()

    async def _read_inclusion(self) -> StatusReading:
        """The zone inclusion, and the dimmer levels with it."""
        reading = await self.panel.read_zone_inclusion()
        self._output_levels = await self._read_dimmer_levels()
        return reading

    def _read_failed(
        self, status: PanelStatus, failure: UpdateFailed, err: Exception
    ) -> None:
        """A group read failed: the entities become unavailable until a read
        goes through; Home Assistant logs the first failure.

        What the groups before it read is kept, without telling the
        entities: the next good read hands it over with the rest, and the
        events read meanwhile with it.
        """
        failure.__cause__ = err
        self.schedule.failed(_now())
        self.data = replace(
            self.data, status=status, new_events=(), output_levels=self._output_levels
        )
        self.async_set_update_error(failure)

    def _log_unexpected(self, err: Exception) -> None:
        """An unexpected error: its traceback once, then at debug until a
        read goes through."""
        if self._unexpected_logged:
            _LOGGER.debug("(%s) Unexpected error fetching data: %s", self.name, err)
            return
        self._unexpected_logged = True
        _LOGGER.error(
            "(%s) Unexpected error fetching data: %s", self.name, err, exc_info=True
        )

    def _publish(self, data: PanelData) -> None:
        """Hand new data to the entities, unless nothing changed.

        Not async_set_updated_data: it would cancel a refresh waiting after
        a command.
        """
        recovered = not self.last_update_success
        if not recovered and not data.new_events and self.data is not None:
            if replace(data, new_events=()) == replace(self.data, new_events=()):
                return
        self.data = data
        self.last_update_success = True
        self.last_exception = None
        if recovered:
            self.logger.info("Fetching %s data recovered", self.name)
        self.async_update_listeners()

    def _check_pace(self, group: PollGroup, now: float) -> None:
        """Report once when a group's reads keep starting late, and when
        they catch up."""
        late = self.schedule.pace(group, now)
        if late:
            _LOGGER.warning(
                "%s: the panel cannot be read every %g s (%s); reads start "
                "later than that. A longer interval in the Polling options "
                "avoids it",
                self.name,
                self.intervals[group],
                group,
            )
        elif late is False:
            _LOGGER.info(
                "%s: reads keep up with the %g s interval (%s) again",
                self.name,
                self.intervals[group],
                group,
            )

    # --------------------------------------------------------------- parts

    def _panel_data(self, status: PanelStatus) -> PanelData:
        """The data to hand over, with the events not handed over yet."""
        new_events, self._pending_events = self._pending_events, ()
        return PanelData(
            status=status,
            new_events=new_events,
            latest_event_index=self._latest_event_index,
            output_levels=self._output_levels,
        )

    async def _read_new_events(self) -> None:
        """Read the events added since the last read.

        They wait, newest first, until data is handed to the entities. The
        first read after startup reads as many as the event log shows.
        An event read that fails gives none, and the poll goes on: the
        failure is logged once, and the recovery. A read that stops partway
        (a record that cannot be read) still moves on to the newest event,
        so the events not read in that window are not shown.
        """
        first = self._latest_event_index is None
        count = (
            self.config_entry.options.get(CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE)
            if first
            else None
        )
        if first:
            _LOGGER.info(
                "First event poll for %s or no last index known, requesting up "
                "to %s recent events",
                self.name,
                count,
            )
        try:
            batch = await self.panel.read_events(
                count=count, after=None if first else self._latest_event_index
            )
        except PANEL_ERRORS as err:
            if self._events_failing:
                _LOGGER.debug("Cannot read the event pointers: %s", err)
            else:
                _LOGGER.warning(
                    "Cannot read the event pointers: %s; the event log is read "
                    "again once they can be read",
                    err,
                )
                self._events_failing = True
            return
        if self._events_failing:
            _LOGGER.info("Event pointers of %s read again", self.name)
            self._events_failing = False
        if batch.events:
            _LOGGER.info(
                "Fetched %s new compact events for %s", len(batch.events), self.name
            )
        if batch.latest_index is not None:
            self._latest_event_index = batch.latest_index
        self._pending_events = batch.events + self._pending_events

    async def _read_dimmer_levels(self) -> dict[int, int | None]:
        """The levels of the imported dimmers, if the panel reports them.

        Only imported dimmers are read; a failed read leaves them unknown
        until the next read.
        """
        dimmers = [
            item.terminal
            for item in imported_outputs(
                self.config_entry.options, panel_config(self.config_entry.data)
            )
            if is_dimmer(item) and item.address is not None
        ]
        if not dimmers:
            return {}
        try:
            return await self.panel.read_output_levels(dimmers)
        except PANEL_ERRORS as err:
            _LOGGER.debug("(%s) Dimmer levels not read: %s", self.name, err)
            return {}
