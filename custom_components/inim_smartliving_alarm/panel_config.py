"""Reading the panel configuration and storing it in the config entry."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import ProgrammingModeError, SmartLivingPanel
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DOMAIN,
)
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .stored_config import store
from .transport.base import TransportError

# Error keys of PanelConfigError
PANEL_PROGRAMMING = "panel_programming"
AUTH_FAILED = "auth_failed"
INVALID_PANEL_RESPONSE = "invalid_panel_response"
INVALID_PANEL_CONFIGURATION = "invalid_panel_configuration"

_LOGGER = logging.getLogger(__name__)


class PanelUnreachable(ConnectionError):
    """The panel could not be reached to read its configuration."""


class PanelConfigError(ValueError):
    """The panel's configuration could not be read; key tells why.

    One of PANEL_PROGRAMMING, AUTH_FAILED, INVALID_PANEL_RESPONSE and
    INVALID_PANEL_CONFIGURATION; the message is the key, the detail the
    underlying error.
    """

    def __init__(self, key: str, detail: str = "") -> None:
        """An error with its key and, for the log, what went wrong."""
        super().__init__(key)
        self.key = key
        self.detail = detail or key


def _where(panel: SmartLivingPanel) -> str:
    return f"{panel.transport.host}:{panel.transport.port}"


async def async_fetch_panel_configuration(
    hass: HomeAssistant, panel: SmartLivingPanel
) -> dict[str, Any]:
    """Read the panel configuration, in the shape stored in the entry.

    Raises PanelUnreachable if the panel cannot be reached and
    PanelConfigError if the configuration cannot be read: the panel is in
    programming mode, a reply is invalid (AUTH_FAILED when its error names
    authentication), or the panel gives no model string.
    """
    where = _where(panel)
    _LOGGER.debug("Fetching the panel configuration from %s", where)
    try:
        config = await panel.read_configuration()
    except ProgrammingModeError as err:
        _LOGGER.warning(
            "The panel at %s is in programming mode; its configuration is "
            "not read until programming has ended",
            where,
        )
        raise PanelConfigError(PANEL_PROGRAMMING) from err
    except TransportError as err:
        _LOGGER.error("Cannot read the panel configuration from %s: %s", where, err)
        raise PanelUnreachable(str(err)) from err
    except (ProtocolError, UnsupportedPanelError, ValueError) as err:
        _LOGGER.error("Invalid panel configuration from %s: %s", where, err)
        # A reply error that names authentication is reported as such
        auth = not isinstance(err, ValueError) and "auth" in str(err).lower()
        raise PanelConfigError(
            AUTH_FAILED if auth else INVALID_PANEL_CONFIGURATION, str(err)
        ) from err
    if not config.model_string.strip(b" \x00"):
        _LOGGER.error("System information is missing from the panel configuration")
        raise PanelConfigError(INVALID_PANEL_RESPONSE)
    _LOGGER.info("Panel configuration read from %s", where)
    return store(config)


async def async_reread_panel_configuration(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Read the panel configuration again, store it in the entry and reload it.

    Options are left untouched. The entry's own panel connection is used, so
    the read waits for any poll in progress instead of opening a second
    socket.
    """
    panel: SmartLivingPanel = hass.data[DOMAIN][entry.entry_id][DATA_PANEL]
    try:
        panel_config = await async_fetch_panel_configuration(hass, panel)
    except PanelUnreachable as err:
        # A SmartLAN/SI board may be held by another app
        in_use = entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI) == BOARD_SMARTLAN_SI
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect_in_use" if in_use else "cannot_connect",
            translation_placeholders={
                "host": panel.transport.host,
                "port": str(panel.transport.port),
            },
        ) from err
    except PanelConfigError as err:
        if err.key == PANEL_PROGRAMMING:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="panel_programming_config"
            ) from err
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_panel_configuration",
            translation_placeholders={"error": err.detail},
        ) from err

    _LOGGER.info("Panel configuration re-read for %s, reloading", entry.title)
    # A changed entry is reloaded by the entry's update listener; reload an
    # unchanged one here so that the action always ends with exactly one reload.
    if not hass.config_entries.async_update_entry(
        entry, data={**entry.data, DATA_INITIAL_PANEL_CONFIG: panel_config}
    ):
        hass.config_entries.async_schedule_reload(entry.entry_id)
