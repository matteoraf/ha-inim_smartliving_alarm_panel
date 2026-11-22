"""The user code the PIN belongs to, by name: shown, never the PIN itself.

The panel is checked for it whenever the integration connects; the names
come from the stored configuration.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .api import SmartLivingPanel
from .stored_config import area_label, name_table

# Code types of the code records (firmware 5.1 and later)
CODE_TYPES = {0: "standard", 1: "manager", 2: "master"}


@dataclass(frozen=True, slots=True)
class UserCodeInfo:
    """Name, 1-based number, areas (by name) and type of a user code."""

    name: str
    number: int
    areas: list[str]
    code_type: str | None


def user_code_info(
    panel: SmartLivingPanel, config: Mapping[str, Any]
) -> UserCodeInfo | None:
    """The user code of the PIN; None if it matches none (or not checked yet)."""
    index, code = panel.user_code_index, panel.user_code
    if index is None or code is None:
        return None
    names = name_table(config, "codes")
    name = (names[index] if index < len(names) else "") or f"Code {index + 1}"
    return UserCodeInfo(
        name=name,
        number=index + 1,
        areas=[area_label(config, area) for area in sorted(code.areas)],
        code_type=CODE_TYPES.get(code.code_type)
        if code.code_type is not None
        else None,
    )


def user_code_placeholders(
    panel: SmartLivingPanel | None, config: Mapping[str, Any]
) -> dict[str, str]:
    """The user code and its areas, for a form's description ("-" if none)."""
    info = user_code_info(panel, config) if panel is not None else None
    if info is None:
        return {"user_code": "-", "user_areas": "-"}
    return {
        "user_code": f"{info.number} - {info.name}",
        "user_areas": ", ".join(info.areas) or "-",
    }
