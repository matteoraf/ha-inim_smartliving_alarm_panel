"""How decoded panel values are shown in entity attributes.

Stable English values that templates and automations can rely on: enum
members as lower-case keys, times with a short unit.
"""

from datetime import timedelta
from typing import Any

from .protocol.config import PanelTime, TimeUnit, ZoneType

# Enum members whose lower-case name is not how one would write them
_VALUE_KEYS: dict[Any, str] = {ZoneType.H24: "24_hour"}

_UNIT_TEXT = {TimeUnit.SECONDS: "s", TimeUnit.MINUTES: "min"}


def value_key(value: Any) -> Any:
    """An enum member as a stable English key; other values as they are."""
    if value in _VALUE_KEYS:
        return _VALUE_KEYS[value]
    name = getattr(value, "name", None)
    return name.lower() if isinstance(name, str) else value


def time_text(time: PanelTime) -> str:
    """A panel time with a short unit: "30 s", "2 min"."""
    return f"{time.value} {_UNIT_TEXT[time.unit]}"


def minutes_text(minutes: int) -> str:
    """A number of minutes: "3 min"."""
    return f"{minutes} min"


def duration_text(duration: timedelta) -> str:
    """Whole seconds as "20 s", anything else in milliseconds ("300 ms")."""
    milliseconds = round(duration.total_seconds() * 1000)
    if milliseconds and milliseconds % 1000 == 0:
        return f"{milliseconds // 1000} s"
    return f"{milliseconds} ms"
