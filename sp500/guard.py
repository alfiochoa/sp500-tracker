"""Account-wide Twelve Data safety guard.

The API key is shared with another project (market-desk), so a repo-local counter cannot see its spending. Before every paid
batch (and every CHUNK calls inside it) we read the free `api_usage` endpoint (costs no credits) and only spend while
daily_usage + planned <= ACCOUNT_CAP (market-desk may use more; this project yields first). If api_usage cannot be read, we fail
closed: spend nothing and keep the previous data. The per-minute limit is checked the same way (retry once after 62 s).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .budget import PER_MINUTE
from .http import make_session

log = logging.getLogger("sp500")
USAGE_URL = "https://api.twelvedata.com/api_usage"
ACCOUNT_CAP = 560      # hard stop for anything automatic (free plan: 800/day)
CHUNK = 25             # re-check the account usage every this many paid calls
MINUTE_WAIT = 62       # seconds to wait for the per-minute window to clear


class UsageUnavailable(RuntimeError):
    """api_usage could not be read or parsed: callers must fail closed."""


def fetch_usage(key: str, session=None) -> dict:
    try:  # the key must never leak through exception text
        r = (session or make_session()).get(USAGE_URL, params={"apikey": key}, timeout=20)
        r.raise_for_status()
        d = r.json()
        if d.get("status") == "error":
            raise ValueError(f"code {d.get('code')}")
        return {"daily_usage": int(d["daily_usage"]), "plan_daily_limit": int(d.get("plan_daily_limit", 800)),
                "current_usage": int(d.get("current_usage", 0))}
    except Exception as e:  # noqa: BLE001
        raise UsageUnavailable(type(e).__name__) from None


def allowed_credits(usage: dict, planned: int, cap: int = ACCOUNT_CAP) -> int:
    """How many of `planned` credits may be spent: daily_usage + spend <= cap."""
    return max(0, min(planned, cap - usage["daily_usage"]))


def minute_clear(read: Callable[[], dict], sleep: Callable[[float], None] = time.sleep, per_minute: int = PER_MINUTE) -> bool:
    """True when the per-minute window has room; otherwise wait 62 s once and look again."""
    for attempt in range(2):
        if read()["current_usage"] < per_minute:
            return True
        if attempt == 0:
            log.warning("event=minute_limit_busy waiting=%ss", MINUTE_WAIT)
            sleep(MINUTE_WAIT)
    return False


class AccountGuard:
    """Wraps a usage reader (injectable for tests). Use `start()` before a batch, `allow(n)` before each chunk, `finish()` after."""

    def __init__(self, read: Callable[[], dict], sleep: Callable[[float], None] = time.sleep, cap: int = ACCOUNT_CAP):
        self.read, self.sleep, self.cap, self.before = read, sleep, cap, None

    def start(self, planned: int) -> int:
        """Credits we may spend, 0 if unsafe. Raises UsageUnavailable (callers fail closed)."""
        u = self.read()
        self.before = u["daily_usage"]
        if not minute_clear(self.read, self.sleep):
            log.warning("event=minute_limit_still_busy action=keep_old_data")
            return 0
        n = allowed_credits(self.read(), planned, self.cap)
        if n < planned:
            log.warning("event=account_cap planned=%d allowed=%d cap=%d daily_usage=%d", planned, n, self.cap, self.before)
        return n

    def allow(self, spent_so_far: int, wanted: int = CHUNK) -> int:
        return allowed_credits(self.read(), wanted, self.cap)

    def finish(self) -> None:
        try:
            after = self.read()["daily_usage"]
        except UsageUnavailable:
            after = "?"
        log.info("twelvedata account usage: %s/800 before, %s after", self.before, after)
