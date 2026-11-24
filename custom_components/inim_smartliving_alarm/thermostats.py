"""The keypads' thermostats: which are read, and when a keypad's
temperature is left out after it went unanswered.

Which keypads get a thermostat is the user's selection (CONF_THERMOSTATS,
see selection.py). An entry without one has not had its keypads checked
yet: the keypads whose room temperature read answers are selected then,
once (see __init__.py). No Home Assistant imports.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import CONF_THERMOSTATS, DOMAIN
from .selection import available_thermostats, selected

# A keypad whose temperature read got no answer is left out of the next
# read, then of the next 2, 4, ... reads, for at most this long
TEMPERATURE_BACKOFF_MAX = 600.0  # seconds


def thermostats_signal(entry_id: str) -> str:
    """The signal that an entry's first thermostat selection was made:
    the platforms add the entities of the thermostats selected."""
    return f"{DOMAIN}_{entry_id}_thermostats_selected"


def first_selection(
    before: Mapping[str, Any] | None, options: Mapping[str, Any]
) -> bool:
    """Whether the only change from these options (the entry's at setup)
    is its first thermostat selection: added without a reload."""
    if before is None or CONF_THERMOSTATS in before or CONF_THERMOSTATS not in options:
        return False
    return {k: v for k, v in options.items() if k != CONF_THERMOSTATS} == dict(before)


def keypads_to_check(options: Mapping[str, Any]) -> bool:
    """Whether the keypads have not been checked for a thermostat yet."""
    return CONF_THERMOSTATS not in options


def with_new_keypads(
    options: Mapping[str, Any],
    old_config: Mapping[str, Any],
    found: Iterable[int],
) -> list[int] | None:
    """The thermostat selection after the configuration was read again and
    the keypads checked (found: 1-based keypads that answered).

    The keypads found that were not enabled before are added. Those that
    were (selected or not) stay as the user left them. None when nothing
    changes, or when the keypads were never checked (the entry checks them
    at its next start).
    """
    if CONF_THERMOSTATS not in options:
        return None
    current = {int(number) for number in options[CONF_THERMOSTATS]}
    before = set(available_thermostats(old_config))
    added = {number for number in found if number not in before} - current
    return sorted(current | added) if added else None


def selected_keypads(
    options: Mapping[str, Any], config: Mapping[str, Any]
) -> list[int]:
    """The selected thermostats' keypads, 0-based."""
    return [number - 1 for number in selected(options, config, CONF_THERMOSTATS)]


def program_time(now: datetime) -> tuple[int, int]:
    """(hour, weekday) for the programs: weekday 0 = Sunday ... 6 = Saturday."""
    return now.hour, (now.weekday() + 1) % 7


@dataclass
class _Failing:
    failures: int = 0  # unanswered reads in a row
    skip: int = 0  # reads still to leave it out of


@dataclass
class TemperatureBackoff:
    """Which keypads' temperature to leave out of a read.

    interval is the seconds between reads, to cap the wait at
    TEMPERATURE_BACKOFF_MAX.
    """

    interval: float
    _keypads: dict[int, _Failing] = field(default_factory=dict)

    def skipped(self) -> frozenset[int]:
        """The keypads to leave out of this read (counts it as one read)."""
        left_out = set()
        for keypad, state in self._keypads.items():
            if state.skip > 0:
                state.skip -= 1
                left_out.add(keypad)
        return frozenset(left_out)

    def record(
        self, read: Iterable[int], failed: Collection[int]
    ) -> tuple[set[int], set[int]]:
        """The outcome of a read: (keypads failing for the first time,
        keypads answering again)."""
        longest = max(1, int(TEMPERATURE_BACKOFF_MAX // max(self.interval, 1.0)))
        first, again = set(), set()
        for keypad in read:
            state = self._keypads.get(keypad)
            if keypad in failed:
                if state is None:
                    state = self._keypads[keypad] = _Failing()
                    first.add(keypad)
                state.failures += 1
                state.skip = min(2 ** (state.failures - 1), longest)
            elif state is not None:
                del self._keypads[keypad]
                again.add(keypad)
        return first, again
