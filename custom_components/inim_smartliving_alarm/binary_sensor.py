"""Platform for binary sensor entities for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
    CONF_PANEL_NAME,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_LIMIT_SCENARIOS,
    DEFAULT_LIMIT_ZONES,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREAS,
    KEY_INIT_SCENARIO_NAMES,
    KEY_INIT_SCENARIOS,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONES,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
)

# from .inim_api import InimAlarmAPI # For type hinting if API instance is passed

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm binary sensor entities from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'binary_sensor' platform entities for entry: %s",
        entry.title,
    )

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get("system_info", {})  # For model/version

    # Get user-defined panel name
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    # Get user-defined limits (from options first, then data, then default)
    limit_zones = entry.options.get(
        CONF_LIMIT_ZONES, entry.data.get(CONF_LIMIT_ZONES, DEFAULT_LIMIT_ZONES)
    )
    limit_areas = entry.options.get(
        CONF_LIMIT_AREAS, entry.data.get(CONF_LIMIT_AREAS, DEFAULT_LIMIT_AREAS)
    )
    limit_scenarios = entry.options.get(
        CONF_LIMIT_SCENARIOS,
        entry.data.get(CONF_LIMIT_SCENARIOS, DEFAULT_LIMIT_SCENARIOS),
    )

    scenario_data = initial_panel_config.get(KEY_INIT_SCENARIOS, {})
    all_scenario_names = (
        scenario_data.get(KEY_INIT_SCENARIO_NAMES, []) if scenario_data else []
    )

    area_data = initial_panel_config.get(KEY_INIT_AREAS, {})
    all_area_names = area_data.get(KEY_INIT_AREA_NAMES, []) if area_data else []

    zone_data = initial_panel_config.get(KEY_INIT_ZONES, {})
    all_zone_names = zone_data.get(KEY_INIT_ZONE_NAMES, []) if zone_data else []

    zone_configurations_data = initial_panel_config.get(KEY_INIT_ZONES_CONFIG, {})
    detailed_zone_configs = (
        zone_configurations_data.get(KEY_INIT_ZONES_CONFIG_DETAILED, [])
        if zone_configurations_data
        else []
    )

    binary_sensors_to_add = []

    num_zones_to_create = min(len(all_zone_names), limit_zones)
    _LOGGER.debug(
        "Setting up %s Zone Binary Sensors (Limit: %s, Available: %s)",
        num_zones_to_create,
        limit_zones,
        len(all_zone_names),
    )
    for i in range(num_zones_to_create):
        zone_name = (
            all_zone_names[i]
            if i < len(all_zone_names) and all_zone_names[i]
            else f"Zone {i + 1}"
        )
        zone_config_detail = next(
            (zc for zc in detailed_zone_configs if zc.get("zone_index") == i), None
        )
        binary_sensors_to_add.append(
            InimZoneBinarySensor(
                coordinator,
                entry,
                panel_display_name,
                system_info,
                i,
                zone_name,
                zone_config_detail,
            )
        )

    num_areas_to_create = min(len(all_area_names), limit_areas)
    _LOGGER.debug(
        "Setting up %s Area Triggered Binary Sensors (Limit: %s, Available: %s)",
        num_areas_to_create,
        limit_areas,
        len(all_area_names),
    )
    for i in range(num_areas_to_create):
        area_name = all_area_names[i] if all_area_names[i] else f"Area {i + 1}"
        binary_sensors_to_add.append(
            InimAreaTriggeredBinarySensor(
                coordinator, entry, panel_display_name, system_info, i + 1, area_name
            )
        )

    num_scenarios_to_create = min(len(all_scenario_names), limit_scenarios)
    _LOGGER.debug(
        "Setting up %s Scenario Active Binary Sensors (Limit: %a, Available: %s)",
        num_scenarios_to_create,
        limit_scenarios,
        len(all_scenario_names),
    )
    for i in range(num_scenarios_to_create):
        scenario_name = (
            all_scenario_names[i] if all_scenario_names[i] else f"Scenario {i + 1}"
        )
        binary_sensors_to_add.append(
            InimSpecificScenarioActiveBinarySensor(
                coordinator, entry, panel_display_name, system_info, i, scenario_name
            )
        )

    if binary_sensors_to_add:
        async_add_entities(binary_sensors_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm 'binary_sensor' entities for %s",
            len(binary_sensors_to_add),
            entry.title,
        )
    else:
        _LOGGER.info("No Inim Alarm 'binary_sensor' entities added for %s", entry.title)


class BaseInimBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for Inim binary sensors linked to the main panel device."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
    ) -> None:
        """Initialize the base Inim binary sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info  # Still useful for model/manufacture

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,  # Use the user-defined panel name here
            manufacturer="Inim Electronics",
            model=self._system_info.get(
                "type", "SmartLiving"
            ),  # Model can still come from system_info
            sw_version=self._system_info.get("version", "Unknown"),
        )


class InimZoneBinarySensor(BaseInimBinarySensor):
    """Binary sensor for the status of an individual Inim Zone."""

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        zone_index_0_based: int,
        zone_name: str,
        zone_config_detail: dict[str, Any] | None,
    ) -> None:
        """Initialize the Inim zone binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._zone_index_0_based = zone_index_0_based
        self._zone_id_1_based = zone_index_0_based + 1
        self._zone_name = zone_name
        self._zone_config_detail = zone_config_detail if zone_config_detail else {}
        self._attr_name = f"Zone {self._zone_id_1_based} ({self._zone_name})"
        self._attr_unique_id = f"{config_entry.entry_id}_zone_{self._zone_id_1_based}"

        # Determine device class based on zone configuration
        self._attr_device_class = None  # Default to None (no specific device class)
        if self._zone_config_detail:
            balancing_type = self._zone_config_detail.get("balancing_type_desc")
            sensor_type = self._zone_config_detail.get("sensor_type_desc")

            if balancing_type == "Double Balancing" or sensor_type == "Shutter type":
                self._attr_device_class = BinarySensorDeviceClass.MOTION
            elif balancing_type in ["Normally Closed", "Normally Open"]:
                self._attr_device_class = BinarySensorDeviceClass.OPENING
            # If none of the above, it remains None, which is fine for a generic binary sensor.

        self._attr_extra_state_attributes = {}  # Initialize attribute dict
        self._update_state_from_coordinator()  # Initial state and attributes

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_zone_{self._zone_id_1_based}"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        status_text = "Unknown"
        if self.coordinator.data and self.coordinator.data.get("zones_status"):
            zones_data = self.coordinator.data["zones_status"]
            if zones_data.get("zone_statuses"):
                status_text = zones_data["zone_statuses"].get(
                    self._zone_id_1_based, "Unknown"
                )
                if status_text.lower() == "alarmed":
                    is_on_state = True
        self._attr_is_on = is_on_state
        # Update attributes here as well, as they might include dynamic status_text
        self._attr_extra_state_attributes = self._get_attributes(status_text)

    def _get_attributes(self, current_status_text: str) -> dict[str, Any]:
        attrs = {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "status_text": current_status_text,
        }
        if self._zone_config_detail:
            for key, value in self._zone_config_detail.items():
                if (
                    key
                    not in [
                        "zone_index",
                        "config_part1_hex",
                        "config_part2_hex",
                    ]
                    and not key.startswith("_raw")
                    and not key.endswith("_val")
                ):
                    attr_key = key.replace("_desc", "")
                    attrs[attr_key] = value
        return attrs

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return self._attr_extra_state_attributes


class InimAreaTriggeredBinarySensor(BaseInimBinarySensor):
    """Binary sensor indicating if an Inim Area is currently triggered."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        area_id_1_based: int,
        area_name: str,
    ) -> None:
        """Initialize the Inim area triggered binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._area_id_1_based = area_id_1_based
        self._area_name = area_name
        self._attr_name = f"Area {self._area_id_1_based} Triggered ({self._area_name})"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_area_{self._area_id_1_based}_triggered_bs"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_area_{self._area_id_1_based}_triggered"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        if self.coordinator.data and self.coordinator.data.get("areas_status"):
            triggered_list = self.coordinator.data["areas_status"].get(
                "triggered_areas", []
            )
            if self._area_id_1_based in triggered_list:
                is_on_state = True
        self._attr_is_on = is_on_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {"area_id": self._area_id_1_based, "area_name": self._area_name}


class InimSpecificScenarioActiveBinarySensor(BaseInimBinarySensor):
    """Binary sensor indicating if a specific Inim Scenario is currently active."""

    _attr_device_class = None  # No specific device class, will appear as generic on/off

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        scenario_index_0_based: int,
        scenario_name: str,
    ) -> None:
        """Initialize the Inim specific scenario active binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._scenario_index_0_based = scenario_index_0_based
        self._scenario_name = scenario_name
        self._attr_name = f"Scenario '{self._scenario_name}' Active"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_scenario_{self._scenario_index_0_based}_active_bs"
        )
        self._attr_icon = "mdi:play-circle-outline"
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return (
            f"{self._panel_display_name}_scenario_{self._scenario_index_0_based}_active"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        if self.coordinator.data and self.coordinator.data.get("active_scenario"):
            active_idx = self.coordinator.data["active_scenario"].get(
                "active_scenario_number"
            )
            if active_idx == self._scenario_index_0_based:
                is_on_state = True
        self._attr_is_on = is_on_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "scenario_index": self._scenario_index_0_based,
            "scenario_name": self._scenario_name,
        }
