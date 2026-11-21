"""The Inim Alarm integration."""

import copy
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.alarm_control_panel import (
    DOMAIN as ALARM_CONTROL_PANEL_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import (
    async_extract_entity_ids,
    async_set_service_schema,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.yaml import load_yaml_dict
import voluptuous as vol

from .const import (
    ARM_MODE_DISARM,
    ARM_MODE_INSTANT,
    ARM_MODE_PARTIAL,
    ARM_MODE_TOTAL,
    ATTR_AREAS,
    ATTR_FORCE_ARM,
    ATTR_MODE,
    ATTR_SCENARIO,
    BOARD_SMARTLAN_SI,
    CONF_AREAS,
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
    CONF_SCENARIOS,
    CONF_USERNAME,
    DATA_ALARM_PANEL,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    KEY_INIT_AREA_RECORDS,
    KEY_INIT_CODES,
    KEY_INIT_EXPANDERS,
    KEY_INIT_EXTRA_OUTPUT_OPTIONS,
    KEY_INIT_FIXED_OUTPUT_LABELS,
    KEY_INIT_FIXED_OUTPUT_RECORDS,
    KEY_INIT_GENERAL_OPTIONS,
    KEY_INIT_KEYS,
    KEY_INIT_PERIPHERALS,
    KEY_INIT_PHYSICAL_MAP,
    KEY_INIT_READERS,
    KEY_INIT_SIRENS,
    KEY_INIT_TIMERS,
    KEY_INIT_USER_CODE_OUTPUTS,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    PLATFORMS,
    SERVICE_ACTIVATE_SCENARIO,
    SERVICE_ALARM_RESET,
    SERVICE_ARM_AREA,
    SERVICE_REREAD_PANEL_CONFIGURATION,
)

# Import the custom coordinator
from .coordinator import InimDataUpdateCoordinator
from .errors import InimCommandError

# Import API
from .legacy import (
    LegacyPanelClient,
    create_client,
    upgrade_zone_configuration,
    zone_exists,
)
from .panel_config import (
    async_fetch_panel_configuration,
    async_reread_panel_configuration,
)
from .protocol.commands import ArmMode
from .protocol.errors import UnsupportedPanelError
from .protocol.model import model_for
from .readiness import async_arm
from .report_state import async_drop_report_state
from .selection import (
    area_label,
    is_disarm_scenario,
    item_of_unique_id,
    migrated_selection,
    panel_config,
    scenario_label,
    selected,
    stored_layout_fits,
    stored_model,
    unselected_items,
)
from .utils import async_activate_scenario

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Data this version needs in the stored panel configuration: a configuration
# stored by an earlier version without any of them is read again once at
# setup. Add a key here whenever the configuration read stores something new.
_REQUIRED_CONFIGURATION_KEYS = (
    KEY_INIT_CODES,
    KEY_INIT_PERIPHERALS,
    KEY_INIT_GENERAL_OPTIONS,
    KEY_INIT_AREA_RECORDS,
    KEY_INIT_FIXED_OUTPUT_RECORDS,
    KEY_INIT_FIXED_OUTPUT_LABELS,
    KEY_INIT_PHYSICAL_MAP,
    KEY_INIT_USER_CODE_OUTPUTS,
    KEY_INIT_EXTRA_OUTPUT_OPTIONS,
    KEY_INIT_KEYS,
    KEY_INIT_READERS,
    KEY_INIT_EXPANDERS,
    KEY_INIT_TIMERS,
    KEY_INIT_SIRENS,
)

# Where the action descriptions from services.yaml are kept (hass.data)
_ACTION_DESCRIPTIONS = f"{DOMAIN}_action_descriptions"
_LIMIT_KEYS = (CONF_LIMIT_AREAS, CONF_LIMIT_ZONES, CONF_LIMIT_SCENARIOS)

REREAD_PANEL_CONFIGURATION_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
)


ARM_AREA_MODES = {
    ARM_MODE_TOTAL: ArmMode.AWAY,
    ARM_MODE_PARTIAL: ArmMode.STAY,
    ARM_MODE_INSTANT: ArmMode.INSTANT,
    ARM_MODE_DISARM: ArmMode.DISARM,
}

ARM_AREA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_AREAS): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(min=1))],
            vol.Length(min=1),
        ),
        vol.Required(ATTR_MODE): vol.In(list(ARM_AREA_MODES)),
        vol.Optional(ATTR_FORCE_ARM, default=False): cv.boolean,
    }
)

ACTIVATE_SCENARIO_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SCENARIO): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_FORCE_ARM, default=False): cv.boolean,
    }
)


def _loaded_entry(
    hass: HomeAssistant, entry_id: str, not_loaded_key: str
) -> ConfigEntry:
    """The integration's entry with this ID; it must be loaded."""
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
            translation_key=not_loaded_key,
            translation_placeholders={"title": entry.title},
        )
    return entry


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""

    async def async_reread_service(call: ServiceCall) -> None:
        """Read the panel configuration of one entry again and reload it."""
        entry = _loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded")
        await async_reread_panel_configuration(hass, entry)

    async def async_arm_area_service(call: ServiceCall) -> None:
        """Arm or disarm one or more areas of one panel in a single command."""
        entry = _loaded_entry(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded_command"
        )
        api: LegacyPanelClient = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]
        areas = sorted(set(call.data[ATTR_AREAS]))
        config = panel_config(entry.data)
        imported = selected(entry.options, config, CONF_AREAS)
        if missing := [area for area in areas if area not in imported]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="area_not_imported",
                translation_placeholders={
                    "areas": ", ".join(str(area) for area in missing),
                    "title": entry.title,
                },
            )
        target = ", ".join(area_label(config, area) for area in areas)
        mode = ARM_AREA_MODES[call.data[ATTR_MODE]]
        _LOGGER.info("arm_area: %s -> %s", target, call.data[ATTR_MODE])
        # Arming checks the zones first; disarming is never checked
        await async_arm(
            hass,
            entry,
            lambda check: api.panel.arm_areas(
                dict.fromkeys(areas, mode), on_not_ready=check
            ),
            target,
            force=call.data[ATTR_FORCE_ARM],
        )
        coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
        await coordinator.async_request_refresh()

    async def async_activate_scenario_service(call: ServiceCall) -> None:
        """Activate one imported scenario of one panel."""
        entry = _loaded_entry(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded_command"
        )
        number = call.data[ATTR_SCENARIO]
        config = panel_config(entry.data)
        if number not in selected(entry.options, config, CONF_SCENARIOS):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scenario_not_imported",
                translation_placeholders={
                    "scenario": str(number),
                    "title": entry.title,
                },
            )
        label = scenario_label(config, number)
        data = hass.data[DOMAIN][entry.entry_id]
        await async_activate_scenario(
            hass,
            entry,
            data[DATA_COORDINATOR],
            data[DATA_API_CLIENT],
            config,
            f"{entry.entry_id}_activate_scenario_action",
            entry.title,
            number - 1,
            f"activate scenario '{label}'",
            is_disarm_scenario=is_disarm_scenario(config, number),
            force=call.data[ATTR_FORCE_ARM],
        )

    async def async_alarm_reset_service(call: ServiceCall) -> None:
        """Reset the alarm and tamper memories of the targeted panels' areas."""
        registry = er.async_get(hass)
        failures: list[InimCommandError] = []
        for entity_id in sorted(await async_extract_entity_ids(call)):
            registered = registry.async_get(entity_id)
            if (
                registered is None
                or registered.platform != DOMAIN
                or registered.domain != ALARM_CONTROL_PANEL_DOMAIN
                or registered.config_entry_id is None
            ):
                continue
            entry = _loaded_entry(
                hass, registered.config_entry_id, "entry_not_loaded_command"
            )
            alarm_panel = hass.data[DOMAIN][entry.entry_id].get(DATA_ALARM_PANEL)
            if alarm_panel is None:
                continue
            try:
                await alarm_panel.async_reset_memories()
            except InimCommandError as err:
                failures.append(err)
        if failures:
            raise failures[0]

    hass.services.async_register(DOMAIN, SERVICE_ALARM_RESET, async_alarm_reset_service)
    hass.services.async_register(
        DOMAIN,
        SERVICE_REREAD_PANEL_CONFIGURATION,
        async_reread_service,
        schema=REREAD_PANEL_CONFIGURATION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ARM_AREA, async_arm_area_service, schema=ARM_AREA_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACTIVATE_SCENARIO,
        async_activate_scenario_service,
        schema=ACTIVATE_SCENARIO_SCHEMA,
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

    await _reread_configuration_if_outdated(hass, entry, api)
    _report_unsupported_panel(hass, entry, api)
    _remove_entities_not_imported(hass, entry)

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
    _LOGGER.debug("(%s) Forwarding setup to platforms: %s", entry.title, PLATFORMS)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Keep nothing of a failed setup: the client's connection included
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
        await api.close()
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
            await entry_data[DATA_API_CLIENT].close()
            _LOGGER.debug("Cleaned up data for %s", entry.title)
        if not hass.data.get(DOMAIN):  # If no more entries for this domain
            hass.data.pop(DOMAIN, None)
            _LOGGER.debug("Cleaned up domain data for %s", DOMAIN)
        await async_update_action_descriptions(hass)
        if DOMAIN not in hass.data:  # the last panel: nothing to describe
            hass.data.pop(_ACTION_DESCRIPTIONS, None)
    else:
        _LOGGER.error("Failed to unload platforms for %s", entry.title)
    return unload_ok


async def async_update_action_descriptions(hass: HomeAssistant) -> None:
    """List the imported areas and scenarios of the loaded panels in the actions.

    The arm_area form offers only the imported areas and the
    activate_scenario form only the imported scenarios, by their panel
    labels. The value of each choice is the number; with several panels, a
    number lists the item of each panel that imports it, with the panel's
    name. services.yaml holds the rest of the descriptions.
    """
    base = hass.data.get(_ACTION_DESCRIPTIONS)
    if base is None:
        services = await hass.async_add_executor_job(
            load_yaml_dict, str(Path(__file__).parent / "services.yaml")
        )
        base = hass.data[_ACTION_DESCRIPTIONS] = {
            SERVICE_ARM_AREA: services[SERVICE_ARM_AREA],
            SERVICE_ACTIVATE_SCENARIO: services[SERVICE_ACTIVATE_SCENARIO],
        }
    entries = [
        entry
        for entry_id in hass.data.get(DOMAIN, {})
        if (entry := hass.config_entries.async_get_entry(entry_id)) is not None
    ]
    for service, field, kind, label_of in (
        (SERVICE_ARM_AREA, ATTR_AREAS, CONF_AREAS, area_label),
        (SERVICE_ACTIVATE_SCENARIO, ATTR_SCENARIO, CONF_SCENARIOS, scenario_label),
    ):
        labels: dict[int, list[str]] = {}
        for entry in entries:
            config = panel_config(entry.data)
            for number in selected(entry.options, config, kind):
                label = label_of(config, number)
                if len(entries) > 1:
                    label = f"{label} ({entry.title})"
                labels.setdefault(number, []).append(label)
        description: dict[str, Any] = copy.deepcopy(base[service])
        description["fields"][field]["selector"]["select"]["options"] = [
            {"value": str(number), "label": ", ".join(names)}
            for number, names in sorted(labels.items())
        ]
        async_set_service_schema(hass, DOMAIN, service, description)


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
    hass: HomeAssistant, entry: ConfigEntry, api: LegacyPanelClient
) -> None:
    """A repair issue while the panel has no profile of its own.

    Such a panel (firmware 5.0x, another model) set up earlier keeps running
    with the fallback layout, so its names, zones and scenarios may be
    wrong. The issue goes away once the panel is supported.
    """
    try:
        firmware = api.panel.firmware
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
    hass: HomeAssistant, entry: ConfigEntry, api: LegacyPanelClient
) -> None:
    """Read the configuration again if it was stored by an earlier version.

    Called at every setup, but it only looks at the stored configuration:
    nothing is read from the panel unless it lacks the names of user codes,
    keys and readers, the enabled peripherals or the general options
    (stored from this version on), or it was stored with another layout.
    The panel model is known from the first status read, and the stored
    terminal records and peripherals must have its lengths; they differ
    once for a panel that got its own profile (another model, or firmware
    5.1) after it was set up with an earlier version, which read it with the
    SmartLiving 1050's firmware 6 addresses. After the re-read neither
    applies. Unique IDs do not change. If the re-read fails, setup goes on
    with the stored configuration, and the blocks that do not fit are not
    decoded.
    """
    try:
        model = api.panel.model
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
    elif any(key not in config for key in _REQUIRED_CONFIGURATION_KEYS):
        _LOGGER.info(
            "%s: reading the panel configuration again for what this version "
            "adds to it (such as the outputs' programming and names)",
            entry.title,
        )
    else:
        return
    try:
        new_config = await async_fetch_panel_configuration(hass, api)
    except (ConnectionError, ValueError) as err:
        _LOGGER.error(
            "%s: cannot read the panel configuration again (%s); use 'Re-read "
            "panel configuration' later",
            entry.title,
            err,
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
    """Migrate an entry to the current version (1.5).

    1.2 adds the board type; every older entry uses SmartLAN/SI. 1.3 stores
    every zone number with its terminal type, rebuilt from the raw panel
    data the entry already holds (no panel access). 1.4 replaces the counts
    of areas, zones and scenarios with lists of the items to import, which
    keep the same entities. 1.5 drops the reader names option: reader
    names are read from the panel. A newer major version (from a later
    integration release) cannot be loaded.
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
