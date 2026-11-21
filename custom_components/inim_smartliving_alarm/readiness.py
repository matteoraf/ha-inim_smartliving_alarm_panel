"""What happens when zones are not ready for arming.

Before arming areas or activating a scenario the integration asks the panel
which zones would prevent it. The "When zones are not ready" option decides
what then: refuse (nothing is sent, an error lists the zones) or arm anyway
with a warning. An action's `force` field arms anyway for that call. When
the installer set the panel to refuse arming with zones not ready, the
panel's own refusal always stands.

Either way an event lists the zones, for automations and announcements.
"""

from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import NotReadyPolicy
from .const import (
    CONF_ZONES_NOT_READY,
    DEFAULT_ZONES_NOT_READY,
    EVENT_ZONES_NOT_READY,
    KEY_INIT_GENERAL_OPTIONS,
    KEY_INIT_RAW_HEX,
    ZONES_NOT_READY_WARN,
)
from .errors import (
    CommandRefusedError,
    ZonesNotReadyError,
    async_send_command,
    zone_label,
)
from .notifications import Texts, async_texts
from .protocol.commands import CommandResult
from .protocol.config import refuses_arming_when_not_ready
from .protocol.errors import ProtocolError
from .selection import panel_config, stored_model, zone_names

_LOGGER = logging.getLogger(__name__)

ACTION_REFUSED = "refused"
ACTION_ARMED_ANYWAY = "armed_anyway"
ACTION_REFUSED_BY_PANEL = "refused_by_panel"


def panel_refuses_not_ready(config: Mapping[str, Any]) -> bool | None:
    """Whether the panel itself refuses arming with zones not ready.

    Decoded from the stored general options; None when they are not stored
    (an earlier version), were read with another layout, or cannot be
    decoded.
    """
    raw = (config.get(KEY_INIT_GENERAL_OPTIONS) or {}).get(KEY_INIT_RAW_HEX)
    if stored_model(config) is None:
        return None
    try:
        return refuses_arming_when_not_ready(bytes.fromhex(raw))
    except (TypeError, ValueError, ProtocolError):
        return None


class ReadyCheck:
    """The decision for one command, and the zones the panel listed."""

    def __init__(self, entry: ConfigEntry, *, force: bool = False) -> None:
        """Arm anyway when forced or when the option says so."""
        self.send_anyway = force or (
            entry.options.get(CONF_ZONES_NOT_READY, DEFAULT_ZONES_NOT_READY)
            == ZONES_NOT_READY_WARN
        )
        self.zones: frozenset[int] = frozenset()  # 0-based

    def __call__(self, zones: frozenset[int]) -> bool:
        """Record the zones; True sends the command anyway."""
        self.zones = zones
        return self.send_anyway


async def async_arm(
    hass: HomeAssistant,
    entry: ConfigEntry,
    command: Callable[[NotReadyPolicy], Awaitable[CommandResult]],
    target: str,
    *,
    force: bool = False,
) -> None:
    """Send an arming command with the zone check; raise unless carried out.

    command builds the API call from the policy (the ReadyCheck). Refused
    for zones not ready: the event fires and ZonesNotReadyError is raised.
    Sent anyway: the event fires and a notification lists the zones.
    """
    config = panel_config(entry.data)
    names = zone_names(config)
    check = ReadyCheck(entry, force=force)
    try:
        await async_send_command(command(check), target, zone_names=names)
    except ZonesNotReadyError as err:
        _fire(hass, entry, target, err.zones, ACTION_REFUSED, config)
        raise
    except CommandRefusedError as err:
        # Sent anyway, the panel itself refused it for the open zones
        if check.zones and err.reason == "open_zones":
            zones = sorted(zone + 1 for zone in check.zones)
            _fire(hass, entry, target, zones, ACTION_REFUSED_BY_PANEL, config)
        raise
    if check.zones:
        zones = sorted(zone + 1 for zone in check.zones)
        _LOGGER.warning("%s: armed with zones not ready: %s", target, zones)
        _fire(hass, entry, target, zones, ACTION_ARMED_ANYWAY, config)
        texts = await async_texts(hass)
        persistent_notification.async_create(
            hass,
            _warning(texts, target, zones, names, panel_refuses_not_ready(config)),
            title=texts("notification_armed_not_ready_title", target=target),
            notification_id=f"{entry.entry_id}_armed_not_ready",
        )


def _fire(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: str,
    zones: list[int] | tuple[int, ...],
    action: str,
    config: Mapping[str, Any],
) -> None:
    names = zone_names(config)
    hass.bus.async_fire(
        EVENT_ZONES_NOT_READY,
        {
            "config_entry_id": entry.entry_id,
            "target": target,
            "action": action,
            "zones": [
                {"zone_id": zone, "zone_name": zone_label(zone, names)}
                for zone in zones
            ],
            "panel_refuses_not_ready": panel_refuses_not_ready(config),
        },
    )


def _warning(
    texts: Texts,
    target: str,
    zones: list[int],
    names: list[str],
    panel_refuses: bool | None,
) -> str:
    listed = "\n".join(f"- {zone_label(zone, names)}" for zone in zones)
    ending = (
        "notification_armed_not_ready_panel_allows"
        if panel_refuses is False
        else "notification_armed_not_ready_can_trigger"
    )
    return (
        texts("notification_armed_not_ready_message", target=target, zones=listed)
        + "\n\n"
        + texts(ending)
    )
