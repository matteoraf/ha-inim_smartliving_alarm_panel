"""Arming mode of each area as a select entity."""

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import SmartLivingPanel
from .attribute_values import area_mode_text, area_settings_attributes
from .const import (
    AREA_MODE_DISARMED,
    AREA_MODE_INSTANT,
    AREA_MODE_PARTIAL,
    AREA_MODE_TOTAL,
    CONF_AREAS,
    CONF_PANEL_NAME,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_PANEL_NAME,
    DOMAIN,
)
from .entity import InimCoordinatorEntity, panel_device_info
from .protocol.commands import ArmMode
from .protocol.masking import Level
from .readiness import async_arm
from .report_state import async_set_level, level_signal, report_state
from .selection import (
    selected,
)
from .stored_config import (
    area_label,
    area_records,
)

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
    """Set up one arming-mode select per imported area, and the diagnostics level."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    panel: SmartLivingPanel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)
    # The areas' delays and options, decoded from the stored records
    records = area_records(initial_panel_config) or []
    async_add_entities(
        InimAreaModeSelect(
            coordinator,
            panel,
            entry,
            panel_display_name,
            area,
            area_label(initial_panel_config, area),
            area_settings_attributes(records[area - 1]) if area <= len(records) else {},
        )
        for area in selected(entry.options, initial_panel_config, CONF_AREAS)
    )
    async_add_entities([InimDiagnosticsLevelSelect(entry, panel_display_name)])


class InimAreaModeSelect(InimCoordinatorEntity, SelectEntity):
    """The arming mode of one area; choosing a mode arms or disarms it."""

    _attr_has_entity_name = True
    _attr_translation_key = "area_arming_mode"
    _attr_options = list(OPTION_MODES)
    _attr_icon = "mdi:shield-home-outline"

    def __init__(
        self,
        coordinator: Any,
        panel: SmartLivingPanel,
        config_entry: ConfigEntry,
        panel_display_name: str,
        area: int,
        area_name: str,
        settings: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the select of one area (1-based).

        settings: the area's delays and options as attributes (empty when
        they are not known).
        """
        super().__init__(coordinator)
        self.panel = panel
        self._entry = config_entry
        self._area = area
        self._area_name = area_name
        self._settings = settings or {}
        self._attr_unique_id = f"{config_entry.entry_id}_area_mode_select_{area}"
        self._attr_translation_placeholders = {"area": str(area)}
        self._attr_device_info = panel_device_info(config_entry)
        self._update_from_coordinator()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_coordinator()
        self.async_write_ha_state()

    def _update_from_coordinator(self) -> None:
        mode = self.area_mode(self._area)
        # None while unknown
        self._attr_current_option = (
            None if mode is None else _MODE_OPTIONS.get(area_mode_text(mode))
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The area's number, label, delays and options."""
        return {"area_id": self._area, "area_name": self._area_name, **self._settings}

    async def async_select_option(self, option: str) -> None:
        """Arm the area in the chosen mode, or disarm it."""
        _LOGGER.info("Setting area %s (%s) to %s", self._area, self._area_name, option)
        # Disarming is never checked; arming checks the zones of the area
        await async_arm(
            self.hass,
            self._entry,
            lambda check: self.panel.arm_areas(
                {self._area: OPTION_MODES[option]}, on_not_ready=check
            ),
            self._area_name,
        )
        self._attr_current_option = option  # until the next update says otherwise
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class InimDiagnosticsLevelSelect(SelectEntity):
    """How much the next diagnostics download (the system report) shows.

    Kept in memory only: Redacted after every restart, and again after a
    report at a Full level has been downloaded in both forms.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "diagnostics_level"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_options = [str(level) for level in Level]
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
    ) -> None:
        """Initialize the select of one entry."""
        self._entry_id = config_entry.entry_id
        self._attr_unique_id = f"{config_entry.entry_id}_diagnostics_level"
        self._attr_device_info = panel_device_info(config_entry)

    @property
    def current_option(self) -> str:
        """The level of the next report."""
        return str(report_state(self.hass, self._entry_id).level)

    async def async_added_to_hass(self) -> None:
        """Follow changes made by the diagnostics download."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, level_signal(self._entry_id), self.async_write_ha_state
            )
        )

    async def async_select_option(self, option: str) -> None:
        """Choose the level of the next report."""
        async_set_level(self.hass, self._entry_id, Level(option))
