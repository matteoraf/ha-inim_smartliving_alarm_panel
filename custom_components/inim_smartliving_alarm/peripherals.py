"""The keypads, readers, expanders and sirens enabled on the panel.

Read with the configuration and shown in the setup, the options and a
diagnostic sensor, with the names the panel gives them.
"""

from collections.abc import Mapping
from typing import Any

from .const import (
    KEY_INIT_EXPANDERS,
    KEY_INIT_KEYBOARDS,
    KEY_INIT_NAMES,
    KEY_INIT_PERIPHERALS,
    KEY_INIT_READERS,
    KEY_INIT_SIRENS,
)
from .selection import keypad_names

# Kind -> (its name table in the stored configuration, name when it has none)
PERIPHERAL_KINDS: dict[str, tuple[str, str]] = {
    "keypads": (KEY_INIT_KEYBOARDS, "Keypad"),
    "readers": (KEY_INIT_READERS, "Reader"),
    "expanders": (KEY_INIT_EXPANDERS, "Expander"),
    "sirens": (KEY_INIT_SIRENS, "Siren"),
}


def enabled_peripherals(config: Mapping[str, Any]) -> dict[str, list[str]] | None:
    """The names of the enabled devices of each kind; None if not stored."""
    stored = config.get(KEY_INIT_PERIPHERALS)
    if not isinstance(stored, Mapping):
        return None
    found = {}
    for kind, (table, fallback) in PERIPHERAL_KINDS.items():
        names = (
            keypad_names(config)
            if table == KEY_INIT_KEYBOARDS
            else (config.get(table) or {}).get(KEY_INIT_NAMES) or []
        )
        found[kind] = [
            (names[number - 1] if number <= len(names) else "")
            or f"{fallback} {number}"
            for number in stored.get(kind) or []
        ]
    return found


def peripheral_placeholders(config: Mapping[str, Any]) -> dict[str, str]:
    """The enabled devices of each kind, for a form's description."""
    found = enabled_peripherals(config) or {}
    return {kind: ", ".join(found.get(kind) or []) or "-" for kind in PERIPHERAL_KINDS}
