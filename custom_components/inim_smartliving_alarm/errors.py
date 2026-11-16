"""Errors raised when a command to the panel is not carried out.

These cases are kept apart:
- ZonesNotReadyError: the integration did not send the command, because its
  own check found zones that are not ready;
- PanelProgrammingError: the panel is in programming mode, so the command was
  not sent, or the panel entered it while the command was sent and may have
  ignored it;
- CommandRefusedError: the panel received the command and refused it (wrong
  code, code not enabled, arming not allowed while zones are not ready, ...);
- CommandFailedError: the command could not be sent, or its result could not
  be read (connection or protocol problem).
"""

from collections.abc import Awaitable, Sequence
import logging

from homeassistant.exceptions import HomeAssistantError

from .api import NotReadyError, ProgrammingModeError
from .const import DOMAIN
from .protocol.commands import CommandResult
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)

# Panel results that have a message of their own; any other is "unknown"
REFUSAL_REASONS = frozenset(
    {
        "invalid_code",
        "open_zones",
        "code_not_enabled",
        "already_in_state",
        "slot_out_of_range",
        "slot_not_connected",
        "null_terminal",
        "cannot_be_bypassed",
        "no_areas",
        "double_zone_error",
        "output_not_enabled",
        "areas_armed",
        "refused",
    }
)

_FAILURES = (TransportError, ProtocolError, UnsupportedPanelError, ValueError)


class InimCommandError(HomeAssistantError):
    """A command was not carried out."""


class ZonesNotReadyError(InimCommandError):
    """Not sent: the integration found zones that are not ready."""

    def __init__(self, target: str, zones: Sequence[int], zone_names: Sequence[str]):
        """Name the target and the zones (1-based) that are not ready."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key="zones_not_ready",
            translation_placeholders={
                "target": target,
                "zones": ", ".join(zone_label(zone, zone_names) for zone in zones),
            },
        )
        self.zones = tuple(zones)


class PanelProgrammingError(InimCommandError):
    """The panel is in programming mode, so the command was not carried out."""

    def __init__(self, target: str, *, sent: bool) -> None:
        """Name the target; sent says whether the command had been sent."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key="panel_programming_started"
            if sent
            else "panel_programming",
            translation_placeholders={"target": target},
        )
        self.sent = sent


class CommandRefusedError(InimCommandError):
    """The panel refused the command."""

    def __init__(self, target: str, result: CommandResult) -> None:
        """Name the target and keep the panel's result."""
        reason = result.reason if result.reason in REFUSAL_REASONS else "unknown"
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=f"refused_{reason}",
            translation_placeholders={"target": target, "code": str(result.code)},
        )
        self.reason = reason
        self.code = result.code


class CommandFailedError(InimCommandError):
    """The command could not be sent or its result could not be read."""

    def __init__(self, target: str, error: Exception) -> None:
        """Name the target and the underlying error."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"target": target, "error": str(error)},
        )


def zone_label(zone: int, zone_names: Sequence[str]) -> str:
    """A zone (1-based) as its label and number, e.g. "Front door (3)"."""
    name = zone_names[zone - 1] if 0 < zone <= len(zone_names) else ""
    return f"{name} ({zone})" if name else str(zone)


async def async_send_command(
    command: Awaitable[CommandResult],
    target: str,
    *,
    zone_names: Sequence[str] = (),
) -> None:
    """Wait for a command and raise an InimCommandError unless it was carried out.

    target names what the command acts on (an area, zone or scenario label)
    in the error message; zone_names labels the zones of a not-ready error.
    """
    try:
        result = await command
    except NotReadyError as err:
        zones = sorted(zone + 1 for zone in err.zones)
        _LOGGER.warning("%s: not sent, zones not ready: %s", target, zones)
        raise ZonesNotReadyError(target, zones, zone_names) from err
    except ProgrammingModeError as err:
        _LOGGER.warning("%s: %s", target, err)
        raise PanelProgrammingError(target, sent=err.sent) from err
    except _FAILURES as err:
        _LOGGER.warning("%s: command failed: %s", target, err)
        raise CommandFailedError(target, err) from err
    if not result.ok:
        _LOGGER.warning(
            "%s: refused by the panel: %s (result %s)",
            target,
            result.reason,
            result.code,
        )
        raise CommandRefusedError(target, result)
