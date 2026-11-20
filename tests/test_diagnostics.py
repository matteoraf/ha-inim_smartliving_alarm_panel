"""Diagnostics downloads: the system report at each level, and the two-file flow."""

import base64
from datetime import timedelta
import json
import logging
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from homeassistant.config_entries import RELOAD_AFTER_UPDATE_DELAY, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.inim_smartliving_alarm import diagnostics, report_crypto
from custom_components.inim_smartliving_alarm.const import DATA_API_CLIENT, DOMAIN
from custom_components.inim_smartliving_alarm.diagnostics import (
    PRIVATE_COPY,
    SNAPSHOT_LIFETIME,
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
    async_set_level,
    report_state,
)
from custom_components.inim_smartliving_alarm.protocol.masking import Level
from custom_components.inim_smartliving_alarm.report_state import (
    async_drop_report_state,
)

from .fake_panel import FakePanel
from .fake_panel.discovery import si_reply

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
def private_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Encrypt to a throwaway key made for the test."""
    key = X25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    original = report_crypto.encrypt

    def encrypt(report: bytes, **kwargs):
        return original(report, public_key=base64.b64encode(public).decode(), **kwargs)

    monkeypatch.setattr(diagnostics, "encrypt", encrypt)
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


@pytest.fixture(autouse=True)
def board_reply(monkeypatch: pytest.MonkeyPatch, panel: FakePanel) -> None:
    """The board answers discovery at once (no UDP in tests)."""

    async def reply(host: str, board: str, *args, **kwargs) -> bytes:
        return si_reply(panel.host, panel.port)

    monkeypatch.setattr(diagnostics, "async_board_reply", reply)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A clock the test moves by hand."""
    now = [1000.0]
    monkeypatch.setattr(diagnostics, "_now", lambda: now[0])
    return now


async def test_redacted_is_one_plain_download(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The default level: a plain report, nothing kept."""
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["level"] == "redacted"
    assert report["blocks"]
    assert "encrypted" not in report
    assert report_state(hass, entry.entry_id).snapshot is None
    text = json.dumps(report)
    assert not re.search(rf"(?<![0-9]){entry.data['pin']}(?![0-9])", text)
    assert entry.data["host"] not in text


async def test_full_downloads_encrypted_then_readable(
    hass: HomeAssistant, entry: ConfigEntry, private_key: bytes, clock
) -> None:
    """Encrypted first (with the Redacted view), then the same report readable."""
    async_set_level(hass, entry.entry_id, Level.FULL)
    first = await async_get_config_entry_diagnostics(hass, entry)
    assert first["level"] == "full"
    assert first["redacted_view"]["level"] == "redacted"
    assert first["redacted_view"]["report_id"] == first["report_id"]
    plain = json.loads(
        report_crypto.decrypt(
            first["encrypted"],
            private_key,
            report_id=first["report_id"],
            level="full",
        )
    )
    assert plain["level"] == "full"
    notifications = hass.data.get("persistent_notification") or {}
    assert notifications is not None

    second = await async_get_config_entry_diagnostics(hass, entry)
    assert PRIVATE_COPY in second
    assert second["report_id"] == first["report_id"]
    assert {k: v for k, v in second.items() if k != PRIVATE_COPY} == plain
    state = report_state(hass, entry.entry_id)
    assert state.level is Level.REDACTED
    assert state.snapshot is None

    third = await async_get_config_entry_diagnostics(hass, entry)
    assert third["level"] == "redacted"
    assert PRIVATE_COPY not in third


async def test_the_readable_copy_expires(
    hass: HomeAssistant, entry: ConfigEntry, private_key: bytes, clock
) -> None:
    """After 10 minutes the snapshot is gone and a new report is encrypted."""
    async_set_level(hass, entry.entry_id, Level.CONFIDENTIAL)
    first = await async_get_config_entry_diagnostics(hass, entry)
    clock[0] += SNAPSHOT_LIFETIME + 1
    second = await async_get_config_entry_diagnostics(hass, entry)
    assert "encrypted" in second
    assert second["report_id"] != first["report_id"]


async def enable_level_select(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Enable the Diagnostics level select (off by default); its entity ID."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_diagnostics_level"
    )
    assert entity_id is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    # Home Assistant reloads the entry for it after a short delay
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=RELOAD_AFTER_UPDATE_DELAY + 1)
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is not None
    return entity_id


def fire_after_lifetime(hass: HomeAssistant, clock: list[float]) -> None:
    """Move both clocks past the snapshot's lifetime and run due timers."""
    clock[0] += SNAPSHOT_LIFETIME + 1
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=SNAPSHOT_LIFETIME + 1)
    )


async def test_the_level_goes_back_after_10_minutes(
    hass: HomeAssistant, entry: ConfigEntry, private_key: bytes, clock
) -> None:
    """Without a second download, the timer sets Redacted and drops the report."""
    select = await enable_level_select(hass, entry)
    async_set_level(hass, entry.entry_id, Level.FULL)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "full"
    first = await async_get_config_entry_diagnostics(hass, entry)
    assert "encrypted" in first

    fire_after_lifetime(hass, clock)
    await hass.async_block_till_done()
    state = report_state(hass, entry.entry_id)
    assert state.level is Level.REDACTED
    assert state.snapshot is None
    assert hass.states.get(select).state == "redacted"

    following = await async_get_config_entry_diagnostics(hass, entry)
    assert following["level"] == "redacted"
    assert "encrypted" not in following
    assert PRIVATE_COPY not in following


async def test_a_reload_goes_back_to_redacted(
    hass: HomeAssistant,
    entry: ConfigEntry,
    private_key: bytes,
    clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reload within the 10 minutes drops the level and the waiting report."""
    select = await enable_level_select(hass, entry)
    async_set_level(hass, entry.entry_id, Level.CONFIDENTIAL)
    first = await async_get_config_entry_diagnostics(hass, entry)
    assert "encrypted" in first

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = report_state(hass, entry.entry_id)
    assert state.level is Level.REDACTED
    assert state.snapshot is None
    assert hass.states.get(select).state == "redacted"

    clock[0] += 60  # still within the 10 minutes
    following = await async_get_config_entry_diagnostics(hass, entry)
    assert following["level"] == "redacted"
    assert "encrypted" not in following
    assert PRIVATE_COPY not in following

    # The old timer is gone: a level chosen after the reload stays
    async_set_level(hass, entry.entry_id, Level.FULL)
    caplog.clear()
    fire_after_lifetime(hass, clock)
    await hass.async_block_till_done()
    assert report_state(hass, entry.entry_id).level is Level.FULL
    assert hass.states.get(select).state == "full"
    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith(("custom_", "homeassistant"))
    ]


async def test_an_unload_during_the_reads(
    hass: HomeAssistant,
    entry: ConfigEntry,
    private_key: bytes,
    clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloaded while the report is read: Redacted, and nothing waits."""
    async_set_level(hass, entry.entry_id, Level.FULL)
    original = diagnostics._async_report_input

    async def reads(*args, **kwargs):
        data = await original(*args, **kwargs)
        async_drop_report_state(hass, entry.entry_id)
        return data

    monkeypatch.setattr(diagnostics, "_async_report_input", reads)
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["level"] == "redacted"
    assert "encrypted" not in report
    assert PRIVATE_COPY not in report
    state = report_state(hass, entry.entry_id)
    assert state.level is Level.REDACTED
    assert state.snapshot is None

    # No timer was left behind: a level chosen afterwards stays
    async_set_level(hass, entry.entry_id, Level.FULL)
    fire_after_lifetime(hass, clock)
    await hass.async_block_till_done()
    assert report_state(hass, entry.entry_id).level is Level.FULL


async def test_changing_the_level_drops_the_snapshot(
    hass: HomeAssistant, entry: ConfigEntry, private_key: bytes, clock
) -> None:
    """A snapshot is only handed out at the level it was made for."""
    async_set_level(hass, entry.entry_id, Level.FULL)
    await async_get_config_entry_diagnostics(hass, entry)
    async_set_level(hass, entry.entry_id, Level.CONFIDENTIAL)
    assert report_state(hass, entry.entry_id).snapshot is None


async def test_device_diagnostics_are_the_entry_report(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The panel's device gives the same report."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    report = await async_get_device_diagnostics(hass, entry, device)
    assert report["level"] == "redacted"
    assert report["blocks"]


async def test_the_select_follows_the_level(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The select (disabled by default) offers the three levels, Redacted first."""
    state = report_state(hass, entry.entry_id)
    assert state.level is Level.REDACTED
    async_set_level(hass, entry.entry_id, Level.FULL)
    assert report_state(hass, entry.entry_id).level is Level.FULL


async def test_report_while_the_entry_is_not_loaded(
    hass: HomeAssistant, entry: ConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the entry's client a connection of its own reads the panel."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_API_CLIENT]
    await client.close()  # the board takes one client
    monkeypatch.delitem(data, DATA_API_CLIENT)
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["blocks"]
    assert report["errors"] == []
    monkeypatch.setitem(data, DATA_API_CLIENT, client)
