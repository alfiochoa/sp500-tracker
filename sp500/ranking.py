"""Market-cap ranking of the S&P 500 constituents from free, documented sources.

market cap = shares outstanding (SEC EDGAR XBRL, dei:EntityCommonStockSharesOutstanding from the company's latest
10-K/10-Q cover page, all share classes summed) x close (Twelve Data) on one common date.

Because a close for all ~503 names would cost ~503 API credits, SEC's dei:EntityPublicFloat (aggregate market value
of non-affiliates, reported once a year) is used only to pick a generous candidate set that is then priced.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .http import make_session

SEC_USER_AGENT = "Alfredo Ochoa sp500-tracker alf8a94@gmail.com"  # SEC fair access: descriptive name + contact

log = logging.getLogger("sp500")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_MIN_INTERVAL = 0.2   # <= 5 requests/second (SEC fair-access limit is 10/s)
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT"}
TOP_N = 200
CANDIDATES = 300


def sec_ticker(symbol: str) -> str:
    return symbol.replace(".", "-")


def latest_cover_shares(facts: list[dict]) -> tuple[int, str] | None:
    """Sum of the distinct share counts (one per share class) on the cover of the most recently filed 10-K/10-Q.

    Returns (total shares, 'cover date' ISO) or None.
    """
    rows = [f for f in facts if f.get("form") in FORMS and f.get("val", 0) > 0]
    if not rows:
        return None
    latest = max(rows, key=lambda f: (f["filed"], f["end"]))
    same = [f for f in rows if f["accn"] == latest["accn"]]
    return int(sum({f["val"] for f in same})), max(f["end"] for f in same)


def latest_by_end(facts: list[dict]) -> tuple[int, str] | None:
    """Fallback for companies whose cover-page share count is only reported per class (e.g. Alphabet, Meta):
    the latest 10-K/10-Q balance-sheet or diluted-weighted-average share count, which covers all classes."""
    rows = [f for f in facts if f.get("form") in FORMS and f.get("val", 0) > 0]
    if not rows:
        return None
    latest = max(rows, key=lambda f: (f["end"], f["filed"]))
    return int(latest["val"]), latest["end"]


def latest_public_float(facts: list[dict]) -> float | None:
    rows = [f for f in facts if f.get("form") in FORMS and f.get("val", 0) > 0]
    if not rows:
        return None
    latest = max(rows, key=lambda f: (f["end"], f["filed"]))
    return float(latest["val"])


class SecClient:
    def __init__(self, session=None, sleep=time.sleep):
        self.session, self._sleep, self._last = session or make_session(), sleep, 0.0
        self.session.headers["User-Agent"] = SEC_USER_AGENT
        self._facts_cache: tuple[int, dict] = (-1, {})

    def get_json(self, url: str):
        wait = SEC_MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        r = self.session.get(url, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def ticker_map(self) -> dict[str, int]:
        return {v["ticker"].upper(): int(v["cik_str"]) for v in self.get_json(TICKERS_URL).values()}

    def concept(self, cik: int, tag: str, taxonomy: str = "dei") -> list[dict]:
        d = self.get_json(CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, tag=tag))
        units = (d or {}).get("units") or {}
        vals = units.get("shares") or units.get("USD") or []
        if d is not None and not isinstance(vals, list) or (d is not None and not vals):
            # companyconcept sometimes answers with empty units for large filers; companyfacts has the same data
            vals = self._facts(cik).get(taxonomy, {}).get(tag, {}).get("units", {})
            vals = vals.get("shares") or vals.get("USD") or []
        return vals if isinstance(vals, list) else []

    def _facts(self, cik: int) -> dict:
        if self._facts_cache[0] != cik:
            d = self.get_json(FACTS_URL.format(cik=cik)) or {}
            self._facts_cache = (cik, d.get("facts", {}))
        return self._facts_cache[1]


@dataclass
class Candidate:
    symbol: str
    cik: int
    shares: int
    shares_date: str
    public_float: float | None


def collect_sec(symbols: list[str], client: SecClient) -> tuple[pd.DataFrame, list[str]]:
    """Per symbol: CIK, shares outstanding (+cover date), public float. Second value lists unresolved symbols."""
    tmap = client.ticker_map()
    rows, missing = [], []
    for sym in symbols:
        cik = tmap.get(sec_ticker(sym))
        if cik is None:
            missing.append(sym)
            continue
        try:
            basis = "cover_page"
            sh = latest_cover_shares(client.concept(cik, "EntityCommonStockSharesOutstanding"))
            if sh is None:
                basis, sh = "balance_sheet", latest_by_end(client.concept(cik, "CommonStockSharesOutstanding", "us-gaap"))
            if sh is None:
                basis, sh = "diluted_weighted_avg", latest_by_end(client.concept(cik, "WeightedAverageNumberOfDilutedSharesOutstanding", "us-gaap"))
            fl = latest_public_float(client.concept(cik, "EntityPublicFloat"))
        except Exception as e:  # noqa: BLE001
            log.warning("event=sec_failed symbol=%s err=%s", sym, type(e).__name__)
            missing.append(sym)
            continue
        if sh is None:
            missing.append(sym)
            continue
        rows.append({"symbol": sym, "cik": cik, "shares": sh[0], "shares_date": sh[1], "shares_basis": basis, "public_float_usd": fl})
    return pd.DataFrame(rows), missing


def pick_candidates(sec: pd.DataFrame, n: int = CANDIDATES) -> pd.DataFrame:
    """Top-n by public float; companies with no float are kept (never silently dropped)."""
    s = sec.copy()
    s["float_rank"] = s.public_float_usd.rank(ascending=False, method="first")
    s["float_rank"] = s["float_rank"].fillna(0)
    return s.sort_values("float_rank").pipe(lambda d: d[(d.float_rank <= n)]).reset_index(drop=True)


def common_price_date(last_dates: dict[str, date]) -> date:
    """The latest date for which every candidate has a close."""
    return min(last_dates.values())


def rank_by_market_cap(cand: pd.DataFrame, closes: pd.DataFrame, as_of: date, top_n: int = TOP_N) -> pd.DataFrame:
    """closes: wide frame (index=date, columns=symbol). Uses each symbol's last close on or before `as_of`.

    Share classes of one company (same CIK) share the company-level market cap computed from the total share count.
    """
    px = closes[closes.index <= pd.Timestamp(as_of)].ffill().iloc[-1]
    d = cand[cand.symbol.isin(px.dropna().index)].copy()
    d["close"] = d.symbol.map(px)
    d["market_cap_usd_b"] = (d.shares * d.close / 1e9).round(2)
    d = d.sort_values("market_cap_usd_b", ascending=False).head(top_n).reset_index(drop=True)
    d.insert(0, "rank", range(1, len(d) + 1))
    d["price_date"] = as_of.isoformat()
    return d
