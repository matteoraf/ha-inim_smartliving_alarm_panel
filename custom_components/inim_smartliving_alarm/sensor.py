"""Platform for sensor entities for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import stored_config
from .const import (
    CONF_EVENT_LOG_SIZE,
    CONF_PANEL_NAME,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_EVENT_LOG_SIZE,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    SENSOR_LAST_EVENT_LOG_SUFFIX,
    SYSTEM_MAX_EVENT_LOG_SIZE,
)
from .coordinator import PanelData
from .entity import InimCoordinatorEntity, panel_device_info
from .event_log import event_attributes
from .peripherals import enabled_peripherals
from .stored_config import (
    SystemInfo,
    scenario_names,
)
from .user_code import UserCodeInfo, user_code_info

_LOGGER = logging.getLogger(__name__)

# The active scenario sensor's state when the areas' state matches none
NO_SCENARIO_MATCHES = "No scenario matches"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm sensor entities from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'sensor' platform entities for entry: %s", entry.title
    )

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = stored_config.system_info(initial_panel_config)

    # Get user-defined panel name
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    all_scenario_names = scenario_names(initial_panel_config)

    sensors_to_add = []

    # Sensor for the name of the single active Inim scenario
    sensors_to_add.append(
        ActiveInimScenarioNameSensor(
            coordinator, entry, panel_display_name, all_scenario_names
        )
    )

    # Sensor for Panel Firmware Version (static after setup)
    if system_info and system_info.version:
        sensors_to_add.append(
            PanelFirmwareSensor(entry, panel_display_name, system_info)
        )
    else:
        _LOGGER.warning(
            "System info or firmware version not available for %s, PanelFirmwareSensor not created",
            entry.title,
        )

    # Sensor for Panel System Type (static after setup)
    if system_info and system_info.type:
        sensors_to_add.append(
            PanelSystemTypeSensor(entry, panel_display_name, system_info)
        )
    else:
        _LOGGER.warning(
            "System info or system type not available for %s, PanelSystemTypeSensor not created",
            entry.title,
        )

    # Keypads, readers, expanders and sirens enabled on the panel
    peripherals = enabled_peripherals(initial_panel_config)
    if peripherals is not None:
        sensors_to_add.append(
            PanelPeripheralsSensor(entry, panel_display_name, peripherals)
        )

    # The user code of the PIN (checked when the integration connected)
    panel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
    sensors_to_add.append(
        PanelUserCodeSensor(
            entry,
            panel_display_name,
            user_code_info(panel, initial_panel_config),
        )
    )

    # Sensor for Events Log
    sensors_to_add.append(
        InimEventLogSensor(coordinator, entry, initial_panel_config, panel_display_name)
    )

    if sensors_to_add:
        async_add_entities(sensors_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm 'sensor' entities for %s",
            len(sensors_to_add),
            entry.title,
        )
    else:
        _LOGGER.info(
            "No Inim Alarm 'sensor' entities added for %s",
            entry.title,
        )


class BaseInimSensorEntity(InimCoordinatorEntity, SensorEntity):
    """Base class for Inim sensors linked to the main panel device that use the coordinator."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        panel_display_name: str,
    ) -> None:
        """Initialize the Base Inim Sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)


class ActiveInimScenarioNameSensor(BaseInimSensorEntity):
    """Sensor to display the name of the currently active Inim scenario."""

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        scenario_names: list[str],
    ) -> None:
        """Initialize the Active Scenario Sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
        self._scenario_names = scenario_names
        self._attr_name = "Active Scenario Name"
        self._attr_unique_id = f"{config_entry.entry_id}_active_scenario_name"
        self._attr_icon = "mdi:list-status"
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_active_scenario"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        status = self.status
        active_idx = None
        raw_hex = None
        if status is None:
            new_value = "Unknown (No data)"
        elif status.active_scenario is None:
            new_value = NO_SCENARIO_MATCHES
        else:
            active_idx = status.active_scenario
            if active_idx < len(self._scenario_names):
                name = self._scenario_names[active_idx]
                new_value = f"{name if name else f'Scenario {active_idx + 1}'} (Index {active_idx})"
            else:
                new_value = f"Unknown Scenario (Index {active_idx})"
        if status is not None:
            raw_hex = status.raw_active_scenario.hex()
        self._attr_native_value = new_value
        self._attr_extra_state_attributes = {
            "active_scenario_index": active_idx,
            "raw_active_scenario_hex": raw_hex,
        }


class PanelFirmwareSensor(SensorEntity):
    """Panel Firmware Sensor Class."""

    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: SystemInfo | None,
    ) -> None:
        """Initialize the Panel Firmware Sensor."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._attr_name = "Firmware Version"
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_version"
        self._attr_icon = "mdi:chip"
        self._attr_native_value = (
            self._system_info.version if self._system_info is not None else "Unknown"
        )

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_firmware_version"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)


class PanelSystemTypeSensor(SensorEntity):
    """Panel System Type Sensor."""

    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: SystemInfo | None,
    ) -> None:
        """Initialize the Panel System Type Sensor."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._attr_name = "System Type"
        self._attr_unique_id = f"{config_entry.entry_id}_system_type"
        self._attr_icon = "mdi:information-outline"
        self._attr_native_value = (
            self._system_info.type if self._system_info is not None else "Unknown"
        )

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_system_type"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)


class PanelPeripheralsSensor(SensorEntity):
    """The keypads, readers, expanders and sirens enabled on the panel.

    The state is how many there are; the attributes list their names.
    Static: it changes when the panel configuration is read again.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "peripherals"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:devices"

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
        peripherals: dict[str, list[str]],
    ) -> None:
        """Initialize the sensor from the stored configuration."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._attr_unique_id = f"{config_entry.entry_id}_peripherals"
        self._attr_native_value = sum(len(names) for names in peripherals.values())
        self._attr_extra_state_attributes = peripherals

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)


class PanelUserCodeSensor(SensorEntity):
    """The user code the PIN belongs to: its name, never the PIN.

    Unknown if the PIN matches no user code (commands are refused then).
    The attributes give its number, the areas it controls and its type.
    Static: checked again when the integration reloads.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "user_code"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-key"

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
        info: UserCodeInfo | None,
    ) -> None:
        """Initialize the sensor from what the connection found."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._attr_unique_id = f"{config_entry.entry_id}_user_code"
        self._attr_native_value = info.name if info else None
        self._attr_extra_state_attributes = {
            "number": info.number if info else None,
            "areas": info.areas if info else [],
            "type": info.code_type if info else None,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)


class InimEventLogSensor(BaseInimSensorEntity):
    """Sensor to display the latest Inim alarm events."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        initial_panel_config: dict[str, Any],
        panel_display_name: str,
    ) -> None:
        """Initialize the event log sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
        self._initial_panel_config = initial_panel_config
        self._attr_name = f"{panel_display_name} Event Log"
        self._attr_unique_id = f"{config_entry.entry_id}_{SENSOR_LAST_EVENT_LOG_SUFFIX}"
        self._attr_icon = "mdi:format-list-bulleted-type"

        # Get configured log size from options, falling back to data, then default
        self._event_log_display_size = min(
            config_entry.options.get(CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE),
            SYSTEM_MAX_EVENT_LOG_SIZE,  # Cap with system max
        )

        # The poll whose events are in the log
        self._processed_data: PanelData | None = None
        self._event_log: list[
            dict[str, Any]
        ] = []  # Stores the enriched event log for attributes

        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_{SENSOR_LAST_EVENT_LOG_SUFFIX}"

    @property
    def available(self) -> bool:
        """Return True if the entity is available.
        For the event log, we consider it available as long as the coordinator exists,
        as it can serve its persisted log even if the last update failed.
        The state of the sensor (latest index) will reflect update issues."""

        return self.coordinator is not None  # Or simply True after first init

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        """Update the state and attributes of the sensor from coordinator data."""
        current_panel_latest_index_state = "Unknown Index"
        latest_event_in_log_description = "No events logged"
        latest_event_in_log_timestamp = None

        data = self.coordinator.data
        if data is not None:
            if data.latest_event_index is not None:
                current_panel_latest_index_state = str(data.latest_event_index)
            # Each poll's events are added once: the coordinator also calls
            # back after a failed poll, with the last poll's data. A poll is
            # told by its data object (ring indexes wrap around, so they cannot
            # tell a new event from an old one).
            fresh = data is not self._processed_data
            self._processed_data = data
            if fresh and data.new_events:  # the log keeps its events between polls
                _LOGGER.debug(
                    "Processing %s new raw events for event log sensor",
                    len(data.new_events),
                )
                # the panel's local time, in Home Assistant's time zone
                time_zone = dt_util.get_default_time_zone()
                new_items = [
                    event_attributes(logged, self._initial_panel_config, time_zone)
                    for logged in data.new_events
                ]
                # newest first: the new events go before the ones already shown
                self._event_log = (new_items + self._event_log)[
                    : self._event_log_display_size
                ]
                _LOGGER.debug(
                    "Event log now contains %s events after processing new and trimming",
                    len(self._event_log),
                )

        # Update attributes based on the current state of self._event_log
        if self._event_log:
            latest_event_in_log = self._event_log[0]  # First item is newest
            latest_event_in_log_description = latest_event_in_log.get(
                "description", "Unknown Event"
            )
            latest_event_in_log_timestamp = latest_event_in_log.get("timestamp")

        self._attr_native_value = current_panel_latest_index_state
        self._attr_extra_state_attributes = {
            "latest_event_description": latest_event_in_log_description,
            "latest_event_timestamp": latest_event_in_log_timestamp,
            "event_log_size_configured": self._event_log_display_size,
            "event_log_count_current": len(self._event_log),
            "event_log": self._event_log,
        }
