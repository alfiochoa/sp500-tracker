"""Generate the bundled DEMO snapshot (synthetic prices, seeded & deterministic). Not real market data."""
from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2026-10-12"
DAYS = 300
# Rough orders of magnitude (USD bn) so the demo weights look plausible; everything else is seeded noise.
KNOWN_CAPS = {"NVDA": 4500, "MSFT": 3900, "AAPL": 3800, "AMZN": 2400, "GOOGL": 3000, "GOOG": 3000, "META": 1800,
              "AVGO": 1500, "TSLA": 1300, "BRK.B": 1050, "JPM": 800, "LLY": 780, "V": 650, "WMT": 750, "ORCL": 700,
              "XOM": 480, "MA": 520, "NFLX": 500, "COST": 430, "JNJ": 430, "PG": 360, "HD": 380, "BAC": 360}


def main() -> None:
    cons = pd.read_csv(ROOT / "data/constituents.csv", keep_default_na=False)
    dates = pd.bdate_range(end=AS_OF, periods=DAYS)
    rng = np.random.default_rng(500)
    mkt = rng.normal(0.0004, 0.0085, DAYS)
    sectors = {s: rng.normal(0.0001, 0.006, DAYS) for s in cons.sector.unique()}
    closes, caps = {}, {}
    for sym, sec in zip(cons.symbol, cons.sector):
        r = np.random.default_rng(zlib.crc32(sym.encode()))
        beta, sbeta = r.uniform(0.6, 1.4), r.uniform(0.6, 1.3)
        ret = beta * mkt + sbeta * sectors[sec] + r.normal(0.0001, r.uniform(0.008, 0.019), DAYS)
        closes[sym] = np.round(r.lognormal(4.5, 0.8) * np.exp(np.cumsum(ret)), 2)
        caps[sym] = KNOWN_CAPS.get(sym, round(float(r.lognormal(3.6, 0.9)), 1))
    pd.DataFrame(closes, index=dates.strftime("%Y-%m-%d")).rename_axis("date").to_csv(
        ROOT / "data/snapshot/prices.csv.gz", compression={"method": "gzip", "mtime": 0})
    pd.DataFrame({"symbol": list(caps), "market_cap_b": list(caps.values())}).to_csv(
        ROOT / "data/snapshot/market_caps.csv", index=False)
    (ROOT / "data/snapshot/meta.json").write_text(json.dumps({
        "label": "DEMO SNAPSHOT - synthetic prices, not real market data", "as_of": AS_OF,
        "generator": "scripts/make_snapshot.py (seeded)", "market_caps": "illustrative orders of magnitude"}, indent=2))
    print("snapshot written", len(closes), "symbols", file=sys.stderr)


if __name__ == "__main__":
    main()
