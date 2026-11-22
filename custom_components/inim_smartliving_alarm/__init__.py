"""The Inim Alarm integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import SmartLivingPanel, create_panel
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_HOST,
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_POLLING_INTERVAL,
    CONF_PORT,
    CONF_READER_NAMES,
    CONF_USERNAME,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import InimDataUpdateCoordinator
from .panel_config import (
    PanelConfigError,
    PanelUnreachable,
    async_fetch_panel_configuration,
)
from .protocol.errors import UnsupportedPanelError
from .protocol.model import model_for
from .report_state import async_drop_report_state
from .selection import (
    item_of_unique_id,
    migrated_selection,
    panel_config,
    unselected_items,
)
from .services import (
    async_forget_action_descriptions,
    async_register_services,
    async_update_action_descriptions,
)
from .stored_config import (
    migrate_v1,
    missing_blocks,
    stored_layout_fits,
    stored_model,
    stored_zones,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LIMIT_KEYS = (CONF_LIMIT_AREAS, CONF_LIMIT_ZONES, CONF_LIMIT_SCENARIOS)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_register_services(hass)
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

    # One panel object per entry, holding one connection to the panel
    panel = create_panel(
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
        panel=panel,
        name=coordinator_name,
        update_interval_seconds=polling_interval,
    )

    # Fetch initial data to ensure coordinator has data before entities are set up.
    # This also serves as a connection test on startup for the coordinator's update loop.
    _LOGGER.debug(
        "(%s) Performing initial data refresh for coordinator...", entry.title
    )
    # Until the platforms are set up, any failure closes the panel's
    # connection: the board may accept only one
    try:
        await coordinator.async_config_entry_first_refresh()
        await _reread_configuration_if_outdated(hass, entry, panel)
        _report_unsupported_panel(hass, entry, panel)
        _remove_entities_not_imported(hass, entry)

        # The panel and the coordinator for the platforms; the stored panel
        # configuration is in entry.data[DATA_INITIAL_PANEL_CONFIG]
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            DATA_PANEL: panel,
            DATA_COORDINATOR: coordinator,
        }
        _LOGGER.debug("(%s) Forwarding setup to platforms: %s", entry.title, PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Keep nothing of a failed setup: the panel's connection included
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN].pop(entry.entry_id)
            if not hass.data[DOMAIN]:
                hass.data.pop(DOMAIN)
        await panel.close()
        raise
    _warn_about_leftover_zone_entities(hass, entry)
    await async_update_action_descriptions(hass)

    # Listen for option updates (e.g., if user changes polling interval via UI)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    # The diagnostics level and any report waiting for its readable download
    # do not outlive the entry: a reload starts at Redacted
    entry.async_on_unload(lambda: async_drop_report_state(hass, entry.entry_id))

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
            await entry_data[DATA_PANEL].close()
            _LOGGER.debug("Cleaned up data for %s", entry.title)
        if not hass.data.get(DOMAIN):  # If no more entries for this domain
            hass.data.pop(DOMAIN, None)
            _LOGGER.debug("Cleaned up domain data for %s", DOMAIN)
        await async_update_action_descriptions(hass)
        async_forget_action_descriptions(hass)
    else:
        _LOGGER.error("Failed to unload platforms for %s", entry.title)
    return unload_ok


def _remove_entities_not_imported(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entities of areas, zones and scenarios not imported.

    Only items on the panel that the user left out: entities of zone numbers
    that are not zones are left to the warning below.
    """
    config = panel_config(entry.data)
    unselected = unselected_items(entry.options, config)
    model = stored_model(config)
    terminals = model.terminals if model is not None else None
    registry = er.async_get(hass)
    removed = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        item = item_of_unique_id(entry.entry_id, entity.unique_id, terminals)
        if item is not None and item[1] in unselected[item[0]]:
            registry.async_remove(entity.entity_id)
            removed.append(entity.entity_id)
    if removed:
        _LOGGER.info(
            "%s: removed the entities of areas, zones, scenarios and outputs "
            "that are not imported: %s",
            entry.title,
            ", ".join(sorted(removed)),
        )


def _unsupported_issue_id(entry: ConfigEntry) -> str:
    return f"unsupported_panel_{entry.entry_id}"


def _report_unsupported_panel(
    hass: HomeAssistant, entry: ConfigEntry, panel: SmartLivingPanel
) -> None:
    """A repair issue while the panel has no profile of its own.

    Such a panel (firmware 5.0x, another model) set up earlier keeps running
    with the fallback layout, so its names, zones and scenarios may be
    wrong. The issue goes away once the panel is supported.
    """
    try:
        firmware = panel.firmware
    except RuntimeError:  # not identified (no successful read)
        return
    try:
        model_for(firmware)
    except UnsupportedPanelError:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _unsupported_issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unsupported_panel",
            translation_placeholders={"title": entry.title, "panel": firmware.text},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, _unsupported_issue_id(entry))


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entry's repair issue with it."""
    ir.async_delete_issue(hass, DOMAIN, _unsupported_issue_id(entry))


async def _reread_configuration_if_outdated(
    hass: HomeAssistant, entry: ConfigEntry, panel: SmartLivingPanel
) -> None:
    """Read the configuration again if the stored one is incomplete or outdated.

    Called at every setup, but it only looks at the stored configuration:
    nothing is read from the panel unless a block is missing (a
    configuration stored by an earlier version, or a re-read that failed)
    or it was stored with another layout. The panel model is known from the
    first status read, and the stored terminal records and peripherals must
    have its lengths; they differ once for a panel that got its own profile
    (another model, or firmware 5.1) after it was set up with an earlier
    version, which read it with the SmartLiving 1050's firmware 6 addresses.
    Unique IDs do not change. If the re-read fails, setup goes on with the
    stored configuration and the check runs again at the next start.
    """
    try:
        model = panel.model
    except RuntimeError:  # not identified (no successful read)
        return
    config = entry.data.get(DATA_INITIAL_PANEL_CONFIG) or {}
    if not config:
        return
    if not stored_layout_fits(config, model):
        _LOGGER.warning(
            "%s: the stored panel configuration does not fit the %s (it was "
            "read with another layout); reading it again from the panel",
            entry.title,
            model.name,
        )
    elif missing_blocks(config):
        _LOGGER.info(
            "%s: reading the panel configuration again for what is not stored "
            "yet (such as the names of user codes, keys and readers)",
            entry.title,
        )
    else:
        return
    try:
        new_config = await async_fetch_panel_configuration(hass, panel)
    except (PanelUnreachable, PanelConfigError) as err:
        _LOGGER.error(
            "%s: cannot read the panel configuration again (%s); it is tried "
            "again at the next start, or use 'Re-read panel configuration'",
            entry.title,
            getattr(err, "detail", err),
        )
        return
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, DATA_INITIAL_PANEL_CONFIG: new_config}
    )
    _LOGGER.info("%s: panel configuration read again", entry.title)


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
    not_zones = {zone.number for zone in stored_zones(config) if not zone.exists}
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
    """Migrate an entry to the current version (2.1).

    1.2 adds the board type; every older entry uses SmartLAN/SI. 1.3 stored
    every zone number with its terminal type; nothing is left of it, as 2.1
    decodes the zones from the stored records. 1.4 replaces the counts of
    areas, zones and scenarios with lists of the items to import, which keep
    the same entities. 1.5 drops the reader names option: reader names are
    read from the panel. 2.1 keeps the panel configuration as the blocks
    read from the panel, without what was derived from them (no panel
    access); what the earlier versions did not keep is read again at setup.
    A newer major version (from a later integration release) cannot be
    loaded.
    """
    version, minor = config_entry.version, config_entry.minor_version
    _LOGGER.debug("Checking migration of config entry version %s.%s", version, minor)
    if version > 2:
        _LOGGER.error(
            "Config entry version %s.%s is newer than this integration supports",
            version,
            minor,
        )
        return False
    if version > 1:
        return True
    if minor < 2:
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, CONF_BOARD: BOARD_SMARTLAN_SI},
            minor_version=2,
        )
        _LOGGER.info("Migrated config entry %s to version 1.2", config_entry.title)
    if config_entry.minor_version < 3:
        hass.config_entries.async_update_entry(config_entry, minor_version=3)
        _LOGGER.info("Migrated config entry %s to version 1.3", config_entry.title)
    if config_entry.minor_version < 4:
        selection = migrated_selection({**config_entry.data, **config_entry.options})
        options = {
            key: value
            for key, value in config_entry.options.items()
            if key not in _LIMIT_KEYS
        }
        data = {
            key: value
            for key, value in config_entry.data.items()
            if key not in _LIMIT_KEYS
        }
        hass.config_entries.async_update_entry(
            config_entry, data=data, options={**options, **selection}, minor_version=4
        )
        _LOGGER.info("Migrated config entry %s to version 1.4", config_entry.title)
    if config_entry.minor_version < 5:
        options = dict(config_entry.options)
        options.pop(CONF_READER_NAMES, None)
        hass.config_entries.async_update_entry(
            config_entry, options=options, minor_version=5
        )
        _LOGGER.info("Migrated config entry %s to version 1.5", config_entry.title)
    data = dict(config_entry.data)
    if DATA_INITIAL_PANEL_CONFIG in data:
        data[DATA_INITIAL_PANEL_CONFIG] = migrate_v1(
            data[DATA_INITIAL_PANEL_CONFIG] or {}
        )
    hass.config_entries.async_update_entry(
        config_entry, data=data, version=2, minor_version=1
    )
    _LOGGER.info("Migrated config entry %s to version 2.1", config_entry.title)
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
