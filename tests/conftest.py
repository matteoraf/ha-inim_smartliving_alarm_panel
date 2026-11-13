"""Shared fixtures for the Inim SmartLiving Alarm tests."""

from collections.abc import AsyncGenerator

import pytest

from .fake_panel import FakePanel


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let the Home Assistant test instance load integrations from custom_components/."""
    return


@pytest.fixture
async def fake_panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """A fake SmartLiving 1050 on SmartLAN/SI, listening on a free local port."""
    panel = FakePanel()
    await panel.start()
    yield panel
    await panel.stop()
