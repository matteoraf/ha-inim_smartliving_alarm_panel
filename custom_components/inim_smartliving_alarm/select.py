"""Arming mode of each area as a select entity."""

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    CONF_AREAS,
    CONF_PANEL_NAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
    KEY_LIVE_AREA_MODES_MAP,
    KEY_LIVE_AREAS_STATUS,
)
from .legacy import LegacyPanelClient
from .protocol.commands import ArmMode
from .readiness import async_arm
from .selection import area_label, selected

_LOGGER = logging.getLogger(__name__)

# Options of the select, and the command each one sends
OPTION_DISARMED = "disarmed"
OPTION_ARMED_TOTAL = "armed_total"
OPTION_ARMED_PARTIAL = "armed_partial"
OPTION_ARMED_INSTANT = "armed_instant"
OPTION_MODES = {
    OPTION_DISARMED: ArmMode.DISARM,
    OPTION_ARMED_TOTAL: ArmMode.AWAY,
    OPTION_ARMED_PARTIAL: ArmMode.STAY,
    OPTION_ARMED_INSTANT: ArmMode.INSTANT,
}
# The area's mode as decoded from the panel status -> option
_MODE_OPTIONS = {
    AREA_MODE_DISARMED: OPTION_DISARMED,
    AREA_MODE_TOTAL: OPTION_ARMED_TOTAL,
    AREA_MODE_PARTIAL: OPTION_ARMED_PARTIAL,
    AREA_MODE_INSTANT: OPTION_ARMED_INSTANT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one arming-mode select per imported area."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    api_client: LegacyPanelClient = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get(KEY_INIT_SYSTEM_INFO, {})
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)
    async_add_entities(
        InimAreaModeSelect(
            coordinator,
            api_client,
            entry,
            panel_display_name,
            system_info,
            area,
            area_label(initial_panel_config, area),
        )
        for area in selected(entry.options, initial_panel_config, CONF_AREAS)
    )


class InimAreaModeSelect(CoordinatorEntity, SelectEntity):
    """The arming mode of one area; choosing a mode arms or disarms it."""

    _attr_has_entity_name = True
    _attr_translation_key = "area_arming_mode"
    _attr_options = list(OPTION_MODES)
    _attr_icon = "mdi:shield-home-outline"

    def __init__(
        self,
        coordinator: Any,
        api_client: LegacyPanelClient,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        area: int,
        area_name: str,
    ) -> None:
        """Initialize the select of one area (1-based)."""
        super().__init__(coordinator)
        self.api_client = api_client
        self._entry = config_entry
        self._area = area
        self._area_name = area_name
        self._attr_unique_id = f"{config_entry.entry_id}_area_mode_select_{area}"
        self._attr_translation_placeholders = {"area": str(area)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=panel_display_name,
            manufacturer="Inim Electronics",
            model=system_info.get(KEY_INIT_SYSTEM_INFO_TYPE, "SmartLiving"),
            sw_version=system_info.get(KEY_INIT_SYSTEM_INFO_VERSION, "Unknown"),
        )
        self._update_from_coordinator()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_coordinator()
        self.async_write_ha_state()

    def _update_from_coordinator(self) -> None:
        areas = (self.coordinator.data or {}).get(KEY_LIVE_AREAS_STATUS) or {}
        mode = (areas.get(KEY_LIVE_AREA_MODES_MAP) or {}).get(self._area)
        self._attr_current_option = _MODE_OPTIONS.get(mode)  # None while unknown

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The area's number and label."""
        return {"area_id": self._area, "area_name": self._area_name}

    async def async_select_option(self, option: str) -> None:
        """Arm the area in the chosen mode, or disarm it."""
        _LOGGER.info("Setting area %s (%s) to %s", self._area, self._area_name, option)
        # Disarming is never checked; arming checks the zones of the area
        await async_arm(
            self.hass,
            self._entry,
            lambda check: self.api_client.panel.arm_areas(
                {self._area: OPTION_MODES[option]}, on_not_ready=check
            ),
            self._area_name,
        )
        self._attr_current_option = option  # until the next update says otherwise
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
