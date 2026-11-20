"""The level of the next system report, and the report waiting for its
readable download.

Kept in memory only, per config entry; shared by the diagnostics download
and the Diagnostics level select.
"""

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN
from .protocol.masking import Level

_LOGGER = logging.getLogger(__name__)

DATA_REPORT_STATE = "report_state"


def level_signal(entry_id: str) -> str:
    """Dispatcher signal sent when an entry's report level changes."""
    return f"{DOMAIN}_{entry_id}_report_level"


@dataclass(slots=True)
class Snapshot:
    """A report at a Full level, waiting for its readable download."""

    report_id: str
    level: Level
    report: dict[str, Any]
    expires: float


@dataclass(slots=True)
class ReportState:
    """The report level of an entry and its pending snapshot (memory only)."""

    level: Level = Level.REDACTED
    snapshot: Snapshot | None = None
    _cancel_expiry: CALLBACK_TYPE | None = field(default=None, repr=False)

    def cancel_expiry(self) -> None:
        """Stop the expiry timer, if any."""
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    def expire_later(self, cancel: CALLBACK_TYPE) -> None:
        """Keep the cancel function of the snapshot's expiry timer."""
        self.cancel_expiry()
        self._cancel_expiry = cancel

    def expired(self) -> None:
        """The expiry timer has fired."""
        self._cancel_expiry = None


def report_state(hass: HomeAssistant, entry_id: str) -> ReportState:
    """The entry's report state, created on first use."""
    states: dict[str, ReportState] = hass.data.setdefault(
        f"{DOMAIN}_{DATA_REPORT_STATE}", {}
    )
    return states.setdefault(entry_id, ReportState())


def current_report_state(hass: HomeAssistant, entry_id: str) -> ReportState | None:
    """The entry's report state if it has one; never creates it."""
    states: dict[str, ReportState] = hass.data.get(f"{DOMAIN}_{DATA_REPORT_STATE}", {})
    return states.get(entry_id)


@callback
def async_drop_report_state(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the entry's level and snapshot, and stop the expiry timer.

    Called when the entry is unloaded: the next setup starts at Redacted.
    """
    states: dict[str, ReportState] = hass.data.get(f"{DOMAIN}_{DATA_REPORT_STATE}", {})
    state = states.pop(entry_id, None)
    if state is not None:
        state.cancel_expiry()


@callback
def async_set_level(hass: HomeAssistant, entry_id: str, level: Level) -> None:
    """Choose the level of the next report; leaving Full drops the snapshot."""
    state = report_state(hass, entry_id)
    if level is not state.level:
        state.snapshot = None
        state.cancel_expiry()
    state.level = level
    if level is not Level.REDACTED:
        _LOGGER.warning(
            "The diagnostics level is %s: the next diagnostics download reads "
            "the panel for a system report at that level",
            level,
        )
    async_dispatcher_send(hass, level_signal(entry_id))
