"""Generate the SYNTHETIC snapshot used only by tests/CI/offline dev (seeded random walks, NOT market data).

The deployed demo never reads it. Enable locally with SP500_SYNTHETIC=1.
"""
from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dev" / "synthetic_snapshot"
AS_OF = "2026-10-12"
DAYS, N = 300, 200


def main() -> None:
    cons = pd.read_csv(ROOT / "data/constituents.csv", keep_default_na=False).sort_values("symbol").head(N)
    dates = pd.bdate_range(end=AS_OF, periods=DAYS)
    rng = np.random.default_rng(500)
    mkt = rng.normal(0.0004, 0.0085, DAYS)
    sectors = {s: rng.normal(0.0001, 0.006, DAYS) for s in cons.sector.unique()}
    closes, caps = {}, []
    for sym, sec in zip(cons.symbol, cons.sector):
        r = np.random.default_rng(zlib.crc32(sym.encode()))
        beta, sbeta = r.uniform(0.6, 1.4), r.uniform(0.6, 1.3)
        ret = beta * mkt + sbeta * sectors[sec] + r.normal(0.0001, r.uniform(0.008, 0.019), DAYS)
        closes[sym] = np.round(r.lognormal(4.5, 0.8) * np.exp(np.cumsum(ret)), 2)
        caps.append(round(float(r.lognormal(4.2, 0.7)), 1))
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(closes, index=dates.strftime("%Y-%m-%d")).rename_axis("date").to_csv(
        OUT / "prices.csv.gz", compression={"method": "gzip", "mtime": 0})
    u = cons[["symbol", "name", "sector"]].copy()
    u["market_cap_usd_b"] = caps
    u.to_csv(OUT / "universe.csv", index=False)
    (OUT / "meta.json").write_text(json.dumps({
        "label": "SYNTHETIC test data (seeded random walks) - not market data, never shown in the deployed demo",
        "as_of": AS_OF, "generator": "dev/make_synthetic.py"}, indent=2))
    print("synthetic snapshot written", len(closes), "symbols", file=sys.stderr)


if __name__ == "__main__":
    main()
