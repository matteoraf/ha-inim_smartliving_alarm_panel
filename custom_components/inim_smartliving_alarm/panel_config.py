"""Reading the panel configuration and storing it in the config entry."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import PANEL_ERRORS, ProgrammingModeError, SmartLivingPanel
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_THERMOSTATS,
    DATA_COORDINATOR,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DOMAIN,
)
from .protocol.errors import ProtocolError, UnsupportedPanelError
from .selection import available_thermostats
from .stored_config import store
from .thermostats import with_new_keypads
from .transport.base import TransportError

# Error keys of PanelConfigError
PANEL_PROGRAMMING = "panel_programming"
AUTH_FAILED = "auth_failed"
INVALID_PANEL_RESPONSE = "invalid_panel_response"
INVALID_PANEL_CONFIGURATION = "invalid_panel_configuration"

_LOGGER = logging.getLogger(__name__)


class PanelUnreachable(ConnectionError):
    """The panel could not be reached to read its configuration."""


class SetupError(ValueError):
    """A panel cannot be set up with these details; key tells why.

    The key is the setup form's error key; the message is the key, the
    detail the underlying error.
    """

    def __init__(self, key: str, detail: str = "") -> None:
        """An error with its key and, for the log, what went wrong."""
        super().__init__(key)
        self.key = key
        self.detail = detail or key


class PanelConfigError(SetupError):
    """The panel's configuration could not be read; key tells why.

    One of PANEL_PROGRAMMING, AUTH_FAILED, INVALID_PANEL_RESPONSE and
    INVALID_PANEL_CONFIGURATION.
    """


def _where(panel: SmartLivingPanel) -> str:
    return f"{panel.transport.host}:{panel.transport.port}"


async def async_fetch_panel_configuration(
    hass: HomeAssistant, panel: SmartLivingPanel
) -> dict[str, Any]:
    """Read the panel configuration, in the shape stored in the entry.

    Raises PanelUnreachable if the panel cannot be reached and
    PanelConfigError if the configuration cannot be read: the panel is in
    programming mode, a reply is invalid, or the panel gives no model
    string.
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
        raise PanelConfigError(INVALID_PANEL_CONFIGURATION, str(err)) from err
    if not config.model_string.strip(b" \x00"):
        _LOGGER.error("System information is missing from the panel configuration")
        raise PanelConfigError(INVALID_PANEL_RESPONSE)
    _LOGGER.info("Panel configuration read from %s", where)
    return store(config)


async def async_reread_panel_configuration(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Read the panel configuration again, store it in the entry and reload it.

    The keypads are checked for a thermostat: those enabled since are added
    to the thermostats (see thermostats.with_new_keypads); the other options
    are left untouched. The entry's own panel connection is used, so the
    reads wait for any poll in progress instead of opening a second socket.
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

    options = entry.options
    if (thermostats := await _new_thermostats(hass, entry, panel_config)) is not None:
        options = {**options, CONF_THERMOSTATS: thermostats}
    _LOGGER.info("Panel configuration re-read for %s, reloading", entry.title)
    # A changed entry is reloaded by the entry's update listener; reload an
    # unchanged one here so that the action always ends with exactly one reload.
    if not hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, DATA_INITIAL_PANEL_CONFIG: panel_config},
        options=options,
    ):
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def _new_thermostats(
    hass: HomeAssistant, entry: ConfigEntry, config: dict[str, Any]
) -> list[int] | None:
    """The thermostat selection with the keypads enabled since, if they
    answer the temperature read (None: no change, or not checked)."""
    if CONF_THERMOSTATS not in entry.options:
        return None  # checked at the next start
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    keypads = [number - 1 for number in available_thermostats(config)]
    try:
        found = await coordinator.async_probe_thermostats(keypads)
    except PANEL_ERRORS as err:
        _LOGGER.info("%s: the keypads were not checked again: %s", entry.title, err)
        return None
    return with_new_keypads(
        entry.options,
        entry.data.get(DATA_INITIAL_PANEL_CONFIG, {}),
        (keypad + 1 for keypad in found),
    )
