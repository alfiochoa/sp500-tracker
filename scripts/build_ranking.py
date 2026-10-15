"""Build the committed top-200 market-cap ranking (run manually / quarterly; see README "Ranking method").

  python scripts/build_ranking.py sec    # 1) SEC EDGAR shares + public float -> data/ranking_candidates.csv  (no key)
  python scripts/refresh.py --universe candidates   # 2) real Twelve Data closes for the candidates (needs the key)
  python scripts/build_ranking.py rank   # 3) shares x close on a common date -> data/ranking.csv + ranking_meta.json,
                                         #    prunes data/live_prices.csv.gz to the ranked symbols
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sp500.constituents import load_constituents
from sp500.http import setup_logging
from sp500.providers import LIVE_FILE
from sp500.ranking import (
    CANDIDATES,
    TOP_N,
    SecClient,
    collect_sec,
    latest_public_float,
    pick_candidates,
    rank_by_market_cap,
    resolve_shares,
    share_options,
)

DATA = ROOT / "data"
METHOD = ("Market cap = shares outstanding x Twelve Data close on one common date. Shares: SEC EDGAR XBRL, the freshest (<= 200 days) of "
          "dei:EntityCommonStockSharesOutstanding (10-K/10-Q cover, all classes summed), us-gaap CommonStockSharesOutstanding, diluted then "
          "basic weighted-average shares; for the few companies with none (typically multi-class), an estimate = SEC public float / close "
          f"on the float date (shares_basis=public_float_proxy). Candidates were pre-selected as the {CANDIDATES} largest by SEC "
          "dei:EntityPublicFloat (so only they need a price); the final ranking is by market cap.")


def phase_sec(reuse: bool = False) -> int:
    cons = load_constituents(DATA / "constituents.csv", refresh=False, max_age_days=10**6)
    known = pd.read_csv(DATA / "sec_facts.csv") if reuse and (DATA / "sec_facts.csv").exists() else pd.DataFrame()
    todo = sorted(set(cons.symbol) - set(known.get("symbol", [])))
    sec, missing = collect_sec(todo, SecClient())
    sec = pd.concat([known[["symbol", "cik", "shares", "shares_date", "shares_basis", "public_float_usd"]], sec]) if len(known) else sec
    sec = sec.drop(columns=["name", "sector"], errors="ignore")
    sec = sec.merge(cons[["symbol", "name", "sector"]], on="symbol")
    cand = pick_candidates(sec, CANDIDATES)
    client = SecClient()
    cand = cand.drop(columns=["shares", "shares_date", "shares_basis"], errors="ignore")
    cand["share_options"] = [json.dumps(share_options(client, int(c))) for c in cand.cik]
    fl = [latest_public_float(client.concept(int(c), "EntityPublicFloat")) for c in cand.cik]
    cand["public_float_usd"] = [f[0] if f else None for f in fl]
    cand["public_float_date"] = [f[1] if f else None for f in fl]
    cand.to_csv(DATA / "ranking_candidates.csv", index=False)
    sec.to_csv(DATA / "sec_facts.csv", index=False)
    (DATA / "ranking_sec_meta.json").write_text(json.dumps({"unresolved": missing, "resolved": len(sec), "candidates": len(cand)}, indent=1))
    print(f"SEC: resolved {len(sec)}/{len(cons)}, unresolved {missing}, candidates {len(cand)}")
    return 0


def phase_anchor() -> int:
    """Close on the public-float date for companies with no fresh share count (needs the Twelve Data key; <= a few credits)."""
    import os

    from sp500.budget import load_state, record_usage, remaining
    from sp500.providers import TwelveDataProvider
    cand = pd.read_csv(DATA / "ranking_candidates.csv")
    live = pd.read_csv(LIVE_FILE)
    as_of = date.fromisoformat(live.groupby("symbol").date.max().min())
    _, unresolved = resolve_shares(cand, as_of)
    rows = []
    state, today = load_state(DATA / "refresh_state.json"), datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if unresolved and not key:
        print("::warning::TWELVE_DATA_API_KEY not set; cannot fetch float-date closes for", unresolved)
        return 0
    from sp500.guard import AccountGuard, UsageUnavailable, fetch_usage
    guard, used = AccountGuard(lambda: fetch_usage(key)), 0
    if unresolved:
        try:
            allowed = guard.start(2 * len(unresolved))  # one retry per symbol after a rate-limit wait
        except UsageUnavailable as e:
            print(f"::warning::Twelve Data api_usage unavailable ({e}); failing closed, no anchor credits spent.")
            return 0
    else:
        allowed = 0
    for r in cand[cand.symbol.isin(unresolved)].itertuples():
        if used >= allowed or remaining(state, today) - used <= 0 or not isinstance(r.public_float_date, str):
            continue
        prov = TwelveDataProvider(key)
        bars = prov.fetch_one(r.symbol, 450)
        used += 1
        if not bars and prov.last_code == 429:  # shared key, per-minute limit: wait once and retry once
            import time
            time.sleep(62)
            bars = prov.fetch_one(r.symbol, 450)
            used += 1
        prior = [b for b in bars if b.date.isoformat() <= r.public_float_date]
        if prior:
            rows.append({"symbol": r.symbol, "float_date": r.public_float_date, "date": prior[-1].date.isoformat(), "close": prior[-1].close})
    if used:
        guard.finish()
        (DATA / "refresh_state.json").write_text(json.dumps(record_usage(state, today, used)))
    prev = pd.read_csv(DATA / "float_anchor_prices.csv") if (DATA / "float_anchor_prices.csv").exists() else pd.DataFrame()
    new = pd.DataFrame(rows, columns=["symbol", "float_date", "date", "close"])
    pd.concat([prev, new]).drop_duplicates("symbol", keep="last").to_csv(DATA / "float_anchor_prices.csv", index=False)
    print(f"anchors for {len(rows)}/{len(unresolved)} unresolved symbols; credits used {used}")
    return 0


def phase_rank() -> int:
    cand = pd.read_csv(DATA / "ranking_candidates.csv")
    live = pd.read_csv(LIVE_FILE)
    live = live[live.symbol.isin(cand.symbol)]
    missing = sorted(set(cand.symbol) - set(live.symbol))
    if missing:
        print(f"ERROR: no real closes yet for {len(missing)} candidates (e.g. {missing[:8]}); rerun refresh --universe candidates")
        return 1
    last = live.groupby("symbol").date.max()
    as_of = date.fromisoformat(last.min())
    closes = live.pivot(index="date", columns="symbol", values="close")
    closes.index = pd.to_datetime(closes.index)
    anchors = pd.read_csv(DATA / "float_anchor_prices.csv") if (DATA / "float_anchor_prices.csv").exists() else pd.DataFrame()
    cand, unresolved = resolve_shares(cand, as_of, dict(zip(anchors.symbol, anchors.close)) if len(anchors) else None)
    top = rank_by_market_cap(cand, closes, as_of, TOP_N)
    cols = ["rank", "symbol", "name", "sector", "cik", "shares", "shares_date", "shares_basis", "price_date", "close", "market_cap_usd_b",
            "public_float_usd", "float_rank"]
    top[cols].to_csv(DATA / "ranking.csv", index=False)
    meta = {"as_of": as_of.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "top_n": TOP_N, "candidates": len(cand), "method": METHOD,
            "max_float_rank_in_top": int(top.float_rank.max()), "unresolved_shares": unresolved, "cutoff_market_cap_usd_b": float(top.market_cap_usd_b.iloc[-1]),
            "sources": ["SEC EDGAR XBRL API (data.sec.gov)", "Twelve Data time_series (1day close)",
                        "Wikipedia List of S&P 500 companies (CC BY-SA 4.0)"]}
    (DATA / "ranking_meta.json").write_text(json.dumps(meta, indent=1))
    live[live.symbol.isin(top.symbol)].to_csv(LIVE_FILE, index=False, compression={"method": "gzip", "mtime": 0})
    print("no fresh share count for:", unresolved)
    print(f"ranked top {len(top)} as of {as_of}; #200 cap ${meta['cutoff_market_cap_usd_b']}B; "
          f"deepest float rank among final top {TOP_N}: {meta['max_float_rank_in_top']} of {CANDIDATES}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["sec", "anchor", "rank"])
    ap.add_argument("--reuse", action="store_true", help="sec phase: keep rows already in data/sec_facts.csv, query only the rest")
    a = ap.parse_args()
    setup_logging()
    raise SystemExit({"sec": lambda: phase_sec(a.reuse), "anchor": phase_anchor, "rank": phase_rank}[a.phase]())
