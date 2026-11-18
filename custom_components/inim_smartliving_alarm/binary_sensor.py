"""Platform for binary sensor entities for the Inim Alarm integration."""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .attribute_values import duration_text, time_text, value_key
from .const import (
    CONF_AREAS,
    CONF_PANEL_NAME,
    CONF_SCENARIOS,
    CONF_ZONES,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONES,
    KEY_INIT_ZONES_CONFIG,
    KEY_INIT_ZONES_CONFIG_DETAILED,
    KEY_LIVE_ALARM_AREAS_LIST,
    KEY_LIVE_AREAS_STATUS,
    KEY_LIVE_PANEL_PROGRAMMING,
    KEY_LIVE_TAMPER_AREAS_LIST,
    KEY_LIVE_TAMPER_MEMORY_AREAS_LIST,
    KEY_LIVE_TRIGGERED_AREAS_LIST,
    KEY_LIVE_ZONE_STATUSES_MAP,
    KEY_LIVE_ZONE_TAMPER_MEMORY_STATUSES_MAP,
    KEY_LIVE_ZONE_TEST_STATUSES_MAP,
    KEY_LIVE_ZONE_TRIGGERED_STATUSES_MAP,
    KEY_LIVE_ZONES_EXCLUDED_STATUS,
    KEY_LIVE_ZONES_STATUS,
    KEY_LIVE_ZONES_TRIGGERED_STATUS,
    ZONE_TAMPER_STATES,
)
from .legacy import zones_to_create
from .protocol.config import (
    Balancing,
    SensorKind,
    Terminal,
    ZoneConfig,
)
from .readiness import panel_refuses_not_ready
from .selection import (
    area_label,
    scenario_label,
    selected,
    terminal_records,
    zone_records,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inim Alarm binary sensor entities from a config entry."""
    _LOGGER.debug(
        "Setting up Inim Alarm 'binary_sensor' platform entities for entry: %s",
        entry.title,
    )

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    initial_panel_config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})

    # Get Panel model/version
    system_info = initial_panel_config.get("system_info", {})

    # Get user-defined panel name
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    zone_data = initial_panel_config.get(KEY_INIT_ZONES, {})
    all_zone_names = zone_data.get(KEY_INIT_ZONE_NAMES, []) if zone_data else []

    zone_configurations_data = initial_panel_config.get(KEY_INIT_ZONES_CONFIG, {})
    detailed_zone_configs = (
        zone_configurations_data.get(KEY_INIT_ZONES_CONFIG_DETAILED, [])
        if zone_configurations_data
        else []
    )

    binary_sensors_to_add = []
    # The zone and terminal records, decoded from the stored configuration
    records = zone_records(initial_panel_config)
    terminals = terminal_records(initial_panel_config)

    zones = zones_to_create(
        all_zone_names,
        detailed_zone_configs,
        selected(entry.options, initial_panel_config, CONF_ZONES),
    )
    _LOGGER.debug("Setting up binary sensors for %s zones", len(zones))
    for zone_index, zone_name, zone_config_detail in zones:
        binary_sensors_to_add.append(
            InimZoneBinarySensor(
                coordinator,
                entry,
                panel_display_name,
                system_info,
                zone_index,
                zone_name,
                zone_config_detail,
                *_zone_programming(records, terminals, zone_index),
            )
        )
        binary_sensors_to_add.extend(
            sensor_class(
                coordinator,
                entry,
                panel_display_name,
                system_info,
                zone_index,
                zone_name,
            )
            for sensor_class in (
                InimZoneTriggeredBinarySensor,
                InimZoneTamperBinarySensor,
            )
        )

    areas = selected(entry.options, initial_panel_config, CONF_AREAS)
    _LOGGER.debug(
        "Setting up alarm, tamper and alarm memory binary sensors for areas %s",
        areas,
    )
    for area in areas:
        area_name = area_label(initial_panel_config, area)
        binary_sensors_to_add.extend(
            sensor_class(
                coordinator, entry, panel_display_name, system_info, area, area_name
            )
            for sensor_class in (
                InimAreaAlarmMemoryBinarySensor,
                InimAreaAlarmBinarySensor,
                InimAreaTamperBinarySensor,
            )
        )

    scenarios = selected(entry.options, initial_panel_config, CONF_SCENARIOS)
    _LOGGER.debug("Setting up scenario active binary sensors for %s", scenarios)
    for number in scenarios:
        binary_sensors_to_add.append(
            InimSpecificScenarioActiveBinarySensor(
                coordinator,
                entry,
                panel_display_name,
                system_info,
                number - 1,
                scenario_label(initial_panel_config, number),
            )
        )

    binary_sensors_to_add.append(
        InimProgrammingModeBinarySensor(
            coordinator, entry, panel_display_name, system_info
        )
    )
    binary_sensors_to_add.append(
        InimRefusesNotReadyBinarySensor(
            coordinator,
            entry,
            panel_display_name,
            system_info,
            panel_refuses_not_ready(initial_panel_config),
        )
    )

    if binary_sensors_to_add:
        async_add_entities(binary_sensors_to_add)
        _LOGGER.info(
            "Added %s Inim Alarm 'binary_sensor' entities for %s",
            len(binary_sensors_to_add),
            entry.title,
        )
    else:
        _LOGGER.info("No Inim Alarm 'binary_sensor' entities added for %s", entry.title)


class BaseInimBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for Inim binary sensors linked to the main panel device."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
    ) -> None:
        """Initialize the base Inim binary sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name
        self._system_info = system_info

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._panel_display_name,
            manufacturer="Inim Electronics",
            model=self._system_info.get("type", "SmartLiving"),
            sw_version=self._system_info.get("version", "Unknown"),
        )

    def _zone_value(self, group: str, key: str, default: Any = None) -> Any:
        """This zone's value in a map of the live status (zone sensors only)."""
        data = self.coordinator.data or {}
        values = (data.get(group) or {}).get(key) or {}
        return values.get(self._zone_id_1_based, default)

    def _zone_flag(self, group: str, key: str) -> bool:
        return bool(self._zone_value(group, key, False))


# Device class from the terminal's programming (a wired zone has no detector
# type): the sensor kind first, then the balancing
_KIND_CLASSES = {
    SensorKind.SHOCK: BinarySensorDeviceClass.VIBRATION,
    SensorKind.ROLLER_BLIND: BinarySensorDeviceClass.OPENING,
}
_BALANCING_CLASSES = {
    Balancing.NORMALLY_OPEN: BinarySensorDeviceClass.OPENING,
    Balancing.NORMALLY_CLOSED: BinarySensorDeviceClass.OPENING,
    Balancing.SINGLE_EOL: BinarySensorDeviceClass.OPENING,
    Balancing.DOUBLE_EOL: BinarySensorDeviceClass.MOTION,
}


def zone_programming_attributes(
    zone: ZoneConfig, terminal: Terminal, terminal_index: int, second_zone: bool
) -> dict[str, Any]:
    """A zone's programming as attributes: stable English keys and values.

    Byte 6 of the record is shown per sensor kind: the alarm pulse duration of a
    generic sensor, the sensitivity of a shock sensor, nothing for a roller
    blind.
    """
    kind = terminal.sensor_kind
    attributes: dict[str, Any] = {
        "assigned_areas": sorted(zone.areas),
        "zone_type": value_key(zone.zone_type),
        "alarm_cycles": "unlimited" if zone.alarm_cycles is None else zone.alarm_cycles,
    }
    if kind is SensorKind.GENERIC:
        attributes["alarm_pulse_duration"] = duration_text(zone.alarm_pulse_duration)
    elif kind is SensorKind.SHOCK:
        attributes["shock_sensitivity"] = zone.shock_sensitivity
    attributes |= {
        "pulse_window_time": time_text(zone.pulse_window_time),
        "pulse_count": zone.pulse_count,
        "balancing": value_key(terminal.balancing),
        "sensor_kind": kind.value,
        "terminal_index": terminal_index,
        "terminal_type": value_key(terminal.terminal_type),
        "second_zone": second_zone,
        **zone.flags,
    }
    return attributes


def zone_device_class(terminal: Terminal) -> BinarySensorDeviceClass | None:
    """A guess from the programming: shock, roller blind, then balancing."""
    return _KIND_CLASSES.get(terminal.sensor_kind) or _BALANCING_CLASSES.get(
        terminal.balancing
    )


def _zone_programming(
    records: list[ZoneConfig] | None,
    terminals: list[Terminal] | None,
    zone_index: int,
) -> tuple[dict[str, Any], BinarySensorDeviceClass | None]:
    """Attributes and device class of a zone (0-based); empty if unknown."""
    if not records or not terminals or zone_index >= len(records):
        return {}, None
    terminal_index = zone_index % len(terminals)
    terminal = terminals[terminal_index]
    attributes = zone_programming_attributes(
        records[zone_index],
        terminal,
        terminal_index,
        second_zone=zone_index >= len(terminals),
    )
    return attributes, zone_device_class(terminal)


class InimZoneBinarySensor(BaseInimBinarySensor):
    """Binary sensor for the status of an individual Inim Zone."""

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        zone_index_0_based: int,
        zone_name: str,
        zone_config_detail: dict[str, Any] | None,
        programming: dict[str, Any] | None = None,
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        """Initialize the Inim zone status binary sensor.

        programming: the zone's programming attributes; device_class: the
        guess from its terminal (see zone_device_class).
        """
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._zone_index_0_based = zone_index_0_based
        self._zone_id_1_based = zone_index_0_based + 1
        self._zone_name = zone_name
        self._zone_config_detail = zone_config_detail if zone_config_detail else {}
        self._programming = programming or {}
        self._attr_name = f"Zone {self._zone_id_1_based} ({self._zone_name})"
        self._attr_unique_id = f"{config_entry.entry_id}_zone_{self._zone_id_1_based}"

        self._attr_device_class = device_class

        self._attr_extra_state_attributes = {}  # Initialize attribute dict
        self._update_state_from_coordinator()  # Initial state and attributes

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_zone_{self._zone_id_1_based}"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        status_text = "Unknown"
        if self.coordinator.data and self.coordinator.data.get("zones_status"):
            zones_data = self.coordinator.data["zones_status"]
            if zones_data.get("zone_statuses"):
                status_text = zones_data["zone_statuses"].get(
                    self._zone_id_1_based, "Unknown"
                )
                if status_text.lower() == "alarmed":
                    is_on_state = True
        self._attr_is_on = is_on_state
        # Update attributes here as well, as they might include dynamic status_text
        self._attr_extra_state_attributes = self._get_attributes(status_text)

    def _get_attributes(self, current_status_text: str) -> dict[str, Any]:
        attrs = {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "status_text": current_status_text,
            "test": self._zone_flag(
                KEY_LIVE_ZONES_EXCLUDED_STATUS, KEY_LIVE_ZONE_TEST_STATUSES_MAP
            ),
        }
        # The physical slot comes from the stored terminal map
        if "internal_index" in self._zone_config_detail:
            attrs["internal_index"] = self._zone_config_detail["internal_index"]
        # The zone's programming, decoded from the stored records
        attrs.update(self._programming)
        return attrs

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return self._attr_extra_state_attributes


class InimZoneTriggeredBinarySensor(BaseInimBinarySensor):
    """Alarm memory of a zone: on after an alarm until the memory is reset.

    Was the "triggered" sensor; its unique and object IDs are kept.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        zone_index_0_based: int,
        zone_name: str,
    ) -> None:
        """Initialize the Inim zone triggered binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._zone_id_1_based = zone_index_0_based + 1
        self._zone_name = zone_name
        self._attr_name = (
            f"Zone {self._zone_id_1_based} Alarm memory ({self._zone_name})"
        )
        self._attr_unique_id = (
            f"{config_entry.entry_id}_zone_triggered_{self._zone_id_1_based}"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_zone_{self._zone_id_1_based}_triggered"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        if self.coordinator.data and self.coordinator.data.get(
            KEY_LIVE_ZONES_TRIGGERED_STATUS
        ):
            triggered_data = self.coordinator.data[KEY_LIVE_ZONES_TRIGGERED_STATUS]
            if triggered_data.get(KEY_LIVE_ZONE_TRIGGERED_STATUSES_MAP):
                triggered_statuses = triggered_data[
                    KEY_LIVE_ZONE_TRIGGERED_STATUSES_MAP
                ]
                status_text = triggered_statuses.get(self._zone_id_1_based, "clear")
                if status_text.lower() == "triggered":
                    is_on_state = True
        self._attr_is_on = is_on_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, with the terminal's tamper memory."""
        return {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "tamper_memory": self._zone_flag(
                KEY_LIVE_ZONES_TRIGGERED_STATUS,
                KEY_LIVE_ZONE_TAMPER_MEMORY_STATUSES_MAP,
            ),
        }


class InimZoneTamperBinarySensor(BaseInimBinarySensor):
    """A zone in tamper or short circuit now."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        zone_index_0_based: int,
        zone_name: str,
    ) -> None:
        """Initialize the zone tamper binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._zone_id_1_based = zone_index_0_based + 1
        self._zone_name = zone_name
        self._attr_name = f"Zone {self._zone_id_1_based} Tamper ({self._zone_name})"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_zone_tamper_{self._zone_id_1_based}"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return f"{self._panel_display_name}_zone_{self._zone_id_1_based}_tamper"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _status(self) -> str:
        return self._zone_value(
            KEY_LIVE_ZONES_STATUS, KEY_LIVE_ZONE_STATUSES_MAP, "Unknown"
        )

    def _update_state_from_coordinator(self) -> None:
        self._attr_is_on = self._status() in ZONE_TAMPER_STATES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "status_text": self._status(),
        }


class InimAreaBinarySensor(BaseInimBinarySensor):
    """On while an area is in one of the area lists of the live status."""

    _status_key: str  # list of areas in the coordinator's areas_status
    _label: str  # shown in the name
    _unique_id_suffix: str
    _object_id_suffix: str

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        area_id_1_based: int,
        area_name: str,
    ) -> None:
        """Initialize the area binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._area_id_1_based = area_id_1_based
        self._area_name = area_name
        self._attr_name = (
            f"Area {self._area_id_1_based} {self._label} ({self._area_name})"
        )
        self._attr_unique_id = (
            f"{config_entry.entry_id}_area_{self._area_id_1_based}"
            f"_{self._unique_id_suffix}"
        )
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return (
            f"{self._panel_display_name}_area_{self._area_id_1_based}"
            f"_{self._object_id_suffix}"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _areas(self, key: str) -> list[int]:
        if self.coordinator.data and self.coordinator.data.get(KEY_LIVE_AREAS_STATUS):
            return self.coordinator.data[KEY_LIVE_AREAS_STATUS].get(key) or []
        return []

    def _update_state_from_coordinator(self) -> None:
        self._attr_is_on = self._area_id_1_based in self._areas(self._status_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {"area_id": self._area_id_1_based, "area_name": self._area_name}


class InimAreaAlarmMemoryBinarySensor(InimAreaBinarySensor):
    """Alarm memory of an area: on after an alarm until the memory is reset.

    Was the "triggered" sensor; its unique and object IDs are kept.
    """

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _status_key = KEY_LIVE_TRIGGERED_AREAS_LIST
    _label = "Alarm memory"
    _unique_id_suffix = "triggered_bs"
    _object_id_suffix = "triggered"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, with the area's tamper memory."""
        return {
            **super().extra_state_attributes,
            "tamper_memory": self._area_id_1_based
            in self._areas(KEY_LIVE_TAMPER_MEMORY_AREAS_LIST),
        }


class InimAreaAlarmBinarySensor(InimAreaBinarySensor):
    """An area in alarm now."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _status_key = KEY_LIVE_ALARM_AREAS_LIST
    _label = "Alarm"
    _unique_id_suffix = "alarm_bs"
    _object_id_suffix = "alarm"


class InimAreaTamperBinarySensor(InimAreaBinarySensor):
    """An area in tamper now."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _status_key = KEY_LIVE_TAMPER_AREAS_LIST
    _label = "Tamper"
    _unique_id_suffix = "tamper_bs"
    _object_id_suffix = "tamper"


class InimSpecificScenarioActiveBinarySensor(BaseInimBinarySensor):
    """Binary sensor indicating if a specific Inim Scenario is currently active."""

    _attr_device_class = None  # No specific device class, will appear as generic on/off

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        scenario_index_0_based: int,
        scenario_name: str,
    ) -> None:
        """Initialize the Inim specific scenario active binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._scenario_index_0_based = scenario_index_0_based
        self._scenario_name = scenario_name
        self._attr_name = f"Scenario '{self._scenario_name}' Active"
        self._attr_unique_id = (
            f"{config_entry.entry_id}_scenario_{self._scenario_index_0_based}_active_bs"
        )
        self._attr_icon = "mdi:play-circle-outline"
        self._update_state_from_coordinator()

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested object ID for the entity."""
        return (
            f"{self._panel_display_name}_scenario_{self._scenario_index_0_based}_active"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state_from_coordinator()
        self.async_write_ha_state()

    def _update_state_from_coordinator(self) -> None:
        is_on_state = False
        if self.coordinator.data and self.coordinator.data.get("active_scenario"):
            active_idx = self.coordinator.data["active_scenario"].get(
                "active_scenario_number"
            )
            if active_idx == self._scenario_index_0_based:
                is_on_state = True
        self._attr_is_on = is_on_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "scenario_index": self._scenario_index_0_based,
            "scenario_name": self._scenario_name,
        }


class InimProgrammingModeBinarySensor(BaseInimBinarySensor):
    """On while the panel is in programming mode; it ignores commands then.

    Unavailable when the panel's state flags are not read (a panel handled
    with another model's layout).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "programming_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:wrench-clock"

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
    ) -> None:
        """Initialize the programming mode sensor of the panel."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._attr_unique_id = f"{config_entry.entry_id}_programming_mode"

    def _value(self) -> bool | None:
        return (self.coordinator.data or {}).get(KEY_LIVE_PANEL_PROGRAMMING)

    @property
    def available(self) -> bool:
        """Available while the state flags are read."""
        return super().available and self._value() is not None

    @property
    def is_on(self) -> bool | None:
        """Whether the panel is in programming mode."""
        return self._value()


class InimRefusesNotReadyBinarySensor(BaseInimBinarySensor):
    """On when the panel itself refuses arming with zones not ready.

    An installer setting read with the panel configuration; when it is on,
    each zone's programming decides whether it stops arming. Unknown until
    the configuration is read again by this version.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refuses_arming_not_ready"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-alert-outline"

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: dict[str, Any],
        refuses: bool | None,
    ) -> None:
        """Initialize from the stored configuration."""
        super().__init__(coordinator, config_entry, panel_display_name, system_info)
        self._attr_unique_id = f"{config_entry.entry_id}_refuses_arming_not_ready"
        self._refuses = refuses

    @property
    def is_on(self) -> bool | None:
        """Whether the panel refuses arming with zones not ready."""
        return self._refuses
