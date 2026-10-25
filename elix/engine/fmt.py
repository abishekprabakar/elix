"""Small formatting helpers shared by the sentence templates.

Every sentence Elix writes is built from these and from fixed templates. No
model writes or edits them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from elix.engine.clock import parse_ts

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def cents_of(quantity: float, unit_price_cents: int) -> int:
    """quantity times a unit price, rounded half up to a whole cent."""
    value = Decimal(str(quantity)) * Decimal(unit_price_cents)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def money(cents: Optional[int]) -> str:
    if cents is None:
        return "$0.00"
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def money_short(cents: int) -> str:
    """$40 for whole dollars, otherwise $36.50."""
    return money(cents)[:-3] if cents % 100 == 0 else money(cents)


def qty(value: Optional[float]) -> str:
    if value is None:
        return "0"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


def fmt_pct(value: float) -> str:
    """18 for 18.04, 4.5 for 4.5. Used in sentences as '18 percent'."""
    rounded = round(value, 1)
    return f"{rounded:.0f}" if abs(rounded - round(rounded)) < 1e-9 or abs(rounded) >= 10 else f"{rounded:.1f}"


def plural(count: float, singular: str, plural_form: Optional[str] = None) -> str:
    word = singular if abs(count - 1) < 1e-9 else (plural_form or singular + "s")
    return f"{qty(count)} {word}"


def short_addr(address: Optional[str]) -> str:
    if not address:
        return "an unknown address"
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def short_hash(value: Optional[str], keep: int = 8) -> str:
    if not value:
        return ""
    return value if len(value) <= keep + 4 else f"{value[:keep]}…"


def duration(seconds: float) -> str:
    seconds = int(round(abs(seconds)))
    if seconds < 90:
        return plural(seconds, "second")
    minutes = round(seconds / 60)
    if minutes < 90:
        return plural(minutes, "minute")
    hours = round(seconds / 3600)
    if hours < 48:
        return plural(hours, "hour")
    return plural(round(seconds / 86400), "day")


def seconds_between(earlier: str, later: str) -> float:
    return (parse_ts(later) - parse_ts(earlier)).total_seconds()


def day(value: str | datetime) -> str:
    """Oct 9"""
    dt = parse_ts(value) if isinstance(value, str) else value
    return f"{SHORT_MONTHS[dt.month - 1]} {dt.day}"


def day_long(value: str | datetime) -> str:
    """October 9, 2026"""
    dt = parse_ts(value) if isinstance(value, str) else value
    return f"{MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def time_of_day(value: str) -> str:
    dt = parse_ts(value)
    return f"{dt.strftime('%H:%M')} UTC"


def period_name(period: str) -> str:
    """2026-09 -> September 2026"""
    return f"{MONTHS[int(period[5:7]) - 1]} {period[:4]}"


def month_name(period: str) -> str:
    return MONTHS[int(period[5:7]) - 1]


def join_words(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def possessive(name: str) -> str:
    """Atlas's, Lumen Compute's. Names are singular, so always 's."""
    return f"{name}'s"
