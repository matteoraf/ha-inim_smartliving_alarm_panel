"""Platform for switch entities for the Inim Alarm integration (Areas)."""

import functools  # Import functools
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_LIMIT_AREAS,
    CONF_PANEL_NAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREAS,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
    KEY_LIVE_AREA_STATUSES_MAP,
    KEY_LIVE_AREAS_STATUS,
)
from .inim_api import InimAlarmAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm switch entities (areas) from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'switch' platform entities for entry: %s", entry.title
    )

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    api_client: InimAlarmAPI = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get(KEY_INIT_SYSTEM_INFO, {})
    area_data = initial_panel_config.get(KEY_INIT_AREAS, {})
    all_area_names = area_data.get(KEY_INIT_AREA_NAMES, []) if area_data else []

    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)
    limit_areas = entry.options.get(
        CONF_LIMIT_AREAS, entry.data.get(CONF_LIMIT_AREAS, DEFAULT_LIMIT_AREAS)
    )

    switches_to_add = []

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

    if switches_to_add:
        async_add_entities(switches_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm area switch entities for %s",
            len(switches_to_add),
            entry.title,
        )
    else:
        _LOGGER.info("No Inim Alarm area switch entities added for %s", entry.title)


class InimAreaSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Inim Alarm Area as a switch."""

    def __init__(
        self,
        coordinator: Any,
        api_client: InimAlarmAPI,
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
        """Turn the switch on (arm the area)."""
        _LOGGER.info("Arming Area %s (%s)", self._area_id_1_based, self._area_name)

        # Use functools.partial to pre-fill keyword arguments
        func_to_call = functools.partial(
            self.api_client.execute_arm_disarm_areas,
            areas_to_arm=[self._area_id_1_based],
        )
        success = await self.hass.async_add_executor_job(func_to_call)

        if success:
            self._attr_is_on = True  # Optimistic update
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
            _LOGGER.info("ARM command for Area %s acknowledged", self._area_id_1_based)
        else:
            _LOGGER.error("Failed to arm Area %s", self._area_id_1_based)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (disarm the area)."""
        _LOGGER.info("Disarming Area %s (%s)", self._area_id_1_based, self._area_name)

        # Use functools.partial to pre-fill keyword arguments
        func_to_call = functools.partial(
            self.api_client.execute_arm_disarm_areas,
            areas_to_disarm=[self._area_id_1_based],
        )
        success = await self.hass.async_add_executor_job(func_to_call)

        if success:
            self._attr_is_on = False  # Optimistic update
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
            _LOGGER.info(
                "DISARM command for Area %s acknowledged", self._area_id_1_based
            )
        else:
            _LOGGER.error("Failed to disarm Area %s", self._area_id_1_based)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional Attributes."""
        attrs = {"area_id": self._area_id_1_based, "area_name": self._area_name}
        if self.coordinator.data and self.coordinator.data.get(KEY_LIVE_AREAS_STATUS):
            area_statuses = self.coordinator.data[KEY_LIVE_AREAS_STATUS].get(
                KEY_LIVE_AREA_STATUSES_MAP, {}
            )
            attrs["status_text"] = area_statuses.get(self._area_id_1_based, "Unknown")
        return attrs
