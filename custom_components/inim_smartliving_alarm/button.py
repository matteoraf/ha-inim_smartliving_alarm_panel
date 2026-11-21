"""Platform for button entities for the Inim Alarm integration (Scenarios)."""

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_PANEL_NAME,
    CONF_SCENARIOS,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
)
from .legacy import LegacyPanelClient
from .panel_config import async_reread_panel_configuration
from .selection import (
    is_disarm_scenario,
    scenario_actions,
    scenario_label,
    scenario_names,
    selected,
)
from .utils import async_activate_scenario

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm button entities (scenarios) from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'button' platform entities for entry: %s", entry.title
    )

    api_client: LegacyPanelClient = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    coordinator: DataUpdateCoordinator | None = hass.data[DOMAIN][entry.entry_id].get(
        DATA_COORDINATOR
    )

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get(KEY_INIT_SYSTEM_INFO, {})
    all_scenario_names = scenario_names(initial_panel_config)
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    async_add_entities(
        [InimRereadConfigurationButton(entry, panel_display_name, system_info)]
    )

    buttons_to_add = []

    if not all_scenario_names:
        _LOGGER.warning(
            "No scenario names found in initial config for %s. Cannot create scenario buttons",
            entry.title,
        )
        return

    scenarios = selected(entry.options, initial_panel_config, CONF_SCENARIOS)
    _LOGGER.debug("Setting up scenario buttons for %s", scenarios)

    for number in scenarios:
        scenario_index_0_based = number - 1
        scenario_name = scenario_label(initial_panel_config, number)
        buttons_to_add.append(
            InimScenarioActivationButton(
                api_client,
                coordinator,
                entry,
                initial_panel_config,
                panel_display_name,
                system_info,
                scenario_index_0_based,
                scenario_name,
            )
        )

    if buttons_to_add:
        async_add_entities(buttons_to_add)
    _LOGGER.info(
        "Added %s Inim Alarm scenario button entities for %s",
        len(buttons_to_add),
        entry.title,
    )


class InimScenarioActivationButton(ButtonEntity):
    """Representation of an Inim Alarm Scenario as a button."""

    def __init__(
        self,
        api_client: LegacyPanelClient,
        coordinator: DataUpdateCoordinator | None,
        config_entry: ConfigEntry,
        initial_panel_config: dict[str, Any],
        panel_display_name: str,
        system_info: dict[str, Any],
        scenario_index_0_based: int,
        scenario_name: str,
    ) -> None:
        """Initialize the Inim Scenario activation button."""
        self.api_client = api_client
        self._coordinator = coordinator
        self.config_entry = config_entry
        self._initial_panel_config = initial_panel_config
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._scenario_index_0_based = scenario_index_0_based
        self._scenario_name = scenario_name

        self._attr_name = f"Activate '{self._scenario_name}'"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_scenario_button_{self._scenario_index_0_based}"
        )
        self._attr_icon = "mdi:play-box"

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_scenario_{self._scenario_index_0_based}"

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

    async def async_press(self) -> None:
        """Handle the button press to activate the scenario."""
        action_name = f"activate scenario '{self._scenario_name}' (index {self._scenario_index_0_based})"
        _LOGGER.info("Button '%s' pressed: Attempting to %s", self.name, action_name)
        # A refusal is explained by open zones unless the scenario only disarms
        is_disarm = is_disarm_scenario(
            self._initial_panel_config, self._scenario_index_0_based + 1
        )

        # Checks that no zone prevents it first; raises an error if not
        # carried out, after a notification and the activation-failed event
        await async_activate_scenario(
            self.hass,
            self.config_entry,
            self._coordinator,
            self.api_client,
            self._initial_panel_config,
            self.unique_id,
            self.name,
            self._scenario_index_0_based,
            action_name,
            is_disarm_scenario=is_disarm,
            action_key="activate_scenario",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        attrs = {
            "scenario_index": self._scenario_index_0_based,
            "scenario_name": self._scenario_name,
        }
        # Per area: keep, total, partial, instant or disarm
        if actions := scenario_actions(
            self._initial_panel_config, self._scenario_index_0_based + 1
        ):
            attrs["area_actions_details"] = actions
        return attrs


class InimRereadConfigurationButton(ButtonEntity):
    """Button that reads the panel configuration again and reloads the entry."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:database-refresh"

    def __init__(
        self,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
    ) -> None:
        """Initialize the button."""
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._attr_name = "Re-read panel configuration"
        self._attr_unique_id = f"{config_entry.entry_id}_reread_panel_configuration"

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_reread_panel_configuration"

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

    async def async_press(self) -> None:
        """Read the panel configuration again and reload the entry."""
        await async_reread_panel_configuration(self.hass, self.config_entry)
