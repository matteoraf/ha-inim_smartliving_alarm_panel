"""Shared fixtures for the Inim SmartLiving Alarm tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let the Home Assistant test instance load integrations from custom_components/."""
    return
