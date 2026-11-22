"""Platform for Inim Alarm Control Panel."""

import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import stored_config
from .api import SmartLivingPanel
from .const import (
    CONF_PANEL_NAME,
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
    DATA_ALARM_PANEL,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_LIVE_ACTIVE_SCENARIO,
    KEY_LIVE_ACTIVE_SCENARIO_NUMBER,
    KEY_LIVE_ALARM_AREAS_LIST,
    KEY_LIVE_AREAS_STATUS,
    KEY_LIVE_TAMPER_MEMORY_AREAS_LIST,
    KEY_LIVE_TRIGGERED_AREAS_LIST,
)
from .errors import InimCommandError, async_send_command
from .stored_config import (
    SystemInfo,
    area_names,
    device_model,
    device_sw_version,
)
from .utils import async_activate_scenario

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm Control Panel from a config entry."""
    _LOGGER.debug("Setting up Inim Alarm Control Panel for entry: %s", entry.title)

    panel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    # Initial config fetched by config_flow and stored in entry.data
    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    system_info = stored_config.system_info(initial_panel_config)

    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    # Scenario mappings are stored in entry.options by the options flow
    scenario_mappings = entry.options

    alarm_panel = InimAlarmControlPanel(
        coordinator,
        panel,
        entry,
        initial_panel_config,
        panel_display_name,
        system_info,
        scenario_mappings,
    )
    async_add_entities([alarm_panel])
    _LOGGER.info("Inim Alarm Control Panel %s added", alarm_panel.name)

    # The alarm_reset action (registered once, in __init__) reaches it here
    hass.data[DOMAIN][entry.entry_id][DATA_ALARM_PANEL] = alarm_panel


class InimAlarmControlPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of an Inim Alarm Control Panel."""

    def __init__(
        self,
        coordinator: Any,
        panel: SmartLivingPanel,
        config_entry: ConfigEntry,
        initial_panel_config: dict[str, Any],
        panel_display_name: str,
        system_info: SystemInfo | None,
        scenario_mappings: dict[str, int | None],
    ) -> None:
        """Initialize the alarm control panel entity."""
        super().__init__(coordinator)
        self.panel = panel
        self.config_entry = config_entry
        self._initial_panel_config = initial_panel_config
        self._panel_display_name = panel_display_name
        self._system_info = system_info
        self._scenario_mappings = scenario_mappings

        self._attr_name = self._panel_display_name
        self._attr_unique_id = f"{config_entry.entry_id}_alarm_control_panel"

        self._attr_code_arm_required = (
            False  # PIN is handled by the API during scenario activation
        )
        # self._attr_code_format = None  # No code format needed from HA UI

        # Determine supported features based on mapped scenarios
        supported_features = AlarmControlPanelEntityFeature(
            0
        )  # Initialize with 0 or specific base features
        if self._scenario_mappings.get(CONF_SCENARIO_ARM_AWAY) is not None:
            supported_features |= AlarmControlPanelEntityFeature.ARM_AWAY
        if self._scenario_mappings.get(CONF_SCENARIO_ARM_HOME) is not None:
            supported_features |= AlarmControlPanelEntityFeature.ARM_HOME
        if self._scenario_mappings.get(CONF_SCENARIO_ARM_NIGHT) is not None:
            supported_features |= AlarmControlPanelEntityFeature.ARM_NIGHT
        if self._scenario_mappings.get(CONF_SCENARIO_ARM_VACATION) is not None:
            supported_features |= AlarmControlPanelEntityFeature.ARM_VACATION
        # Disarm is fundamental, assumed to be supported if CONF_SCENARIO_DISARM is mapped.
        # The base AlarmControlPanelEntity already supports disarm if other arm modes are supported.
        self._attr_supported_features = supported_features

        # This internal attribute will hold the current state enum
        self._current_ha_state_enum: AlarmControlPanelState | None = (
            AlarmControlPanelState.DISARMED  # Default to DISARMED initially
        )
        # The unmapped active scenario last warned about, so that the warning
        # comes once per change rather than at every poll
        self._unmapped_scenario_logged: int | None = None
        self._update_internal_state()  # Initial state update

        _LOGGER.debug(
            "Initialized Inim Alarm Panel: %s, Unique ID: %s",
            self.name,
            self.unique_id,
        )
        _LOGGER.debug("Scenario Mappings: %s", self._scenario_mappings)
        _LOGGER.debug("Supported Features: %s", self.supported_features)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this alarm panel."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=device_model(self._system_info),
            sw_version=device_sw_version(self._system_info),
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the state of the entity using AlarmControlPanelState enum."""
        # The state is derived and stored by _update_internal_state()
        # which is called by _handle_coordinator_update()
        return self._current_ha_state_enum

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Alarm panel '%s' received coordinator update", self.name)
        self._update_internal_state()  # This updates _current_ha_state_enum
        self.async_write_ha_state()  # Update HA state, which will use the new alarm_state property

    def _update_internal_state(self) -> None:
        """Update the internal HA state based on coordinator data, using AlarmControlPanelState enum."""
        if self.coordinator.data is None:
            _LOGGER.warning(
                "Coordinator data is None for %s, cannot update state. Previous state: %s",
                self.name,
                self._current_ha_state_enum,
            )
            # Optionally set to UNKNOWN or keep previous state.
            # self._current_ha_state_enum = AlarmControlPanelState.UNKNOWN # Or None
            return

        active_scenario_data = self.coordinator.data.get(KEY_LIVE_ACTIVE_SCENARIO)
        areas_status_data = self.coordinator.data.get(KEY_LIVE_AREAS_STATUS)

        current_active_scenario_idx = -1  # Default to unknown
        if (
            active_scenario_data
            and active_scenario_data.get(KEY_LIVE_ACTIVE_SCENARIO_NUMBER) is not None
        ):
            current_active_scenario_idx = active_scenario_data.get(
                KEY_LIVE_ACTIVE_SCENARIO_NUMBER
            )

        _LOGGER.debug(
            "%s: Current active Inim scenario index: %s",
            self.name,
            current_active_scenario_idx,
        )

        # None (unknown) unless a state can be determined below; without a
        # mapped Disarm scenario nothing may set it
        new_state_enum: AlarmControlPanelState | None = None

        # Default to DISARMED if no other conditions are met and a disarm scenario is defined.
        # If no disarm scenario, it's harder to default.
        if self._scenario_mappings.get(CONF_SCENARIO_DISARM) is not None:
            new_state_enum = AlarmControlPanelState.DISARMED

        # TRIGGERED while an area is in alarm now (highest priority); the alarm
        # memory that stays after the alarm is not considered here
        if areas_status_data and areas_status_data.get(KEY_LIVE_ALARM_AREAS_LIST):
            if any(areas_status_data[KEY_LIVE_ALARM_AREAS_LIST]):
                _LOGGER.info(
                    "%s: Panel is TRIGGERED. Areas in alarm: %s",
                    self.name,
                    areas_status_data[KEY_LIVE_ALARM_AREAS_LIST],
                )
                new_state_enum = AlarmControlPanelState.TRIGGERED
                # If triggered, this state takes precedence over scenario-based states
                if self._current_ha_state_enum != new_state_enum:
                    _LOGGER.info(
                        "%s: State changed from %s to %s (Triggered)",
                        self.name,
                        self._current_ha_state_enum,
                        new_state_enum,
                    )
                    self._current_ha_state_enum = new_state_enum
                else:
                    _LOGGER.debug(
                        "%s: State remains %s (Triggered)",
                        self.name,
                        self._current_ha_state_enum,
                    )
                return  # Exit early as TRIGGERED is definitive

        # If not triggered, determine state based on active scenario
        unmapped = False
        if current_active_scenario_idx == self._scenario_mappings.get(
            CONF_SCENARIO_DISARM
        ):
            new_state_enum = AlarmControlPanelState.DISARMED
        elif current_active_scenario_idx == self._scenario_mappings.get(
            CONF_SCENARIO_ARM_HOME
        ):
            new_state_enum = AlarmControlPanelState.ARMED_HOME
        elif current_active_scenario_idx == self._scenario_mappings.get(
            CONF_SCENARIO_ARM_AWAY
        ):
            new_state_enum = AlarmControlPanelState.ARMED_AWAY
        elif current_active_scenario_idx == self._scenario_mappings.get(
            CONF_SCENARIO_ARM_NIGHT
        ):
            new_state_enum = AlarmControlPanelState.ARMED_NIGHT
        elif current_active_scenario_idx == self._scenario_mappings.get(
            CONF_SCENARIO_ARM_VACATION
        ):
            new_state_enum = AlarmControlPanelState.ARMED_VACATION
        else:
            # Active scenario does not match any mapped HA arm state.
            # If it's a valid scenario index but not the disarm one, it implies some form of armed state.
            if (
                current_active_scenario_idx != -1  # A valid scenario is active
                and self._scenario_mappings.get(CONF_SCENARIO_DISARM)
                is not None  # Disarm scenario is known
                and current_active_scenario_idx
                != self._scenario_mappings.get(
                    CONF_SCENARIO_DISARM
                )  # And it's not that disarm scenario
            ):
                if current_active_scenario_idx != self._unmapped_scenario_logged:
                    _LOGGER.warning(
                        "%s: Active scenario %s is not mapped to a Home Assistant "
                        "state; shown as armed (custom bypass)",
                        self.name,
                        current_active_scenario_idx,
                    )
                    self._unmapped_scenario_logged = current_active_scenario_idx
                unmapped = True
                new_state_enum = AlarmControlPanelState.ARMED_CUSTOM_BYPASS

            elif (
                current_active_scenario_idx == -1
                and new_state_enum == AlarmControlPanelState.DISARMED
            ):
                _LOGGER.debug(
                    "%s: No active scenario reported by panel (-1), defaulting to DISARMED",
                    self.name,
                )
            # If new_state_enum is still None at this point (e.g. no disarm scenario defined and no match)
            # then it will remain None or be UNKNOWN.
        if not unmapped:
            self._unmapped_scenario_logged = None  # warn again on the next one

        if self._current_ha_state_enum != new_state_enum:
            _LOGGER.info(
                "%s: State changed from %s to %s",
                self.name,
                self._current_ha_state_enum,
                new_state_enum,
            )
            self._current_ha_state_enum = new_state_enum
        else:
            _LOGGER.debug(
                "%s: State remains %s", self.name, self._current_ha_state_enum
            )

    async def _async_activate_scenario_by_mapping(
        self, scenario_mapping_key: str, action_name: str
    ) -> None:
        """Activate the scenario mapped to an action; raise an error if not done."""
        scenario_idx_to_activate = self._scenario_mappings.get(scenario_mapping_key)

        if scenario_idx_to_activate is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scenario_not_mapped",
                translation_placeholders={"action": action_name, "panel": self.name},
            )

        _LOGGER.info(
            "%s: Attempting to %s (scenario index %s)",
            self.name,
            action_name,
            scenario_idx_to_activate,
        )
        # Checks that no zone prevents it first; raises an error if not
        # carried out, after a notification and the activation-failed event
        await async_activate_scenario(
            self.hass,
            self.config_entry,
            self.coordinator,
            self.panel,
            self._initial_panel_config,
            self.unique_id,
            self.name,
            scenario_idx_to_activate,
            action_name,
            is_disarm_scenario=scenario_mapping_key == CONF_SCENARIO_DISARM,
            # "arm away" -> arm_away: its text in the translations
            action_key=action_name.replace(" ", "_"),
        )

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        await self._async_activate_scenario_by_mapping(CONF_SCENARIO_DISARM, "disarm")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home command."""
        await self._async_activate_scenario_by_mapping(
            CONF_SCENARIO_ARM_HOME, "arm home"
        )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        await self._async_activate_scenario_by_mapping(
            CONF_SCENARIO_ARM_AWAY, "arm away"
        )

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Send arm night command."""
        await self._async_activate_scenario_by_mapping(
            CONF_SCENARIO_ARM_NIGHT, "arm night"
        )

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        """Send arm vacation command."""
        await self._async_activate_scenario_by_mapping(
            CONF_SCENARIO_ARM_VACATION, "arm vacation"
        )

    async def async_reset_memories(self) -> None:
        """Reset every area with an alarm or tamper memory.

        Every area is tried; the first that was not reset raises.
        """
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.warning(
                "No coordinator data available for %s to find triggered areas",
                self.name,
            )
            return
        areas_status = coordinator_data.get(KEY_LIVE_AREAS_STATUS, {})
        triggered_areas = sorted(
            set(areas_status.get(KEY_LIVE_TRIGGERED_AREAS_LIST) or [])
            | set(areas_status.get(KEY_LIVE_TAMPER_MEMORY_AREAS_LIST) or [])
        )
        if not triggered_areas:
            _LOGGER.info(
                "Alarm Reset called for %s, but no area has an alarm or tamper memory",
                self.name,
            )
            return
        _LOGGER.info(
            "Found areas with an alarm or tamper memory to reset on %s: %s",
            self.name,
            triggered_areas,
        )
        failures: list[InimCommandError] = []
        for area_id in triggered_areas:
            try:
                await self.async_alarm_reset(area_id=area_id)
            except InimCommandError as err:
                failures.append(err)
        if failures:
            raise failures[0]

    async def async_alarm_reset(self, area_id: int) -> None:
        """Reset the alarm memory of one area; raise an error if not done."""
        _LOGGER.info("%s: Attempting to reset alarm for area %d", self.name, area_id)
        names = area_names(self._initial_panel_config)
        area_name = (
            names[area_id - 1] if 0 < area_id <= len(names) else ""
        ) or f"Area {area_id}"
        try:
            await async_send_command(
                self.panel.reset_alarm_memory(frozenset({area_id})),
                area_name,
            )
        finally:
            await self.coordinator.async_request_refresh()
        _LOGGER.info(
            "%s: Alarm reset command for area %d carried out by the panel",
            self.name,
            area_id,
        )
