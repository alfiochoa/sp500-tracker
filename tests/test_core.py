import json

import pandas as pd
import pytest

from sp500 import analytics as an
from sp500.alerts import MockWebhook, Rules, evaluate
from sp500.constituents import StructureChanged, detect_breakage, load_constituents, parse_table
from sp500.http import RateLimiter
from sp500.providers import LiveFileProvider, TwelveDataProvider, YahooProvider, load_real_prices, load_synthetic_prices
from sp500.store import Store


def synth():
    """Synthetic test snapshot (dev/synthetic_snapshot): hermetic, never real data."""
    return load_synthetic_prices()


def _table_html(n=500, drop=None):
    rows = "".join(f"<tr><td>S{chr(65 + i % 26)}{chr(65 + i // 26 % 26)}</td><td>Co {i}</td><td>Energy</td>"
                   f"<td>Oil</td><td>Town, TX</td><td>2000-01-01</td></tr>" for i in range(n))
    cols = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Headquarters Location", "Date added"]
    if drop:
        cols.remove(drop)
        rows = rows.replace("<td>Town, TX</td>", "")
    head = "".join(f"<th>{c}</th>" for c in cols)
    return f'<table id="constituents"><tr>{head}</tr>{rows}</table>'


def test_breakage_detector_accepts_good_table():
    assert len(detect_breakage(parse_table(_table_html()))) == 500


def test_breakage_detector_flags_missing_column_and_row_count():
    with pytest.raises(StructureChanged, match="missing columns"):
        detect_breakage(pd.DataFrame({"Symbol": ["A"]}))
    with pytest.raises(StructureChanged, match="row count"):
        detect_breakage(parse_table(_table_html(50)))


def test_constituents_cache_used_when_fresh(tmp_path):
    p = tmp_path / "c.csv"
    pd.DataFrame({"symbol": ["A"], "name": ["x"], "sector": ["Energy"]}).to_csv(p, index=False)
    assert load_constituents(p, session=object()).symbol.tolist() == ["A"]  # session unused => no network


def test_constituents_failure_falls_back_to_cache(tmp_path):
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("down")
    p = tmp_path / "c.csv"
    pd.DataFrame({"symbol": ["A"], "name": ["x"], "sector": ["Energy"]}).to_csv(p, index=False)
    assert len(load_constituents(p, refresh=True, session=Boom())) == 1


def test_synthetic_snapshot_is_valid_and_labelled():
    uni, px, meta = synth()
    assert px.shape[1] == 200 == len(uni) and len(px) >= 250
    assert "SYNTHETIC" in meta["label"] and "never shown in the deployed demo" in meta["label"]


def test_analytics_shapes_and_ranges():
    _, px, _ = synth()
    ret, sc = an.returns_table(px), an.scores(px)
    assert list(ret.columns) == ["1d", "1w", "1m", "YTD"]
    assert sc.range_pos.between(0, 100).all() and sc.momentum.between(0, 100).all()
    top, bot = an.movers(ret, "1d", 5)
    assert top.iloc[0, 0] >= top.iloc[-1, 0] and bot.iloc[0, 0] <= bot.iloc[-1, 0]
    cm = an.correlation(px, list(px.columns[:3]))
    assert cm.shape == (3, 3) and cm.values.diagonal().tolist() == [1.0] * 3


def test_sector_weights_sum_to_100():
    uni, px, _ = synth()
    d = an.sector_weights(uni, uni.set_index("symbol").market_cap_usd_b, "cap")
    assert d.weight.sum() == pytest.approx(100)
    assert an.sector_summary(d, an.returns_table(px)).weight.sum() == pytest.approx(100)


def test_alerts_and_mock_webhook(tmp_path):
    ret = pd.DataFrame({"1d": [4.0, 0.1]}, index=["A", "B"])
    sc = pd.DataFrame({"range_pos": [99.0, 50.0]}, index=["A", "B"])
    al = evaluate(["A", "B", "ZZZ"], ret, sc, Rules())
    assert {a["rule"] for a in al} == {"daily move", "near 52w high"}
    hook = MockWebhook(log_path=tmp_path / "o.jsonl")
    body = hook.send(al, "2026-01-01")
    assert "not investment advice" in body["disclaimer"].lower() and hook.outbox and (tmp_path / "o.jsonl").exists()


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["symbol"])
        return FakeResp(self.payloads[params["symbol"]])


def test_twelvedata_parses_validates_and_skips_errors():
    good = {"status": "ok", "values": [{"datetime": "2026-01-03", "close": "11.5", "volume": "10"},
                                       {"datetime": "2026-01-02", "close": "10.0", "volume": "5"},
                                       {"datetime": "2026-01-01", "close": "-1", "volume": "5"}]}
    sess = FakeSession({"AAA": good, "BBB": {"status": "error", "code": 429, "message": "limit"}})
    p = TwelveDataProvider("k", store=Store(":memory:"), session=sess, limiter=RateLimiter(60000))
    out = p.history(["AAA", "BBB"])
    assert [b.close for b in out["AAA"]] == [10.0, 11.5] and "BBB" not in out  # bad bar dropped, error skipped
    assert len(p.store.bars()) == 2


def test_twelvedata_never_leaks_key(caplog):
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("https://x?apikey=SECRET123")
    p = TwelveDataProvider("SECRET123", session=Boom(), limiter=RateLimiter(60000))
    p.history(["AAA"])
    assert "SECRET123" not in caplog.text


def test_yahoo_is_off_by_default():
    with pytest.raises(RuntimeError, match="disabled by default"):
        YahooProvider()


def test_rate_limiter_spaces_calls():
    t = [0.0]
    slept = []
    rl = RateLimiter(8, clock=lambda: t[0], sleep=lambda d: (slept.append(d), t.__setitem__(0, t[0] + d)))
    for _ in range(3):
        rl.wait()
    assert slept == pytest.approx([7.5, 7.5])


def test_load_real_prices_uses_only_ranked_symbols_and_never_invents(tmp_path):
    f, r = tmp_path / "live.csv.gz", tmp_path / "ranking.csv"
    pd.DataFrame({"symbol": ["AAA"] * 3 + ["ZZZ"] * 3, "date": ["2026-10-08", "2026-10-09", "2026-10-12"] * 2,
                  "close": [1.0, 2.0, 3.0] * 2}).to_csv(f, index=False)
    pd.DataFrame({"symbol": ["AAA", "BBB"], "name": ["A", "B"], "sector": ["Energy"] * 2, "market_cap_usd_b": [9, 8]}).to_csv(r, index=False)
    uni, px, meta = load_real_prices(r, LiveFileProvider(f))
    assert list(px.columns) == ["AAA"] and list(uni.symbol) == ["AAA"] and px["AAA"].iloc[-1] == 3.0  # BBB has no data: dropped, not faked
    assert meta["mode"] == "real"
    json.dumps(meta)
    assert load_real_prices(r, LiveFileProvider(tmp_path / "missing.csv.gz")) == (None, None, {})


def test_twelvedata_marks_rate_limiting_so_callers_can_wait_and_retry():
    import requests

    class Limited:
        def get(self, *a, **k):
            raise requests.exceptions.RetryError("too many 429 responses")
    p = TwelveDataProvider("k", session=Limited(), limiter=RateLimiter(60000))
    assert p.fetch_one("AAA", 5) == [] and p.last_code == 429
