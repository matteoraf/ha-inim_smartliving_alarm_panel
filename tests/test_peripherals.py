"""Keypads, readers, expanders and sirens enabled on the panel."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.inim_smartliving_alarm.api import SmartLivingPanel
from custom_components.inim_smartliving_alarm.const import (
    DATA_INITIAL_PANEL_CONFIG,
    DOMAIN,
)
from custom_components.inim_smartliving_alarm.peripherals import (
    enabled_peripherals,
    peripheral_placeholders,
)
from custom_components.inim_smartliving_alarm.protocol.config import (
    decode_peripherals,
)
from custom_components.inim_smartliving_alarm.protocol.errors import ProtocolError
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_1050_FW6,
)
from custom_components.inim_smartliving_alarm.stored_config import peripherals
from custom_components.inim_smartliving_alarm.transport.smartlan_si import (
    SmartLanSiTransport,
)

from .conftest import label_block
from .fake_panel import FakePanel, panel_10100
from .test_config_flow import open_options

pytestmark = pytest.mark.usefixtures("socket_enabled")

FAKE_1050 = {
    "keypads": "Tast Zona Giorno, Tast Garage",
    "readers": "PK Giorno, PK Garage, Reader 3",
    "expanders": "Exp Kitchen",
    "sirens": "Outdoor siren",
}


# --------------------------------------------------------------- decoding


def test_decode_peripherals() -> None:
    """Bit q is device q; bits beyond the model's counts are ignored."""
    data = bytearray(22)
    data[0:4] = ((1 << 0) | (1 << 19) | (1 << 20) | (1 << 31)).to_bytes(4, "little")
    data[4:10] = ((1 << 2) | (1 << 20)).to_bytes(6, "little")
    data[10:12] = ((1 << 9) | (1 << 10)).to_bytes(2, "little")
    data[12:14] = ((1 << 0) | (1 << 10)).to_bytes(2, "little")
    data[14:22] = b"\xff" * 8  # other devices, not decoded
    found = decode_peripherals(bytes(data), SMARTLIVING_1050_FW6)
    assert found.readers == (0, 19)  # 20 readers on a 1050
    assert found.expanders == (2,)
    assert found.keypads == (9,)  # 10 keypads
    assert found.sirens == (0,)  # 10 sirens


def test_decode_peripherals_length() -> None:
    """The block is 22 bytes."""
    with pytest.raises(ProtocolError):
        decode_peripherals(bytes(14), SMARTLIVING_1050_FW6)


# -------------------------------------------------------------------- API


async def read(fake: FakePanel):
    """The configuration of a fake panel."""
    panel = SmartLivingPanel(SmartLanSiTransport(fake.host, fake.port), "1234")
    try:
        return await panel.read_configuration()
    finally:
        await panel.close()


async def test_configuration_has_the_peripherals(fake_panel: FakePanel) -> None:
    """The fake 1050 has two keypads, three readers, an expander and a siren."""
    found = (await read(fake_panel)).peripherals
    assert found.keypads == (0, 1)
    assert found.readers == (0, 1, 2)
    assert found.expanders == (0,)
    assert found.sirens == (0,)


async def test_10100_peripherals() -> None:
    """The 10100's counts: 15 keypads, 30 readers, 40 expanders."""
    fake = FakePanel(panel_10100())
    await fake.start()
    try:
        found = (await read(fake)).peripherals
    finally:
        await fake.stop()
    assert found.keypads == (0, 1, 2, 14)
    assert found.readers == (0, 29)
    assert found.expanders == (0, 39)
    assert found.sirens == ()


# ---------------------------------------------------------------- storage


async def test_the_peripherals_are_stored(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Stored as read, decoded as 0-based device numbers."""
    stored = peripherals(entry.data[DATA_INITIAL_PANEL_CONFIG])
    assert stored is not None
    assert stored.keypads == (0, 1)
    assert stored.readers == (0, 1, 2)
    assert stored.expanders == (0,)
    assert stored.sirens == (0,)


def test_names_of_the_peripherals() -> None:
    """The panel's names, else the kind and number."""
    enabled = bytearray(22)  # the 1050's bitmaps
    enabled[10] = 0b101  # keypads 1 and 3
    enabled[4] = 0b10  # expander 2
    config = {
        "model_string": b"6.07 01050  ".hex(),
        "peripherals": enabled.hex(),
        "keypad_labels": label_block(["Hall", "", ""]),
        "expander_labels": label_block(["", "Kitchen"]),
    }
    assert enabled_peripherals(config) == {
        "keypads": ["Hall", "Keypad 3"],
        "readers": [],
        "expanders": ["Kitchen"],
        "sirens": [],
    }
    assert peripheral_placeholders(config) == {
        "keypads": "Hall, Keypad 3",
        "readers": "-",
        "expanders": "Kitchen",
        "sirens": "-",
    }


def test_configuration_without_the_peripherals() -> None:
    """A configuration stored by an earlier version: nothing to show."""
    assert enabled_peripherals({}) is None
    assert set(peripheral_placeholders({}).values()) == {"-"}


# ------------------------------------------------------------------ forms


async def test_setup_shows_the_peripherals(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    """The step choosing areas, zones and scenarios lists them."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": panel.host, "port": panel.port, "pin": "1234", "panel_name": "Home"},
    )
    assert result["step_id"] == "items"
    assert result["description_placeholders"] == {"panel_name": "Home", **FAKE_1050}
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_options_show_the_peripherals(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The options' items step lists them too."""
    result = await open_options(hass, entry, "items")
    assert result["step_id"] == "items"
    assert result["description_placeholders"] == FAKE_1050
    hass.config_entries.options.async_abort(result["flow_id"])


# ----------------------------------------------------------------- sensor


async def test_peripherals_sensor(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A diagnostic sensor: how many, and their names by kind."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_peripherals"
    )
    assert entity_id is not None
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    state = hass.states.get(entity_id)
    assert state.state == "7"
    assert state.attributes["keypads"] == ["Tast Zona Giorno", "Tast Garage"]
    assert state.attributes["readers"] == ["PK Giorno", "PK Garage", "Reader 3"]
    assert state.attributes["expanders"] == ["Exp Kitchen"]
    assert state.attributes["sirens"] == ["Outdoor siren"]
