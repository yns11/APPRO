"""Working-day calendar used by every date computation of the engine.

Business rule (configurable): a *working day* is a weekday listed in ``working_weekdays``
(ISO weekday numbers, Monday = 1 … Sunday = 7) that is not a holiday / plant closure day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable


@dataclass(frozen=True)
class WorkCalendar:
    """Immutable working-day calendar.

    Parameters
    ----------
    working_weekdays:
        ISO weekdays considered open (default Monday–Friday).
    holidays:
        Closure dates (public holidays, plant shutdown days).
    """

    working_weekdays: frozenset[int] = frozenset({1, 2, 3, 4, 5})
    holidays: frozenset[dt.date] = field(default_factory=frozenset)

    @classmethod
    def from_spec(cls, weekdays: Iterable[int] | str | None = None,
                  holidays: Iterable[dt.date | str] | None = None) -> "WorkCalendar":
        """Build a calendar from loosely-typed specs (``"1,2,3,4,5"`` or ``[1, 2, 3]``)."""
        if weekdays is None:
            wd = {1, 2, 3, 4, 5}
        elif isinstance(weekdays, str):
            wd = {int(x) for x in weekdays.split(",") if x.strip()}
        else:
            wd = {int(x) for x in weekdays}
        if not wd:
            raise ValueError("A calendar needs at least one working weekday")
        hd = set()
        for h in holidays or ():
            hd.add(dt.date.fromisoformat(h) if isinstance(h, str) else h)
        return cls(frozenset(wd), frozenset(hd))

    # ------------------------------------------------------------------ predicates
    def is_working_day(self, day: dt.date) -> bool:
        return day.isoweekday() in self.working_weekdays and day not in self.holidays

    def is_open_weekday(self, day: dt.date, allowed_weekdays: frozenset[int] | None = None) -> bool:
        """Working day *and* (optionally) an allowed delivery weekday for a supplier."""
        if not self.is_working_day(day):
            return False
        return allowed_weekdays is None or day.isoweekday() in allowed_weekdays

    # ------------------------------------------------------------------ navigation
    def next_working_day(self, day: dt.date, inclusive: bool = True,
                         allowed_weekdays: frozenset[int] | None = None) -> dt.date:
        d = day if inclusive else day + dt.timedelta(days=1)
        for _ in range(0, 400):
            if self.is_open_weekday(d, allowed_weekdays):
                return d
            d += dt.timedelta(days=1)
        raise ValueError("No working day found within 400 days – check the calendar")

    def previous_working_day(self, day: dt.date, inclusive: bool = True,
                             allowed_weekdays: frozenset[int] | None = None) -> dt.date:
        d = day if inclusive else day - dt.timedelta(days=1)
        for _ in range(0, 400):
            if self.is_open_weekday(d, allowed_weekdays):
                return d
            d -= dt.timedelta(days=1)
        raise ValueError("No working day found within 400 days – check the calendar")

    def add_working_days(self, day: dt.date, n: int) -> dt.date:
        """Move ``n`` working days forward (``n`` may be negative).

        ``add_working_days(d, 0)`` returns ``d`` itself, even if it is a closure day.
        """
        if n == 0:
            return day
        step = 1 if n > 0 else -1
        remaining = abs(n)
        d = day
        while remaining > 0:
            d += dt.timedelta(days=step)
            if self.is_working_day(d):
                remaining -= 1
        return d

    def working_days_between(self, start: dt.date, end: dt.date) -> int:
        """Number of working days in ``(start, end]`` (negative when ``end < start``)."""
        if end < start:
            return -self.working_days_between(end, start)
        n = 0
        d = start
        while d < end:
            d += dt.timedelta(days=1)
            if self.is_working_day(d):
                n += 1
        return n

    def open_days_in_week(self, monday: dt.date) -> list[dt.date]:
        return [monday + dt.timedelta(days=i) for i in range(7) if self.is_working_day(monday + dt.timedelta(days=i))]


def iso_week_monday(day: dt.date) -> dt.date:
    """Monday of the ISO week containing ``day``."""
    return day - dt.timedelta(days=day.isoweekday() - 1)


def iso_week_label(day: dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


@lru_cache(maxsize=8)
def default_calendar() -> WorkCalendar:
    return WorkCalendar()
