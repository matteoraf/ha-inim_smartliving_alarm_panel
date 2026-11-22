"""The keypads, readers, expanders and sirens enabled on the panel.

Read with the configuration and shown in the setup, the options and a
diagnostic sensor, with the names the panel gives them.
"""

from collections.abc import Mapping
from typing import Any

from .stored_config import keypad_names, name_table, peripherals

# Kind -> name when it has none
PERIPHERAL_KINDS: dict[str, str] = {
    "keypads": "Keypad",
    "readers": "Reader",
    "expanders": "Expander",
    "sirens": "Siren",
}


def enabled_peripherals(config: Mapping[str, Any]) -> dict[str, list[str]] | None:
    """The names of the enabled devices of each kind; None if not stored."""
    stored = peripherals(config)
    if stored is None:
        return None
    found = {}
    for kind, fallback in PERIPHERAL_KINDS.items():
        names = keypad_names(config) if kind == "keypads" else name_table(config, kind)
        found[kind] = [
            (names[index] if index < len(names) else "") or f"{fallback} {index + 1}"
            for index in getattr(stored, kind)
        ]
    return found


def peripheral_placeholders(config: Mapping[str, Any]) -> dict[str, str]:
    """The enabled devices of each kind, for a form's description."""
    found = enabled_peripherals(config) or {}
    return {kind: ", ".join(found.get(kind) or []) or "-" for kind in PERIPHERAL_KINDS}
