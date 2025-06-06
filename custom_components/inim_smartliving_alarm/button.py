"""Platform for button entities for the Inim Alarm integration (Scenarios)."""

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_LIMIT_SCENARIOS,
    CONF_PANEL_NAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_LIMIT_SCENARIOS,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS,
    KEY_INIT_SCENARIO_ACTIVATION_INDEX,
    KEY_INIT_SCENARIO_ACTIVATIONS,
    KEY_INIT_SCENARIO_NAMES,
    KEY_INIT_SCENARIOS,
    KEY_INIT_SYSTEM_INFO,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
)
from .inim_api import InimAlarmAPI
from .utils import async_handle_scenario_activation_failure

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

    api_client: InimAlarmAPI = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]

    coordinator: DataUpdateCoordinator | None = hass.data[DOMAIN][entry.entry_id].get(
        DATA_COORDINATOR
    )

    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = initial_panel_config.get(KEY_INIT_SYSTEM_INFO, {})
    scenario_data = initial_panel_config.get(KEY_INIT_SCENARIOS, {})
    all_scenario_names = (
        scenario_data.get(KEY_INIT_SCENARIO_NAMES, []) if scenario_data else []
    )
    scenario_activations_list = initial_panel_config.get(
        KEY_INIT_SCENARIO_ACTIVATIONS, []
    )

    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)
    limit_scenarios = entry.options.get(
        CONF_LIMIT_SCENARIOS,
        entry.data.get(CONF_LIMIT_SCENARIOS, DEFAULT_LIMIT_SCENARIOS),
    )

    buttons_to_add = []

    if not all_scenario_names:
        _LOGGER.warning(
            "No scenario names found in initial config for %s. Cannot create scenario buttons",
            entry.title,
        )
        return

    num_scenarios_to_create = min(len(all_scenario_names), limit_scenarios)
    _LOGGER.debug(
        "Setting up %s Scenario Buttons (Limit: %s, Available: %s)",
        num_scenarios_to_create,
        limit_scenarios,
        len(all_scenario_names),
    )

    for i in range(num_scenarios_to_create):
        scenario_index_0_based = i
        scenario_name = (
            all_scenario_names[i]
            if all_scenario_names[i]
            else f"Scenario {scenario_index_0_based + 1}"
        )
        current_scenario_activation_details = next(
            (
                sa
                for sa in scenario_activations_list
                if sa.get(KEY_INIT_SCENARIO_ACTIVATION_INDEX) == scenario_index_0_based
            ),
            None,
        )
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
                current_scenario_activation_details,
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
        api_client: InimAlarmAPI,
        coordinator: DataUpdateCoordinator | None,
        config_entry: ConfigEntry,
        initial_panel_config: dict[str, Any],
        panel_display_name: str,
        system_info: dict[str, Any],
        scenario_index_0_based: int,
        scenario_name: str,
        scenario_activation_details: dict[str, Any] | None = None,
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
        self._scenario_activation_details = scenario_activation_details

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
        try:
            # API's execute_activate_scenario handles connect/disconnect and internal checks
            success = await self.hass.async_add_executor_job(
                self.api_client.execute_activate_scenario, self._scenario_index_0_based
            )
            if success:
                _LOGGER.info(
                    "Scenario %s ('%s') activation command acknowledged by panel",
                    self._scenario_index_0_based,
                    self._scenario_name,
                )
                if self._coordinator:  # Refresh data if coordinator is available
                    await self._coordinator.async_request_refresh()
            else:
                # --- MODIFIED SECTION FOR FAILURE NOTIFICATION ---
                _LOGGER.error(
                    "API reported failure for %s via button '%s'",
                    action_name,
                    self.name,
                )
                is_disarm = False
                if self._scenario_activation_details:
                    # area_actions is a dict like {'1': 'arm', '2': 'disarm'}
                    area_actions_dict = self._scenario_activation_details.get(
                        KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS, {}
                    )
                    actions_present = set(
                        area_actions_dict.values()
                    )  # e.g., {'arm', 'keep'} or {'disarm'}

                    if "disarm" in actions_present and "arm" not in actions_present:
                        # Only consider it a disarm scenario if 'disarm' is present AND 'arm' is NOT.
                        is_disarm = True

                await async_handle_scenario_activation_failure(
                    self.hass,
                    self._coordinator,  # Pass the coordinator instance (can be None)
                    self._initial_panel_config,  # Pass the stored initial config
                    self.unique_id,
                    self.name,  # Use button's HA entity name
                    self._scenario_index_0_based,
                    action_name,  # Pass the constructed action name
                    is_disarm_scenario=is_disarm,
                )
                # The utility function now handles the coordinator refresh on failure if coordinator is passed.
        except Exception as e:  # General fallback for truly unexpected errors
            _LOGGER.error(
                "Unexpected error during button press for scenario %s ('%s'): %s",
                self._scenario_index_0_based,
                self._scenario_name,
                e,
                exc_info=True,  # Add exc_info for better debugging
            )
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Inim Alarm Button - Unexpected Error",
                    "message": f"An unexpected error occurred while trying to {action_name} via button '{self.name}': {e}",
                    "notification_id": f"{DOMAIN}_{self.unique_id}_unexpected_error_button",
                },
                blocking=False,
            )
        finally:  # Ensure coordinator is refreshed if available, even after handled exceptions
            if self._coordinator:
                await self._coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        attrs = {
            "scenario_index": self._scenario_index_0_based,
            "scenario_name": self._scenario_name,
        }
        if self._scenario_activation_details and self._scenario_activation_details.get(
            KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS
        ):
            attrs["area_actions_details"] = self._scenario_activation_details.get(
                KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS
            )
        return attrs
