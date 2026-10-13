"""Watchlist alert rules + a MOCKED webhook (records payloads; never makes a network call)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass
class Rules:
    move_pct: float = 3.0       # |1d return| >= x %
    near_high: float = 95.0     # 52-week range position >= x
    near_low: float = 5.0       # 52-week range position <= x


def evaluate(symbols: list[str], ret: pd.DataFrame, sc: pd.DataFrame, rules: Rules) -> list[dict]:
    alerts = []
    for s in symbols:
        if s not in ret.index:
            continue
        r1, pos = ret.at[s, "1d"], sc.at[s, "range_pos"]
        if abs(r1) >= rules.move_pct:
            alerts.append({"symbol": s, "rule": "daily move", "detail": f"{r1:+.2f}% on the day"})
        if pos >= rules.near_high:
            alerts.append({"symbol": s, "rule": "near 52w high", "detail": f"range position {pos:.0f}/100"})
        if pos <= rules.near_low:
            alerts.append({"symbol": s, "rule": "near 52w low", "detail": f"range position {pos:.0f}/100"})
    return alerts


@dataclass
class MockWebhook:
    """Stand-in for Slack/Teams/Zapier. `send` only records the JSON body that WOULD be POSTed."""
    url: str = "https://example.invalid/hooks/demo"
    outbox: list[dict] = field(default_factory=list)
    log_path: Path | None = None

    def send(self, alerts: list[dict], as_of: str) -> dict:
        body = {"text": f"{len(alerts)} watchlist alert(s) - demo data as of {as_of}",
                "alerts": alerts, "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "disclaimer": "Informational only. Not investment advice."}
        self.outbox.append(body)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(body) + "\n")
        return body
