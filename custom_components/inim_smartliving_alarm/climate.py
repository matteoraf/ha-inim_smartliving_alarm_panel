"""The keypads' thermostats as climate entities.

The panel runs each keypad's thermostat. Its season sets the HVAC mode
(winter heats, summer cools) and its regulation mode is the preset: manual,
the daily program, the weekly program, or antifreeze (5.0 degrees, always
heating). The panel's mode OFF is the HVAC mode off.

Changes write the season, the manual setpoint and the regulation mode only;
the programs are never changed. The panel acts on a change at its next
check of that keypad, so whether it heats or cools follows a little later.
"""

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from .api import SmartLivingPanel, ThermostatState
from .const import DATA_COORDINATOR, DATA_INITIAL_PANEL_CONFIG, DATA_PANEL, DOMAIN
from .coordinator import InimDataUpdateCoordinator
from .entity import InimCoordinatorEntity, name_item, panel_device_info
from .errors import async_write_thermostat
from .protocol.thermostat import (
    ANTIFREEZE_SETPOINT,
    SETPOINT_MAX,
    SETPOINT_MIN,
    Season,
    ThermostatMode,
)
from .stored_config import keypad_names
from .thermostats import selected_keypads, thermostats_signal

_LOGGER = logging.getLogger(__name__)

TARGET_STEP = 0.5  # degrees; the panel itself keeps tenths

PRESETS: dict[ThermostatMode, str] = {
    ThermostatMode.MANUAL: "manual",
    ThermostatMode.DAILY: "daily",
    ThermostatMode.WEEKLY: "weekly",
    ThermostatMode.ANTIFREEZE: "antifreeze",
}
_MODE_OF_PRESET = {preset: mode for mode, preset in PRESETS.items()}
# The modes a thermostat turned on (from off or antifreeze) goes back to
_ON_MODES = (ThermostatMode.MANUAL, ThermostatMode.DAILY, ThermostatMode.WEEKLY)
_SEASON_OF_HVAC_MODE = {HVACMode.HEAT: Season.WINTER, HVACMode.COOL: Season.SUMMER}
# The modes a new target temperature takes to manual (off keeps it for later)
_TO_MANUAL = (ThermostatMode.DAILY, ThermostatMode.WEEKLY, ThermostatMode.ANTIFREEZE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a climate entity for each selected keypad's thermostat; those
    of a first selection made later are added then."""
    data = hass.data[DOMAIN][entry.entry_id]
    config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    names = keypad_names(config)
    added: set[int] = set()

    @callback
    def add_selected() -> None:
        new = [k for k in selected_keypads(entry.options, config) if k not in added]
        added.update(new)
        if new:
            async_add_entities(
                InimThermostat(
                    data[DATA_COORDINATOR],
                    data[DATA_PANEL],
                    entry,
                    keypad + 1,
                    names[keypad] if keypad < len(names) else "",
                )
                for keypad in new
            )

    add_selected()
    entry.async_on_unload(
        async_dispatcher_connect(hass, thermostats_signal(entry.entry_id), add_selected)
    )


class _LastMode(ExtraStoredData):
    """The last regulation mode the thermostat was on in, kept over restarts."""

    def __init__(self, mode: ThermostatMode) -> None:
        self.mode = mode

    def as_dict(self) -> dict[str, Any]:
        return {"last_mode": int(self.mode)}


class InimThermostat(InimCoordinatorEntity, ClimateEntity, RestoreEntity):
    """A keypad's thermostat."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_preset_modes = list(PRESETS.values())
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = TARGET_STEP
    _attr_min_temp = SETPOINT_MIN
    _attr_max_temp = SETPOINT_MAX

    def __init__(
        self,
        coordinator: InimDataUpdateCoordinator,
        panel: SmartLivingPanel,
        entry: ConfigEntry,
        keypad: int,
        label: str,
    ) -> None:
        """A keypad's (1-based) thermostat; label is the keypad's name."""
        super().__init__(coordinator)
        self.panel = panel
        self._keypad = keypad
        self._target = f"Thermostat {keypad}" + (f" ({label})" if label else "")
        self._attr_unique_id = f"{entry.entry_id}_thermostat_{keypad}"
        self._attr_device_info = panel_device_info(entry)
        name_item(self, "thermostat", "Keypad", keypad, label)
        self._last_mode = ThermostatMode.MANUAL

    @property
    def suggested_object_id(self) -> str | None:
        """Thermostat N: the ID does not depend on the keypad's name."""
        return f"thermostat_{self._keypad}"

    async def async_added_to_hass(self) -> None:
        """Restore the last regulation mode the thermostat was on in."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            mode = extra.as_dict().get("last_mode")
            if mode in [int(m) for m in _ON_MODES]:
                self._last_mode = ThermostatMode(mode)
        self._note_mode()

    @property
    def extra_restore_state_data(self) -> _LastMode:
        """The last regulation mode the thermostat was on in."""
        return _LastMode(self._last_mode)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._note_mode()
        super()._handle_coordinator_update()

    def _note_mode(self) -> None:
        state = self._state
        if state is not None and state.head.mode in _ON_MODES:
            self._last_mode = state.head.mode

    @property
    def _state(self) -> ThermostatState | None:
        return self.thermostat(self._keypad)

    @property
    def available(self) -> bool:
        """Available once the thermostat has been read."""
        return super().available and self._state is not None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Off, or heat (winter, and antifreeze) or cool (summer)."""
        state = self._state
        if state is None or state.head.mode is None:
            return None
        if state.head.mode is ThermostatMode.OFF:
            return HVACMode.OFF
        if state.head.mode is ThermostatMode.ANTIFREEZE:
            return HVACMode.HEAT  # the panel always heats in antifreeze
        if state.head.season is None:
            return None
        return HVACMode.HEAT if state.head.season is Season.WINTER else HVACMode.COOL

    @property
    def hvac_action(self) -> HVACAction | None:
        """Heating or cooling while the panel's thermostat is on; None where
        the panel does not tell (firmware 5.1)."""
        state = self._state
        if state is None or state.on is None:
            return None
        mode = self.hvac_mode
        if mode is None:
            return None
        if mode is HVACMode.OFF:
            return HVACAction.OFF
        if not state.on:
            return HVACAction.IDLE
        return HVACAction.HEATING if mode is HVACMode.HEAT else HVACAction.COOLING

    @property
    def preset_mode(self) -> str | None:
        """The regulation mode; none when off."""
        state = self._state
        return None if state is None else PRESETS.get(state.head.mode)

    @property
    def target_temperature(self) -> float | None:
        """The setpoint the thermostat regulates to now.

        Off: the manual setpoint (a temperature set while off is kept for
        later). The programs: this hour's setpoint, by Home Assistant's
        clock.
        """
        state = self._state
        if state is None:
            return None
        mode = state.head.mode
        if mode in (ThermostatMode.OFF, ThermostatMode.MANUAL):
            return state.head.setpoint
        if mode is ThermostatMode.DAILY:
            return state.head.program_setpoint(dt_util.now().hour)
        if mode is ThermostatMode.WEEKLY:
            return state.weekly_setpoint
        if mode is ThermostatMode.ANTIFREEZE:
            return ANTIFREEZE_SETPOINT
        return None

    @property
    def current_temperature(self) -> float | None:
        """The keypad's room temperature."""
        state = self._state
        return None if state is None else state.temperature

    # -------------------------------------------------------------- changes

    def _mode(self) -> ThermostatMode | None:
        state = self._state
        return None if state is None else state.head.mode

    def _turned_on_mode(self) -> ThermostatMode | None:
        """The mode to write when a change turns the thermostat on, or takes
        it out of antifreeze: the last one it was on in."""
        if self._mode() in (None, ThermostatMode.OFF, ThermostatMode.ANTIFREEZE):
            return self._last_mode
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Off; or heat or cool: the season, and from off or antifreeze also
        the last mode the thermostat was on in.

        Heat in antifreeze changes nothing: it already heats, and asking for
        heat (an automation making sure it heats) must not end antifreeze.
        """
        if hvac_mode == HVACMode.OFF:
            await self._async_write(mode=ThermostatMode.OFF)
            return
        if hvac_mode == HVACMode.HEAT and self._mode() is ThermostatMode.ANTIFREEZE:
            return
        await self._async_write(
            season=_SEASON_OF_HVAC_MODE[HVACMode(hvac_mode)],
            mode=self._turned_on_mode(),
        )

    async def async_turn_on(self) -> None:
        """Back to the last mode the thermostat was on in, if it is off."""
        if self._mode() is ThermostatMode.OFF:
            await self._async_write(mode=self._last_mode)

    async def async_turn_off(self) -> None:
        """Regulation mode off."""
        await self._async_write(mode=ThermostatMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """The regulation mode (it turns the thermostat on if off)."""
        await self._async_write(mode=_MODE_OF_PRESET[preset_mode])

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """The manual setpoint.

        In a program or antifreeze the thermostat also goes to manual; off,
        it stays off with the new setpoint kept for later. With an HVAC
        mode given too, that is applied with it.
        """
        setpoint = kwargs.get(ATTR_TEMPERATURE)
        season = None
        mode = self._mode()
        new_mode: ThermostatMode | None = None
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            if hvac_mode == HVACMode.OFF:
                new_mode = ThermostatMode.OFF
            else:
                season = _SEASON_OF_HVAC_MODE[HVACMode(hvac_mode)]
                if mode in (None, ThermostatMode.OFF):
                    new_mode = ThermostatMode.MANUAL
        if setpoint is not None and new_mode is None and mode in _TO_MANUAL:
            new_mode = ThermostatMode.MANUAL
        await self._async_write(season=season, setpoint=setpoint, mode=new_mode)

    async def _async_write(
        self,
        *,
        season: Season | None = None,
        setpoint: float | None = None,
        mode: ThermostatMode | None = None,
    ) -> None:
        """Write the settings given; the entity shows the panel's values at
        once."""
        head = await async_write_thermostat(
            self.panel.set_thermostat(
                self._keypad - 1, season=season, setpoint=setpoint, mode=mode
            ),
            self._target,
        )
        self.coordinator.async_thermostat_written(self._keypad - 1, head)
