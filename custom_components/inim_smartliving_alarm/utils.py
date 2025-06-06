# custom_components/inim_smartliving_alarm/utils.py
"""Utility functions for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

# Import keys from const.py
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

_LOGGER = logging.getLogger(__name__)


async def async_handle_scenario_activation_failure(
    hass: HomeAssistant,
    coordinator: DataUpdateCoordinator | None,
    initial_panel_config: dict[str, Any],
    entity_unique_id: str,
    entity_name: str,
    scenario_idx_to_activate: int,
    action_name: str,
    is_disarm_scenario: bool,
) -> None:
    """Handles the failure of a scenario activation, providing detailed notifications."""

    _LOGGER.error(
        "%s: Failed to %s (scenario index %s) - API reported failure",
        entity_name,
        action_name,
        scenario_idx_to_activate,
    )

    # Determine default scenario name for messages/titles if details aren't fully available
    base_scenario_name_for_msg = f"Scenario {scenario_idx_to_activate + 1}"
    if initial_panel_config:
        all_scenario_names_list_for_default = initial_panel_config.get(
            KEY_INIT_SCENARIOS, {}
        ).get(KEY_INIT_SCENARIO_NAMES, [])
        if 0 <= scenario_idx_to_activate < len(all_scenario_names_list_for_default):
            base_scenario_name_for_msg = (
                all_scenario_names_list_for_default[scenario_idx_to_activate]
                or base_scenario_name_for_msg
            )

    notification_title = (
        f"Inim Alarm - '{base_scenario_name_for_msg}' Failed"  # Default title
    )
    notification_message = (
        f"Failed to {action_name} for '{entity_name}'. Check panel for details."
    )
    notification_id_suffix = f"action_failed_generic_{scenario_idx_to_activate}"

    if (
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
            notification_title = (
                f"Inim Alarm - '{target_scenario_name}' Failed"  # MODIFIED title
            )
            notification_message = (
                f"Failed to activate {target_scenario_name} for '{entity_name}'. "
                "Check panel for details (no specific areas identified or details missing)."
            )
            notification_id_suffix = (
                f"activation_failed_no_areas_{scenario_idx_to_activate}"
            )
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
                    if zone_config:
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

            if problematic_zones_messages:
                notification_title = f"Inim Alarm - Scenario '{target_scenario_name}' Blocked"  # MODIFIED title
                # MODIFIED: Format zones as an ordered list
                ordered_zones_list_str = "\n".join(
                    [
                        f"{i + 1}. {msg}"
                        for i, msg in enumerate(problematic_zones_messages)
                    ]
                )
                notification_message = (
                    f"Cannot activate {target_scenario_name}. "
                    f"The following zones are preventing activation:\n{ordered_zones_list_str}"  # MODIFIED
                )
                notification_id_suffix = (
                    f"activation_blocked_{scenario_idx_to_activate}"
                )
                _LOGGER.info("%s: %s", entity_name, notification_message)
            else:
                notification_title = (
                    f"Inim Alarm - '{target_scenario_name}' Failed"  # MODIFIED title
                )
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
        notification_title = (
            f"Inim Alarm - '{base_scenario_name_for_msg}' Failed"  # MODIFIED title
        )
        notification_message = f"Failed to {action_name} for '{entity_name}'. Reason undetermined (data unavailable)."
        notification_id_suffix = f"activation_failed_no_data_{scenario_idx_to_activate}"
        _LOGGER.warning(
            "%s: Data unavailable to determine cause of %s failure",
            entity_name,
            action_name,
        )
    else:  # Disarm scenario failed or other generic failure
        # Title already includes base_scenario_name_for_msg from default initialization
        notification_message = f"Failed to {action_name} ({base_scenario_name_for_msg}) for '{entity_name}'. Check panel."
        # notification_id_suffix remains as initialized: "action_failed_generic_{scenario_idx_to_activate}"

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": notification_title,
            "message": notification_message,
            "notification_id": f"{DOMAIN}_{entity_unique_id}_{notification_id_suffix}",
        },
        blocking=False,
    )

    if coordinator:
        await coordinator.async_request_refresh()
