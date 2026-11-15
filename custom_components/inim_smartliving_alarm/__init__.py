"""The Inim Alarm integration."""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_POLLING_INTERVAL,
    CONF_PORT,
    CONF_USERNAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    PLATFORMS,
    SERVICE_REREAD_PANEL_CONFIGURATION,
)

# Import the custom coordinator
from .coordinator import InimDataUpdateCoordinator

# Import API
from .legacy import create_client, upgrade_zone_configuration, zone_exists
from .panel_config import async_reread_panel_configuration

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

REREAD_PANEL_CONFIGURATION_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""

    async def async_reread_service(call: ServiceCall) -> None:
        """Read the panel configuration of one entry again and reload it."""
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"title": entry.title},
            )
        await async_reread_panel_configuration(hass, entry)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REREAD_PANEL_CONFIGURATION,
        async_reread_service,
        schema=REREAD_PANEL_CONFIGURATION_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Inim Alarm from a config entry."""
    _LOGGER.info(
        "Setting up Inim Alarm integration for entry: %s (%s)",
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

    # One client per entry, holding one connection to the panel
    api = create_client(
        host,
        port,
        pin,
        board=entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI),
        username=entry.data.get(CONF_USERNAME, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
    )

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
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await api.close()
        raise

    # Check if the first refresh failed critically (e.g., connection error, auth error handled by coordinator)
    if not coordinator.last_update_success:
        _LOGGER.error(
            "(%s) Initial data fetch failed. Integration setup will be retried.",
            entry.title,
        )
        # ConfigEntryNotReady tells HA to retry setup later.
        # The specific error (ConnectionError, AuthError) would have been logged by the coordinator.
        await api.close()
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
    _warn_about_leftover_zone_entities(hass, entry)

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
            entry_data = hass.data[DOMAIN].pop(entry.entry_id)
            await entry_data[DATA_API_CLIENT].close()
            _LOGGER.debug("Cleaned up data for %s", entry.title)
        if not hass.data[DOMAIN]:  # If no more entries for this domain
            hass.data.pop(DOMAIN)
            _LOGGER.debug("Cleaned up domain data for %s", DOMAIN)
    else:
        _LOGGER.error("Failed to unload platforms for %s", entry.title)
    return unload_ok


# Unique ID suffixes of the zone entities, before the 1-based zone number
_ZONE_ENTITY_PREFIXES = (
    "_zone_",
    "_zone_triggered_",
    "_zone_tamper_",
    "_zone_enabled_switch_",
)


def _warn_about_leftover_zone_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Log the entities of zone numbers that are not zones on the panel.

    Such entities (unused or output terminals, second zones of terminals
    that are not double) were created by earlier versions. They are not
    removed automatically: the user deletes them.
    """
    config = entry.data.get(DATA_INITIAL_PANEL_CONFIG) or {}
    details = (config.get(KEY_INIT_ZONES_CONFIG) or {}).get(
        KEY_INIT_ZONES_CONFIG_DETAILED
    ) or []
    not_zones = {
        detail["zone_index"] + 1
        for detail in details
        if "zone_index" in detail and not zone_exists(detail)
    }
    if not not_zones:
        return
    leftovers = []
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        suffix = entity.unique_id.removeprefix(entry.entry_id)
        for prefix in _ZONE_ENTITY_PREFIXES:
            number = suffix.removeprefix(prefix)
            if number != suffix and number.isdigit() and int(number) in not_zones:
                leftovers.append(entity.entity_id)
                break
    if leftovers:
        _LOGGER.warning(
            "%s: these entities belong to terminals that are not zones on the "
            "panel (unused or output terminals) and are no longer updated; "
            "delete them in Settings > Devices & services > Entities: %s",
            entry.title,
            ", ".join(sorted(leftovers)),
        )


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate an entry to the current version (1.3).

    1.2 adds the board type; every older entry uses SmartLAN/SI. 1.3 stores
    every zone number with its terminal type, rebuilt from the raw panel
    data the entry already holds (no panel access). A newer major version
    (from a later integration release) cannot be loaded.
    """
    version, minor = config_entry.version, config_entry.minor_version
    _LOGGER.debug("Checking migration of config entry version %s.%s", version, minor)
    if version > 1:
        _LOGGER.error(
            "Config entry version %s.%s is newer than this integration supports",
            version,
            minor,
        )
        return False
    if minor < 2:
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, CONF_BOARD: BOARD_SMARTLAN_SI},
            minor_version=2,
        )
        _LOGGER.info("Migrated config entry %s to version 1.2", config_entry.title)
    if config_entry.minor_version < 3:
        data = dict(config_entry.data)
        upgraded = upgrade_zone_configuration(data.get(DATA_INITIAL_PANEL_CONFIG) or {})
        if upgraded is None:
            _LOGGER.warning(
                "The stored panel configuration of %s has no complete zone data; "
                "zones are shown as before. Use 'Re-read panel configuration' to "
                "read it from the panel",
                config_entry.title,
            )
        else:
            data[DATA_INITIAL_PANEL_CONFIG] = upgraded
        hass.config_entries.async_update_entry(config_entry, data=data, minor_version=3)
        _LOGGER.info("Migrated config entry %s to version 1.3", config_entry.title)
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info(
        "Inim Alarm configuration options updated for %s, reloading entry to apply changes.",
        entry.title,
    )
    # This is called when options (e.g., polling interval, scenario mappings) are changed via UI.
    # Reload the entry to apply the new options.
    await hass.config_entries.async_reload(entry.entry_id)
