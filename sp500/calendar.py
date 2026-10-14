"""NYSE trading calendar (full-day holidays only) and 'last complete trading day' logic. No network, no deps."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.easter import easter

NY = ZoneInfo("America/New_York")
CLOSE_PLUS = time(17, 30)  # daily bars are treated as final from 17:30 New York time


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    return d


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    """Saturday holidays are observed Friday; Sunday holidays Monday (NYSE rule; New Year's Saturday is not moved)."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    h = {
        _nth_weekday(year, 1, 0, 3),            # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),            # Washington's Birthday
        easter(year) - timedelta(days=2),       # Good Friday
        _last_weekday(year, 5, 0),              # Memorial Day
        _nth_weekday(year, 9, 0, 1),            # Labor Day
        _nth_weekday(year, 11, 3, 4),           # Thanksgiving
    }
    for d in (date(year, 6, 19), date(year, 7, 4), date(year, 12, 25)):  # Juneteenth, Independence Day, Christmas
        h.add(_observed(d))
    ny = date(year, 1, 1)
    if ny.weekday() != 5:  # when Jan 1 is a Saturday the NYSE stays open on the previous Friday
        h.add(_observed(ny))
    return h


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def last_complete_trading_day(now: datetime | None = None) -> date:
    """Most recent trading day whose daily close should already be published (utc `now`)."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    d = now.date()
    if is_trading_day(d) and now.time() >= CLOSE_PLUS:
        return d
    return previous_trading_day(d)


def market_open_today(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    return is_trading_day(now.date())


def trading_days_between(last_have: date, target: date) -> int:
    """Number of trading days after `last_have` up to and including `target` (0 if up to date)."""
    n, d = 0, last_have
    while d < target:
        d += timedelta(days=1)
        n += is_trading_day(d)
    return n
