"""The documentation's reference tables match the integration.

The tables on the site are written by hand. These tests read them and
compare them with the integration both ways: every action, field, event,
option, entity and attribute is documented, and nothing documented is gone.
"""

from collections.abc import AsyncGenerator
from contextlib import suppress
import json
from pathlib import Path
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events
import yaml

from custom_components.inim_smartliving_alarm import const
from custom_components.inim_smartliving_alarm.attribute_values import value_key
from custom_components.inim_smartliving_alarm.const import DATA_COORDINATOR, DOMAIN
from custom_components.inim_smartliving_alarm.errors import REFUSAL_REASONS
from custom_components.inim_smartliving_alarm.polling import (
    GROUPS,
    SLOW_POLLING_DURATION,
    SLOW_POLLING_INTERVAL,
)
from custom_components.inim_smartliving_alarm.protocol.config import (
    Balancing,
    SensorKind,
    TerminalType,
    ZoneType,
)

from .conftest import items_form, offered
from .fake_panel import FakePanel, const as c, panel_demo
from .test_config_flow import si_input

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INTEGRATION = ROOT / "custom_components" / DOMAIN
EN = json.loads((INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8"))
IT = json.loads((INTEGRATION / "translations" / "it.json").read_text(encoding="utf-8"))
# The pages of each language and the texts of its UI: the Italian pages quote
# the Italian UI.
LANGUAGES = {"en": (DOCS, EN), "it": (DOCS / "it", IT)}
# Words the tables use, in each language
YES_NO = {"en": ("yes", "no"), "it": ("sì", "no")}
DISABLED = {
    "en": "disabled by default",
    "it": "disattivata per impostazione predefinita",
}
# The titles of the collapsible blocks on the Italian pages
BLOCK_TITLES_IT = {
    "Attributes of the area entities": "Attributi delle entità delle aree",
    "Attributes of the zone entities": "Attributi delle entità delle zone",
    "Attributes of the scenario entities": "Attributi delle entità degli scenari",
    "The event log in detail": "Il registro eventi nel dettaglio",
    "The active scenario in detail": "Lo scenario attivo nel dettaglio",
    "Details and attributes": "Dettagli e attributi",
    "Attributes of the outputs": "Attributi delle uscite",
}
SERVICES = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
SOURCE = "\n".join(
    path.read_text(encoding="utf-8") for path in INTEGRATION.rglob("*.py")
)

# Attributes Home Assistant adds itself
HA_ATTRIBUTES = {
    "friendly_name",
    "icon",
    "device_class",
    "options",
    "supported_features",
    "code_format",
    "changed_by",
    "code_arm_required",
    "brightness",
    "color_mode",
    "supported_color_modes",
    "restored",
    # Climate entities and sensors with a unit
    "hvac_modes",
    "hvac_action",
    "min_temp",
    "max_temp",
    "target_temp_step",
    "preset_modes",
    "preset_mode",
    "current_temperature",
    "temperature",
    "state_class",
    "unit_of_measurement",
}

pytestmark = pytest.mark.usefixtures("socket_enabled")


# ------------------------------------------------------------------ parsing


def page(name: str, language: str = "en") -> str:
    return (LANGUAGES[language][0] / f"{name}.md").read_text(encoding="utf-8")


def block_in(name: str, title: str, language: str) -> str:
    """The collapsible block with this (English) title on a page."""
    if language == "it":
        title = BLOCK_TITLES_IT[title]
    return block(page(name, language), title)


def section_with_id(text: str, heading_id: str) -> str:
    """The text under the heading with this id, to the next one of the same
    or a higher level: the same section in every language."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        match = re.match(r"(#+) .* \{ #([\w-]+) \}$", line)
        if match and match.group(2) == heading_id:
            level = len(match.group(1))
            body = []
            for line in lines[start + 1 :]:
                other = re.match(r"(#+) ", line)
                if other and len(other.group(1)) <= level:
                    break
                body.append(line)
            return "\n".join(body)
    raise AssertionError(f"no heading with id {heading_id!r}")


def number(value: float, language: str) -> str:
    """A number as the pages write it: a decimal comma in Italian."""
    text = f"{value:g}"
    return text.replace(".", ",") if language == "it" else text


def value_range(low: float, high: float, language: str) -> str:
    """A range of values as the option table writes it."""
    if language == "it":
        return f"da {number(low, language)} a {number(high, language)}"
    return f"{number(low, language)} to {number(high, language)}"


def section(text: str, heading: str) -> str:
    """The text under the heading that starts with this text, to the next one
    of the same or a higher level."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        match = re.match(r"(#+) (.*)", line)
        if match and match.group(2).startswith(heading):
            level = len(match.group(1))
            body = []
            for line in lines[start + 1 :]:
                other = re.match(r"(#+) ", line)
                if other and len(other.group(1)) <= level:
                    break
                body.append(line)
            return "\n".join(body)
    raise AssertionError(f"no heading {heading!r}")


def block(text: str, title: str) -> str:
    """The indented body of the collapsible block with this title."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if line.startswith("??? ") and f'"{title}"' in line:
            body = []
            for line in lines[start + 1 :]:
                if line.strip() and not line.startswith("    "):
                    break
                body.append(line[4:])
            return "\n".join(body)
    raise AssertionError(f"no block {title!r}")


def tables(text: str) -> list[list[list[str]]]:
    """The tables in a text, as rows of cells, without the header rows."""
    found: list[list[list[str]]] = []
    current: list[list[str]] | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if current is None:
                current = []  # the header row
            elif not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                current.append(cells)
        elif current is not None:
            found.append(current)
            current = None
    if current is not None:
        found.append(current)
    return found


def names(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def first_column(rows: list[list[str]]) -> set[str]:
    return {name for row in rows for name in names(row[0])}


def row_for(rows: list[list[str]], name: str) -> list[str]:
    for row in rows:
        if name in names(row[0]) or f"**{name}**" in row[0]:
            return row
    raise AssertionError(f"no row for {name!r}")


# ------------------------------------------------------------------ actions


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("action", sorted(SERVICES))
def test_every_action_and_field_is_documented(action: str, language: str) -> None:
    """Each action has its section, with a row for each field, and no more."""
    anchor = "action-" + action.replace("_", "-")
    text = section_with_id(page("actions-events", language), anchor)
    yes, no = YES_NO[language]
    assert f"`{DOMAIN}.{action}`" in text
    rows = tables(text)[0]
    spec = SERVICES[action]
    fields = spec.get("fields", {})
    documented = {name for row in rows for name in names(row[0])}
    if "target" in spec:
        assert any(row[0].startswith("target") for row in rows)
        documented -= {"entity_id"}
    assert documented == set(fields)
    for field, details in fields.items():
        row = row_for(rows, field)
        assert row[1] == (yes if details.get("required") else no), field
        if "default" in details:
            assert f"`{json.dumps(details['default'])}`" == row[3], field


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_other_actions_are_documented(language: str) -> None:
    text = page("actions-events", language)
    documented = set(re.findall(rf"`{DOMAIN}\.([a-z_]+)`", text))
    assert documented <= set(SERVICES)


@pytest.mark.parametrize("language", LANGUAGES)
def test_action_names_match_the_ui(language: str) -> None:
    """Each action's heading is its name in the UI of the page's language."""
    text = page("actions-events", language)
    for action in SERVICES:
        anchor = "action-" + action.replace("_", "-")
        name = LANGUAGES[language][1]["services"][action]["name"]
        assert f"### {name} {{ #{anchor} }}" in text, (action, name)


@pytest.mark.parametrize("language", LANGUAGES)
def test_refusal_reasons_are_documented(language: str) -> None:
    """Every reason the panel can give, in the event table and on Troubleshooting."""
    events = page("actions-events", language)
    trouble = section_with_id(
        page("troubleshooting", language), "when-the-panel-refuses-a-command"
    )
    for reason in REFUSAL_REASONS:
        assert f"`{reason}`" in events, reason
        assert f"`{reason}`" in trouble, reason


# ------------------------------------------------------------------ options


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_option_is_documented(language: str) -> None:
    """Each option of the setup and the options flow has a row, by its label
    in the page's language."""
    texts = LANGUAGES[language][1]
    rows = tables(section_with_id(page("options", language), "all-options"))[0]
    labels = {
        *texts["config"]["step"]["initial_options"]["data"].values(),
        *texts["options"]["step"]["general"]["data"].values(),
        *texts["options"]["step"]["polling"]["data"].values(),
        texts["options"]["step"]["area_switches"]["title"],
    }
    documented = {re.sub(r"\*\*(.*)\*\*", r"\1", row[0]) for row in rows}
    assert documented == labels


@pytest.mark.parametrize("language", LANGUAGES)
def test_option_defaults_and_ranges(language: str) -> None:
    texts = LANGUAGES[language][1]
    polling = texts["options"]["step"]["polling"]["title"]
    rows = tables(section_with_id(page("options", language), "all-options"))[0]
    for spec in GROUPS.values():
        row = row_for(rows, texts["options"]["step"]["polling"]["data"][spec.option])
        assert row[1] == polling
        assert row[2] == value_range(spec.minimum, spec.maximum, language)
        assert row[3] == number(spec.default, language)
    for spec in (SLOW_POLLING_INTERVAL, SLOW_POLLING_DURATION):
        row = row_for(rows, texts["options"]["step"]["polling"]["data"][spec.option])
        assert row[1] == f"{polling} (SmartLAN/SI)"
        assert row[2] == value_range(spec.minimum, spec.maximum, language)
        assert row[3] == number(spec.default, language)
    general = texts["options"]["step"]["general"]["data"]
    events = row_for(rows, general["event_log_size"])
    assert events[2] == value_range(1, const.SYSTEM_MAX_EVENT_LOG_SIZE, language)
    assert events[3] == str(const.DEFAULT_EVENT_LOG_SIZE)
    for key in ("zones_not_ready", "area_switches_default_arm_mode"):
        row = row_for(rows, general[key])
        for label in texts["selector"][key]["options"].values():
            assert f"*{label}*" in row[2], label


# ------------------------------------------------------------ demo panel


async def set_up(hass: HomeAssistant, fake: FakePanel) -> ConfigEntry:
    """The demo panel with every area, zone, scenario, output and thermostat
    imported."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "smartlan_si"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], si_input(fake)
    )
    kinds = ("areas", "zones", "scenarios", "outputs", "thermostats")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        items_form(result, {kind: offered(result, kind) for kind in kinds}),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "event_log_size": 50,
            "scenario_arm_home": "none",
            "scenario_arm_away": "0",
            "scenario_arm_night": "1",
            "scenario_arm_vacation": "none",
            "scenario_disarm": "2",
        },
    )
    await hass.async_block_till_done()
    return result["result"]


@pytest.fixture
async def demo(hass: HomeAssistant) -> AsyncGenerator[ConfigEntry]:
    fake = FakePanel(panel_demo())
    await fake.start()
    entry = await set_up(hass, fake)
    hass.data["docs_fake"] = fake
    yield entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    await fake.stop()


def suffix(entity_id: str) -> str:
    """domain.object_id with every number as N."""
    return re.sub(r"\d+", "N", entity_id)


def is_output(entry: er.RegistryEntry) -> bool:
    return "terminal_output" in entry.unique_id or "fixed_output" in entry.unique_id


def documented_ids(language: str = "en") -> set[str]:
    """The ID endings of the entity tables, without outputs."""
    return {
        name
        for rows in tables(page("entities", language))
        for name in first_column(rows)
        if "." in name and not name.endswith("…_")
    }


def matches(entity_id: str, pattern: str) -> bool:
    domain, _, end = pattern.partition(".…")
    return entity_id.startswith(f"{domain}.") and suffix(entity_id).endswith(end)


# ---------------------------------------------------------------- entities


@pytest.mark.parametrize("language", LANGUAGES)
async def test_every_entity_is_documented(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    """Each entity matches one documented ID ending, and each ending is used."""
    patterns = documented_ids(language)
    used: set[str] = set()
    for entry in er.async_entries_for_config_entry(er.async_get(hass), demo.entry_id):
        if is_output(entry):
            text = page("entities", language)
            assert f"`{entry.domain}.…_`" in text, entry.entity_id
            continue
        found = {pattern for pattern in patterns if matches(entry.entity_id, pattern)}
        # The alarm panel's pattern matches any ID of its domain
        if len(found) > 1:
            found = {pattern for pattern in found if not pattern.endswith(".…")}
        assert len(found) == 1, (entry.entity_id, found)
        used |= found
    assert used == patterns


@pytest.mark.parametrize("language", LANGUAGES)
async def test_categories_and_disabled_entities(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    """The diagnostic table lists exactly the entities with a category, and
    only the entities disabled by default say so."""
    text = page("entities", language)
    diagnostic = first_column(
        tables(section_with_id(text, "diagnostic-and-configuration-entities"))[0]
    )
    rows = [row for found in tables(text) for row in found]
    for entry in er.async_entries_for_config_entry(er.async_get(hass), demo.entry_id):
        if is_output(entry):
            continue
        (pattern,) = [
            pattern
            for pattern in documented_ids(language)
            if matches(entry.entity_id, pattern) and not pattern.endswith(".…")
        ] or ["alarm_control_panel.…"]
        assert (pattern in diagnostic) == (entry.entity_category is not None), pattern
        row = row_for(rows, pattern)
        disabled = DISABLED[language] in " ".join(row)
        assert disabled == (entry.disabled_by is not None), pattern


@pytest.mark.parametrize("language", LANGUAGES)
def test_entity_names_match_the_ui(language: str) -> None:
    """The names in the entity tables are the names the UI shows, in the
    page's language: N for the number, the item's name in brackets."""
    shown = {
        re.sub(r" \(\{label\}\)", "", name).replace("{number}", "N")
        for platform in LANGUAGES[language][1]["entity"].values()
        for entity in platform.values()
        if (name := entity.get("name"))
    }
    documented = set()
    for rows in tables(page("entities", language)):
        for row in rows:
            ids = names(row[0])
            if (
                len(row) > 1
                and ids
                and "." in ids[0]
                and not ids[0].endswith(("…", "…_"))
            ):
                documented.add(re.sub(r" \(\*[^*]+\*\)$", "", row[1]))
    assert documented
    assert documented <= shown, documented - shown


ATTRIBUTE_TABLES = {
    "_area_": ("entities", "Attributes of the area entities"),
    "_zone_": ("entities", "Attributes of the zone entities"),
    "_scenario_": ("entities", "Attributes of the scenario entities"),
    "_event_log": ("entities", "The event log in detail"),
    "_active_scenario": ("entities", "The active scenario in detail"),
    "_user_code": ("entities", "Details and attributes"),
    "_peripherals": ("entities", "Details and attributes"),
    "_slow_polling": ("entities", "Details and attributes"),
}


def documented_attributes(entity_id: str, output: bool, language: str) -> set[str]:
    if output:
        title = "Attributes of the outputs"
    else:
        title = next(
            (title for key, (_, title) in ATTRIBUTE_TABLES.items() if key in entity_id),
            None,
        )
        if title is None:
            return set()
    return first_column(tables(block_in("entities", title, language))[0])


@pytest.mark.parametrize("language", LANGUAGES)
async def test_every_attribute_is_documented(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    """Each attribute an entity shows is in its table."""
    for entry in er.async_entries_for_config_entry(er.async_get(hass), demo.entry_id):
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        shown = set(state.attributes) - HA_ATTRIBUTES
        documented = documented_attributes(entry.entity_id, is_output(entry), language)
        assert shown <= documented, (entry.entity_id, shown - documented)


@pytest.mark.parametrize("language", LANGUAGES)
async def test_event_log_items_are_documented(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    (log,) = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_event_log")
    ]
    keys = {key for item in log.attributes["event_log"] for key in item}
    documented = first_column(
        tables(block_in("entities", "The event log in detail", language))[1]
    )
    assert keys <= documented


@pytest.mark.parametrize("language", LANGUAGES)
def test_documented_attributes_exist(language: str) -> None:
    """Every attribute the tables name is still in the integration's code."""
    titles = {title for _, title in ATTRIBUTE_TABLES.values()} | {
        "Attributes of the outputs"
    }
    for title in titles:
        for rows in tables(block_in("entities", title, language)):
            for name in first_column(rows):
                assert f'"{name}"' in SOURCE, name


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("attribute", "members"),
    [
        ("zone_type", list(ZoneType)),
        ("balancing", list(Balancing)),
        ("terminal_type", list(TerminalType)),
        ("sensor_kind", [kind.value for kind in SensorKind]),
    ],
)
def test_every_attribute_value_is_documented(
    attribute: str, members: list, language: str
) -> None:
    """Every value the code can give these attributes is in their row."""
    title = "Attributes of the zone entities"
    rows = tables(block_in("entities", title, language))[0]
    row = row_for(rows, attribute)
    for member in members:
        assert f"`{value_key(member)}`" in row[2], (attribute, member)


# ------------------------------------------------------------------- events


@pytest.mark.parametrize("language", LANGUAGES)
async def test_events_have_the_documented_fields(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    """Both events, fired for real, carry the fields their tables list."""
    fake: FakePanel = hass.data["docs_fake"]
    not_ready = async_capture_events(hass, f"{DOMAIN}_zones_not_ready")
    failed = async_capture_events(hass, f"{DOMAIN}_scenario_activation_failed")
    fake.state.zone_states[2] = c.ZONE_ALARM  # Kitchen window open, area 1
    coordinator = hass.data[DOMAIN][demo.entry_id][DATA_COORDINATOR]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    switch = registry.async_get_entity_id(
        "switch", DOMAIN, f"{demo.entry_id}_area_switch_1"
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch}, blocking=True
        )
    button = registry.async_get_entity_id(
        "button", DOMAIN, f"{demo.entry_id}_scenario_button_0"
    )
    with suppress(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    await hass.async_block_till_done()
    assert not_ready and failed

    text = page("actions-events", language)
    for events, anchor in (
        (not_ready, "event-zones-not-ready"),
        (failed, "event-scenario-activation-failed"),
    ):
        body = section_with_id(text, anchor)
        assert f"`{events[0].event_type}`" in body
        rows = tables(body)[0]
        for event in events:
            assert set(event.data) == first_column(rows), event.event_type
            for key, value in event.data.items():
                if isinstance(value, list):
                    for item in value:
                        for inner in item:
                            assert f"`{inner}`" in row_for(rows, key)[1], (key, inner)


@pytest.mark.parametrize("language", LANGUAGES)
async def test_event_log_card_renders(
    hass: HomeAssistant, demo: ConfigEntry, language: str
) -> None:
    """The Markdown card on Actions and events shows the demo's events."""
    body = section_with_id(
        page("actions-events", language), "the-event-log-on-a-dashboard"
    )
    card = yaml.safe_load(re.search(r"```yaml\n(.*?)```", body, re.S).group(1))
    (log,) = [
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_event_log")
    ]
    content = card["content"].replace("sensor.home_event_log", log)
    rendered = Template(content, hass).async_render(parse_result=False)
    assert "Area disarmed" in rendered  # description_local, English here
    assert "Family" in rendered
