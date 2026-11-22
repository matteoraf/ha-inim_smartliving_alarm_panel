"""DataUpdateCoordinator for the Inim Alarm integration."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PANEL_ERRORS, LoggedEvent, PanelStatus, SmartLivingPanel
from .const import CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
from .selection import imported_outputs, is_dimmer, panel_config

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PanelData:
    """One poll of the panel."""

    status: PanelStatus
    new_events: tuple[LoggedEvent, ...]  # read in this poll, newest first
    latest_event_index: int | None  # newest event in the ring, as last known
    # Imported dimmers' levels: logical terminal (0-based) -> 0-100 or None
    output_levels: Mapping[int, int | None]


class InimDataUpdateCoordinator(DataUpdateCoordinator[PanelData]):
    """Manages fetching data from the Inim alarm panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        panel: SmartLivingPanel,
        name: str,
        update_interval_seconds: int,
    ) -> None:
        """Initialize the data update coordinator."""
        self.panel = panel
        self.panel_host_port = f"{panel.transport.host}:{panel.transport.port}"
        self._latest_event_index: int | None = None
        # Whether the last event read failed (it is logged once)
        self._events_failing = False
        self.config_entry = entry

        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        _LOGGER.info(
            "InimDataUpdateCoordinator initialized for %s with update interval %ss",
            self.name,
            update_interval_seconds,
        )

    async def _async_update_data(self) -> PanelData:
        """Read the status, the dimmer levels and the events added since the
        last poll."""
        _LOGGER.debug("Polling Inim panel (%s) for live status and events", self.name)
        try:
            try:
                status = await self.panel.read_status()
            except PANEL_ERRORS as err:
                # Home Assistant logs the first failure and the recovery
                raise UpdateFailed(
                    f"Failed to connect to Inim panel at {self.panel_host_port} "
                    "for live update"
                ) from err
            output_levels = await self._read_dimmer_levels()
            new_events = await self._read_new_events()
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.error(
                "(%s) Unexpected error fetching data: %s", self.name, err, exc_info=True
            )
            raise UpdateFailed(
                f"Error communicating with API for {self.panel_host_port}: {err}"
            ) from err
        return PanelData(
            status=status,
            new_events=new_events,
            latest_event_index=self._latest_event_index,
            output_levels=output_levels,
        )

    async def _read_new_events(self) -> tuple[LoggedEvent, ...]:
        """The events added since the last poll, newest first.

        The first poll after startup reads as many as the event log shows.
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
            return ()
        if self._events_failing:
            _LOGGER.info("Event pointers of %s read again", self.name)
            self._events_failing = False
        if batch.events:
            _LOGGER.info(
                "Fetched %s new compact events for %s", len(batch.events), self.name
            )
        if batch.latest_index is not None:
            self._latest_event_index = batch.latest_index
        return batch.events

    async def _read_dimmer_levels(self) -> dict[int, int | None]:
        """The levels of the imported dimmers, if the panel reports them.

        Only imported dimmers are read; a failed read leaves them unknown
        for this poll.
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
