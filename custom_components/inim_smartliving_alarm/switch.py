"""Platform for switch entities for the Inim Alarm integration.

Areas, zone bypass, outputs, and the panel's slow polling mode.
"""

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .api import SmartLivingPanel
from .attribute_values import area_mode_text, area_status_text, switch_arm_mode
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_AREA_SWITCH_ARM_MODE_BY_AREA,
    CONF_AREA_SWITCHES_DEFAULT_ARM_MODE,
    CONF_AREAS,
    CONF_BOARD,
    CONF_PANEL_NAME,
    CONF_ZONES,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_AREA_SWITCHES_ARM_MODE,
    DEFAULT_PANEL_NAME,
    DOMAIN,
)
from .entity import InimCoordinatorEntity, name_item, panel_device_info
from .errors import async_send_command
from .outputs import InimOutputEntity
from .protocol.commands import ArmMode
from .readiness import async_arm
from .selection import (
    imported_outputs,
    is_dimmer,
    selected,
)
from .stored_config import (
    area_label,
    zone_label,
    zones_to_create,
)

_LOGGER = logging.getLogger(__name__)


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
    panel: SmartLivingPanel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    switches_to_add = []

    # --- Area switches: every selected area of the model ---
    areas = selected(entry.options, initial_panel_config, CONF_AREAS)
    _LOGGER.debug("Setting up area switches for areas %s", areas)
    for area_id_1_based in areas:
        switches_to_add.append(
            InimAreaSwitch(
                coordinator,
                panel,
                entry,
                panel_display_name,
                area_id_1_based,
                area_label(initial_panel_config, area_id_1_based),
            )
        )

    # --- Zone bypass switches: a switch needs the terminal's physical slot ---
    zones = zones_to_create(
        initial_panel_config,
        selected(entry.options, initial_panel_config, CONF_ZONES),
    )
    if without_slot := [zone.number for zone in zones if zone.slot is None]:
        _LOGGER.debug(
            "%s: no bypass switch for zones %s: their terminal is in no physical slot",
            entry.title,
            ", ".join(str(number) for number in without_slot),
        )
    _LOGGER.debug("Setting up zone bypass switches for %s zones", len(zones))
    for zone in zones:
        if zone.slot is None:
            continue
        switches_to_add.append(
            InimZoneEnabledSwitch(
                coordinator,
                panel,
                entry,
                panel_display_name,
                zone.number,
                zone_label(initial_panel_config, zone.number),
                zone.slot,
                second_zone=zone.second_zone,
            )
        )

    # --- Outputs switched on and off (dimmers are lights) ---
    for item in imported_outputs(entry.options, initial_panel_config):
        if item.address is not None and not is_dimmer(item):
            switches_to_add.append(
                InimOutputSwitch(
                    coordinator,
                    panel,
                    entry,
                    panel_display_name,
                    item,
                )
            )

    # --- Slow polling mode: for the board that accepts one client at a time ---
    if entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI) == BOARD_SMARTLAN_SI:
        switches_to_add.append(InimSlowPollingSwitch(coordinator, entry))

    if switches_to_add:
        async_add_entities(switches_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm switch entities for %s",
            len(switches_to_add),
            entry.title,
        )
    else:
        _LOGGER.info("No Inim Alarm switch entities added for %s", entry.title)


class InimAreaSwitch(InimCoordinatorEntity, SwitchEntity):
    """An Inim area as a switch: on in any armed mode.

    Turning it on arms in the mode chosen in the options (total by default).
    """

    def __init__(
        self,
        coordinator: Any,
        panel: SmartLivingPanel,
        config_entry: ConfigEntry,
        panel_display_name: str,
        area_id_1_based: int,
        area_name: str,
    ) -> None:
        """Initialize the Area Switch."""
        super().__init__(coordinator)
        self.panel = panel
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._area_id_1_based = area_id_1_based
        self._area_name = area_name

        name_item(self, "area", "Area", self._area_id_1_based, area_name)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_area_switch_{self._area_id_1_based}"
        )

        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"area_{self._area_id_1_based}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        mode = self.area_mode(self._area_id_1_based)
        self._attr_is_on = mode is not None and mode.armed
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

        options = self.config_entry.options
        # This area's own mode, else the default of every area switch
        option = (options.get(CONF_AREA_SWITCH_ARM_MODE_BY_AREA) or {}).get(
            str(self._area_id_1_based)
        ) or options.get(
            CONF_AREA_SWITCHES_DEFAULT_ARM_MODE, DEFAULT_AREA_SWITCHES_ARM_MODE
        )
        mode = switch_arm_mode(option)
        await async_arm(
            self.hass,
            self.config_entry,
            lambda check: self.panel.arm_areas(
                {self._area_id_1_based: mode}, on_not_ready=check
            ),
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
            self.panel.arm_areas({self._area_id_1_based: ArmMode.DISARM}),
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
        if self.status is not None:
            mode = self.area_mode(self._area_id_1_based)
            attrs["status_text"] = "Unknown" if mode is None else area_status_text(mode)
            attrs["arming_mode"] = "unknown" if mode is None else area_mode_text(mode)
        return attrs


class InimZoneEnabledSwitch(InimCoordinatorEntity, SwitchEntity):
    """Representation of an Inim Zone's enabled status as a switch. ON = Enabled, OFF = Excluded."""

    def __init__(
        self,
        coordinator: Any,
        panel: SmartLivingPanel,
        config_entry: ConfigEntry,
        panel_display_name: str,
        zone_id_1_based: int,
        zone_name: str,
        zone_internal_index: int,
        *,
        second_zone: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self.panel = panel
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._zone_id_1_based = zone_id_1_based
        self._zone_name = zone_name
        self._zone_internal_index = zone_internal_index
        self._second_zone = second_zone  # the second zone of a double terminal
        name_item(self, "zone_included", "Zone", self._zone_id_1_based, zone_name)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_zone_enabled_switch_{self._zone_id_1_based}"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"zone_{self._zone_id_1_based}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        # On (included) unless the panel says the zone is bypassed
        self._attr_is_on = self.zone_included(self._zone_id_1_based) is not False
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
            self.panel.bypass_zone(
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
            self.panel.bypass_zone(
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


class InimOutputSwitch(InimOutputEntity, SwitchEntity):
    """An on/off or monostable output of the panel.

    A monostable output switches itself off after its time; the next poll
    shows it off.
    """

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the output on."""
        await self._async_switch(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the output off."""
        await self._async_switch(0)


class InimSlowPollingSwitch(InimCoordinatorEntity, SwitchEntity, RestoreEntity):
    """Slow polling mode: read the panel less often, so that other apps can
    connect to the board between the reads.

    It turns itself off after the time set in the Polling options; after a
    restart it is on again if it was on and that time has not passed.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_translation_key = "slow_polling"
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator: Any, config_entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_slow_polling"

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return "slow_polling_mode"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)

    @property
    def available(self) -> bool:
        """Always: it can be turned off while the panel does not answer."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether slow polling mode is on."""
        return self.coordinator.slow_polling

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When the mode turns itself off (None: when turned off)."""
        until = self.coordinator.slow_polling_until
        return {"until": until.isoformat() if until is not None else None}

    async def async_added_to_hass(self) -> None:
        """Turn the mode on again if it was on and its time has not passed."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state != STATE_ON or self.coordinator.slow_polling:
            return
        until: datetime | None = None
        if (text := last.attributes.get("until")) is not None:
            until = dt_util.parse_datetime(str(text))
            if until is None:  # not a time: do not turn it on for ever
                return
        await self.coordinator.async_restore_slow_polling(until)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn slow polling mode on."""
        await self.coordinator.async_set_slow_polling(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn slow polling mode off."""
        await self.coordinator.async_set_slow_polling(False)
