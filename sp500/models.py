"""Pydantic schemas: every external payload is validated before it touches the app."""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

SECTORS = {
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
    "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities",
}
_SYMBOL = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")


class Constituent(BaseModel):
    symbol: str
    name: str = Field(min_length=1)
    sector: str
    sub_industry: str = ""
    headquarters: str = ""
    date_added: str = ""

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        v = v.strip().upper().replace(" ", "")
        if not _SYMBOL.match(v):
            raise ValueError(f"bad ticker {v!r}")
        return v

    @field_validator("sector")
    @classmethod
    def _sector(cls, v: str) -> str:
        if v not in SECTORS:
            raise ValueError(f"unknown GICS sector {v!r}")
        return v


class PriceBar(BaseModel):
    symbol: str
    date: date
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
