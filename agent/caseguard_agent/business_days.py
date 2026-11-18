"""Business-day arithmetic. Done in CODE, never by the LLM: models are unreliable at date math."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def business_days_late(promised: date, today: date, holidays: Iterable[date] = ()) -> int:
    """Weekdays strictly after `promised`, up to and including `today`, excluding holidays.

    Promised Friday, today Monday -> 1 (only Monday counts; the weekend doesn't).
    Not late yet (today <= promised) -> 0.
    """
    if today <= promised:
        return 0
    skip = set(holidays)
    days, d = 0, promised + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5 and d not in skip:
            days += 1
        d += timedelta(days=1)
    return days
