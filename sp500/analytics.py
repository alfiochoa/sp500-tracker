"""Pure-pandas analytics on a wide close matrix (index=date, columns=symbol). Descriptive only."""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS = {"1d": 1, "1w": 5, "1m": 21}


def returns_table(px: pd.DataFrame) -> pd.DataFrame:
    """1d/1w/1m/YTD simple returns (%) as of the last row; YTD uses the last close of the prior year."""
    last = px.iloc[-1]
    out = {k: (last / px.iloc[-1 - n] - 1) * 100 for k, n in PERIODS.items() if len(px) > n}
    prior = px[px.index < pd.Timestamp(px.index[-1].year, 1, 1)]
    base = prior.iloc[-1] if len(prior) else px.iloc[0]
    out["YTD"] = (last / base - 1) * 100
    return pd.DataFrame(out)


def movers(ret: pd.DataFrame, col: str = "1d", n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = ret[col].dropna().sort_values()
    return s.tail(n).iloc[::-1].to_frame(), s.head(n).to_frame()


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def scores(px: pd.DataFrame) -> pd.DataFrame:
    """Momentum (6m return skipping the latest week, percentile 0-100), volatility (60d annualised %, plus
    percentile) and 52-week range position (0 = at low, 100 = at high)."""
    n = len(px)
    skip, look = 5, min(126, n - 1)
    mom_raw = px.iloc[-1 - skip] / px.iloc[-1 - look] - 1
    vol = px.pct_change().tail(60).std() * np.sqrt(252) * 100
    win = px.tail(252)
    lo, hi = win.min(), win.max()
    pos = ((px.iloc[-1] - lo) / (hi - lo).replace(0, np.nan)) * 100
    return pd.DataFrame({"momentum": _pct_rank(mom_raw), "volatility": vol, "vol_rank": _pct_rank(vol),
                         "range_pos": pos, "low_52w": lo, "high_52w": hi, "price": px.iloc[-1]})


def sector_weights(cons: pd.DataFrame, caps: pd.Series | None, mode: str = "cap") -> pd.DataFrame:
    """Weights per symbol (equal or cap-weighted using the supplied caps) and their sector totals (%)."""
    d = cons[["symbol", "name", "sector"]].copy()
    d["weight"] = d.symbol.map(caps).fillna(0) if mode == "cap" and caps is not None else 1.0
    if d.weight.sum() == 0:
        d["weight"] = 1.0
    d["weight"] = d.weight / d.weight.sum() * 100
    return d


def sector_summary(d: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Per-sector weight, count and weight-averaged returns (%)."""
    j = d.set_index("symbol").join(ret).dropna(subset=list(ret.columns))
    rows = {}
    for sec, g in j.groupby("sector"):
        w = g.weight / g.weight.sum()
        rows[sec] = {"weight": g.weight.sum(), "stocks": len(g),
                     **{c: float((g[c] * w).sum()) for c in ret.columns}}
    return pd.DataFrame(rows).T.sort_values("weight", ascending=False)


def correlation(px: pd.DataFrame, symbols: list[str], window: int = 126) -> pd.DataFrame:
    return px[symbols].pct_change().tail(window).corr()


def to_export(cons: pd.DataFrame, ret: pd.DataFrame, sc: pd.DataFrame, source: pd.Series) -> pd.DataFrame:
    out = cons.set_index("symbol")[["name", "sector"]].join(ret).join(sc[["price", "momentum", "volatility", "range_pos"]])
    out["data_source"] = source
    return out.round(2).reset_index()
