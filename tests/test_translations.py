"""Consistency of the translation files with strings.json."""

import json
from pathlib import Path
from typing import Any

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
