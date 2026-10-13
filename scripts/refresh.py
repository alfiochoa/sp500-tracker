"""Scheduled refresh: fetch one rotating batch of symbols from Twelve Data, merge into data/live_prices.csv.gz.

Respects 8 req/min and 800/day (state in data/refresh_state.json). Needs env TWELVE_DATA_API_KEY.
Exit 0 with a notice (no commit) when no key is configured, so forks/CI stay green.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sp500.constituents import load_constituents
from sp500.http import setup_logging
from sp500.providers import LIVE_FILE, TwelveDataProvider, rotate
from sp500.store import Store

STATE = ROOT / "data" / "refresh_state.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=40, help="symbols per run (8/min => 40 takes ~5 min)")
    ap.add_argument("--no-constituents", action="store_true")
    a = ap.parse_args()
    setup_logging()
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not key:
        print("TWELVE_DATA_API_KEY not set - skipping live refresh (demo snapshot remains in use).")
        return 0
    cons = load_constituents(ROOT / "data/constituents.csv", refresh=not a.no_constituents, max_age_days=7)
    symbols = sorted(cons.symbol)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"cursor": 0, "day": "", "calls": 0}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state["day"] != today:
        state.update(day=today, calls=0)
    n = min(a.batch, TwelveDataProvider.PER_DAY - state["calls"])
    if n <= 0:
        print("Daily quota reached; nothing to do.")
        return 0
    batch, cursor = rotate(symbols, state["cursor"], n)
    store = Store(":memory:")
    if LIVE_FILE.exists():
        old = pd.read_csv(LIVE_FILE)
        from sp500.models import PriceBar
        store.upsert(PriceBar(symbol=r.symbol, date=r.date, close=r.close) for r in old.itertuples())
    got = TwelveDataProvider(key, store=store).history(batch, days=300)
    state.update(cursor=cursor, calls=state["calls"] + len(batch))
    rows = store.bars()
    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"])[["symbol", "date", "close"]]
    df.to_csv(LIVE_FILE, index=False, compression={"method": "gzip", "mtime": 0})
    STATE.write_text(json.dumps(state))
    print(f"refreshed {len(got)}/{len(batch)} symbols; cursor={cursor}; calls_today={state['calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
