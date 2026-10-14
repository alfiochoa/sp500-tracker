import importlib.util
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from sp500.guard import ACCOUNT_CAP, AccountGuard, UsageUnavailable, allowed_credits, fetch_usage, minute_clear

ROOT = Path(__file__).resolve().parent.parent


def usage(daily=354, current=0):
    return {"daily_usage": daily, "plan_daily_limit": 800, "current_usage": current}


def test_thresholds_spend_only_while_daily_plus_planned_fits_560():
    assert ACCOUNT_CAP == 560
    assert allowed_credits(usage(354), 200) == 200          # 354 + 200 = 554 <= 560
    assert allowed_credits(usage(354), 300) == 206          # clipped so that daily_usage + spend == 560
    assert allowed_credits(usage(560), 10) == 0             # hard stop at 560
    assert allowed_credits(usage(700), 10) == 0             # market-desk may be above: we spend nothing


def test_fetch_usage_parses_and_fails_closed(monkeypatch):
    class R:
        def __init__(self, payload, code=200):
            self.payload, self.code = payload, code

        def raise_for_status(self):
            if self.code >= 400:
                raise RuntimeError("boom https://x?apikey=SECRET")

        def json(self):
            return self.payload

    class S:
        def __init__(self, r):
            self.r = r

        def get(self, *a, **k):
            if isinstance(self.r, Exception):
                raise self.r
            return self.r
    ok = fetch_usage("k", S(R({"daily_usage": 354, "plan_daily_limit": 800, "current_usage": 2})))
    assert ok == usage(354, 2)
    for bad in (R({}, 500), R({"status": "error", "code": 401}), R({"nothing": 1}), ConnectionError("x")):
        with pytest.raises(UsageUnavailable) as e:
            fetch_usage("SECRET", S(bad))
        assert "SECRET" not in str(e.value)


def test_minute_limit_waits_once_then_gives_up_or_proceeds():
    sleeps = []
    seq = iter([usage(current=8), usage(current=0)])
    assert minute_clear(lambda: next(seq), sleeps.append) is True and sleeps == [62]
    sleeps.clear()
    assert minute_clear(lambda: usage(current=8), sleeps.append) is False and sleeps == [62]  # busy twice: keep old data


def test_guard_logs_before_after(caplog):
    seq = iter([usage(354), usage(354), usage(354), usage(412)])
    g = AccountGuard(lambda: next(seq), lambda s: None)
    with caplog.at_level(logging.INFO, logger="sp500"):
        assert g.start(58) == 58
        g.finish()
    assert "twelvedata account usage: 354/800 before, 412 after" in caplog.text


def _refresh_module():
    spec = importlib.util.spec_from_file_location("refresh", ROOT / "scripts/refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def refresh(monkeypatch, tmp_path):
    mod = _refresh_module()
    live = tmp_path / "live.csv.gz"
    pd.DataFrame({"symbol": ["AAA", "BBB"], "date": ["2026-10-09"] * 2, "close": [1.0, 2.0]}).to_csv(live, index=False)
    monkeypatch.setattr(mod, "LIVE_FILE", live)
    monkeypatch.setattr(mod, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "STATUS", tmp_path / "status.json")
    monkeypatch.setattr(mod, "universe_symbols", lambda which: ["AAA", "BBB"])
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "k")

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("must not spend credits")
    monkeypatch.setattr(mod, "TwelveDataProvider", Boom)
    return mod


WED = datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc)


def test_refresh_fails_closed_when_api_usage_is_unreadable(refresh, capsys):
    def broken():
        raise UsageUnavailable("ConnectionError")
    assert refresh.main([], now=WED, guard=AccountGuard(broken, lambda s: None)) == 0   # exit 0, nothing spent
    assert "failing closed" in capsys.readouterr().out


def test_refresh_spends_nothing_above_the_account_cap(refresh, capsys):
    # 559 + 2 planned would exceed 560: only 1 credit allowed, so the provider is built and Boom proves the guard let one through
    with pytest.raises(AssertionError, match="must not spend credits"):
        refresh.main([], now=WED, guard=AccountGuard(lambda: usage(559), lambda s: None))
    assert refresh.main([], now=WED, guard=AccountGuard(lambda: usage(560), lambda s: None)) == 0
    assert "no credits may be spent" in capsys.readouterr().out


def test_streamlit_app_and_analytics_never_reference_twelve_data():
    for f in ("streamlit_app.py", "sp500/analytics.py", "sp500/alerts.py"):
        assert "twelvedata" not in (ROOT / f).read_text().lower(), f
