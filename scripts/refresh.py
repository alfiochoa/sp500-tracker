"""Scheduled refresh: real end-of-day closes from Twelve Data for the ranked top-200, fetched only when stale.

- Skips the run when the US market is closed today (weekend/holiday) unless --force.
- Fetches only symbols whose latest stored close is older than the last complete trading day.
- Self-limits to DAILY_CAP credits per UTC day (shared free key) and 8 requests/minute.
Needs env TWELVE_DATA_API_KEY; exits 0 without changes when it is absent (forks / CI stay green).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sp500.budget import DAILY_CAP, HISTORY_DAYS, load_state, plan_fetch, record_usage, remaining
from sp500.calendar import last_complete_trading_day, market_open_today, trading_days_between
from sp500.guard import CHUNK, MINUTE_WAIT, AccountGuard, UsageUnavailable, fetch_usage
from sp500.http import setup_logging
from sp500.providers import LIVE_FILE, TwelveDataProvider

DATA = ROOT / "data"
STATE, STATUS = DATA / "refresh_state.json", DATA / "refresh_status.json"
log = logging.getLogger("sp500")


def universe_symbols(which: str) -> list[str]:
    f = DATA / ("ranking.csv" if which == "ranking" else "ranking_candidates.csv")
    if not f.exists():
        return []
    return sorted(pd.read_csv(f).symbol)


def main(argv=None, now: datetime | None = None, guard: AccountGuard | None = None, sleep=None) -> int:
    import time
    sleep = sleep or time.sleep
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["ranking", "candidates"], default="ranking")
    ap.add_argument("--target-date", help="fetch up to this close date (YYYY-MM-DD) instead of the last completed session")
    ap.add_argument("--force", action="store_true", help="run even if the market is closed today (manual use)")
    a = ap.parse_args(argv)
    setup_logging()
    now = now or datetime.now(timezone.utc)
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not key:
        print("::warning::TWELVE_DATA_API_KEY is not set - no data fetched; the dashboard keeps the last committed real closes.")
        return 0
    if not market_open_today(now) and not a.force:
        print(f"US market closed on {now.date()} (weekend/holiday): nothing to fetch.")
        return 0
    symbols = universe_symbols(a.universe)
    if not symbols:
        print("::warning::data/ranking.csv not built yet (scripts/build_ranking.py); nothing to refresh.")
        return 0
    target = date.fromisoformat(a.target_date) if a.target_date else last_complete_trading_day(now)
    old = pd.read_csv(LIVE_FILE) if LIVE_FILE.exists() else pd.DataFrame(columns=["symbol", "date", "close"])
    old = old[old.date <= target.isoformat()]
    last = old.groupby("symbol").date.max().to_dict()
    last_dates = {s: (date.fromisoformat(last[s]) if s in last else None) for s in symbols}
    today = now.strftime("%Y-%m-%d")
    state = load_state(STATE)
    budget = remaining(state, today)
    plan = plan_fetch(last_dates, target, budget)
    stale = sum(1 for d in last_dates.values() if d is None or d < target)
    print(f"target close {target}; {len(symbols) - stale}/{len(symbols)} fresh; fetching {len(plan)} (budget left {budget}/{DAILY_CAP})")
    if not plan:
        return 0
    guard = guard or AccountGuard(lambda: fetch_usage(key))
    try:
        allowed = guard.start(len(plan))
    except UsageUnavailable as e:  # fail closed: spend nothing, keep the previous data
        print(f"::warning::Twelve Data api_usage unavailable ({e}); failing closed, no credits spent, previous data kept.")
        return 0
    if allowed <= 0:
        print("::warning::account guard: no credits may be spent now (daily cap 560 or per-minute limit busy); previous data kept.")
        return 0
    plan = plan[:allowed]
    p = TwelveDataProvider(key)
    frames, got, spent = [old], 0, 0
    for i, sym in enumerate(plan):
        if i and i % CHUNK == 0:
            try:
                if guard.allow(spent) < min(CHUNK, len(plan) - i):
                    print("::warning::account guard: stopping early (shared daily cap reached).")
                    break
            except UsageUnavailable:
                print("::warning::api_usage unavailable mid-run; stopping (fail closed).")
                break
        have = last_dates[sym]
        days = HISTORY_DAYS if have is None else min(HISTORY_DAYS, trading_days_between(have, target) + 5)
        bars = p.fetch_one(sym, days)
        spent += 1
        if not bars and p.last_code == 429:  # minute/daily limit hit (the other project may be running): wait once, retry once
            sleep(MINUTE_WAIT)
            bars = p.fetch_one(sym, days)
            spent += 1
            if not bars and p.last_code == 429:
                print("::warning::rate limit still hit after waiting; keeping data fetched so far.")
                break
        if bars:
            got += 1
            frames.append(pd.DataFrame([(b.symbol, b.date.isoformat(), b.close) for b in bars], columns=["symbol", "date", "close"]))
    df = pd.concat(frames).drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"])
    df = df[df.date <= target.isoformat()]  # never keep an in-progress (intraday) bar: only completed sessions are real closes
    if a.universe == "ranking":
        df = df[df.symbol.isin(symbols)]
    df = df.groupby("symbol", group_keys=False).tail(HISTORY_DAYS)
    df.to_csv(LIVE_FILE, index=False, compression={"method": "gzip", "mtime": 0})
    STATE.write_text(json.dumps(record_usage(state, today, spent)))
    guard.finish()
    fresh = int((df.groupby("symbol").date.max().reindex(symbols) >= target.isoformat()).sum())
    STATUS.write_text(json.dumps({"refreshed_at": now.isoformat(timespec="seconds"), "target_close": target.isoformat(),
                                  "symbols": len(symbols), "fresh": fresh, "fetched": got, "credits_used_run": spent}))
    print(f"fetched {got}/{spent}; fresh {fresh}/{len(symbols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
