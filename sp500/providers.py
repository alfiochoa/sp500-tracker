"""Pluggable price providers. All return validated PriceBar objects keyed by symbol."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from .http import RateLimiter, make_session
from .models import PriceBar
from .store import Store

log = logging.getLogger("sp500")
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "dev" / "synthetic_snapshot"   # synthetic, tests/offline dev only (SP500_SYNTHETIC=1)
LIVE_FILE = ROOT / "data" / "live_prices.csv.gz"
RANKING_FILE = ROOT / "data" / "ranking.csv"


class PriceProvider(ABC):
    name: str
    is_demo: bool = False

    @abstractmethod
    def history(self, symbols: Sequence[str], days: int = 300) -> dict[str, list[PriceBar]]:
        """Daily closes for each symbol (oldest first). Symbols that fail are omitted, never faked."""


def bars_from_wide(df: pd.DataFrame, symbols: Sequence[str] | None = None) -> dict[str, list[PriceBar]]:
    out: dict[str, list[PriceBar]] = {}
    for sym in (symbols or df.columns):
        if sym not in df.columns:
            continue
        s = df[sym].dropna()
        out[sym] = [PriceBar(symbol=sym, date=date.fromisoformat(str(d)[:10]), close=float(v)) for d, v in s.items()]
    return out


class SnapshotProvider(PriceProvider):
    """Synthetic snapshot (dev/synthetic_snapshot): tests, CI and offline dev ONLY. Never used by the deployed demo."""
    name, is_demo = "snapshot", True

    def __init__(self, directory: Path = SNAPSHOT_DIR):
        self.dir = Path(directory)

    def meta(self) -> dict:
        import json
        return json.loads((self.dir / "meta.json").read_text())

    def universe(self) -> pd.DataFrame:
        return pd.read_csv(self.dir / "universe.csv")

    def history(self, symbols, days=300):
        df = pd.read_csv(self.dir / "prices.csv.gz", index_col="date").tail(days)
        return bars_from_wide(df, symbols)


class LiveFileProvider(PriceProvider):
    """Reads data/live_prices.csv.gz, the small file the scheduled GitHub Action commits."""
    name = "live-file"

    def __init__(self, path: Path = LIVE_FILE):
        self.path = Path(path)

    def available(self) -> bool:
        return self.path.exists()

    def history(self, symbols, days=300):
        if not self.available():
            return {}
        df = pd.read_csv(self.path)
        out: dict[str, list[PriceBar]] = {}
        for sym, g in df[df.symbol.isin(symbols)].groupby("symbol"):
            g = g.sort_values("date").tail(days)
            out[sym] = [PriceBar(symbol=sym, date=date.fromisoformat(d), close=c) for d, c in zip(g.date, g.close)]
        return out


class TwelveDataProvider(PriceProvider):
    """Twelve Data free tier: 8 requests/min, 800/day. One request per symbol; rotate symbols across runs."""
    name = "twelvedata"
    URL = "https://api.twelvedata.com/time_series"
    PER_MIN, PER_DAY = 8, 800

    def __init__(self, api_key: str, store: Store | None = None, session=None, limiter: RateLimiter | None = None):
        if not api_key:
            raise ValueError("TWELVE_DATA_API_KEY is required")
        self._key, self.store, self.last_code = api_key, store, None
        self.session = session or make_session()
        self.limiter = limiter or RateLimiter(self.PER_MIN)

    def fetch_one(self, symbol: str, days: int) -> list[PriceBar]:
        self.limiter.wait()
        self.last_code = None
        try:  # never let the key leak through exception text / logs
            r = self.session.get(self.URL, params={"symbol": symbol, "interval": "1day", "outputsize": days,
                                                   "apikey": self._key}, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("event=fetch_failed provider=twelvedata symbol=%s err=%s", symbol, type(e).__name__)
            return []
        if payload.get("status") == "error" or "values" not in payload:
            self.last_code = payload.get("code")
            log.warning("event=provider_error provider=twelvedata symbol=%s code=%s", symbol, self.last_code)
            return []
        bars = []
        for v in reversed(payload["values"]):
            try:
                bars.append(PriceBar(symbol=symbol, date=v["datetime"][:10], close=v["close"], volume=v.get("volume")))
            except (ValidationError, KeyError, TypeError):
                log.warning("event=bad_bar symbol=%s", symbol)
        return bars

    def history(self, symbols, days=300):
        out = {}
        for sym in symbols:
            bars = self.fetch_one(sym, days)
            if bars:
                out[sym] = bars
                if self.store:
                    self.store.upsert(bars)
        return out


class YahooProvider(PriceProvider):
    """OFF by default. Unofficial endpoint, ToS-restricted: personal/educational use only (see README)."""
    name = "yahoo"
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

    def __init__(self, enabled: bool = False, session=None):
        if not enabled:
            raise RuntimeError("YahooProvider is disabled by default; pass enabled=True (personal use only, see README)")
        self.session = session or make_session()
        self.limiter = RateLimiter(20)

    def history(self, symbols, days=300):
        out = {}
        for sym in symbols:
            self.limiter.wait()
            try:
                r = self.session.get(self.URL.format(sym=sym.replace(".", "-")), params={"range": "2y", "interval": "1d"}, timeout=20)
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                closes = res["indicators"]["quote"][0]["close"]
                out[sym] = [PriceBar(symbol=sym, date=pd.Timestamp(t, unit="s").date(), close=c)
                            for t, c in zip(res["timestamp"], closes) if c][-days:]
            except Exception as e:  # noqa: BLE001
                log.warning("event=fetch_failed provider=yahoo symbol=%s err=%s", sym, type(e).__name__)
        return out


def rotate(symbols: Sequence[str], cursor: int, n: int) -> tuple[list[str], int]:
    """Next `n` symbols after `cursor`, wrapping around; returns (batch, new_cursor)."""
    m = len(symbols)
    n = min(n, m)
    return [symbols[(cursor + i) % m] for i in range(n)], (cursor + n) % m


def to_wide(by_symbol: dict[str, list[PriceBar]]) -> pd.DataFrame:
    df = pd.DataFrame({s: pd.Series({pd.Timestamp(b.date): b.close for b in bars}) for s, bars in by_symbol.items()})
    return df.sort_index()


def load_real_prices(ranking: Path = RANKING_FILE, live: LiveFileProvider | None = None):
    """Real data only: the ranked universe (data/ranking.csv) and its committed Twelve Data closes.

    Returns (universe df, wide closes, meta dict) or (None, None, {}) when no real data exists - nothing is ever invented.
    """
    lv = live or LiveFileProvider()
    if not Path(ranking).exists() or not lv.available():
        return None, None, {}
    uni = pd.read_csv(ranking)
    bars = lv.history(list(uni.symbol))
    if not bars:
        return None, None, {}
    px = to_wide(bars)
    uni = uni[uni.symbol.isin(px.columns)].reset_index(drop=True)
    return uni, px, {"mode": "real"}


def load_synthetic_prices(snapshot: SnapshotProvider | None = None):
    """SYNTHETIC test data (seeded random walks) - only when SP500_SYNTHETIC=1."""
    snap = snapshot or SnapshotProvider()
    uni = snap.universe()
    px = to_wide(snap.history(list(uni.symbol)))
    return uni, px, {**snap.meta(), "mode": "synthetic"}
