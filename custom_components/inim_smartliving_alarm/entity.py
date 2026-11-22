"""What every entity of the integration shares: the panel's device and the
typed live status of the last poll."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PanelStatus
from .const import CONF_PANEL_NAME, DEFAULT_PANEL_NAME, DOMAIN
from .coordinator import InimDataUpdateCoordinator
from .protocol.status import AreaMode, ZoneState
from .selection import panel_config
from .stored_config import system_info


def panel_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The panel's device: its name, and the type and firmware version from
    the stored model string."""
    info = system_info(panel_config(entry.data))
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME),
        manufacturer="Inim Electronics",
        model=info.type if info is not None else "SmartLiving",
        sw_version=info.version if info is not None else "Unknown",
    )


class InimCoordinatorEntity(CoordinatorEntity[InimDataUpdateCoordinator]):
    """An entity that shows the panel's live status.

    The helpers take 1-based zone and area numbers and give None when the
    status is not known yet or the number is not on the panel.
    """

    @property
    def status(self) -> PanelStatus | None:
        """The live status of the last poll; None before the first one."""
        data = self.coordinator.data
        return data.status if data is not None else None

    def zone_state(self, zone: int) -> ZoneState | None:
        """A zone's state now."""
        status = self.status
        if status is None or not 0 < zone <= len(status.zone_states):
            return None
        return status.zone_states[zone - 1]

    def zone_included(self, zone: int) -> bool | None:
        """Whether a zone is included (not bypassed)."""
        status = self.status
        if status is None or not 0 < zone <= len(status.zone_inclusion.included):
            return None
        return status.zone_inclusion.included[zone - 1]

    def zone_in_test(self, zone: int) -> bool | None:
        """Whether a zone is in test."""
        status = self.status
        if status is None or not 0 < zone <= len(status.zone_inclusion.test):
            return None
        return status.zone_inclusion.test[zone - 1]

    def zone_alarm_memory(self, zone: int) -> bool | None:
        """Whether a zone has an alarm memory."""
        status = self.status
        if status is None or not 0 < zone <= len(status.zone_memory.alarm_memory):
            return None
        return status.zone_memory.alarm_memory[zone - 1]

    def zone_tamper_memory(self, zone: int) -> bool | None:
        """Whether a zone's terminal has a tamper memory."""
        status = self.status
        if status is None or not 0 < zone <= len(status.zone_states):
            return None
        return status.zone_memory.zone_tamper_memory(zone - 1)

    def area_mode(self, area: int) -> AreaMode | None:
        """An area's arming mode."""
        status = self.status
        return None if status is None else status.areas.modes.get(area)
