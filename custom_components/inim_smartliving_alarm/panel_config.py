"""Reading the panel configuration and storing it in the config entry."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DATA_API_CLIENT, DATA_INITIAL_PANEL_CONFIG, DOMAIN
from .inim_api import InimAlarmAPI

_LOGGER = logging.getLogger(__name__)


async def async_fetch_panel_configuration(
    hass: HomeAssistant, api: InimAlarmAPI
) -> dict[str, Any]:
    """Read the panel configuration (names, zone setup, scenarios) and check it.

    Raises ConnectionError if the panel cannot be reached and ValueError if
    the configuration is incomplete or invalid.
    """
    host, port = api.host, api.port
    _LOGGER.debug("Fetching the panel configuration from %s:%s", host, port)

    initial_config = await hass.async_add_executor_job(
        api.get_initial_panel_configuration
    )

    if initial_config is None:
        _LOGGER.error(
            "Connection/API error for %s:%s (API returned None)",
            host,
            port,
        )
        raise ConnectionError(
            "Failed to connect to the Inim panel or API error during fetch."
        )

    # Check for explicit errors reported by the API method
    if initial_config.get("errors"):
        _LOGGER.error(
            "Errors reported by API for %s:%s: %s",
            host,
            port,
            initial_config["errors"],
        )
        # If critical data is missing due to these errors, we'll catch it below.

    essential_keys = [
        "system_info",
        "areas",
        "zones",
        "zones_config",
        "scenarios",
        "scenario_activations",
    ]
    missing_or_failed_parts = [
        key for key in essential_keys if initial_config.get(key) is None
    ]

    if missing_or_failed_parts:
        error_message = f"Failed to retrieve essential data for: {', '.join(missing_or_failed_parts)}."
        _LOGGER.error("%s From %s:%s", error_message, host, port)
        if any("auth" in error.lower() for error in initial_config.get("errors", [])):
            raise ValueError("auth_failed")
        raise ValueError(error_message)

    if not initial_config.get("system_info") or not initial_config["system_info"].get(
        "ascii"
    ):
        _LOGGER.error("System information is missing from initial configuration")
        raise ValueError("invalid_panel_response")

    _LOGGER.info(
        "Panel configuration successfully fetched and validated for %s:%s",
        host,
        port,
    )
    return initial_config


async def async_reread_panel_configuration(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Read the panel configuration again, store it in the entry and reload it.

    Options are left untouched. The entry's own API client is used, so the
    read waits for any poll in progress instead of opening a second socket.
    """
    api: InimAlarmAPI = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]
    try:
        panel_config = await async_fetch_panel_configuration(hass, api)
    except ConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": api.host, "port": str(api.port)},
        ) from err
    except ValueError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_panel_configuration",
            translation_placeholders={"error": str(err)},
        ) from err

    _LOGGER.info("Panel configuration re-read for %s, reloading", entry.title)
    # A changed entry is reloaded by the entry's update listener; reload an
    # unchanged one here so that the action always ends with exactly one reload.
    if not hass.config_entries.async_update_entry(
        entry, data={**entry.data, DATA_INITIAL_PANEL_CONFIG: panel_config}
    ):
        hass.config_entries.async_schedule_reload(entry.entry_id)
