"""The world clock.

The engine never reads the wall clock directly. Seeding and the eval move this
clock to each event time, which keeps every run deterministic. The server uses a
live clock that starts at the world's "now" and then ticks with real time, so
timestamps keep moving forward during a demo.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

DEFAULT_NOW = "2026-10-24T16:00:00Z"


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def period_of(value: str | datetime | date) -> str:
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    return value[:7]


def next_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}-{month:02d}"


def period_start(period: str) -> datetime:
    return datetime(int(period[:4]), int(period[5:7]), 1, tzinfo=timezone.utc)


def period_end_date(period: str) -> str:
    """Last calendar day of the period as YYYY-MM-DD."""
    last = period_start(next_period(period)) - timedelta(days=1)
    return last.strftime("%Y-%m-%d")


class Clock:
    """A settable clock. `live=True` lets time pass at wall speed from the base."""

    def __init__(self, now: str | datetime = DEFAULT_NOW, live: bool = False):
        self._base = parse_ts(now)
        self._live = live
        self._started = time.monotonic()
        self._offset = 0.0

    @property
    def live(self) -> bool:
        return self._live

    def now_dt(self) -> datetime:
        elapsed = time.monotonic() - self._started if self._live else 0.0
        return (self._base + timedelta(seconds=self._offset + elapsed)).replace(microsecond=0)

    def now(self) -> str:
        return iso(self.now_dt())

    def today(self) -> str:
        return self.now_dt().strftime("%Y-%m-%d")

    def period(self) -> str:
        return period_of(self.now_dt())

    def set(self, when: str | datetime) -> None:
        self._base = parse_ts(when)
        self._offset = 0.0
        self._started = time.monotonic()

    def advance(self, seconds: float) -> str:
        self._offset += seconds
        return self.now()
