"""Top 200 S&P 500 by market cap - Streamlit dashboard. Real delayed closes (Twelve Data); not investment advice."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from sp500 import analytics as an
from sp500.alerts import MockWebhook, Rules, evaluate
from sp500.calendar import last_complete_trading_day
from sp500.constituents import ATTRIBUTION, SOURCE_URL
from sp500.providers import load_real_prices, load_synthetic_prices

st.set_page_config(page_title="S&P 500 Tracker", page_icon="📈", layout="wide")

BLUE, RED = "#2a78d6", "#e34948"
PAL = {
    "light": dict(ink="#0b0b0b", muted="#52514e", grid="#e3e2de", mid="#f0efec", pos="#256abf", neg="#e34948"),
    "dark": dict(ink="#f0efec", muted="#c3c2b7", grid="#383835", mid="#383835", pos="#3987e5", neg="#e66767"),
}
mode = "dark" if getattr(st.context.theme, "type", "light") == "dark" else "light"
P = PAL[mode]
DIV = [[0, P["neg"]], [0.5, P["mid"]], [1, P["pos"]]]

st.markdown("""<style>
.block-container{padding-top:2.2rem;max-width:1200px}
h1{font-size:1.75rem!important;font-weight:650!important;letter-spacing:-.01em;margin-bottom:.1rem}
h3{font-size:1.05rem!important;font-weight:600!important}
.pill{display:inline-block;padding:.18rem .6rem;border-radius:999px;font-size:.78rem;margin:0 .35rem .35rem 0;
 border:1px solid rgba(128,128,128,.35)}
.sim{border:2px solid #eb9a00;background:SIMBG;color:SIMINK;border-radius:10px;padding:.45rem .8rem;margin:.2rem 0 .5rem;font-size:.92rem;line-height:1.3}
[data-testid=stElementContainer]:has(.sim){position:sticky;top:3.2rem;z-index:50}
.real{border:1px solid rgba(128,128,128,.35);border-radius:10px;padding:.5rem .85rem;margin:.2rem 0 .5rem;font-size:.92rem;line-height:1.35}
.real small{font-size:.78rem;opacity:.85}
.sim small{font-size:.78rem}
.sim b{letter-spacing:.04em}
.pill.warn{background:rgba(235,160,0,.16);border-color:rgba(235,160,0,.5)}
[data-testid=stMetricValue]{font-size:1.45rem}
@media(max-width:640px){.block-container{padding:1.2rem .8rem}h1{font-size:1.4rem!important}}
</style>""".replace("SIMBG", "#faeed2" if mode == "light" else "#3d3216").replace("SIMINK", P["ink"]),
            unsafe_allow_html=True)


DATA = Path(__file__).resolve().parent / "data"
SYNTH = os.environ.get("SP500_SYNTHETIC") == "1"   # tests / offline dev only; the deployed demo never sets this


def _json(name: str) -> dict:
    try:
        return json.loads((DATA / name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _sig() -> tuple:
    """(mtime, size) of the data files: part of the cache key so a refresh commit is picked up."""
    out = []
    for f in (DATA / "live_prices.csv.gz", DATA / "ranking.csv", DATA / "refresh_status.json"):
        st_ = f.stat() if f.exists() else None
        out.append((f.name, st_.st_mtime_ns if st_ else 0, st_.st_size if st_ else 0))
    return tuple(out)


@st.cache_data(show_spinner="Loading data...", ttl=3600)
def load(sig: tuple, synth: bool):
    return load_synthetic_prices() if synth else load_real_prices()


uni, px_all, meta = load(_sig(), SYNTH)
if uni is None:
    st.title("S&P 500 Tracker")
    st.error("No real price data is available yet (data/ranking.csv and data/live_prices.csv.gz). "
             "Nothing is simulated in this app; run the ranking and refresh scripts (see README).")
    st.stop()
sim = SYNTH
rk, status = _json("ranking_meta.json"), _json("refresh_status.json")


def style(fig: go.Figure, h: int = 380, wm_bottom: bool = False, wm: str = "", **kw) -> go.Figure:
    fig.update_layout(height=h, margin=dict(l=4, r=12, t=8 if wm_bottom else 34, b=28 if wm_bottom else 4), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color=P["muted"], size=12), showlegend=False, **kw)
    fig.add_annotation(text=wm or ("SYNTHETIC TEST DATA" if sim else "REAL CLOSES · Twelve Data"), xref="paper", yref="paper", x=1,
                       y=0 if wm_bottom else 1.0, yshift=-18 if wm_bottom else 24, showarrow=False,
                       font={"color": "#eb9a00" if (sim and not wm) else P["muted"], "size": 11}, xanchor="right")
    return fig


def bar_h(labels, values, title_x, colors):
    fig = go.Figure(go.Bar(y=labels, x=values, orientation="h", marker=dict(color=colors, cornerradius=4),
                           text=[f"{v:+.1f}%" if title_x.startswith("%") else f"{v:.1f}%" for v in values],
                           textposition="outside", cliponaxis=False, textfont=dict(color=P["ink"], size=11),
                           hovertemplate="%{y}: %{x:.2f}%<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=P["ink"], size=11))
    lo, hi = min(0, min(values)), max(values)
    fig.update_xaxes(range=[lo * 1.25, hi * 1.25 if hi > 0 else 0.5], showgrid=True, gridcolor=P["grid"], zeroline=True, zerolinecolor=P["muted"], ticksuffix="%")
    return fig


st.title("S&P 500 Tracker")
syms = list(uni.symbol)
N = len(syms)
px = px_all[syms].dropna(how="all").ffill(limit=3)
as_of = str(px.index[-1].date())
target = last_complete_trading_day(datetime.now(timezone.utc))
n_fresh = int((px_all[syms].apply(lambda c: c.last_valid_index()) >= str(target)).sum())
if sim:
    st.markdown(f'<div class="sim"><b>SYNTHETIC TEST DATA</b> (seeded random walks, as of {meta.get("as_of")}) - not market data. '
                "This mode exists only for tests and offline development and is never shown in the deployed demo.</div>",
                unsafe_allow_html=True)
else:
    refreshed = status.get("refreshed_at", "")[:16].replace("T", " ")
    st.markdown(
        f'<div class="real"><b>Top {N} S&amp;P 500 constituents by market cap</b> - real end-of-day closes from Twelve Data, delayed, '
        "not real-time, not investment advice. Constituent list from Wikipedia (CC BY-SA 4.0).<br>"
        f"<small>Ranking as of {rk.get('as_of', '?')} (SEC EDGAR shares outstanding x close). Last refresh {refreshed or '?'} UTC · "
        f"{n_fresh} of {N} symbols fresh (latest close {target}) · prices through {as_of}.</small></div>", unsafe_allow_html=True)
cons = uni[["symbol", "name", "sector"]].copy()
caps = uni.set_index("symbol").market_cap_usd_b
ret, sc = an.returns_table(px), an.scores(px)
tag = ("SYNTHETIC" if sim else "REAL closes") + f", top {N}"
st.markdown(
    f'<span class="pill {"warn" if sim else ""}">{tag}</span><span class="pill">Prices as of {as_of}</span>'
    f'<span class="pill">Reference only · not investment advice</span>', unsafe_allow_html=True)

adv = int((ret["1d"] > 0).sum())
k = st.columns(4)
k[0].metric("Symbols fresh", f"{n_fresh} of {N}")
k[1].metric("Advancers / decliners (1d)", f"{adv} / {len(ret) - adv}")
k[2].metric("Median 1d return", f"{ret['1d'].median():+.2f}%")
k[3].metric("Median YTD return", f"{ret['YTD'].median():+.1f}%")

t_over, t_mov, t_scr, t_cor, t_wl, t_about = st.tabs(
    ["Overview", "Movers", "Screener", "Correlation", "Watchlist", "Sources & limits"])

with t_over:
    wm = st.radio("Sector weighting", ["Cap-weighted (market cap)", "Equal-weighted"], horizontal=True)
    d = an.sector_weights(cons, caps, "cap" if wm.startswith("Cap") else "equal")
    wts = d.groupby("sector").weight.sum().sort_values(ascending=False)
    ss = an.sector_summary(d, ret)
    ss.index = [f"{i} (n={int(n)})" for i, n in zip(ss.index, ss.stocks)]
    a, b = st.columns([5, 6])
    with a:
        st.markdown(f"### Sector weights (top {N} by market cap)")
        fig = bar_h(wts.index, wts.values, "w", BLUE if mode == "light" else "#3987e5")
        fig.update_xaxes(ticksuffix="%")
        st.plotly_chart(style(fig, 400, wm=f"MARKET CAP · top {N}" if not sim else "SYNTHETIC CAPS"), width="stretch", config={"displayModeBar": False})
    with b:
        st.markdown(f"### Sector returns (%) · {tag}")
        z = ss[["1d", "1w", "1m", "YTD"]]
        lim = float(max(abs(z.values.min()), abs(z.values.max()), 1))
        fig = go.Figure(go.Heatmap(z=z.values, x=list(z.columns), y=list(z.index), colorscale=DIV, zmid=0, zmin=-lim,
                                   zmax=lim, text=[[f"{v:+.1f}" for v in r] for r in z.values], texttemplate="%{text}",
                                   xgap=2, ygap=2, colorbar=dict(thickness=8, ticksuffix="%", len=0.9),
                                   hovertemplate="%{y} · %{x}: %{z:+.2f}%<extra></extra>"))
        fig.update_yaxes(autorange="reversed", tickfont=dict(color=P["ink"], size=11))
        fig.update_xaxes(side="top", tickfont=dict(color=P["ink"]))
        st.plotly_chart(style(fig, 420, wm_bottom=True), width="stretch", config={"displayModeBar": False})
    st.caption("Blue = up, red = down. Weights are market caps (SEC shares x close) within this top-" + str(N) + " universe; n = symbols per sector.")

with t_mov:
    per = st.radio("Period", ["1d", "1w", "1m", "YTD"], horizontal=True, key="per")
    top, bot = an.movers(ret, per, 10)
    a, b = st.columns(2)
    for col, df, title, c in ((a, top, "Top 10", P["pos"]), (b, bot, "Bottom 10", P["neg"])):
        with col:
            st.markdown(f"### {title} · {per} · {tag}")
            fig = bar_h(df.index, df[per], "%", c)
            st.plotly_chart(style(fig, 360), width="stretch", config={"displayModeBar": False})

with t_scr:
    st.markdown("### Screener: momentum, volatility, 52-week range" + (" · <span class='pill warn'>SYNTHETIC</span>" if sim else ""), unsafe_allow_html=True)
    f1, f2 = st.columns([2, 3])
    secs = f1.multiselect("Sector", sorted(cons.sector.unique()))
    q = f2.text_input("Search symbol or name", placeholder="e.g. NVDA or Visa")
    exp = an.to_export(cons, ret, sc, caps)
    if secs:
        exp = exp[exp.sector.isin(secs)]
    if q:
        exp = exp[exp.symbol.str.contains(q, case=False) | exp["name"].str.contains(q, case=False)]
    st.dataframe(exp.sort_values("momentum", ascending=False), hide_index=True, height=420, width="stretch",
                 column_order=["symbol", "name", "sector", "price", "market_cap_usd_b", "1d", "1w", "1m", "YTD", "momentum", "volatility", "range_pos"],
                 column_config={
                     "momentum": st.column_config.ProgressColumn("Momentum (0-100)", min_value=0, max_value=100, format="%.0f"),
                     "range_pos": st.column_config.ProgressColumn("52w range pos.", min_value=0, max_value=100, format="%.0f"),
                     "volatility": st.column_config.NumberColumn("Vol 60d ann. %", format="%.1f"),
                     "price": st.column_config.NumberColumn("Price", format="%.2f"),
                     "market_cap_usd_b": st.column_config.NumberColumn("Mkt cap $B", format="%.0f")})
    st.download_button("Download CSV", exp.to_csv(index=False), f"sp500_screen_{as_of}.csv", "text/csv")
    st.caption("Momentum = percentile of 6-month return skipping the latest week. Volatility = 60-day annualised. "
               "Range position: 0 = 52-week low, 100 = 52-week high.")

with t_cor:
    st.markdown("### Correlation of daily returns for a basket")
    default = ([s for s in ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "JNJ", "PG", "HD", "NEE"] if s in cons.symbol.values]
               or sorted(cons.symbol)[:8])
    basket = st.multiselect("Basket (2-25 symbols)", sorted(cons.symbol), default=default, max_selections=25)
    win = st.select_slider("Window (trading days)", [21, 63, 126, 252], value=126)
    if len(basket) >= 2:
        cm = an.correlation(px, basket, win)
        fig = go.Figure(go.Heatmap(z=cm.values, x=list(cm.columns), y=list(cm.index), colorscale=DIV, zmin=-1, zmax=1,
                                   zmid=0, xgap=2, ygap=2, text=cm.round(2).values, texttemplate="%{text}" if len(basket) <= 12 else None,
                                   colorbar=dict(thickness=8), hovertemplate="%{y} / %{x}: %{z:.2f}<extra></extra>"))
        fig.update_yaxes(autorange="reversed", tickfont=dict(color=P["ink"]))
        fig.update_xaxes(tickfont=dict(color=P["ink"]))
        st.plotly_chart(style(fig, 520), width="stretch", config={"displayModeBar": False})
    else:
        st.info("Pick at least two symbols.")

with t_wl:
    st.markdown("### Watchlist & alerts (webhook is mocked)")
    a, b = st.columns([3, 2])
    wl = a.multiselect("Watchlist", sorted(cons.symbol), default=([s for s in ["AAPL", "NVDA", "TSLA", "JPM", "XOM", "PG"] if s in cons.symbol.values] or sorted(cons.symbol)[:6]))
    r1, r2, r3 = b.columns(3)
    rules = Rules(r1.number_input("Move ≥ %", 0.5, 20.0, 2.5, 0.5), r2.number_input("Near high ≥", 50, 100, 95), r3.number_input("Near low ≤", 0, 50, 5))
    alerts = evaluate(wl, ret, sc, rules)
    if alerts:
        st.dataframe(alerts, hide_index=True, width="stretch")
        if sim:
            st.caption("Alerts are computed on SYNTHETIC test prices.")
    else:
        st.success("No rules triggered for this watchlist.")
    if st.button("Send to mock webhook", disabled=not alerts):
        st.json(MockWebhook().send(alerts, as_of), expanded=1)
        st.caption("Nothing was sent anywhere: the webhook is a stand-in that only shows the payload.")

with t_about:
    st.markdown(f"""
### What this is
**Top {N} S&P 500 constituents by market cap**, every symbol with **real end-of-day closes** from Twelve Data (delayed,
not real-time). The other ~{503 - N} index members are not shown and have no prices here. Nothing in this app is simulated.

### Ranking method
Market cap = shares outstanding (SEC EDGAR XBRL cover-page `dei:EntityCommonStockSharesOutstanding`, all share classes
summed) x close on {rk.get('as_of', '?')}. A generous candidate set was pre-selected by SEC `dei:EntityPublicFloat`
and then ranked by market cap. Limits: share counts lag (latest 10-K/10-Q cover), companies with several share classes
use the company total (so both lines of a dual-class company show the same cap), approximate by design. Rebuilt
manually/quarterly by `scripts/build_ranking.py`.

### Data sources & terms
- **Constituents**: [Wikipedia]({SOURCE_URL}) via the MediaWiki API, cached, with a breakage detector. {ATTRIBUTION}
- **Prices**: Twelve Data free tier (8 req/min), refreshed by a scheduled GitHub Action only for symbols whose close is stale.
- **Shares outstanding / public float**: SEC EDGAR XBRL API (public domain, fair-access limits respected).

### Limitations
Not investment advice; no trading features. Delayed end-of-day data; gaps possible. Scores are descriptive statistics, not
forecasts. Prices as of **{as_of}**.
""")
