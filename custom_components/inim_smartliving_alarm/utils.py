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
    zone_text,
)
from .legacy import LegacyPanelClient, zone_exists
from .notifications import ENGLISH, Texts, async_texts
from .readiness import async_arm
from .selection import (
    area_names,
    scenario_armed_areas,
    scenario_label,
    scenario_names,
    zone_label,
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
    action_key: str | None = None,
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
            action_key=action_key,
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


def _quoted(text: str) -> str:
    """A name in quotes, as the notifications show it.

    The quotes are part of the value: a translation may not put them around
    a placeholder.
    """
    return f"'{text}'"


def _blocking_zone_line(texts: Texts, zone_name: str, zone_areas: list[str]) -> str:
    areas = (
        ", ".join(zone_areas) if zone_areas else texts("notification_relevant_areas")
    )
    return texts(
        "notification_blocking_zone_line", zone=_quoted(zone_name), areas=areas
    )


def _blocked_texts(
    texts: Texts, scenario_name: str, zones: list[tuple[str, list[str]]]
) -> tuple[str, str]:
    """Title and message of a scenario blocked by zones, one line per zone."""
    listed = "\n".join(
        f"{i + 1}. {_blocking_zone_line(texts, name, areas)}"
        for i, (name, areas) in enumerate(zones)
    )
    return (
        texts("notification_scenario_blocked_title", scenario=_quoted(scenario_name)),
        texts(
            "notification_scenario_blocked_message",
            scenario=scenario_name,
            zones=listed,
        ),
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
    action_key: str | None = None,
) -> None:
    """Handles the failure of a scenario activation, providing detailed notifications and emitting an event.

    error is what stopped the activation. When the panel refused the command
    for a reason other than zones (wrong PIN, PIN not enabled, ...) or the
    command could not be sent, the notification gives that reason instead
    of looking for zones in the way. The notification is in Home Assistant's
    language; the event's message stays in English. action_key names
    action_name's text in the translations (e.g. arm_away).
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

    def action(texts: Texts) -> str:
        if action_key is None:
            return action_name
        return texts.action(
            action_key, action_name, scenario=_quoted(base_scenario_name_for_msg)
        )

    def failed_title(texts: Texts, scenario: str = base_scenario_name_for_msg) -> str:
        return texts("notification_scenario_failed_title", scenario=_quoted(scenario))

    # Title and message in a language (Texts): set below for each case
    def compose(texts: Texts) -> tuple[str, str]:
        return (
            failed_title(texts),
            texts(
                "notification_scenario_failed_message",
                action=action(texts),
                entity=_quoted(entity_name),
            ),
        )

    notification_id_suffix = f"action_failed_generic_{scenario_idx_to_activate}"

    # Set up default variables for the HA Event bus payload
    event_reason = "unknown"
    final_scenario_name = base_scenario_name_for_msg
    blocking_zones_event_data = []
    panel_result = error.reason if isinstance(error, CommandRefusedError) else None

    if isinstance(error, PanelProgrammingError):
        event_reason = "panel_programming"

        def compose(texts: Texts) -> tuple[str, str]:
            return failed_title(texts), texts.error(error)

        notification_id_suffix = f"activation_failed_{scenario_idx_to_activate}"
    elif isinstance(error, CommandFailedError) or panel_result not in {
        None,
        "refused",
        "open_zones",
    }:
        event_reason = "command_failed" if panel_result is None else panel_result
        failure = error

        def compose(texts: Texts) -> tuple[str, str]:
            return failed_title(texts), texts.error(failure)

        notification_id_suffix = f"activation_failed_{scenario_idx_to_activate}"
    elif isinstance(error, ZonesNotReadyError) and error.zones:
        # The zones the panel reported as not ready, as in the error message
        config = initial_panel_config or {}
        scenario_areas = scenario_armed_areas(config, scenario_idx_to_activate + 1)
        names = zone_names(config)
        blocking: list[tuple[str, list[str]]] = []
        for zone in error.zones:
            zone_areas = _scenario_zone_areas(config, zone, scenario_areas)
            blocking.append((zone_text(zone, names), zone_areas))
            blocking_zones_event_data.append(
                {
                    "zone_id": zone,
                    "zone_name": zone_label(config, zone),
                    "status": "not_ready",
                    "areas": zone_areas,
                }
            )
        event_reason = "zones_blocked"

        def compose(texts: Texts) -> tuple[str, str]:
            return _blocked_texts(texts, base_scenario_name_for_msg, blocking)

        notification_id_suffix = f"activation_blocked_{scenario_idx_to_activate}"
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

            def compose(texts: Texts) -> tuple[str, str]:
                return (
                    failed_title(texts, target_scenario_name),
                    texts(
                        "notification_scenario_failed_no_areas",
                        scenario=target_scenario_name,
                        entity=_quoted(entity_name),
                    ),
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

            problematic_zones: list[tuple[str, list[str]]] = []
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
                            problematic_zones.append((zone_name, area_names_parts))
                            # Add structured data for the event bus payload
                            blocking_zones_event_data.append(
                                {
                                    "zone_id": zone_id_1_based,
                                    "zone_name": zone_name,
                                    "status": zone_live_status,
                                    "areas": area_names_parts,
                                }
                            )

            if problematic_zones:
                event_reason = "zones_blocked"

                def compose(texts: Texts) -> tuple[str, str]:
                    return _blocked_texts(
                        texts, target_scenario_name, problematic_zones
                    )

                notification_id_suffix = (
                    f"activation_blocked_{scenario_idx_to_activate}"
                )
            else:
                event_reason = "unknown_zones_error"

                def compose(texts: Texts) -> tuple[str, str]:
                    return (
                        failed_title(texts, target_scenario_name),
                        texts(
                            "notification_scenario_failed_unknown_zones",
                            scenario=target_scenario_name,
                            entity=_quoted(entity_name),
                        ),
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

        def compose(texts: Texts) -> tuple[str, str]:
            return (
                failed_title(texts),
                texts(
                    "notification_scenario_failed_no_data",
                    action=action(texts),
                    entity=_quoted(entity_name),
                ),
            )

        notification_id_suffix = f"activation_failed_no_data_{scenario_idx_to_activate}"
        _LOGGER.warning(
            "%s: Data unavailable to determine cause of %s failure",
            entity_name,
            action_name,
        )
    else:  # Disarm scenario failed or other generic failure
        event_reason = "disarm_failed"

        def compose(texts: Texts) -> tuple[str, str]:
            return (
                failed_title(texts),
                texts(
                    "notification_scenario_failed_disarm",
                    action=action(texts),
                    scenario=base_scenario_name_for_msg,
                    entity=_quoted(entity_name),
                ),
            )

    # The notification in Home Assistant's language, the event in English
    notification_title, notification_message = compose(await async_texts(hass))
    _english_title, english_message = compose(await async_texts(hass, ENGLISH))
    if event_reason == "zones_blocked":
        _LOGGER.info("%s: %s", entity_name, english_message)

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
        "message": english_message,
    }
    hass.bus.async_fire(f"{DOMAIN}_scenario_activation_failed", event_data)

    if coordinator:
        await coordinator.async_request_refresh()
