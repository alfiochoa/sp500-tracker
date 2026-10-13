"""S&P 500 constituents from Wikipedia (CC BY-SA 4.0): MediaWiki API first, robots-checked HTML fallback.

A breakage detector validates the table shape so a Wikipedia layout change fails loudly instead of
silently feeding the dashboard garbage; on any failure the last good cache is kept.
"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from .http import allowed_by_robots, make_session
from .models import Constituent

log = logging.getLogger("sp500")
PAGE = "List_of_S&P_500_companies"
API = "https://en.wikipedia.org/w/api.php"
HTML = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SOURCE_URL = HTML
ATTRIBUTION = ('Constituent list: "List of S&P 500 companies", Wikipedia contributors, '
               "licensed CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/).")
REQUIRED_COLS = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Headquarters Location", "Date added"]
MIN_ROWS, MAX_ROWS = 480, 520
DEFAULT_CACHE = Path("data/constituents.csv")


class StructureChanged(RuntimeError):
    """Raised by the breakage detector when the source page no longer looks like the S&P 500 table."""


def parse_table(html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    except ValueError:  # id missing -> fall back to "first table with a Symbol column"
        tables = [t for t in pd.read_html(io.StringIO(html)) if "Symbol" in t.columns]
    if not tables:
        raise StructureChanged("no constituents table found")
    return tables[0]


def detect_breakage(df: pd.DataFrame) -> list[Constituent]:
    """Validate shape + every row. Raises StructureChanged with a precise reason."""
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise StructureChanged(f"missing columns: {missing}")
    if not MIN_ROWS <= len(df) <= MAX_ROWS:
        raise StructureChanged(f"row count {len(df)} outside {MIN_ROWS}-{MAX_ROWS}")
    out, bad = [], 0
    for r in df.to_dict("records"):
        try:
            out.append(Constituent(symbol=str(r["Symbol"]), name=str(r["Security"]), sector=str(r["GICS Sector"]),
                                   sub_industry=str(r["GICS Sub-Industry"]),
                                   headquarters=str(r["Headquarters Location"]), date_added=str(r["Date added"])))
        except ValidationError:
            bad += 1
    if bad > 5:
        raise StructureChanged(f"{bad} rows failed schema validation")
    if bad:
        log.warning("event=rows_dropped count=%d", bad)
    return out


def fetch_html(session=None) -> tuple[str, str]:
    """Return (html, via). API first; HTML fallback only if robots.txt allows it."""
    s = session or make_session()
    try:
        r = s.get(API, params={"action": "parse", "page": PAGE, "prop": "text", "format": "json",
                               "formatversion": 2, "redirects": 1}, timeout=20)
        r.raise_for_status()
        return r.json()["parse"]["text"], "mediawiki-api"
    except Exception as e:  # noqa: BLE001 - any API failure triggers the documented fallback
        log.warning("event=api_failed err=%s fallback=html", e)
    if not allowed_by_robots(s, HTML):
        raise RuntimeError("robots.txt disallows HTML fallback")
    r = s.get(HTML, timeout=20)
    r.raise_for_status()
    return r.text, "html"


def load_constituents(cache: Path = DEFAULT_CACHE, refresh: bool = False, max_age_days: float = 7,
                      session=None) -> pd.DataFrame:
    """Cached read. Refreshes only when asked/stale; on failure falls back to the cache."""
    cache = Path(cache)
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_days * 86400
    if cache.exists() and not refresh and fresh:
        return pd.read_csv(cache, keep_default_na=False)
    try:
        html, via = fetch_html(session)
        rows = detect_breakage(parse_table(html))
        df = pd.DataFrame([r.model_dump() for r in rows]).sort_values("symbol").reset_index(drop=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)
        log.info("event=constituents_refreshed rows=%d via=%s", len(df), via)
        return df
    except Exception as e:
        log.error("event=constituents_refresh_failed err=%s", e)
        if cache.exists():
            return pd.read_csv(cache, keep_default_na=False)
        raise
