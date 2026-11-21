"""Texts the integration writes itself: notifications and form choices.

Home Assistant translates exceptions, entities and forms, but a persistent
notification takes plain text. These texts are kept with the exception
messages in the translations (keys starting with notification_ or choice_)
and looked up here, in Home Assistant's language.
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

ENGLISH = "en"
_PREFIX = f"component.{DOMAIN}.exceptions."
_SUFFIX = ".message"


class Texts:
    """The integration's texts in one language, English where there is none."""

    def __init__(self, strings: Mapping[str, str], english: Mapping[str, str]) -> None:
        """Keep the texts of the language and the English ones."""
        self._strings = strings
        self._english = english

    def __call__(self, key: str, **placeholders: Any) -> str:
        """A text with its placeholders filled in."""
        template = self._strings.get(key) or self._english[key]
        return template.format(**placeholders)

    def translated(self, key: str) -> bool:
        """Whether a text differs from its English one in this language."""
        return self._strings.get(key, self._english.get(key)) != self._english.get(key)

    def action(self, key: str, english: str, **placeholders: Any) -> str:
        """What was attempted, e.g. arm away; the English text as given."""
        full_key = f"notification_action_{key}"
        if not self.translated(full_key):
            return english
        return self(full_key, **placeholders)

    def error(self, err: HomeAssistantError) -> str:
        """An error's message in this language; as raised in English."""
        key = err.translation_key
        if key is None or not self.translated(key):
            return str(err)
        return self(key, **(err.translation_placeholders or {}))


async def async_texts(hass: HomeAssistant, language: str | None = None) -> Texts:
    """The texts in a language: Home Assistant's own unless one is given."""
    language = language or hass.config.language or ENGLISH
    strings = await _async_strings(hass, language)
    english = strings if language == ENGLISH else await _async_strings(hass, ENGLISH)
    return Texts(strings, english)


async def _async_strings(hass: HomeAssistant, language: str) -> dict[str, str]:
    flat = await async_get_translations(hass, language, "exceptions", {DOMAIN})
    return {
        key[len(_PREFIX) : -len(_SUFFIX)]: text
        for key, text in flat.items()
        if key.startswith(_PREFIX) and key.endswith(_SUFFIX)
    }
