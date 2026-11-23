"""DataUpdateCoordinator for the Inim Alarm integration.

The live status is read in polling groups, each at its own interval (see
polling.py). A full read of every group runs at setup, after commands and
on request; between them, the coordinator reads only the groups due and
tells the entities only when something changed.

In slow polling mode every group is read at one longer interval, and the
connection is closed after each read or command, so that other apps can
connect to a board that accepts one client at a time.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    PANEL_ERRORS,
    LoggedEvent,
    PanelStatus,
    SmartLivingPanel,
    StatusReading,
)
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_EVENT_LOG_SIZE,
    DEFAULT_EVENT_LOG_SIZE,
)
from .polling import (
    PollGroup,
    PollSchedule,
    intervals,
    slow_intervals,
    slow_polling_minutes,
)
from .selection import imported_outputs, is_dimmer, panel_config
from .transport.base import ConnectError

_LOGGER = logging.getLogger(__name__)

# A refresh asked for (after a command) runs at once; another one asked for
# within this time runs once at its end
REFRESH_COOLDOWN = 1.0  # seconds
# Reads failing for longer than this (from the first failed read to the
# last) are logged when they go through again, with a hint: a panel may stop
# answering for a while after a change to its programming
LONG_OUTAGE = 20.0  # seconds


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
        # When the first of the reads failing now started, and when the last
        # one failed (None: reads go through)
        self._failing_since: float | None = None
        self._last_failure: float | None = None
        # Stopped by unload or a failed setup: slow polling mode is not
        # turned on then (_polling is also False with polling turned off)
        self._stopped = False
        # Whether this board may be held by another app
        self._board_in_use_hint = (
            entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI) == BOARD_SMARTLAN_SI
        )
        # Slow polling mode, and when it turns itself off (None: never)
        self.slow_polling = False
        self.slow_polling_until: datetime | None = None
        self._unsub_slow_polling_end: CALLBACK_TYPE | None = None

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
                    raise self._failure(err) from err
                self._output_levels = await self._read_dimmer_levels()
                await self._read_new_events()
            except UpdateFailed:
                self._note_failure(started)
                raise
            except Exception as err:
                self._note_failure(started)
                self._log_unexpected(err)
                raise UpdateFailed(
                    f"Error communicating with API for {self.panel_host_port}: {err}"
                ) from err
            self.schedule.read_all(started)
            self._note_success()
            return self._panel_data(status)

    # ------------------------------------------------------- scheduled reads

    def async_start_polling(self) -> None:
        """Read the groups at their intervals, from now until stopped.

        Nothing is scheduled while the entry's "Enable polling for updates"
        system option is off; refreshes after commands still read. The end
        of slow polling mode is set again if a stop cancelled it.
        """
        self._stopped = False
        if self.slow_polling and self._unsub_slow_polling_end is None:
            self._arm_slow_polling_end(self.slow_polling_until)
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
        self._stopped = True
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        self._cancel_slow_polling_end()
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
                if group is PollGroup.EVENTS:
                    await self._read_new_events()
                else:
                    status = (await self._read_group(group)).apply(status)
                # Every group of a wake-up counts from its start, so groups
                # due together stay together (one connection a read when
                # the connection is closed after each)
                self.schedule.read(group, now)
        except PANEL_ERRORS as err:
            self._read_failed(status, self._failure(err), err, now)
            return
        except Exception as err:  # noqa: BLE001 - like a full read, it fails the poll
            self._log_unexpected(err)
            self._read_failed(
                status,
                UpdateFailed(
                    f"Error communicating with API for {self.panel_host_port}: {err}"
                ),
                err,
                now,
            )
            return
        self._note_success()
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
        self,
        status: PanelStatus,
        failure: UpdateFailed,
        err: Exception,
        started: float,
    ) -> None:
        """A group read failed: the entities become unavailable until a read
        goes through; Home Assistant logs the first failure.

        What the groups before it read is kept, without telling the
        entities: the next good read hands it over with the rest, and the
        events read meanwhile with it.
        """
        failure.__cause__ = err
        self._note_failure(started)
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

    def _failure(self, err: Exception) -> UpdateFailed:
        """The failure of a read the panel did not answer."""
        if isinstance(err, ConnectError):
            in_use = " or is in use by another app" if self._board_in_use_hint else ""
            return UpdateFailed(
                f"Cannot connect to the Inim panel at {self.panel_host_port}: the "
                f"board can't be reached{in_use}"
            )
        return UpdateFailed(
            f"Failed to connect to Inim panel at {self.panel_host_port} for live update"
        )

    def _note_failure(self, started: float) -> None:
        """A read that started then failed: the next one waits."""
        self.schedule.failed(_now())
        if self._failing_since is None:
            self._failing_since = started
        self._last_failure = _now()

    def _note_success(self) -> None:
        """A read went through: back to the intervals.

        After a long time without answers, one line says how long, and how
        to read the panel's configuration again in case it was changed
        meanwhile (a panel may stop answering after a programming change).
        The time runs from the first failed read to the last, so one read
        that failed, followed by a long interval, is not a long time.
        """
        self.schedule.succeeded()
        self._unexpected_logged = False
        if self._failing_since is None or self._last_failure is None:
            return
        seconds = self._last_failure - self._failing_since
        self._failing_since = self._last_failure = None
        if seconds >= LONG_OUTAGE:
            _LOGGER.info(
                "%s: the panel did not answer for at least %.0f s. If its "
                "programming was changed meanwhile, read its configuration again "
                "with the Re-read panel configuration button or action",
                self.name,
                seconds,
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

    # ---------------------------------------------------- slow polling mode

    @property
    def _closed(self) -> bool:
        """Whether the entry is unloading or failed to set up, or Home
        Assistant is stopping: slow polling mode is not turned on then."""
        return self._stopped or self.hass.is_stopping or self._shutdown_requested

    async def async_set_slow_polling(self, on: bool) -> None:
        """Turn slow polling mode on (for the minutes set in the options) or
        off."""
        if not on:
            await self._async_end_slow_polling()
            return
        if self._closed:
            return
        minutes = slow_polling_minutes(self.config_entry.options)
        until = dt_util.utcnow() + timedelta(minutes=minutes) if minutes else None
        await self._async_start_slow_polling(until)

    async def async_restore_slow_polling(self, until: datetime | None) -> None:
        """Turn slow polling mode on again after a restart or a reload.

        until is when it was to end (None: until turned off); it is not
        turned on if that time has passed.
        """
        if self._closed or (until is not None and until <= dt_util.utcnow()):
            return
        await self._async_start_slow_polling(until)

    async def _async_start_slow_polling(self, until: datetime | None) -> None:
        """Read every group at the slow interval, closing the connection
        after each read, until the given time (None: until turned off)."""
        self._arm_slow_polling_end(until)
        if not self.slow_polling:
            self.slow_polling = True
            self.panel.set_shared(True)
            slow = slow_intervals(self.config_entry.options)
            async with self._reading:
                self.schedule = PollSchedule(slow)
                self.schedule.read_all(_now())  # the next read is one interval away
                await self.panel.release()
            _LOGGER.info(
                "%s: slow polling mode on: the panel is read every %g s and the "
                "connection is closed after each read%s",
                self.name,
                next(iter(slow.values())),
                ""
                if until is None
                else f", until {dt_util.as_local(until).strftime('%H:%M')}",
            )
            self._schedule_tick()
        self.async_update_listeners()

    async def _async_end_slow_polling(self) -> None:
        """Back to the polling groups' intervals: every group is read now."""
        self._cancel_slow_polling_end()
        self.slow_polling_until = None
        if self.slow_polling:
            self.slow_polling = False
            self.panel.set_shared(False)
            async with self._reading:
                self.schedule = PollSchedule(self.intervals)  # every group due
            _LOGGER.info(
                "%s: slow polling mode off: the panel is read at the Polling "
                "intervals again",
                self.name,
            )
            self._schedule_tick()
        self.async_update_listeners()

    def _arm_slow_polling_end(self, until: datetime | None) -> None:
        """End slow polling mode at that time (None: never)."""
        self._cancel_slow_polling_end()
        self.slow_polling_until = until
        if until is not None:
            self._unsub_slow_polling_end = async_track_point_in_utc_time(
                self.hass, self._slow_polling_ended, until
            )

    @callback
    def _slow_polling_ended(self, _fired: datetime) -> None:
        """The time set for slow polling mode is over."""
        self._unsub_slow_polling_end = None
        until = self.slow_polling_until
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_end_slow_polling_at(until),
            f"{self.name} slow polling end",
        )

    async def _async_end_slow_polling_at(self, until: datetime | None) -> None:
        """End slow polling mode, unless it was turned on again meanwhile
        with another end time."""
        if self.slow_polling and self.slow_polling_until == until:
            await self._async_end_slow_polling()

    def _cancel_slow_polling_end(self) -> None:
        if self._unsub_slow_polling_end is not None:
            self._unsub_slow_polling_end()
            self._unsub_slow_polling_end = None

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
