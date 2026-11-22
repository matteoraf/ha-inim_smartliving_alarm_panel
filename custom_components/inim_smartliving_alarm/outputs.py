"""The panel's outputs as entities: what they share.

Terminal outputs and the fixed outputs O1-O3, switched with the output
command. Their on/off state comes from the output image read on every poll;
a dimmer's level, on panels that report it, is read for the imported
dimmers on every poll and after each dimmer command.
"""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .api import PanelStatus, SmartLivingPanel
from .attribute_values import output_attributes
from .coordinator import PanelData
from .entity import InimCoordinatorEntity, panel_device_info
from .errors import CommandRefusedError, async_send_command
from .selection import OutputItem

_LOGGER = logging.getLogger(__name__)


def output_image(
    status: PanelStatus | None,
) -> tuple[tuple[bool, ...], tuple[bool, ...]] | None:
    """(terminal outputs, O1-O3) on/off from the last poll; None if unknown."""
    if status is None:
        return None
    return status.zone_memory.outputs, status.zone_memory.fixed_outputs


class InimOutputEntity(InimCoordinatorEntity):
    """An output of the panel; the platforms add how it is switched."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        panel: SmartLivingPanel,
        config_entry: ConfigEntry,
        panel_display_name: str,
        item: OutputItem,
    ) -> None:
        """Initialize the entity of one output.

        Named from the panel's label; without one, "Output n" or "O1"-"O3".
        """
        super().__init__(coordinator)
        self.panel = panel
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
        self._attr_device_info = panel_device_info(config_entry)
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
        image = output_image(self.status)
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
        assert address is not None  # outputs without an address are not offered
        _LOGGER.info("Switching %s to %s", self._target, value)
        try:
            await async_send_command(
                self.panel.set_output(address, value), self._target
            )
        except CommandRefusedError as err:
            if err.reason != "already_in_state":
                raise
        self._optimistic = value > 0
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


def output_level(data: PanelData | None, item: OutputItem) -> int | None:
    """A dimmer's level (0-100) from the last poll; None if not read."""
    if data is None or item.terminal is None:
        return None
    return data.output_levels.get(item.terminal)
