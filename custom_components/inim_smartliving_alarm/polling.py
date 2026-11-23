"""The polling groups: which parts of the live status are read, how often.

Each group is read at its own interval. The schedule is plain logic on a
clock value it is given: it neither reads nor waits, so the coordinator
drives it and tests can move its clock by hand.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any

from .const import (
    CONF_POLL_INTERVAL_AREAS,
    CONF_POLL_INTERVAL_EVENTS,
    CONF_POLL_INTERVAL_FAULTS,
    CONF_POLL_INTERVAL_INCLUSION,
    CONF_POLL_INTERVAL_ZONES,
    DEFAULT_POLL_INTERVAL_AREAS,
    DEFAULT_POLL_INTERVAL_EVENTS,
    DEFAULT_POLL_INTERVAL_FAULTS,
    DEFAULT_POLL_INTERVAL_INCLUSION,
    DEFAULT_POLL_INTERVAL_ZONES,
)

# After this many failed reads in a row, the next try waits RETRY_INTERVAL
# instead of the shortest interval, so that a board out of reach does not
# get a connection attempt every second
FAILURES_BEFORE_RETRY_INTERVAL = 3
RETRY_INTERVAL = 5.0  # seconds
# Reads that start later than their interval this many times in a row are
# reported once: the panel answers too slowly for the interval
LATE_READS_REPORTED = 10
# A timer may fire a hair before its time; a group due within this much is
# read in that wake-up rather than in one of its own right after
DUE_TOLERANCE = 0.001  # seconds


class PollGroup(StrEnum):
    """A part of the live status read at its own interval."""

    ZONES = "zones"  # zone states and memory (with the output image)
    AREAS = "areas"  # area status and the active scenario
    INCLUSION = "inclusion"  # zone inclusion and test, dimmer levels
    EVENTS = "events"  # the event log
    FAULTS = "faults"  # the faults register (panel state flags)


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """A group's option, its default interval and the allowed range (seconds)."""

    option: str
    default: float
    minimum: float
    maximum: float


# In reading order: the groups read most often come first, so a slower
# group never delays them
GROUPS: dict[PollGroup, GroupSpec] = {
    PollGroup.ZONES: GroupSpec(
        CONF_POLL_INTERVAL_ZONES, DEFAULT_POLL_INTERVAL_ZONES, 0.5, 60
    ),
    PollGroup.AREAS: GroupSpec(
        CONF_POLL_INTERVAL_AREAS, DEFAULT_POLL_INTERVAL_AREAS, 0.5, 60
    ),
    PollGroup.INCLUSION: GroupSpec(
        CONF_POLL_INTERVAL_INCLUSION, DEFAULT_POLL_INTERVAL_INCLUSION, 5, 300
    ),
    PollGroup.EVENTS: GroupSpec(
        CONF_POLL_INTERVAL_EVENTS, DEFAULT_POLL_INTERVAL_EVENTS, 1, 300
    ),
    PollGroup.FAULTS: GroupSpec(
        CONF_POLL_INTERVAL_FAULTS, DEFAULT_POLL_INTERVAL_FAULTS, 10, 600
    ),
}


def intervals(options: Mapping[str, Any]) -> dict[PollGroup, float]:
    """Each group's interval: the option, else the default, within the range."""
    result = {}
    for group, spec in GROUPS.items():
        try:
            value = float(options.get(spec.option, spec.default))
        except (TypeError, ValueError):
            value = spec.default
        if math.isnan(value):
            value = spec.default
        result[group] = min(max(value, spec.minimum), spec.maximum)
    return result


class PollSchedule:
    """When each group is due, from when its last good read started.

    A group is due its interval after its last good read started; a group
    never read, or whose read failed, is due at once. After a failure the
    next read waits the shortest interval, and RETRY_INTERVAL (or the
    shortest interval, if longer) once reads have failed
    FAILURES_BEFORE_RETRY_INTERVAL times in a row.
    """

    def __init__(self, group_intervals: Mapping[PollGroup, float]) -> None:
        """Start with every group due."""
        self.intervals = {g: group_intervals[g] for g in GROUPS if g in group_intervals}
        self._last: dict[PollGroup, float] = {}
        self._failures = 0
        self._not_before = -math.inf
        self._late_reads: dict[PollGroup, int] = {}  # in a row, per group

    @property
    def failures(self) -> int:
        """Failed reads in a row."""
        return self._failures

    def due_at(self, group: PollGroup) -> float:
        """When a group is due (-inf: now)."""
        last = self._last.get(group)
        return -math.inf if last is None else last + self.intervals[group]

    def due(self, now: float) -> list[PollGroup]:
        """The groups due now, in reading order."""
        if now + DUE_TOLERANCE < self._not_before:
            return []
        return [
            group
            for group in self.intervals
            if self.due_at(group) <= now + DUE_TOLERANCE
        ]

    def next_wake(self, now: float) -> float:
        """Seconds from now until a group is due (0: now)."""
        earliest = min((self.due_at(group) for group in self.intervals), default=now)
        return max(earliest, self._not_before, now) - now

    def read(self, group: PollGroup, started: float) -> None:
        """A group was read; the read started then."""
        self._last[group] = started

    def read_all(self, started: float) -> None:
        """Every group was read (a full read that started then)."""
        for group in self.intervals:
            self._last[group] = started

    def failed(self, now: float) -> None:
        """A read failed: the next read waits (see the class)."""
        self._failures += 1
        shortest = min(self.intervals.values())
        wait = (
            max(RETRY_INTERVAL, shortest)
            if self._failures >= FAILURES_BEFORE_RETRY_INTERVAL
            else shortest
        )
        self._not_before = now + wait

    def succeeded(self) -> None:
        """Reads go through again: back to the intervals."""
        self._failures = 0
        self._not_before = -math.inf

    def pace(self, group: PollGroup, now: float) -> bool | None:
        """Track how late a due group's read starts.

        True once its reads have started more than one interval late
        LATE_READS_REPORTED times in a row, False the first time one is on
        time again after that, None otherwise.
        """
        due_at = self.due_at(group)
        if due_at == -math.inf:  # not read yet
            return None
        if now - due_at > self.intervals[group]:
            self._late_reads[group] = self._late_reads.get(group, 0) + 1
            return True if self._late_reads[group] == LATE_READS_REPORTED else None
        reported = self._late_reads.pop(group, 0) >= LATE_READS_REPORTED
        return False if reported else None
