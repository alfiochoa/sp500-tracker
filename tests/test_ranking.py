import json
from datetime import date

import pandas as pd

from sp500.ranking import (
    choose_shares,
    latest_cover_shares,
    latest_public_float,
    pick_candidates,
    rank_by_market_cap,
    resolve_shares,
    sec_ticker,
)


def f(val, end, filed, accn, form="10-Q"):
    return {"val": val, "end": end, "filed": filed, "accn": accn, "form": form}


def test_cover_shares_sums_share_classes_of_the_latest_filing_only():
    facts = [f(100, "2025-01-31", "2025-02-05", "old"), f(60, "2026-07-20", "2026-07-25", "new"),
             f(40, "2026-07-20", "2026-07-25", "new"), f(60, "2026-07-20", "2026-07-25", "new"), f(5, "2026-08-01", "2026-08-02", "x", "8-K")]
    assert latest_cover_shares(facts) == (100, "2026-07-20")  # 60+40, duplicate value counted once, 8-K ignored
    assert latest_cover_shares([]) is None


def test_public_float_uses_latest_period():
    assert latest_public_float([f(5e9, "2025-06-30", "2025-08-01", "a", "10-K"), f(7e9, "2026-06-30", "2026-08-01", "b", "10-K")]) == (7e9, "2026-06-30")


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


def test_stale_share_counts_are_rejected_and_order_of_preference_is_kept():
    as_of = date(2026, 10, 13)
    opts = {"cover_page": (469_000_000, "2010-01-27"), "diluted_weighted_avg": (888_000_000, "2026-06-30")}
    assert choose_shares(opts, as_of) == ("diluted_weighted_avg", 888_000_000, "2026-06-30")  # 2010 cover figure is stale
    assert choose_shares({"cover_page": (1, "2026-07-01"), "diluted_weighted_avg": (2, "2026-06-30")}, as_of)[0] == "cover_page"
    assert choose_shares({"cover_page": (1, "2010-01-27")}, as_of) is None


def test_public_float_proxy_only_when_no_fresh_shares_and_an_anchor_exists():
    cand = pd.DataFrame({"symbol": ["OK", "NOSHARES", "NOANCHOR"], "share_options": [
        json.dumps({"cover_page": [100, "2026-07-01"]}), json.dumps({}), json.dumps({})],
        "public_float_usd": [1e9, 6e11, 5e9], "public_float_date": ["2025-06-30"] * 3})
    out, unresolved = resolve_shares(cand, date(2026, 10, 13), {"NOSHARES": 500.0})
    assert unresolved == ["NOANCHOR"]
    row = out.set_index("symbol")
    assert row.loc["OK", "shares_basis"] == "cover_page"
    assert row.loc["NOSHARES", "shares_basis"] == "public_float_proxy" and row.loc["NOSHARES", "shares"] == 1_200_000_000
