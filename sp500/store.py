"""SQLite cache for fetched bars (dedupes and merges across runs)."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import PriceBar

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (symbol TEXT, date TEXT, close REAL, volume REAL, PRIMARY KEY (symbol, date));
"""


class Store:
    def __init__(self, path: str | Path = ".cache/sp500.sqlite"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def upsert(self, bars: Iterable[PriceBar]) -> int:
        rows = [(b.symbol, b.date.isoformat(), b.close, b.volume) for b in bars]
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?)", rows)
        return len(rows)

    def bars(self, symbols: Iterable[str] | None = None):
        q = "SELECT symbol, date, close, volume FROM bars"
        args: list = []
        if symbols is not None:
            syms = list(symbols)
            q += f" WHERE symbol IN ({','.join('?' * len(syms))})"
            args = syms
        return self.db.execute(q + " ORDER BY symbol, date", args).fetchall()
