"""The demo panel the documentation's screenshots are taken from."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.inim_smartliving_alarm.const import DOMAIN

from .conftest import add_entry, offered, proposed
from .fake_panel import FakePanel, captures, const as c, panel_1050, panel_demo
from .test_config_flow import si_input

PLACEHOLDER = ("Area ", "Zone ", "User ", "Scenario ", "Key ")


def names(labels: dict[int, list[str]]) -> set[str]:
    return {name for values in labels.values() for name in values if name}


def test_no_names_from_captures() -> None:
    """The demo shares no name or record with the panel the captures come from."""
    demo = panel_demo()
    shared = names(demo.labels) & names(panel_1050().labels)
    assert {name for name in shared if not name.startswith(PLACEHOLDER)} == {"Master"}
    assert demo.serial_number == "0000000000000001"
    assert captures.CODE_RECORD_0_REPLY[:-1] not in demo.code_records
    assert captures.CODE_RECORD_1_REPLY[:-1] not in demo.code_records
    assert bytes(demo.physical_map) != captures.PHYSICAL_MAP_REPLY[:-1]
    assert all(event is None or not event.agent_text for event in demo.events)


def test_names_fit_the_panel() -> None:
    """Every name fits the panel's 16 characters, so none is cut on screen."""
    assert all(len(name) <= c.LABEL_LENGTH for name in names(panel_demo().labels))


async def test_what_the_setup_offers(hass: HomeAssistant, socket_enabled) -> None:
    """The items step proposes the five areas, every zone and six scenarios."""
    fake = FakePanel(panel_demo())
    await fake.start()
    try:
        result = await hass.config_entries.flow.async_init(
            "inim_smartliving_alarm", context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "smartlan_si"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], si_input(fake)
        )
        assert result["step_id"] == "items"
        assert proposed(result, "areas") == [1, 2, 3, 4, 5]
        assert proposed(result, "zones") == [*range(1, 13), 54]
        assert offered(result, "zones") == [*range(1, 13), 54]
        assert proposed(result, "scenarios") == [1, 2, 3, 4, 5, 6]
        assert offered(result, "outputs") == proposed(result, "outputs") == [21, 22, 23]
        assert result["description_placeholders"]["expanders"] == "Bedroom expander"
    finally:
        await fake.stop()


async def test_entities(hass: HomeAssistant, socket_enabled) -> None:
    """The entities the screenshots show, with the values the pages describe."""
    fake = FakePanel(panel_demo())
    await fake.start()
    try:
        entry = await add_entry(hass, fake, areas=None, zones=None, scenarios=None)
        registry = er.async_get(hass)

        def state(platform: str, kind: str):
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{entry.entry_id}_{kind}"
            )
            return hass.states.get(entity_id)

        second = state("binary_sensor", "zone_54")
        assert second.attributes["balancing"] == "double_zone"
        assert second.attributes["second_zone"] is True
        shock = state("binary_sensor", "zone_7")
        assert shock.attributes["sensor_kind"] == "shock"
        assert shock.attributes["device_class"] == "vibration"
        log = state("sensor", "event_log")
        assert log.attributes["event_log_count_current"] == 3
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await fake.stop()
