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

from .attribute_values import zone_programming_attributes, zone_state_text
from .const import (
    CONF_AREAS,
    CONF_PANEL_NAME,
    CONF_SCENARIOS,
    CONF_ZONES,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    ZONE_STATE_ALARMED,
    ZONE_TAMPER_STATES,
)
from .entity import InimCoordinatorEntity, panel_device_info
from .protocol.config import (
    Balancing,
    SensorKind,
    Terminal,
    ZoneConfig,
)
from .readiness import panel_refuses_not_ready
from .selection import selected
from .stored_config import (
    area_label,
    scenario_label,
    terminal_records,
    zone_label,
    zone_records,
    zones_to_create,
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

    # Get user-defined panel name
    panel_display_name = entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)

    binary_sensors_to_add = []
    # The zone and terminal records, decoded from the stored configuration
    records = zone_records(initial_panel_config)
    terminals = terminal_records(initial_panel_config)

    zones = zones_to_create(
        initial_panel_config,
        selected(entry.options, initial_panel_config, CONF_ZONES),
    )
    _LOGGER.debug("Setting up binary sensors for %s zones", len(zones))
    for zone in zones:
        zone_index = zone.number - 1
        zone_name = zone_label(initial_panel_config, zone.number)
        binary_sensors_to_add.append(
            InimZoneBinarySensor(
                coordinator,
                entry,
                panel_display_name,
                zone_index,
                zone_name,
                zone.slot,
                *_zone_programming(records, terminals, zone_index),
            )
        )
        binary_sensors_to_add.extend(
            sensor_class(
                coordinator,
                entry,
                panel_display_name,
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
            sensor_class(coordinator, entry, panel_display_name, area, area_name)
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
                number - 1,
                scenario_label(initial_panel_config, number),
            )
        )

    binary_sensors_to_add.append(
        InimProgrammingModeBinarySensor(coordinator, entry, panel_display_name)
    )
    binary_sensors_to_add.append(
        InimRefusesNotReadyBinarySensor(
            coordinator,
            entry,
            panel_display_name,
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


class BaseInimBinarySensor(InimCoordinatorEntity, BinarySensorEntity):
    """Base class for Inim binary sensors linked to the main panel device."""

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        panel_display_name: str,
    ) -> None:
        """Initialize the base Inim binary sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._panel_display_name = panel_display_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the entity."""
        return panel_device_info(self.config_entry)

    def _zone_status_text(self) -> str:
        """This zone's state as text; "Unknown" when not known."""
        state = self.zone_state(self._zone_id_1_based)
        return "Unknown" if state is None else zone_state_text(state)


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
        zone_index_0_based: int,
        zone_name: str,
        slot: int | None,
        programming: dict[str, Any] | None = None,
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        """Initialize the Inim zone status binary sensor.

        slot: the first physical slot of the zone's terminal, if any;
        programming: the zone's programming attributes; device_class: the
        guess from its terminal (see zone_device_class).
        """
        super().__init__(coordinator, config_entry, panel_display_name)
        self._zone_index_0_based = zone_index_0_based
        self._zone_id_1_based = zone_index_0_based + 1
        self._zone_name = zone_name
        self._slot = slot
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
        status_text = self._zone_status_text()
        self._attr_is_on = status_text == ZONE_STATE_ALARMED
        # Update attributes here as well, as they might include dynamic status_text
        self._attr_extra_state_attributes = self._get_attributes(status_text)

    def _get_attributes(self, current_status_text: str) -> dict[str, Any]:
        attrs = {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "status_text": current_status_text,
            "test": bool(self.zone_in_test(self._zone_id_1_based)),
        }
        # The physical slot comes from the stored terminal map
        if self._slot is not None:
            attrs["internal_index"] = self._slot
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
        zone_index_0_based: int,
        zone_name: str,
    ) -> None:
        """Initialize the Inim zone triggered binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
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
        self._attr_is_on = bool(self.zone_alarm_memory(self._zone_id_1_based))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, with the terminal's tamper memory."""
        return {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "tamper_memory": bool(self.zone_tamper_memory(self._zone_id_1_based)),
        }


class InimZoneTamperBinarySensor(BaseInimBinarySensor):
    """A zone in tamper or short circuit now."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        zone_index_0_based: int,
        zone_name: str,
    ) -> None:
        """Initialize the zone tamper binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
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

    def _update_state_from_coordinator(self) -> None:
        self._attr_is_on = self._zone_status_text() in ZONE_TAMPER_STATES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "zone_id": self._zone_id_1_based,
            "zone_name": self._zone_name,
            "status_text": self._zone_status_text(),
        }


class InimAreaBinarySensor(BaseInimBinarySensor):
    """On while an area is in one of the area lists of the live status."""

    _status_key: str  # the areas field of the live status (alarm, tamper, ...)
    _label: str  # shown in the name
    _unique_id_suffix: str
    _object_id_suffix: str

    def __init__(
        self,
        coordinator: Any,
        config_entry: ConfigEntry,
        panel_display_name: str,
        area_id_1_based: int,
        area_name: str,
    ) -> None:
        """Initialize the area binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
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

    def _areas(self, key: str) -> frozenset[int]:
        status = self.status
        return frozenset() if status is None else getattr(status.areas, key)

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
    _status_key = "alarm_memory"
    _label = "Alarm memory"
    _unique_id_suffix = "triggered_bs"
    _object_id_suffix = "triggered"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, with the area's tamper memory."""
        return {
            **super().extra_state_attributes,
            "tamper_memory": self._area_id_1_based in self._areas("tamper_memory"),
        }


class InimAreaAlarmBinarySensor(InimAreaBinarySensor):
    """An area in alarm now."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _status_key = "alarm"
    _label = "Alarm"
    _unique_id_suffix = "alarm_bs"
    _object_id_suffix = "alarm"


class InimAreaTamperBinarySensor(InimAreaBinarySensor):
    """An area in tamper now."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _status_key = "tamper"
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
        scenario_index_0_based: int,
        scenario_name: str,
    ) -> None:
        """Initialize the Inim specific scenario active binary sensor."""
        super().__init__(coordinator, config_entry, panel_display_name)
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
        status = self.status
        self._attr_is_on = (
            status is not None
            and status.active_scenario == self._scenario_index_0_based
        )

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
    ) -> None:
        """Initialize the programming mode sensor of the panel."""
        super().__init__(coordinator, config_entry, panel_display_name)
        self._attr_unique_id = f"{config_entry.entry_id}_programming_mode"

    def _value(self) -> bool | None:
        status = self.status
        if status is None or status.panel_state is None:
            return None
        return status.panel_state.programming

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
        refuses: bool | None,
    ) -> None:
        """Initialize from the stored configuration."""
        super().__init__(coordinator, config_entry, panel_display_name)
        self._attr_unique_id = f"{config_entry.entry_id}_refuses_arming_not_ready"
        self._refuses = refuses

    @property
    def is_on(self) -> bool | None:
        """Whether the panel refuses arming with zones not ready."""
        return self._refuses
