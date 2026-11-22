"""DataUpdateCoordinator for the Inim Alarm integration."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SmartLivingPanel
from .const import (
    CONF_EVENT_LOG_SIZE,
    DEFAULT_EVENT_LOG_SIZE,
    KEY_LATEST_EVENT_INDEX_VAL,
    KEY_LIVE_OUTPUT_LEVELS,
    KEY_PROCESSED_EVENTS,
)
from .legacy import LegacyPanelClient
from .outputs import is_dimmer
from .protocol.errors import ProtocolError
from .selection import imported_outputs, panel_config
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)


class InimDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        # Status and events in the entities' dictionaries
        self._reads = LegacyPanelClient(panel)
        self.panel_host_port = f"{panel.transport.host}:{panel.transport.port}"
        self._last_known_event_index_val: int | None = None
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the Inim Alarm panel using the API."""
        _LOGGER.debug("Polling Inim panel (%s) for live status and events", self.name)
        current_data: dict[str, Any] = {}

        try:
            # Fetch standard status data
            status_data = await self._reads.get_live_status_snapshot()

            if status_data is None:
                raise UpdateFailed(
                    f"Failed to get live status snapshot from Inim panel at {self.panel_host_port} (API returned None)"
                )
            if status_data.get("error") == "Connection failure":
                raise UpdateFailed(
                    f"Failed to connect to Inim panel at {self.panel_host_port} for live update"
                )
            if status_data.get("errors"):
                _LOGGER.warning(
                    "(%s) Errors during live status update: %s",
                    self.name,
                    status_data["errors"],
                )

            current_data.update(
                status_data
            )  # Add status data to the coordinator's data
            current_data[KEY_LIVE_OUTPUT_LEVELS] = await self._read_dimmer_levels()

            # --- Fetch Compact Events ---
            event_fetch_count_config = self.config_entry.options.get(
                CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
            )

            api_call_count_param: int | None = None
            api_call_last_index_param: int | None = self._last_known_event_index_val

            if (
                self._last_known_event_index_val is None
            ):  # First run after startup/reload
                # For the very first fetch, use the configured event_log_size as the 'count'
                # to get a decent backlog. The API will fetch the 'count' newest.
                api_call_count_param = event_fetch_count_config
                api_call_last_index_param = (
                    None  # Don't provide last_index if using count
                )
                _LOGGER.info(
                    "First event poll for %s or no last index known, requesting up to %s recent events",
                    self.name,
                    api_call_count_param,
                )
            # else, for subsequent polls, api_call_count_param remains None, and api_call_last_index_param is used.

            _LOGGER.debug(
                "Fetching compact events for %s. Last known index: %s, Explicit count for API: %s",
                self.name,
                self._last_known_event_index_val
                if self._last_known_event_index_val is not None
                else -1,
                api_call_count_param,
            )

            event_result = await self._reads.execute_get_compact_events(
                count=api_call_count_param,
                last_processed_compact_event_index_val=api_call_last_index_param,
                # the panel stores local time: read it in Home Assistant's zone
                time_zone=dt_util.get_default_time_zone(),
            )

            newly_fetched_events_raw: list[dict[str, Any]] = []
            panel_latest_index: int | None = None

            if event_result:
                newly_fetched_events_raw = event_result.get("events", [])
                panel_latest_index = event_result.get("latest_event_index_val")

                if newly_fetched_events_raw:
                    _LOGGER.info(
                        "Fetched %s new compact events for %s",
                        len(newly_fetched_events_raw),
                        self.name,
                    )
                elif (
                    api_call_count_param is None
                ):  # Only log "no new" if it was an incremental fetch
                    _LOGGER.debug(
                        "No new compact events found for %s since last poll (index %s)",
                        self.name,
                        self._last_known_event_index_val
                        if self._last_known_event_index_val is not None
                        else -1,
                    )

                if panel_latest_index is not None:
                    if self._last_known_event_index_val != panel_latest_index:
                        _LOGGER.debug(
                            "Updating last known event index for %s from %s to %s",
                            self.name,
                            self._last_known_event_index_val
                            if self._last_known_event_index_val is not None
                            else -1,
                            panel_latest_index,
                        )
                    # Always update to the panel's current head.
                    # The sensor will decide which of these newly_fetched_events to add to its internal log.
                    self._last_known_event_index_val = panel_latest_index

            current_data[KEY_PROCESSED_EVENTS] = (
                newly_fetched_events_raw  # Raw events from THIS poll
            )
            current_data[KEY_LATEST_EVENT_INDEX_VAL] = (
                self._last_known_event_index_val
            )  # Panel's absolute latest event index

            return current_data

        except UpdateFailed:
            # Home Assistant logs the first failure and the recovery
            raise
        except Exception as err:
            _LOGGER.error(
                "(%s) Unexpected error fetching data: %s", self.name, err, exc_info=True
            )
            raise UpdateFailed(
                f"Error communicating with API for {self.panel_host_port}: {err}"
            ) from err

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
        except (TransportError, ProtocolError, ValueError) as err:
            _LOGGER.debug("(%s) Dimmer levels not read: %s", self.name, err)
            return {}
