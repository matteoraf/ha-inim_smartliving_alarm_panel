"""The integration's actions: their schemas, handlers and descriptions."""

import copy
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.alarm_control_panel import (
    DOMAIN as ALARM_CONTROL_PANEL_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import (
    async_extract_entity_ids,
    async_set_service_schema,
)
from homeassistant.util.yaml import load_yaml_dict
import voluptuous as vol

from .api import SmartLivingPanel
from .attribute_values import ARM_MODE_TEXT
from .const import (
    ATTR_AREAS,
    ATTR_FORCE_ARM,
    ATTR_MODE,
    ATTR_SCENARIO,
    CONF_AREAS,
    CONF_SCENARIOS,
    DATA_ALARM_PANEL,
    DATA_COORDINATOR,
    DATA_PANEL,
    DOMAIN,
    SERVICE_ACTIVATE_SCENARIO,
    SERVICE_ALARM_RESET,
    SERVICE_ARM_AREA,
    SERVICE_REREAD_PANEL_CONFIGURATION,
)
from .errors import InimCommandError
from .panel_config import async_reread_panel_configuration
from .protocol.commands import ArmMode
from .readiness import async_arm
from .selection import is_disarm_scenario, panel_config, selected
from .stored_config import area_label, scenario_label
from .utils import async_activate_scenario

_LOGGER = logging.getLogger(__name__)

# Where the action descriptions from services.yaml are kept (hass.data)
ACTION_DESCRIPTIONS = f"{DOMAIN}_action_descriptions"

REREAD_PANEL_CONFIGURATION_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
)


# The arm_area action's modes: "total", "partial", "instant", "disarm"
ARM_AREA_MODES = {
    text: mode for mode, text in ARM_MODE_TEXT.items() if mode != ArmMode.KEEP
}

ARM_AREA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_AREAS): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(min=1))],
            vol.Length(min=1),
        ),
        vol.Required(ATTR_MODE): vol.In(list(ARM_AREA_MODES)),
        vol.Optional(ATTR_FORCE_ARM, default=False): cv.boolean,
    }
)

ACTIVATE_SCENARIO_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SCENARIO): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_FORCE_ARM, default=False): cv.boolean,
    }
)


def _loaded_entry(
    hass: HomeAssistant, entry_id: str, not_loaded_key: str
) -> ConfigEntry:
    """The integration's entry with this ID; it must be loaded."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=not_loaded_key,
            translation_placeholders={"title": entry.title},
        )
    return entry


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def async_reread_service(call: ServiceCall) -> None:
        """Read the panel configuration of one entry again and reload it."""
        entry = _loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded")
        await async_reread_panel_configuration(hass, entry)

    async def async_arm_area_service(call: ServiceCall) -> None:
        """Arm or disarm one or more areas of one panel in a single command."""
        entry = _loaded_entry(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded_command"
        )
        panel: SmartLivingPanel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
        areas = sorted(set(call.data[ATTR_AREAS]))
        config = panel_config(entry.data)
        imported = selected(entry.options, config, CONF_AREAS)
        if missing := [area for area in areas if area not in imported]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="area_not_imported",
                translation_placeholders={
                    "areas": ", ".join(str(area) for area in missing),
                    "title": entry.title,
                },
            )
        target = ", ".join(area_label(config, area) for area in areas)
        mode = ARM_AREA_MODES[call.data[ATTR_MODE]]
        _LOGGER.info("arm_area: %s -> %s", target, call.data[ATTR_MODE])
        # Arming checks the zones first; disarming is never checked
        await async_arm(
            hass,
            entry,
            lambda check: panel.arm_areas(
                dict.fromkeys(areas, mode), on_not_ready=check
            ),
            target,
            force=call.data[ATTR_FORCE_ARM],
        )
        coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
        await coordinator.async_request_refresh()

    async def async_activate_scenario_service(call: ServiceCall) -> None:
        """Activate one imported scenario of one panel."""
        entry = _loaded_entry(
            hass, call.data[ATTR_CONFIG_ENTRY_ID], "entry_not_loaded_command"
        )
        number = call.data[ATTR_SCENARIO]
        config = panel_config(entry.data)
        if number not in selected(entry.options, config, CONF_SCENARIOS):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scenario_not_imported",
                translation_placeholders={
                    "scenario": str(number),
                    "title": entry.title,
                },
            )
        label = scenario_label(config, number)
        data = hass.data[DOMAIN][entry.entry_id]
        await async_activate_scenario(
            hass,
            entry,
            data[DATA_COORDINATOR],
            data[DATA_PANEL],
            config,
            f"{entry.entry_id}_activate_scenario_action",
            entry.title,
            number - 1,
            f"activate scenario '{label}'",
            is_disarm_scenario=is_disarm_scenario(config, number),
            action_key="activate_scenario",
            force=call.data[ATTR_FORCE_ARM],
        )

    async def async_alarm_reset_service(call: ServiceCall) -> None:
        """Reset the alarm and tamper memories of the targeted panels' areas."""
        registry = er.async_get(hass)
        failures: list[InimCommandError] = []
        for entity_id in sorted(await async_extract_entity_ids(call)):
            registered = registry.async_get(entity_id)
            if (
                registered is None
                or registered.platform != DOMAIN
                or registered.domain != ALARM_CONTROL_PANEL_DOMAIN
                or registered.config_entry_id is None
            ):
                continue
            entry = _loaded_entry(
                hass, registered.config_entry_id, "entry_not_loaded_command"
            )
            alarm_panel = hass.data[DOMAIN][entry.entry_id].get(DATA_ALARM_PANEL)
            if alarm_panel is None:
                continue
            try:
                await alarm_panel.async_reset_memories()
            except InimCommandError as err:
                failures.append(err)
        if failures:
            raise failures[0]

    hass.services.async_register(DOMAIN, SERVICE_ALARM_RESET, async_alarm_reset_service)
    hass.services.async_register(
        DOMAIN,
        SERVICE_REREAD_PANEL_CONFIGURATION,
        async_reread_service,
        schema=REREAD_PANEL_CONFIGURATION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ARM_AREA, async_arm_area_service, schema=ARM_AREA_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACTIVATE_SCENARIO,
        async_activate_scenario_service,
        schema=ACTIVATE_SCENARIO_SCHEMA,
    )


async def async_update_action_descriptions(hass: HomeAssistant) -> None:
    """List the imported areas and scenarios of the loaded panels in the actions.

    The arm_area form offers only the imported areas and the
    activate_scenario form only the imported scenarios, by their panel
    labels. The value of each choice is the number; with several panels, a
    number lists the item of each panel that imports it, with the panel's
    name. services.yaml holds the rest of the descriptions.
    """
    base = hass.data.get(ACTION_DESCRIPTIONS)
    if base is None:
        services = await hass.async_add_executor_job(
            load_yaml_dict, str(Path(__file__).parent / "services.yaml")
        )
        base = hass.data[ACTION_DESCRIPTIONS] = {
            SERVICE_ARM_AREA: services[SERVICE_ARM_AREA],
            SERVICE_ACTIVATE_SCENARIO: services[SERVICE_ACTIVATE_SCENARIO],
        }
    entries = [
        entry
        for entry_id in hass.data.get(DOMAIN, {})
        if (entry := hass.config_entries.async_get_entry(entry_id)) is not None
    ]
    for service, field, kind, label_of in (
        (SERVICE_ARM_AREA, ATTR_AREAS, CONF_AREAS, area_label),
        (SERVICE_ACTIVATE_SCENARIO, ATTR_SCENARIO, CONF_SCENARIOS, scenario_label),
    ):
        labels: dict[int, list[str]] = {}
        for entry in entries:
            config = panel_config(entry.data)
            for number in selected(entry.options, config, kind):
                label = label_of(config, number)
                if len(entries) > 1:
                    label = f"{label} ({entry.title})"
                labels.setdefault(number, []).append(label)
        description: dict[str, Any] = copy.deepcopy(base[service])
        description["fields"][field]["selector"]["select"]["options"] = [
            {"value": str(number), "label": ", ".join(names)}
            for number, names in sorted(labels.items())
        ]
        async_set_service_schema(hass, DOMAIN, service, description)


@callback
def async_forget_action_descriptions(hass: HomeAssistant) -> None:
    """Drop the kept descriptions once no panel is loaded."""
    if DOMAIN not in hass.data:
        hass.data.pop(ACTION_DESCRIPTIONS, None)
