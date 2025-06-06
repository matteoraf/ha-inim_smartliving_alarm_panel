"""DataUpdateCoordinator for the Inim Alarm integration."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_EVENT_LOG_SIZE,
    DEFAULT_EVENT_LOG_SIZE,
    KEY_LATEST_EVENT_INDEX_VAL,
    KEY_PROCESSED_EVENTS,
)
from .inim_api import InimAlarmAPI

_LOGGER = logging.getLogger(__name__)


class InimDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages fetching data from the Inim alarm panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: InimAlarmAPI,
        name: str,
        update_interval_seconds: int,
    ) -> None:
        """Initialize the data update coordinator."""
        self.api_client = api_client
        self.panel_host_port = f"{api_client.host}:{api_client.port}"
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
            status_data = await self.hass.async_add_executor_job(
                self.api_client.get_live_status_snapshot
            )

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
                "Fetching compact events for %s. Last known index: 0x%04x, Explicit count for API: %s",
                self.name,
                self._last_known_event_index_val
                if self._last_known_event_index_val is not None
                else -1,
                api_call_count_param,
            )

            # Use a lambda function to pass keyword arguments to the executor job
            event_result = await self.hass.async_add_executor_job(
                lambda: self.api_client.execute_get_compact_events(
                    count=api_call_count_param,
                    last_processed_compact_event_index_val=api_call_last_index_param,
                )
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
                        "No new compact events found for %s since last poll (index 0x%04x)",
                        self.name,
                        self._last_known_event_index_val
                        if self._last_known_event_index_val is not None
                        else -1,
                    )

                if panel_latest_index is not None:
                    if self._last_known_event_index_val != panel_latest_index:
                        _LOGGER.debug(
                            "Updating last known event index for %s from 0x%04x to 0x%04x",
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

        except ConfigEntryAuthFailed as auth_err:
            _LOGGER.error("(%s) Authentication failed: %s", self.name, auth_err)
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {auth_err}"
            ) from auth_err
        except UpdateFailed as uf_err:
            _LOGGER.warning(
                "(%s) UpdateFailed during data fetch: %s", self.name, uf_err
            )
            raise
        except Exception as err:
            _LOGGER.error(
                "(%s) Unexpected error fetching data: %s", self.name, err, exc_info=True
            )
            raise UpdateFailed(
                f"Error communicating with API for {self.panel_host_port}: {err}"
            ) from err
