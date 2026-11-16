"""Platform for switch entities for the Inim Alarm integration (Areas and Zones)."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ARM_MODE_INSTANT,
    ARM_MODE_PARTIAL,
    ARM_MODE_TOTAL,
    CONF_AREA_SWITCH_ARM_MODE,
    CONF_LIMIT_AREAS,
    CONF_LIMIT_ZONES,
    CONF_PANEL_NAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_AREA_SWITCH_ARM_MODE,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_LIMIT_ZONES,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREAS,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONES,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    KEY_LIVE_AREA_MODES_MAP,
    KEY_LIVE_AREA_STATUSES_MAP,
    KEY_LIVE_AREAS_STATUS,
    KEY_LIVE_ZONE_EXCLUDED_STATUSES_MAP,
    KEY_LIVE_ZONES_EXCLUDED_STATUS,
)
from .errors import async_send_command
from .legacy import LegacyPanelClient, zones_to_create
from .protocol.commands import ArmMode

_LOGGER = logging.getLogger(__name__)

# The area switch's arm mode option -> command
SWITCH_ARM_MODES = {
    ARM_MODE_TOTAL: ArmMode.AWAY,
    ARM_MODE_PARTIAL: ArmMode.STAY,
    ARM_MODE_INSTANT: ArmMode.INSTANT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm switch entities (areas and zones) from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'switch' platform entities for entry: %s", entry.title
    )

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    api_client: LegacyPanelClient = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get(KEY_INIT_SYSTEM_INFO, {})
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    switches_to_add = []

    # --- Setup Area Switches ---
    area_data = initial_panel_config.get(KEY_INIT_AREAS, {})
    all_area_names = area_data.get(KEY_INIT_AREA_NAMES, []) if area_data else []

    limit_areas = entry.options.get(
        CONF_LIMIT_AREAS, entry.data.get(CONF_LIMIT_AREAS, DEFAULT_LIMIT_AREAS)
    )

    if not all_area_names:
        _LOGGER.warning(
            "No area names found in initial config for %s. Cannot create area switches",
            entry.title,
        )
        return

    num_areas_to_create = min(len(all_area_names), limit_areas)
    _LOGGER.debug(
        "Setting up %s Area Switches (Limit: %s, Available: %s)",
        num_areas_to_create,
        limit_areas,
        len(all_area_names),
    )

    for i in range(num_areas_to_create):
        area_id_1_based = i + 1
        area_name = (
            all_area_names[i] if all_area_names[i] else f"Area {area_id_1_based}"
        )
        switches_to_add.append(
            InimAreaSwitch(
                coordinator,
                api_client,
                entry,
                panel_display_name,
                system_info,
                area_id_1_based,
                area_name,
            )
        )

    # --- Setup Zone Enabled Switches ---
    zone_names_data = initial_panel_config.get(KEY_INIT_ZONES, {})
    all_zone_names = (
        zone_names_data.get(KEY_INIT_ZONE_NAMES, []) if zone_names_data else []
    )

    zones_config = initial_panel_config.get(KEY_INIT_ZONES_CONFIG, {})
    zones_config_detailed = (
        zones_config.get(KEY_INIT_ZONES_CONFIG_DETAILED, []) if zones_config else []
    )

    limit_zones = entry.options.get(
        CONF_LIMIT_ZONES, entry.data.get(CONF_LIMIT_ZONES, DEFAULT_LIMIT_ZONES)
    )

    # a switch needs the zone's physical slot from the zone details
    zones = (
        zones_to_create(all_zone_names, zones_config_detailed, limit_zones)
        if zones_config_detailed
        else []
    )
    _LOGGER.debug(
        "Setting up zone bypass switches for up to %s zones (Limit: %s)",
        len(zones),
        limit_zones,
    )
    for zone_index_0_based, display_zone_name, zone_detail in zones:
        internal_index = zone_detail.get("internal_index") if zone_detail else None
        if internal_index is None:
            _LOGGER.warning(
                "Skipping the bypass switch of zone %s: no physical terminal slot",
                zone_index_0_based + 1,
            )
            continue
        switches_to_add.append(
            InimZoneEnabledSwitch(
                coordinator,
                api_client,
                entry,
                panel_display_name,
                system_info,
                zone_index_0_based + 1,
                display_zone_name,
                internal_index,
                second_zone=bool(zone_detail.get("second_zone")),
            )
        )

    if switches_to_add:
        async_add_entities(switches_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm switch entities for %s",
            len(switches_to_add),
            entry.title,
        )
    else:
        _LOGGER.info("No Inim Alarm switch entities added for %s", entry.title)


class InimAreaSwitch(CoordinatorEntity, SwitchEntity):
    """An Inim area as a switch: on in any armed mode.

    Turning it on arms in the mode chosen in the options (total by default).
    """

    def __init__(
        self,
        coordinator: Any,
        api_client: LegacyPanelClient,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        area_id_1_based: int,
        area_name: str,
    ) -> None:
        """Initialize the Area Switch."""
        super().__init__(coordinator)
        self.api_client = api_client
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._area_id_1_based = area_id_1_based
        self._area_name = area_name

        self._attr_name = f"{self._panel_display_name} Area {self._area_id_1_based} ({self._area_name})"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_area_switch_{self._area_id_1_based}"
        )

        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_area_{self._area_id_1_based}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=self._system_info.get(KEY_INIT_SYSTEM_INFO_TYPE, "SmartLiving"),
            sw_version=self._system_info.get(KEY_INIT_SYSTEM_INFO_VERSION, "Unknown"),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        new_is_on = False
        if self.coordinator.data and self.coordinator.data.get(KEY_LIVE_AREAS_STATUS):
            area_statuses = self.coordinator.data[KEY_LIVE_AREAS_STATUS].get(
                KEY_LIVE_AREA_STATUSES_MAP, {}
            )
            status_text = area_statuses.get(self._area_id_1_based)
            if status_text and status_text.lower() == "armed":
                new_is_on = True
        self._attr_is_on = new_is_on
        self._attr_icon = (
            "mdi:shield-check" if self._attr_is_on else "mdi:shield-off-outline"
        )

    @property
    def is_on(self) -> bool:
        """Area State."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (arm the area in the configured mode)."""
        _LOGGER.info("Arming Area %s (%s)", self._area_id_1_based, self._area_name)

        option = self.config_entry.options.get(
            CONF_AREA_SWITCH_ARM_MODE, DEFAULT_AREA_SWITCH_ARM_MODE
        )
        mode = SWITCH_ARM_MODES.get(option, ArmMode.AWAY)
        await async_send_command(
            self.api_client.panel.arm_areas({self._area_id_1_based: mode}),
            self._area_name,
        )
        self._attr_is_on = True  # Optimistic update
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
        _LOGGER.info("ARM command for Area %s acknowledged", self._area_id_1_based)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (disarm the area)."""
        _LOGGER.info("Disarming Area %s (%s)", self._area_id_1_based, self._area_name)

        await async_send_command(
            self.api_client.panel.arm_areas({self._area_id_1_based: ArmMode.DISARM}),
            self._area_name,
        )
        self._attr_is_on = False  # Optimistic update
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
        _LOGGER.info("DISARM command for Area %s acknowledged", self._area_id_1_based)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional Attributes."""
        attrs = {"area_id": self._area_id_1_based, "area_name": self._area_name}
        if self.coordinator.data and self.coordinator.data.get(KEY_LIVE_AREAS_STATUS):
            area_statuses = self.coordinator.data[KEY_LIVE_AREAS_STATUS].get(
                KEY_LIVE_AREA_STATUSES_MAP, {}
            )
            attrs["status_text"] = area_statuses.get(self._area_id_1_based, "Unknown")
            area_modes = self.coordinator.data[KEY_LIVE_AREAS_STATUS].get(
                KEY_LIVE_AREA_MODES_MAP, {}
            )
            attrs["arming_mode"] = area_modes.get(self._area_id_1_based, "unknown")
        return attrs


class InimZoneEnabledSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Inim Zone's enabled status as a switch. ON = Enabled, OFF = Excluded."""

    def __init__(
        self,
        coordinator: Any,
        api_client: LegacyPanelClient,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        zone_id_1_based: int,
        zone_name: str,
        zone_internal_index: int,
        *,
        second_zone: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._zone_id_1_based = zone_id_1_based
        self._zone_name = zone_name
        self._zone_internal_index = zone_internal_index
        self._second_zone = second_zone  # the second zone of a double terminal
        self._attr_name = f"{self._panel_display_name} Zone {self._zone_id_1_based} Enabled ({self._zone_name})"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_zone_enabled_switch_{self._zone_id_1_based}"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_zone_{self._zone_id_1_based}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=self._system_info.get(KEY_INIT_SYSTEM_INFO_TYPE, "SmartLiving"),
            sw_version=self._system_info.get(KEY_INIT_SYSTEM_INFO_VERSION, "Unknown"),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        # Default to ON (enabled)
        new_is_on = True
        if self.coordinator.data and self.coordinator.data.get(
            KEY_LIVE_ZONES_EXCLUDED_STATUS
        ):
            excluded_statuses = self.coordinator.data[
                KEY_LIVE_ZONES_EXCLUDED_STATUS
            ].get(KEY_LIVE_ZONE_EXCLUDED_STATUSES_MAP, {})
            status_text = excluded_statuses.get(self._zone_id_1_based)
            # The switch is ON if the zone is 'enabled'
            if status_text and status_text.lower() == "disabled":
                new_is_on = False
        self._attr_is_on = new_is_on
        self._attr_icon = (
            "mdi:shield-check" if self._attr_is_on else "mdi:shield-remove-outline"
        )

    @property
    def is_on(self) -> bool:
        """Return true if the zone is enabled (not excluded)."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (enable the zone)."""
        _LOGGER.info(
            "Enabling Zone %s (Internal Index: %s)",
            self._zone_id_1_based,
            self._zone_internal_index,
        )
        await async_send_command(
            self.api_client.panel.bypass_zone(
                self._zone_internal_index,
                bypass=False,
                second_zone=self._second_zone,
            ),
            self._zone_name,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (exclude/bypass the zone)."""
        _LOGGER.info(
            "Excluding Zone %s (Internal Index: %s)",
            self._zone_id_1_based,
            self._zone_internal_index,
        )
        await async_send_command(
            self.api_client.panel.bypass_zone(
                self._zone_internal_index,
                bypass=True,
                second_zone=self._second_zone,
            ),
            self._zone_name,
        )
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "internal_index": self._zone_internal_index,
        }
