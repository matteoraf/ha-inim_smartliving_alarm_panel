# custom_components/inim_smartliving_alarm/utils.py
"""Utility functions for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREAS,
    KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS,
    KEY_INIT_SCENARIO_ACTIVATION_INDEX,
    KEY_INIT_SCENARIO_ACTIVATIONS,
    KEY_INIT_SCENARIO_NAMES,
    KEY_INIT_SCENARIOS,
    KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS,
    KEY_INIT_ZONE_CONFIG_INDEX,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONES,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    KEY_LIVE_ZONE_STATUSES_MAP,
    KEY_LIVE_ZONES_STATUS,
    PROBLEM_ZONE_STATES_FOR_ARMING,
)
from .errors import (
    CommandFailedError,
    CommandRefusedError,
    InimCommandError,
    async_send_command,
)
from .legacy import LegacyPanelClient, zone_exists

_LOGGER = logging.getLogger(__name__)


def scenario_label(initial_panel_config: dict[str, Any], index: int) -> str:
    """A scenario's (0-based) label from the panel configuration."""
    names = (
        (initial_panel_config or {})
        .get(KEY_INIT_SCENARIOS, {})
        .get(KEY_INIT_SCENARIO_NAMES, [])
    )
    if 0 <= index < len(names) and names[index]:
        return names[index]
    return f"Scenario {index + 1}"


def zone_labels(initial_panel_config: dict[str, Any]) -> list[str]:
    """The zone labels from the panel configuration, by zone number - 1."""
    return (
        (initial_panel_config or {})
        .get(KEY_INIT_ZONES, {})
        .get(KEY_INIT_ZONE_NAMES, [])
    )


async def async_activate_scenario(
    hass: HomeAssistant,
    coordinator: DataUpdateCoordinator | None,
    api_client: LegacyPanelClient,
    initial_panel_config: dict[str, Any],
    entity_unique_id: str,
    entity_name: str,
    scenario_index: int,
    action_name: str,
    *,
    is_disarm_scenario: bool,
) -> None:
    """Activate a scenario (0-based) unless zones are not ready for it.

    Raises an InimCommandError when the scenario was not activated; the
    failure also gets a notification and the scenario_activation_failed event.
    """
    try:
        await async_send_command(
            api_client.panel.activate_scenario(scenario_index),
            scenario_label(initial_panel_config, scenario_index),
            zone_names=zone_labels(initial_panel_config),
        )
    except InimCommandError as err:
        await async_handle_scenario_activation_failure(
            hass,
            coordinator,
            initial_panel_config,
            entity_unique_id,
            entity_name,
            scenario_index,
            action_name,
            is_disarm_scenario,
            error=err,
        )
        raise
    _LOGGER.info(
        "%s: %s (scenario %s) carried out by the panel",
        entity_name,
        action_name,
        scenario_index,
    )
    if coordinator:
        await coordinator.async_request_refresh()


async def async_handle_scenario_activation_failure(
    hass: HomeAssistant,
    coordinator: DataUpdateCoordinator | None,
    initial_panel_config: dict[str, Any],
    entity_unique_id: str,
    entity_name: str,
    scenario_idx_to_activate: int,
    action_name: str,
    is_disarm_scenario: bool,
    error: InimCommandError | None = None,
) -> None:
    """Handles the failure of a scenario activation, providing detailed notifications and emitting an event.

    error is what stopped the activation. When the panel refused the command
    for a reason other than zones (wrong PIN, PIN not enabled, ...) or the
    command could not be sent, the notification gives that reason instead
    of looking for zones in the way.
    """

    _LOGGER.error(
        "%s: Failed to %s (scenario index %s): %s",
        entity_name,
        action_name,
        scenario_idx_to_activate,
        error,
    )

    # Determine default scenario name for messages/titles if details aren't fully available
    base_scenario_name_for_msg = scenario_label(
        initial_panel_config, scenario_idx_to_activate
    )

    notification_title = (
        f"Inim Alarm - '{base_scenario_name_for_msg}' Failed"  # Default title
    )
    notification_message = (
        f"Failed to {action_name} for '{entity_name}'. Check panel for details."
    )
    notification_id_suffix = f"action_failed_generic_{scenario_idx_to_activate}"

    # Set up default variables for the HA Event bus payload
    event_reason = "unknown"
    final_scenario_name = base_scenario_name_for_msg
    blocking_zones_event_data = []
    panel_result = error.reason if isinstance(error, CommandRefusedError) else None

    if isinstance(error, CommandFailedError) or panel_result not in {
        None,
        "refused",
        "open_zones",
    }:
        event_reason = "command_failed" if panel_result is None else panel_result
        notification_message = str(error)
        notification_id_suffix = f"activation_failed_{scenario_idx_to_activate}"
    elif (
        not is_disarm_scenario
        and coordinator
        and coordinator.data
        and initial_panel_config
    ):
        all_scenario_names_list = initial_panel_config.get(KEY_INIT_SCENARIOS, {}).get(
            KEY_INIT_SCENARIO_NAMES, []
        )
        all_scenario_activations_list = initial_panel_config.get(
            KEY_INIT_SCENARIO_ACTIVATIONS, []
        )

        target_scenario_name = f"Scenario {scenario_idx_to_activate + 1}"  # Default
        if 0 <= scenario_idx_to_activate < len(all_scenario_names_list):
            target_scenario_name = (
                all_scenario_names_list[scenario_idx_to_activate]
                or target_scenario_name
            )

        final_scenario_name = target_scenario_name

        target_scenario_activation_detail = next(
            (
                sa
                for sa in all_scenario_activations_list
                if sa.get(KEY_INIT_SCENARIO_ACTIVATION_INDEX)
                == scenario_idx_to_activate
            ),
            None,
        )

        areas_controlled_by_scenario = []
        if target_scenario_activation_detail:
            area_actions_dict = target_scenario_activation_detail.get(
                KEY_INIT_SCENARIO_ACTIVATION_AREA_ACTIONS, {}
            )
            areas_controlled_by_scenario = [
                int(area_id)
                for area_id, action in area_actions_dict.items()
                if action not in {"keep", "disarm"}
            ]

        if not areas_controlled_by_scenario:
            _LOGGER.info(
                "%s: Scenario %s (Index %s) "
                "does not appear to control specific areas for arming/disarming or details missing",
                entity_name,
                target_scenario_name,
                scenario_idx_to_activate,
            )
            notification_title = f"Inim Alarm - '{target_scenario_name}' Failed"
            notification_message = (
                f"Failed to activate {target_scenario_name} for '{entity_name}'. "
                "Check panel for details (no specific areas identified or details missing)."
            )
            notification_id_suffix = (
                f"activation_failed_no_areas_{scenario_idx_to_activate}"
            )
            event_reason = "no_areas_identified"
        else:
            all_zone_names_list = initial_panel_config.get(KEY_INIT_ZONES, {}).get(
                KEY_INIT_ZONE_NAMES, []
            )
            all_zones_config_detailed_list = initial_panel_config.get(
                KEY_INIT_ZONES_CONFIG, {}
            ).get(KEY_INIT_ZONES_CONFIG_DETAILED, [])
            all_area_names_list = initial_panel_config.get(KEY_INIT_AREAS, {}).get(
                KEY_INIT_AREA_NAMES, []
            )
            live_zone_statuses_map = coordinator.data.get(
                KEY_LIVE_ZONES_STATUS, {}
            ).get(KEY_LIVE_ZONE_STATUSES_MAP, {})

            problematic_zones_messages = []
            for zone_idx_0_based, zone_name_from_list in enumerate(all_zone_names_list):
                zone_id_1_based = zone_idx_0_based + 1
                # Use the name from the list, or create a default if it's empty/None
                zone_name = zone_name_from_list or f"Zone {zone_id_1_based}"

                zone_live_status = live_zone_statuses_map.get(str(zone_id_1_based))
                if zone_live_status is None:
                    zone_live_status = live_zone_statuses_map.get(zone_id_1_based)

                if zone_live_status in PROBLEM_ZONE_STATES_FOR_ARMING:
                    zone_config = next(
                        (
                            zc
                            for zc in all_zones_config_detailed_list
                            if zc.get(KEY_INIT_ZONE_CONFIG_INDEX) == zone_idx_0_based
                        ),
                        None,
                    )
                    if zone_config and zone_exists(zone_config):
                        assigned_areas = zone_config.get(
                            KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS, []
                        )
                        is_relevant = any(
                            area_id in areas_controlled_by_scenario
                            for area_id in assigned_areas
                        )
                        if is_relevant:
                            area_names_parts = []
                            for area_id in assigned_areas:
                                if area_id in areas_controlled_by_scenario:
                                    an = f"Area {area_id}"
                                    if 0 < area_id <= len(all_area_names_list):
                                        an = all_area_names_list[area_id - 1] or an
                                    area_names_parts.append(an)
                            areas_str = (
                                ", ".join(area_names_parts)
                                if area_names_parts
                                else "relevant area(s)"
                            )
                            problematic_zones_messages.append(
                                f"'{zone_name}' in {areas_str}"
                            )
                            # Add structured data for the event bus payload
                            blocking_zones_event_data.append(
                                {
                                    "zone_id": zone_id_1_based,
                                    "zone_name": zone_name,
                                    "status": zone_live_status,
                                    "areas": area_names_parts,
                                }
                            )

            if problematic_zones_messages:
                event_reason = "zones_blocked"
                notification_title = (
                    f"Inim Alarm - Scenario '{target_scenario_name}' Blocked"
                )
                # Format zones as an ordered list
                ordered_zones_list_str = "\n".join(
                    [
                        f"{i + 1}. {msg}"
                        for i, msg in enumerate(problematic_zones_messages)
                    ]
                )
                notification_message = (
                    f"Cannot activate {target_scenario_name}. "
                    f"The following zones are preventing activation:\n{ordered_zones_list_str}"
                )
                notification_id_suffix = (
                    f"activation_blocked_{scenario_idx_to_activate}"
                )
                _LOGGER.info("%s: %s", entity_name, notification_message)
            else:
                event_reason = "unknown_zones_error"
                notification_title = f"Inim Alarm - '{target_scenario_name}' Failed"
                notification_message = (
                    f"Failed to activate {target_scenario_name} for '{entity_name}'. "
                    "No specific zones found in problematic state. Check panel."
                )
                notification_id_suffix = (
                    f"activation_failed_unknown_zones_{scenario_idx_to_activate}"
                )
                _LOGGER.warning(
                    "%s: Scenario %s arming failed, but no specific problematic zones identified",
                    entity_name,
                    target_scenario_name,
                )

    elif not is_disarm_scenario:
        event_reason = "no_data_available"
        notification_title = f"Inim Alarm - '{base_scenario_name_for_msg}' Failed"
        notification_message = f"Failed to {action_name} for '{entity_name}'. Reason undetermined (data unavailable)."
        notification_id_suffix = f"activation_failed_no_data_{scenario_idx_to_activate}"
        _LOGGER.warning(
            "%s: Data unavailable to determine cause of %s failure",
            entity_name,
            action_name,
        )
    else:  # Disarm scenario failed or other generic failure
        event_reason = "disarm_failed"
        # Title already includes base_scenario_name_for_msg from default initialization
        notification_message = f"Failed to {action_name} ({base_scenario_name_for_msg}) for '{entity_name}'. Check panel."

    # 1. Trigger the Persistent Notification in the UI
    persistent_notification.async_create(
        hass,
        notification_message,
        title=notification_title,
        notification_id=f"{DOMAIN}_{entity_unique_id}_{notification_id_suffix}",
    )

    # 2. Fire the custom HA event for automations
    event_data = {
        "entity_unique_id": entity_unique_id,
        "entity_name": entity_name,
        "action": action_name,
        "scenario_index": scenario_idx_to_activate,
        "scenario_name": final_scenario_name,
        "reason": event_reason,
        "panel_result": panel_result,
        "blocking_zones": blocking_zones_event_data,
        "message": notification_message,
    }
    hass.bus.async_fire(f"{DOMAIN}_scenario_activation_failed", event_data)

    if coordinator:
        await coordinator.async_request_refresh()
