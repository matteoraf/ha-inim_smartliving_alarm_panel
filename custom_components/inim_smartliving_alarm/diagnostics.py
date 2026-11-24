"""Diagnostics: the system report, at the level chosen with the select.

Redacted (the default): one plain download, safe to post. At the Full
levels the first download is the report encrypted to the maintainer's key,
with the Redacted view beside it; downloading again within 10 minutes gives
a readable copy of the same report, to check what is being shared. Then the
level goes back to Redacted. Nothing is stored: the report waits in memory.
"""

from dataclasses import replace
from datetime import datetime
import logging
import secrets
import time
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import SmartLivingPanel, create_panel
from .const import (
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_PORT,
    CONF_USERNAME,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DOMAIN,
)
from .discovery import async_board_reply
from .notifications import async_texts
from .protocol.errors import ProtocolError
from .protocol.masking import Level
from .report_crypto import encrypt
from .report_state import (
    Snapshot,
    async_set_level,
    current_report_state,
    report_state,
)
from .system_report import EntryFacts, ReportInput, build_report, report_text
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_LIFETIME = 600  # seconds the readable copy can be downloaded
PRIVATE_COPY = "PRIVATE COPY"
PRIVATE_COPY_TEXT = (
    "Readable copy of the encrypted report with the same report_id. It may "
    "contain names, the serial number and addresses, and at Full "
    "confidential also PINs, phone numbers and passwords: do not share it, "
    "and delete it when you no longer need it."
)
SHARE_TEXT = "This file can be attached to a GitHub issue."
_REDACT = {CONF_PIN, CONF_PASSWORD, CONF_USERNAME, CONF_HOST}


def _now() -> float:
    """Seconds on a monotonic clock (replaceable in tests)."""
    return time.monotonic()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """The system report at the chosen level."""
    state = report_state(hass, entry.entry_id)
    snapshot = state.snapshot
    if snapshot is not None and snapshot.expires < _now():
        snapshot = state.snapshot = None
    if snapshot is not None and snapshot.level is state.level:
        # Second download: the readable copy, then back to Redacted
        async_set_level(hass, entry.entry_id, Level.REDACTED)
        return {PRIVATE_COPY: PRIVATE_COPY_TEXT, **snapshot.report}

    level = state.level
    report_id = secrets.token_hex(8)
    data = await _async_report_input(hass, entry, report_id, Level.REDACTED)
    # Building a report takes tenths of a second: not in the event loop
    redacted = _finish(await hass.async_add_executor_job(build_report, data))
    if level is Level.REDACTED:
        return redacted

    full = _finish(
        await hass.async_add_executor_job(build_report, _at_level(data, level))
    )
    envelope = await hass.async_add_executor_job(
        lambda: encrypt(report_text(full), report_id=report_id, level=str(level))
    )
    if current_report_state(hass, entry.entry_id) is not state:
        # The entry was unloaded during the reads: its level was dropped,
        # so this download gets the Redacted report and nothing waits
        return redacted
    state.snapshot = Snapshot(report_id, level, full, _now() + SNAPSHOT_LIFETIME)

    @callback
    def expire(_when: datetime) -> None:
        state.expired()
        if state.snapshot is not None and state.snapshot.report_id == report_id:
            async_set_level(hass, entry.entry_id, Level.REDACTED)

    state.expire_later(async_call_later(hass, SNAPSHOT_LIFETIME, expire))
    await _async_notify(hass, entry)
    return {
        "report_version": full["report_version"],
        "report_id": report_id,
        "level": str(level),
        "share": SHARE_TEXT,
        "encrypted": envelope,
        "redacted_view": redacted,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """The same report as the entry's (one device per entry)."""
    return await async_get_config_entry_diagnostics(hass, entry)


def _at_level(data: ReportInput, level: Level) -> ReportInput:
    """The same reading at another level: every other field kept."""
    return replace(data, level=level)


def _finish(report: dict[str, Any]) -> dict[str, Any]:
    """A second net: the entry's credentials never pass, whatever the level."""
    if isinstance(report.get("integration"), dict):
        report["integration"] = async_redact_data(report["integration"], _REDACT)
    return report


async def _async_notify(hass: HomeAssistant, entry: ConfigEntry) -> None:
    texts = await async_texts(hass)
    persistent_notification.async_create(
        hass,
        texts("notification_system_report_message"),
        title=texts("notification_system_report_title"),
        notification_id=f"{DOMAIN}_{entry.entry_id}_system_report",
    )


def _entities(hass: HomeAssistant, entry: ConfigEntry) -> list[dict[str, Any]]:
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    return [
        {
            "kind": item.domain,
            "unique_suffix": item.unique_id.removeprefix(prefix),
            "enabled": item.disabled_by is None,
            "entity_id": item.entity_id,
            "name": item.name or item.original_name,
        }
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    ]


def _temporary_panel(entry: ConfigEntry) -> SmartLivingPanel:
    """A panel connection of our own, for an entry that is not loaded."""
    return create_panel(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_PIN],
        board=entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI),
        username=entry.data.get(CONF_USERNAME, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
    )


async def _async_report_input(
    hass: HomeAssistant, entry: ConfigEntry, report_id: str, level: Level
) -> ReportInput:
    """Read the panel through the entry's own connection (or a temporary one)."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    loaded = entry_data.get(DATA_PANEL)
    temporary = loaded is None
    # The entry is not loaded: the board is free for a connection of our own
    panel = _temporary_panel(entry) if loaded is None else loaded
    reads, read_error = None, None
    try:
        reads = await panel.read_report()
    except (TransportError, ProtocolError, OSError) as err:
        read_error = type(err).__name__
    finally:
        if temporary:
            await panel.close()
    board = entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI)
    # The SmartLAN/SI board answers over TCP from anywhere; else (or when it
    # is in use) the broadcast search, which only reaches its own network
    reply, source = None, None
    if board == BOARD_SMARTLAN_SI:
        reply = await panel.board_reply()
        source = "tcp" if reply is not None else None
    if reply is None:
        reply = await async_board_reply(entry.data[CONF_HOST], board)
        source = "udp" if reply is not None else None
    integration = await async_get_integration(hass, DOMAIN)
    user_code = None
    if panel.user_code_index is not None or panel.user_code_found:
        user_code = {
            "found": panel.user_code_found,
            "number": None
            if panel.user_code_index is None
            else panel.user_code_index + 1,
            "areas": sorted(panel.user_areas),
        }
    stats = panel.transport.stats
    facts = EntryFacts(
        title=entry.title,
        entry_version=f"{entry.version}.{entry.minor_version}",
        integration_version=str(integration.version),
        board=board,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        options=dict(entry.options),
        stored_config=entry.data.get(DATA_INITIAL_PANEL_CONFIG) or {},
        entities=_entities(hass, entry),
        pin=entry.data.get(CONF_PIN),
        username=entry.data.get(CONF_USERNAME) or None,
        password=entry.data.get(CONF_PASSWORD) or None,
    )
    return ReportInput(
        report_id=report_id,
        created=dt_util.now().isoformat(timespec="seconds"),
        level=level,
        entry=facts,
        reads=reads,
        read_error=read_error,
        board_reply=reply,
        board_reply_source=source,
        transport_stats=stats.as_dict(),
        trace=list(stats.trace),
        user_code=user_code,
    )
