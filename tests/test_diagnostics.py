"""Diagnostics downloads: the system report at each level, and the two-file flow."""

import base64
import json
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest

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
