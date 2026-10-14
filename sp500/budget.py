"""Twelve Data credit budget: which symbols to fetch, and how many credits we may spend today.

One time_series request costs one credit per symbol. The key is shared with another project (market-desk), so this
project self-limits to DAILY_CAP credits per UTC day, counted in data/refresh_state.json across runs.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

DAILY_CAP = 320       # hard self-imposed ceiling per UTC day (shared free key: 800/day total, market-desk uses <= 250)
PER_MINUTE = 8        # free-tier rate limit
HISTORY_DAYS = 300    # bars kept per symbol
log = logging.getLogger("sp500")


def remaining(state: dict, today: str, cap: int = DAILY_CAP) -> int:
    used = state.get("credits", state.get("calls", 0)) if state.get("day") == today else 0
    return max(0, cap - used)


def plan_fetch(last_dates: dict[str, date | None], target: date, budget: int) -> list[str]:
    """Symbols to fetch now: only those whose latest stored close is older than `target` (the last complete trading day).

    Never-fetched symbols come first, then the stalest; the list is cut to `budget` credits. Up-to-date symbols are
    never refetched.
    """
    stale = [(s, d) for s, d in last_dates.items() if d is None or d < target]
    stale.sort(key=lambda x: (x[1] is not None, x[1] or date.min, x[0]))
    return [s for s, _ in stale[: max(0, budget)]]


def load_state(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_usage(state: dict, today: str, used: int) -> dict:
    prior = state.get("credits", 0) if state.get("day") == today else 0
    total = prior + used
    log.info("twelvedata credits used: %d (run), %d/%d today", used, total, DAILY_CAP)
    return {**state, "day": today, "credits": total}
