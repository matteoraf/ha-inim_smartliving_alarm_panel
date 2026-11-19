"""The panel's outputs as entities: what they share.

Terminal outputs and the fixed outputs O1-O3, switched with the output
command. Their on/off state comes from the output image read on every poll;
a dimmer's level, on panels that report it, is read for the imported
dimmers on every poll and after each dimmer command.
"""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .attribute_values import time_text
from .const import (
    DOMAIN,
    KEY_INIT_SYSTEM_INFO_TYPE,
    KEY_INIT_SYSTEM_INFO_VERSION,
    KEY_LIVE_OUTPUT_LEVELS,
    KEY_LIVE_ZONES_TRIGGERED_STATUS,
)
from .errors import CommandRefusedError, async_send_command
from .legacy import LegacyPanelClient
from .protocol.config import OutputKind
from .protocol.errors import ProtocolError
from .protocol.status import decode_zone_memory
from .selection import OutputItem

_LOGGER = logging.getLogger(__name__)

# The raw zone memory register in the coordinator's data (with the outputs)
_RAW_HEX = "raw_hex_data"


def output_image(
    data: Mapping[str, Any] | None, api_client: LegacyPanelClient
) -> tuple[tuple[bool, ...], tuple[bool, ...]] | None:
    """(terminal outputs, O1-O3) on/off from the last poll; None if unknown."""
    raw = ((data or {}).get(KEY_LIVE_ZONES_TRIGGERED_STATUS) or {}).get(_RAW_HEX)
    try:
        memory = decode_zone_memory(bytes.fromhex(raw), api_client.panel.model)
    except (TypeError, ValueError, ProtocolError, RuntimeError):
        return None
    return memory.outputs, memory.fixed_outputs


def output_attributes(item: OutputItem) -> dict[str, Any]:
    """An output's programming as attributes (stable English keys)."""
    config = item.config
    attributes: dict[str, Any] = {
        "output_number": item.number,
        "kind": config.kind.value,
    }
    if item.fixed is not None:
        attributes["fixed_output"] = item.fixed
    else:
        attributes["terminal_index"] = item.terminal
    if (time := config.monostable_time) is not None:
        attributes["monostable_time"] = time_text(time)
    if item.home_automation is not None:  # not known on every firmware
        attributes["home_automation"] = item.home_automation
    flags = config.flags
    for name in (
        "normally_closed",
        "flashing",
        "buzzer",
        "keep_on_restoral",
        "toggle",
        "relay_use",
    ):
        attributes[name] = flags[name]
    return attributes


class InimOutputEntity(CoordinatorEntity):
    """An output of the panel; the platforms add how it is switched."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        api_client: LegacyPanelClient,
        config_entry: ConfigEntry,
        panel_display_name: str,
        system_info: Mapping[str, Any],
        item: OutputItem,
    ) -> None:
        """Initialize the entity of one output.

        Named from the panel's label; without one, "Output n" or "O1"-"O3".
        """
        super().__init__(coordinator)
        self.api_client = api_client
        self._item = item
        self._attr_unique_id = f"{config_entry.entry_id}_{item.unique_suffix}"
        if item.name:
            self._attr_name = item.name
        elif item.fixed is not None:
            self._attr_translation_key = "fixed_output"
            self._attr_translation_placeholders = {"number": str(item.fixed)}
        else:
            self._attr_translation_key = "terminal_output"
            self._attr_translation_placeholders = {"number": str(item.number)}
        self._attr_extra_state_attributes = output_attributes(item)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=panel_display_name,
            manufacturer="Inim Electronics",
            model=system_info.get(KEY_INIT_SYSTEM_INFO_TYPE, "SmartLiving"),
            sw_version=system_info.get(KEY_INIT_SYSTEM_INFO_VERSION, "Unknown"),
        )
        self._optimistic: bool | None = None

    @property
    def _target(self) -> str:
        """How the output is named in error messages."""
        return self._item.name or (
            f"O{self._item.fixed}"
            if self._item.fixed is not None
            else f"Output {self._item.number}"
        )

    def _image_state(self) -> bool | None:
        """The output's on/off state in the last poll."""
        image = output_image(self.coordinator.data, self.api_client)
        if image is None:
            return None
        terminals, fixed = image
        if self._item.fixed is not None:
            return fixed[self._item.fixed - 1]
        return terminals[self._item.terminal]

    @property
    def is_on(self) -> bool | None:
        """On while the panel shows the output active."""
        if self._optimistic is not None:
            return self._optimistic
        return self._image_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._optimistic = None  # the poll shows the panel's state again
        super()._handle_coordinator_update()

    async def _async_switch(self, value: int) -> None:
        """Send the output command; "already in that state" is no error."""
        address = self._item.address
        if address is None:  # not offered, so not imported; kept for safety
            raise ValueError(f"{self._target} cannot be switched")
        _LOGGER.info("Switching %s to %s", self._target, value)
        try:
            await async_send_command(
                self.api_client.panel.set_output(address, value), self._target
            )
        except CommandRefusedError as err:
            if err.reason != "already_in_state":
                raise
        self._optimistic = value > 0
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


def is_dimmer(item: OutputItem) -> bool:
    """Whether the output is switched with a level (a light)."""
    return item.config.kind is OutputKind.DIMMER


def output_level(data: Mapping[str, Any] | None, item: OutputItem) -> int | None:
    """A dimmer's level (0-100) from the last poll; None if not read."""
    levels = (data or {}).get(KEY_LIVE_OUTPUT_LEVELS) or {}
    return levels.get(item.terminal)
