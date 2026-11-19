"""Dimmer outputs of the Inim panel as lights with brightness."""

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_PANEL_NAME,
    DATA_API_CLIENT,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_PANEL_NAME,
    DOMAIN,
    KEY_INIT_SYSTEM_INFO,
)
from .outputs import InimOutputEntity, is_dimmer, output_level
from .protocol.errors import ProtocolError
from .selection import imported_outputs
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)

FULL_LEVEL = 100  # the panel's levels are 0-100 %
_MAX_BRIGHTNESS = 255


def level_to_brightness(level: int) -> int:
    """A level 0-100 as a brightness 0-255."""
    return round(level * _MAX_BRIGHTNESS / FULL_LEVEL)


def brightness_to_level(brightness: int) -> int:
    """A brightness 1-255 as a level 1-100 (never 0, which is off)."""
    return max(1, min(FULL_LEVEL, round(brightness * FULL_LEVEL / _MAX_BRIGHTNESS)))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a light for each imported dimmer output."""
    data = hass.data[DOMAIN][entry.entry_id]
    config = entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
    lights = [
        InimDimmerLight(
            data[DATA_COORDINATOR],
            data[DATA_API_CLIENT],
            entry,
            entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME),
            config.get(KEY_INIT_SYSTEM_INFO, {}),
            item,
        )
        for item in imported_outputs(entry.options, config)
        if item.address is not None and is_dimmer(item)
    ]
    if lights:
        async_add_entities(lights)


class InimDimmerLight(InimOutputEntity, LightEntity, RestoreEntity):
    """A dimmer output: on/off from the output image, level from the panel.

    Panels that do not report levels show the last level set from Home
    Assistant.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = frozenset({ColorMode.BRIGHTNESS})

    def __init__(self, *args: Any) -> None:
        """Initialize the light; the last level set is restored at start."""
        super().__init__(*args)
        self._last_level: int | None = None
        self._read_level: int | None = None  # read right after a command

    async def async_added_to_hass(self) -> None:
        """Remember the last level set from Home Assistant (never sent)."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            brightness = state.attributes.get(ATTR_BRIGHTNESS)
            if isinstance(brightness, int) and brightness > 0:
                self._last_level = brightness_to_level(brightness)

    @property
    def _level(self) -> int | None:
        """The panel's level if it reports one, else the last one set."""
        level = self._read_level
        if level is None:
            level = output_level(self.coordinator.data, self._item)
        return level if level is not None else self._last_level

    @property
    def brightness(self) -> int | None:
        """The level as a brightness while the output is on."""
        if not self.is_on:
            return None
        level = self._level
        return level_to_brightness(level) if level else None

    def _handle_coordinator_update(self) -> None:
        self._read_level = None  # the poll has the level now
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch on at a level (the brightness, else the last level, else full)."""
        if ATTR_BRIGHTNESS in kwargs:
            level = brightness_to_level(kwargs[ATTR_BRIGHTNESS])
        else:
            level = self._level or FULL_LEVEL
        self._last_level = level
        await self._async_switch(level)
        await self._async_read_level()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch off (the level is kept for the next "on")."""
        await self._async_switch(0)

    async def _async_read_level(self) -> None:
        """Read the level once after a command, if the panel reports it."""
        try:
            levels = await self.api_client.panel.read_output_levels(
                [self._item.terminal]
            )
        except (TransportError, ProtocolError, ValueError, RuntimeError) as err:
            _LOGGER.debug("%s: level not read after the command: %s", self._target, err)
            return
        self._read_level = levels.get(self._item.terminal)
        self.async_write_ha_state()
