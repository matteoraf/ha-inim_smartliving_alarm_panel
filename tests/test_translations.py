"""Consistency of the translation files with strings.json and the code."""

import ast
import json
from pathlib import Path
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml_dict
import pytest

from custom_components.inim_smartliving_alarm.const import DOMAIN
from custom_components.inim_smartliving_alarm.errors import REFUSAL_REASONS

COMPONENT = (
    Path(__file__).parent.parent / "custom_components" / "inim_smartliving_alarm"
)


def load(path: Path) -> dict[str, Any]:
    """Read a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Dotted paths of all the strings in a translation file."""
    paths = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths |= keys(value, f"{path}.")
        else:
            paths.add(path)
    return paths


def test_english_matches_strings() -> None:
    """Custom integrations read translations/en.json, not strings.json."""
    assert load(COMPONENT / "translations" / "en.json") == load(
        COMPONENT / "strings.json"
    )


def test_italian_has_every_string() -> None:
    """The Italian file translates exactly the strings in strings.json."""
    assert keys(load(COMPONENT / "translations" / "it.json")) == keys(
        load(COMPONENT / "strings.json")
    )


PLACEHOLDER = re.compile(r"{(\w+)}")


def leaves(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Every string of a translation file, by dotted path."""
    found = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            found |= leaves(value, f"{path}.")
        else:
            found[path] = value
    return found


def test_italian_keeps_every_placeholder() -> None:
    """Each Italian text has the same {placeholders} as the English one."""
    english = leaves(load(COMPONENT / "strings.json"))
    italian = leaves(load(COMPONENT / "translations" / "it.json"))
    for path, text in english.items():
        assert set(PLACEHOLDER.findall(italian[path])) == set(
            PLACEHOLDER.findall(text)
        ), path


def test_no_empty_text() -> None:
    """No translation is left empty."""
    for name in ("strings.json", "translations/en.json", "translations/it.json"):
        for path, text in leaves(load(COMPONENT / name)).items():
            assert isinstance(text, str), (name, path)
            assert text.strip(), (name, path)


def _literals(value: ast.expr) -> set[str]:
    """A literal string, or the literals of a conditional expression."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, ast.IfExp):
        return _literals(value.body) | _literals(value.orelse)
    return set()  # built at run time (refused_<reason>): checked separately


def _raised_keys() -> set[str]:
    """The exception and issue translation keys the code names literally."""
    found = set()
    for path in COMPONENT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "translation_key":
                found |= _literals(node.value)
            # The not-loaded keys are passed to a helper by name
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("entry_not_loaded")
            ):
                found.add(node.value)
    return found


def test_every_raised_key_is_translated() -> None:
    """Each exception or issue the code raises has its text."""
    strings = load(COMPONENT / "strings.json")
    known = set(strings["exceptions"]) | set(strings["issues"])
    raised = _raised_keys() | {f"refused_{reason}" for reason in REFUSAL_REASONS}
    raised.add("refused_unknown")
    assert raised, "no key found: the search is broken"
    assert raised - known == set()


@pytest.mark.usefixtures("socket_enabled")
async def test_actions_match_services_yaml(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """The actions registered are exactly those described in services.yaml."""
    described = load_yaml_dict(str(COMPONENT / "services.yaml"))
    assert set(hass.services.async_services().get(DOMAIN, {})) == set(described)
