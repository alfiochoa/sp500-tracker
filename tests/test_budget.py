import logging
from datetime import date, datetime, timezone

from sp500.budget import DAILY_CAP, plan_fetch, record_usage, remaining
from sp500.calendar import is_trading_day, last_complete_trading_day, market_open_today, nyse_holidays, trading_days_between

TARGET = date(2026, 10, 13)


def test_only_stale_symbols_are_fetched_never_fresh_ones():
    last = {"FRESH": TARGET, "NEWER": date(2026, 10, 14), "OLD": date(2026, 10, 9), "NEVER": None}
    assert plan_fetch(last, TARGET, 100) == ["NEVER", "OLD"]


def test_budget_caps_the_plan_and_prioritises_missing_then_stalest():
    last = {"A": date(2026, 9, 1), "B": None, "C": date(2026, 10, 1), "D": None}
    assert plan_fetch(last, TARGET, 3) == ["B", "D", "A"]
    assert plan_fetch(last, TARGET, 0) == []


def test_full_cycle_for_200_symbols_fits_the_daily_cap():
    last = {f"S{i}": None for i in range(200)}
    assert len(plan_fetch(last, TARGET, remaining({}, "2026-10-14"))) == 200 <= DAILY_CAP
    # steady state: one new close per day means 200 credits/day, never more than the cap
    assert DAILY_CAP <= 320


def test_remaining_budget_counts_per_utc_day_across_runs():
    st = {"day": "2026-10-14", "credits": 300}
    assert remaining(st, "2026-10-14") == DAILY_CAP - 300
    assert remaining(st, "2026-10-15") == DAILY_CAP
    assert remaining({"day": "2026-10-14", "credits": 999}, "2026-10-14") == 0


def test_credit_log_line_and_accumulation(caplog):
    with caplog.at_level(logging.INFO, logger="sp500"):
        st = record_usage({"day": "2026-10-14", "credits": 100}, "2026-10-14", 50)
    assert st == {"day": "2026-10-14", "credits": 150}
    assert "twelvedata credits used: 50" in caplog.text


def test_calendar_holidays_weekends_and_close_time():
    assert not is_trading_day(date(2026, 7, 3))      # Independence Day observed (Jul 4 is a Saturday)
    assert not is_trading_day(date(2026, 4, 3))      # Good Friday
    assert not is_trading_day(date(2026, 11, 26))    # Thanksgiving
    assert is_trading_day(date(2026, 10, 14)) and not is_trading_day(date(2026, 10, 17))
    assert date(2026, 12, 25) in nyse_holidays(2026)
    sat = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
    assert last_complete_trading_day(sat) == date(2026, 10, 16) and not market_open_today(sat)
    assert last_complete_trading_day(datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc)) == date(2026, 10, 13)  # before 17:30 NY
    assert last_complete_trading_day(datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)) == date(2026, 10, 14)
    assert trading_days_between(date(2026, 10, 9), date(2026, 10, 13)) == 2


def test_refresh_skips_when_market_closed_and_when_everything_is_fresh(monkeypatch, tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    import pandas as pd
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("refresh", root / "scripts/refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "k")
    sat = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    assert mod.main([], now=sat) == 0 and "market closed" in capsys.readouterr().out
    live = tmp_path / "live.csv.gz"
    pd.DataFrame({"symbol": ["AAA", "BBB"], "date": ["2026-10-13"] * 2, "close": [1.0, 2.0]}).to_csv(live, index=False)
    monkeypatch.setattr(mod, "LIVE_FILE", live)
    monkeypatch.setattr(mod, "universe_symbols", lambda which: ["AAA", "BBB"])
    monkeypatch.setattr(mod, "load_state", lambda p: {})

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("must not fetch: all symbols are fresh")
    monkeypatch.setattr(mod, "TwelveDataProvider", Boom)
    wed = datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc)  # last complete close = Oct 13
    assert mod.main([], now=wed) == 0 and "2/2 fresh" in capsys.readouterr().out
