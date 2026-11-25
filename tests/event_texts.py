"""The event log's texts read from the translation files, for tests.

The integration passes Home Assistant's translations to EventLog; tests
that run without Home Assistant read the same texts from the files here.
"""

from collections.abc import Mapping
from datetime import tzinfo
from functools import cache
import json
from pathlib import Path
from typing import Any

from custom_components.inim_smartliving_alarm.api import LoggedEvent
from custom_components.inim_smartliving_alarm.event_log import (
    EventLog,
    EventTexts,
    fill_text,
)
from custom_components.inim_smartliving_alarm.protocol.model import PanelModel

TRANSLATIONS = (
    Path(__file__).parents[1]
    / "custom_components"
    / "inim_smartliving_alarm"
    / "translations"
)


class BundledTexts:
    """The texts of one of the integration's translation files."""

    def __init__(self, language: str = "en") -> None:
        """Load the event texts of a language, English where there are none."""
        self._strings = _messages(language)
        self._english = _messages("en")

    def __call__(self, key: str, **placeholders: Any) -> str:
        """A text with its placeholders filled in (see fill_text)."""
        return fill_text(key, placeholders, self._strings, self._english)


@cache
def _messages(language: str) -> dict[str, str]:
    """A translation file's messages; none if it cannot be read."""
    path = TRANSLATIONS / f"{language}.json"
    try:
        exceptions = json.loads(path.read_text(encoding="utf-8")).get("exceptions", {})
        return {key: value["message"] for key, value in exceptions.items()}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return {}


def event_attributes(
    logged: LoggedEvent,
    stored: Mapping[str, Any],
    time_zone: tzinfo | None,
    english: EventTexts | None = None,
    local: EventTexts | None = None,
    model: PanelModel | None = None,
) -> dict[str, Any]:
    """One event of the event log sensor, with the bundled texts by default
    (English for both descriptions)."""
    english = english or BundledTexts()
    return EventLog(stored, model, english, local or english).attributes(
        logged, time_zone
    )
