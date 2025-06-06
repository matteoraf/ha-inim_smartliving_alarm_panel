"""The Inim Alarm integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_HOST,
    CONF_PIN,
    CONF_POLLING_INTERVAL,
    CONF_PORT,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    PLATFORMS,
)

# Import the custom coordinator
from .coordinator import InimDataUpdateCoordinator

# Import API
from .inim_api import InimAlarmAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Inim Alarm from a config entry."""
    _LOGGER.info(
        "Setting up Inim Alarm integration for entry: %a (%s)",
        entry.title,
        entry.entry_id,
    )

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    pin = entry.data[CONF_PIN]

    # Polling interval from options (if user changed it) or from initial data
    polling_interval = entry.options.get(
        CONF_POLLING_INTERVAL,
        entry.data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
    )

    # Create API instance
    # The API constructor can take system_max_areas and system_max_zones.
    # These could be determined during config_flow (e.g., from get_initial_panel_configuration)
    # and stored in entry.data if we want to be very specific per panel model.
    # For now, using API defaults for these, which are sensible (e.g., 10 areas, 50 zones).
    api = InimAlarmAPI(host=host, port=port, pin_code_str=pin)

    # Create the custom coordinator instance
    coordinator_name = f"{DOMAIN} data ({entry.title})"
    coordinator = InimDataUpdateCoordinator(
        hass=hass,
        entry=entry,
        api_client=api,
        name=coordinator_name,
        update_interval_seconds=polling_interval,
    )

    # Fetch initial data to ensure coordinator has data before entities are set up.
    # This also serves as a connection test on startup for the coordinator's update loop.
    _LOGGER.debug(
        "(%s) Performing initial data refresh for coordinator...", entry.title
    )
    await coordinator.async_config_entry_first_refresh()

    # Check if the first refresh failed critically (e.g., connection error, auth error handled by coordinator)
    if not coordinator.last_update_success:
        _LOGGER.error(
            "(%s) Initial data fetch failed. Integration setup will be retried.",
            entry.title,
        )
        # ConfigEntryNotReady tells HA to retry setup later.
        # The specific error (ConnectionError, AuthError) would have been logged by the coordinator.
        raise ConfigEntryNotReady(
            f"Failed to fetch initial data from Inim panel at {host}:{port}"
        )

    # Store the API instance and coordinator in hass.data for platforms to access.
    # Platforms will also access static config from entry.data (set by config_flow).
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api,
        DATA_COORDINATOR: coordinator,
        # Static config like scenario names, zone types, etc., is in entry.data
        # (e.g., entry.data[DATA_INITIAL_PANEL_CONFIG])
    }

    # Set up platforms (alarm_control_panel, binary_sensor, switch, button, sensor)
    _LOGGER.debug("(%s) Forwarding setup to platforms: %s"), entry.title, PLATFORMS
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for option updates (e.g., if user changes polling interval via UI)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    _LOGGER.info("Inim Alarm integration for %s set up successfully.", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Inim Alarm integration for %s", entry.title)
    # This is called when an integration entry is removed from HA or HA is shutting down.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Clean up data stored in hass.data
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)
            _LOGGER.debug("Cleaned up data for %s", entry.title)
        if not hass.data[DOMAIN]:  # If no more entries for this domain
            hass.data.pop(DOMAIN)
            _LOGGER.debug("Cleaned up domain data for %s", DOMAIN)
    else:
        _LOGGER.error("Failed to unload platforms for %s", entry.title)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug(
        "Checking migration for Inim Alarm config entry version %s",
        config_entry.version,
    )
    # Add migration logic if config entry version changes in the future.
    # For now, assuming version 1 is the current version and requires no migration.
    if config_entry.version == 1:
        # No migration needed from version 1 to 1
        return True

    _LOGGER.error(
        "Unsupported Inim Alarm config entry version for migration: %s",
        config_entry.version,
    )
    return False  # Return False if migration fails or is not supported


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info(
        "Inim Alarm configuration options updated for %s, reloading entry to apply changes.",
        entry.title,
    )
    # This is called when options (e.g., polling interval, scenario mappings) are changed via UI.
    # Reload the entry to apply the new options.
    await hass.config_entries.async_reload(entry.entry_id)
