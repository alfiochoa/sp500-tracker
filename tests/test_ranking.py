from datetime import date

import pandas as pd

from sp500.ranking import latest_cover_shares, latest_public_float, pick_candidates, rank_by_market_cap, sec_ticker


def f(val, end, filed, accn, form="10-Q"):
    return {"val": val, "end": end, "filed": filed, "accn": accn, "form": form}


def test_cover_shares_sums_share_classes_of_the_latest_filing_only():
    facts = [f(100, "2025-01-31", "2025-02-05", "old"), f(60, "2026-07-20", "2026-07-25", "new"),
             f(40, "2026-07-20", "2026-07-25", "new"), f(60, "2026-07-20", "2026-07-25", "new"), f(5, "2026-08-01", "2026-08-02", "x", "8-K")]
    assert latest_cover_shares(facts) == (100, "2026-07-20")  # 60+40, duplicate value counted once, 8-K ignored
    assert latest_cover_shares([]) is None


def test_public_float_uses_latest_period():
    assert latest_public_float([f(5e9, "2025-06-30", "2025-08-01", "a", "10-K"), f(7e9, "2026-06-30", "2026-08-01", "b", "10-K")]) == 7e9


def test_candidates_are_top_n_by_float_and_keep_missing_float():
    sec = pd.DataFrame({"symbol": list("ABCD"), "public_float_usd": [1.0, 9.0, 5.0, None]})
    got = pick_candidates(sec, 2)
    assert set(got.symbol) == {"B", "C", "D"}  # A is outside the top 2 by float; D (no float reported) is never silently dropped


def test_rank_by_market_cap_uses_common_date_and_orders_descending():
    closes = pd.DataFrame({"X": [10, 11], "Y": [100, 90], "Z": [1, 1]}, index=pd.to_datetime(["2026-10-12", "2026-10-13"]))
    cand = pd.DataFrame({"symbol": list("XYZ"), "shares": [1e9, 1e8, 5e9], "float_rank": [1, 2, 3]})
    top = rank_by_market_cap(cand, closes, date(2026, 10, 12), top_n=2)
    assert list(top.symbol) == ["X", "Y"] and list(top["rank"]) == [1, 2]
    assert top.market_cap_usd_b.tolist() == [10.0, 10.0] and (top.price_date == "2026-10-12").all()


def test_sec_ticker_class_shares():
    assert sec_ticker("BRK.B") == "BRK-B"
