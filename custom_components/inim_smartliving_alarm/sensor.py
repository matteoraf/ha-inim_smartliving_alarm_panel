"""Platform for sensor entities for the Inim Alarm integration."""

import logging
from typing import Any, cast

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    KEY_LATEST_EVENT_INDEX_VAL,
    KEY_LIVE_ACTIVE_SCENARIO,
    KEY_LIVE_ACTIVE_SCENARIO_NUMBER,
    KEY_PROCESSED_EVENTS,
    SENSOR_LAST_EVENT_LOG_SUFFIX,
    SYSTEM_MAX_EVENT_LOG_SIZE,
)
from .peripherals import enabled_peripherals
from .protocol.events import (
    NO_AGENT,
    NO_LOCATION,
    REMOTE_LOCATION,
    VALID_KEY_ACTION,
    reader_index,
)
from .protocol.model import FALLBACK_MODEL
from .stored_config import (
    SystemInfo,
    area_names,
    device_model,
    device_sw_version,
    keypad_names,
    name_table,
    scenario_names,
    zone_names,
)
from .user_code import UserCodeInfo, user_code_info

_LOGGER = logging.getLogger(__name__)


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
            coordinator, entry, panel_display_name, system_info, all_scenario_names
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
            PanelPeripheralsSensor(entry, panel_display_name, system_info, peripherals)
        )

    # The user code of the PIN (checked when the integration connected)
    panel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
    sensors_to_add.append(
        PanelUserCodeSensor(
            entry,
            panel_display_name,
            system_info,
            user_code_info(panel, initial_panel_config),
        )
    )

    # Sensor for Events Log
    sensors_to_add.append(
        InimEventLogSensor(
            coordinator, entry, initial_panel_config, panel_display_name, system_info
        )
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


class BaseInimSensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for Inim sensors linked to the main panel device that use the coordinator."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: SystemInfo | None,
    ) -> None:
        """Initialize the Base Inim Sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),
            sw_version=device_sw_version(self._system_info),
        )


class ActiveInimScenarioNameSensor(BaseInimSensorEntity):
    """Sensor to display the name of the currently active Inim scenario."""

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: SystemInfo | None,
        scenario_names: list[str],
    ) -> None:
        """Initialize the Active Scenario Sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
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
        new_value = "Unknown (No data)"
        active_idx = None
        raw_hex = None
        if self.coordinator.data and self.coordinator.data.get(
            KEY_LIVE_ACTIVE_SCENARIO
        ):
            active_data = self.coordinator.data[KEY_LIVE_ACTIVE_SCENARIO]
            active_idx = active_data.get(KEY_LIVE_ACTIVE_SCENARIO_NUMBER)
            raw_hex = active_data.get("raw_hex")
            if active_idx is not None:
                if 0 <= active_idx < len(self._scenario_names):
                    name = self._scenario_names[active_idx]
                    new_value = f"{name if name else f'Scenario {active_idx + 1}'} (Index {active_idx})"
                else:
                    new_value = f"Unknown Scenario (Index {active_idx})"
            else:
                new_value = "Unknown (No active index)"
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
        self._attr_native_value = device_sw_version(self._system_info)

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_firmware_version"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),
            sw_version=device_sw_version(self._system_info),
        )


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
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),  # Model can also be the system type
            sw_version=device_sw_version(self._system_info),
        )


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
        system_info: SystemInfo | None,
        peripherals: dict[str, list[str]],
    ) -> None:
        """Initialize the sensor from the stored configuration."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._attr_unique_id = f"{config_entry.entry_id}_peripherals"
        self._attr_native_value = sum(len(names) for names in peripherals.values())
        self._attr_extra_state_attributes = peripherals

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),
            sw_version=device_sw_version(self._system_info),
        )


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
        system_info: SystemInfo | None,
        info: UserCodeInfo | None,
    ) -> None:
        """Initialize the sensor from what the connection found."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
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
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),
            sw_version=device_sw_version(self._system_info),
        )


class InimEventLogSensor(BaseInimSensorEntity):
    """Sensor to display the latest Inim alarm events."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        initial_panel_config: dict[str, Any],
        panel_display_name: str,
        system_info: SystemInfo | None,
    ) -> None:
        """Initialize the event log sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._initial_panel_config = initial_panel_config
        self._attr_name = f"{panel_display_name} Event Log"
        self._attr_unique_id = f"{config_entry.entry_id}_{SENSOR_LAST_EVENT_LOG_SUFFIX}"
        self._attr_icon = "mdi:format-list-bulleted-type"

        # Get configured log size from options, falling back to data, then default
        self._event_log_display_size = min(
            config_entry.options.get(CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE),
            SYSTEM_MAX_EVENT_LOG_SIZE,  # Cap with system max
        )

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

    def _enrich_event_data(self, raw_api_event: dict[str, Any]) -> dict[str, Any]:
        """Adds human-readable names to a raw event dictionary."""
        enriched_log_event: dict[str, Any] = {
            "timestamp": raw_api_event.get("timestamp_iso", "N/A"),
            "description": raw_api_event.get("action_description", "Unknown Event"),
        }

        all_scenario_names = scenario_names(self._initial_panel_config)
        all_area_names = area_names(self._initial_panel_config)
        all_zone_names = zone_names(self._initial_panel_config)

        if "scenario_number_0_indexed" in raw_api_event:
            idx = raw_api_event["scenario_number_0_indexed"]
            if 0 <= idx < len(all_scenario_names):
                enriched_log_event["scenario"] = (
                    all_scenario_names[idx] or f"Scenario {idx + 1}"
                )
            else:
                enriched_log_event["scenario"] = f"Scenario {idx + 1} (Unknown)"

        if "area_number_1_indexed" in raw_api_event:
            idx = raw_api_event["area_number_1_indexed"] - 1
            if 0 <= idx < len(all_area_names):
                enriched_log_event["area"] = all_area_names[idx] or f"Area {idx + 1}"
            else:
                enriched_log_event["area"] = f"Area {idx + 1} (Unknown)"

        if "zone_number_1_indexed_for_display" in raw_api_event:
            idx = raw_api_event["zone_number_1_indexed_for_display"] - 1
            if 0 <= idx < len(all_zone_names):
                enriched_log_event["zone"] = all_zone_names[idx] or f"Zone {idx + 1}"
            else:
                enriched_log_event["zone"] = f"Zone {idx + 1} (Unknown)"

        if "affected_areas" in raw_api_event and isinstance(
            raw_api_event["affected_areas"], list
        ):
            area_names_display: list[str] = []
            for area_id_1_based in raw_api_event["affected_areas"]:
                idx = area_id_1_based - 1
                if 0 <= idx < len(all_area_names):
                    area_names_display.append(all_area_names[idx] or f"Area {idx + 1}")
                else:
                    area_names_display.append(f"Area {idx + 1} (Unknown)")
            enriched_log_event["affected_areas"] = (
                ", ".join(area_names_display) if area_names_display else "None"
            )

        names = _EventNames(self._initial_panel_config)
        device = _hex_field(raw_api_event, "device_id_hex")
        agent = _hex_field(raw_api_event, "authorized_id_hex")
        if device is not None:
            enriched_log_event["device_id"] = raw_api_event["device_id_hex"]
        if agent is not None:
            enriched_log_event["authorized_id"] = raw_api_event["authorized_id_hex"]
        if raw_api_event.get("action_code") == VALID_KEY_ACTION:
            if agent is not None:
                enriched_log_event["authorized_by"] = names.key(agent)
            if device is not None:
                enriched_log_event["device"] = names.reader(device)
        else:
            if device is not None:
                enriched_log_event["device"] = names.location(device)
            if agent is not None:
                enriched_log_event["authorized_by"] = names.agent(agent, device)

        if "raw_hex_data" in raw_api_event:  # Keeping this for context if available
            enriched_log_event["raw_hex_data"] = raw_api_event["raw_hex_data"]

        return enriched_log_event

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

        if self.coordinator.data:
            panel_idx_val = self.coordinator.data.get(KEY_LATEST_EVENT_INDEX_VAL)
            if panel_idx_val is not None:
                current_panel_latest_index_state = str(panel_idx_val)

            newly_fetched_raw_events = cast(
                list[dict[str, Any]],
                self.coordinator.data.get(KEY_PROCESSED_EVENTS, []),
            )

            if (
                newly_fetched_raw_events
            ):  # Only update self._event_log if there are new events
                _LOGGER.debug(
                    "Processing %s new raw events for event log sensor",
                    len(newly_fetched_raw_events),
                )
                enriched_new_events_this_poll: list[dict[str, Any]] = []
                # API returns newest first, so iterate and prepend to keep newest first in self._event_log
                for raw_event in newly_fetched_raw_events:
                    enriched_and_slimmed_event = self._enrich_event_data(raw_event)
                    enriched_new_events_this_poll.append(enriched_and_slimmed_event)

                # Prepend new events to our internal log
                self._event_log = enriched_new_events_this_poll + self._event_log

                # Trim the internal log to the configured display size
                self._event_log = self._event_log[: self._event_log_display_size]
                _LOGGER.debug(
                    "Event log now contains %s events after processing new and trimming",
                    len(self._event_log),
                )
            # If no new events, self._event_log remains unchanged (persistence)

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


def _hex_field(event: dict[str, Any], key: str) -> int | None:
    """A hex field of a decoded event as a number, None if absent."""
    value = event.get(key)
    return None if value is None else int(value, 16)


class _EventNames:
    """Names of what an event refers to, from the stored panel configuration.

    An event's location is a keypad (0 to K-1), a reader (K to K+P-1),
    254 for a remote command (app, cloud, Home Assistant or the installer
    software) or 255 for none; its agent is a user code, or a key when the
    location is a reader. K and P are the model's keypad and reader counts.
    Names not stored (a configuration from an earlier version) fall back
    to numbers.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._keypads = keypad_names(config)
        self._codes = name_table(config, "codes")
        self._keys = name_table(config, "keys")
        self._readers = name_table(config, "readers")

    @staticmethod
    def _name(names: list[str], index: int, fallback: str) -> str:
        return (names[index] if 0 <= index < len(names) else "") or fallback

    @property
    def _keypad_count(self) -> int:
        # A configuration without keypad names: the 1050's, as before
        return len(self._keypads) or FALLBACK_MODEL.keypads

    def _reader_index(self, location: int) -> int | None:
        return reader_index(location, self._keypad_count)

    def location(self, location: int) -> str:
        """Where the event comes from."""
        if location == REMOTE_LOCATION:
            return "Remote"
        if location == NO_LOCATION:
            return "-"
        if location < self._keypad_count:
            return self._name(self._keypads, location, f"Keypad {location + 1}")
        if (reader := self._reader_index(location)) is not None:
            return self.reader(reader)
        return f"Location {location}"

    def agent(self, agent: int, location: int | None) -> str:
        """Who caused the event: a user code, or a key used on a reader."""
        if agent == NO_AGENT:
            return "-"
        if location is not None and self._reader_index(location) is not None:
            return self.key(agent)
        if self._codes:
            return self._name(self._codes, agent, f"Code {agent + 1}")
        return "Master Code" if agent == 0 else f"User {agent}"  # as before

    def key(self, index: int) -> str:
        """A key (tag), by its 0-based index."""
        return self._name(self._keys, index, f"Key {index + 1}")

    def reader(self, index: int) -> str:
        """A reader, by its 0-based index."""
        return self._name(self._readers, index, f"Reader {index + 1}")
