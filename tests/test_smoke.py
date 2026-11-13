"""Smoke tests: Home Assistant can find, parse and import the integration."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.inim_smartliving_alarm.const import DOMAIN


async def test_manifest_is_valid(hass: HomeAssistant) -> None:
    """The manifest is found and declares what the integration relies on."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.config_flow is True
    assert integration.iot_class == "local_polling"


async def test_component_imports(hass: HomeAssistant) -> None:
    """Home Assistant can import the integration module."""
    integration = await async_get_integration(hass, DOMAIN)
    component = await integration.async_get_component()

    assert hasattr(component, "async_setup_entry")
    assert hasattr(component, "async_unload_entry")
