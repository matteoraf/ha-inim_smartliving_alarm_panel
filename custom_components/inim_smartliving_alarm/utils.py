# custom_components/inim_smartliving_alarm/utils.py
"""Utility functions for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS,
    KEY_INIT_ZONE_CONFIG_INDEX,
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
    PanelProgrammingError,
    ZonesNotReadyError,
    zone_label,
)
from .legacy import LegacyPanelClient, zone_exists
from .readiness import async_arm
from .selection import (
    area_names,
    scenario_armed_areas,
    scenario_label,
    scenario_names,
    zone_names,
)

_LOGGER = logging.getLogger(__name__)


async def async_activate_scenario(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DataUpdateCoordinator | None,
    api_client: LegacyPanelClient,
    initial_panel_config: dict[str, Any],
    entity_unique_id: str,
    entity_name: str,
    scenario_index: int,
    action_name: str,
    *,
    is_disarm_scenario: bool,
    force: bool = False,
) -> None:
    """Activate a scenario (0-based), checking first that zones are ready.

    Zones not ready: refused or sent anyway, as the entry's option (or
    force) says; see readiness. Raises an InimCommandError when the scenario
    was not activated; the failure also gets a notification and the
    scenario_activation_failed event.
    """
    try:
        await async_arm(
            hass,
            entry,
            lambda check: api_client.panel.activate_scenario(
                scenario_index, on_not_ready=check
            ),
            scenario_label(initial_panel_config or {}, scenario_index + 1),
            force=force,
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


def _scenario_zone_areas(
    initial_panel_config: dict[str, Any], zone: int, scenario_areas: list[int]
) -> list[str]:
    """Names of a zone's (1-based) areas that the scenario arms, as programmed."""
    zones_config = initial_panel_config.get(KEY_INIT_ZONES_CONFIG, {}).get(
        KEY_INIT_ZONES_CONFIG_DETAILED, []
    )
    names_of_areas = area_names(initial_panel_config)
    zone_config = next(
        (zc for zc in zones_config if zc.get(KEY_INIT_ZONE_CONFIG_INDEX) == zone - 1),
        None,
    )
    names = []
    for area_id in (zone_config or {}).get(KEY_INIT_ZONE_CONFIG_ASSIGNED_AREAS, []):
        if area_id in scenario_areas:
            name = f"Area {area_id}"
            if 0 < area_id <= len(names_of_areas):
                name = names_of_areas[area_id - 1] or name
            names.append(name)
    return names


def _blocking_zone_line(zone_name: str, zone_areas: list[str]) -> str:
    areas = ", ".join(zone_areas) if zone_areas else "relevant area(s)"
    return f"'{zone_name}' in {areas}"


def _blocked_notification(scenario_name: str, lines: list[str]) -> tuple[str, str]:
    """Title and message of a scenario blocked by zones, one line per zone."""
    listed = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    return (
        f"Inim Alarm - Scenario '{scenario_name}' Blocked",
        f"Cannot activate {scenario_name}. "
        f"The following zones are preventing activation:\n{listed}",
    )


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
        initial_panel_config or {}, scenario_idx_to_activate + 1
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

    if isinstance(error, PanelProgrammingError):
        event_reason = "panel_programming"
        notification_message = str(error)
        notification_id_suffix = f"activation_failed_{scenario_idx_to_activate}"
    elif isinstance(error, CommandFailedError) or panel_result not in {
        None,
        "refused",
        "open_zones",
    }:
        event_reason = "command_failed" if panel_result is None else panel_result
        notification_message = str(error)
        notification_id_suffix = f"activation_failed_{scenario_idx_to_activate}"
    elif isinstance(error, ZonesNotReadyError) and error.zones:
        # The zones the panel reported as not ready, as in the error message
        config = initial_panel_config or {}
        scenario_areas = scenario_armed_areas(config, scenario_idx_to_activate + 1)
        names = zone_names(config)
        lines = []
        for zone in error.zones:
            zone_areas = _scenario_zone_areas(config, zone, scenario_areas)
            lines.append(_blocking_zone_line(zone_label(zone, names), zone_areas))
            plain = names[zone - 1] if 0 < zone <= len(names) else ""
            blocking_zones_event_data.append(
                {
                    "zone_id": zone,
                    "zone_name": plain or f"Zone {zone}",
                    "status": "not_ready",
                    "areas": zone_areas,
                }
            )
        event_reason = "zones_blocked"
        notification_title, notification_message = _blocked_notification(
            base_scenario_name_for_msg, lines
        )
        notification_id_suffix = f"activation_blocked_{scenario_idx_to_activate}"
        _LOGGER.info("%s: %s", entity_name, notification_message)
    elif (
        not is_disarm_scenario
        and coordinator
        and coordinator.data
        and initial_panel_config
    ):
        all_scenario_names_list = scenario_names(initial_panel_config)

        target_scenario_name = f"Scenario {scenario_idx_to_activate + 1}"  # Default
        if 0 <= scenario_idx_to_activate < len(all_scenario_names_list):
            target_scenario_name = (
                all_scenario_names_list[scenario_idx_to_activate]
                or target_scenario_name
            )

        final_scenario_name = target_scenario_name

        # The areas the scenario arms, in any mode
        areas_controlled_by_scenario = scenario_armed_areas(
            initial_panel_config, scenario_idx_to_activate + 1
        )

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
            all_zone_names_list = zone_names(initial_panel_config)
            all_zones_config_detailed_list = initial_panel_config.get(
                KEY_INIT_ZONES_CONFIG, {}
            ).get(KEY_INIT_ZONES_CONFIG_DETAILED, [])
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
                            area_names_parts = _scenario_zone_areas(
                                initial_panel_config,
                                zone_id_1_based,
                                areas_controlled_by_scenario,
                            )
                            problematic_zones_messages.append(
                                _blocking_zone_line(zone_name, area_names_parts)
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
                notification_title, notification_message = _blocked_notification(
                    target_scenario_name, problematic_zones_messages
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
