"""S&P 500 Tracker & Analysis - Streamlit dashboard. Reference/demo data; not investment advice."""
from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from sp500 import analytics as an
from sp500.alerts import MockWebhook, Rules, evaluate
from sp500.constituents import ATTRIBUTION, SOURCE_URL, load_constituents
from sp500.providers import SnapshotProvider, load_dashboard_prices

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
.sim small{font-size:.78rem}
.sim b{letter-spacing:.04em}
.pill.warn{background:rgba(235,160,0,.16);border-color:rgba(235,160,0,.5)}
[data-testid=stMetricValue]{font-size:1.45rem}
@media(max-width:640px){.block-container{padding:1.2rem .8rem}h1{font-size:1.4rem!important}}
</style>""".replace("SIMBG", "#faeed2" if mode == "light" else "#3d3216").replace("SIMINK", P["ink"]),
            unsafe_allow_html=True)


DATA = Path(__file__).resolve().parent / "data"


def _sig() -> tuple:
    """(mtime, size) of the data files: part of the cache key so a refresh commit is picked up."""
    out = []
    for f in (DATA / "live_prices.csv.gz", DATA / "constituents.csv", DATA / "snapshot" / "prices.csv.gz"):
        st_ = f.stat() if f.exists() else None
        out.append((f.name, st_.st_mtime_ns if st_ else 0, st_.st_size if st_ else 0))
    return tuple(out)


@st.cache_data(show_spinner="Loading data...", ttl=3600)
def load(sig: tuple):
    cons = load_constituents(max_age_days=10**6)  # the app never hits the network; the scheduled job refreshes
    px, source, meta = load_dashboard_prices()
    cons = cons[cons.symbol.isin(px.columns)].reset_index(drop=True)
    return cons, px, source, meta, SnapshotProvider().market_caps()


cons, px_all, source, meta, caps = load(_sig())
n_live = int((source == "live").sum())
n_demo = len(source) - n_live


def style(fig: go.Figure, h: int = 380, wm_bottom: bool = False, wm: str = "", **kw) -> go.Figure:
    fig.update_layout(height=h, margin=dict(l=4, r=12, t=8 if wm_bottom else 34, b=28 if wm_bottom else 4), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color=P["muted"], size=12), showlegend=False, **kw)
    if True:
        fig.add_annotation(text=wm or ("SIMULATED DEMO DATA" if sim else "LIVE DATA · Twelve Data"), xref="paper", yref="paper", x=1, y=0 if wm_bottom else 1.0, yshift=-18 if wm_bottom else 24, showarrow=False,
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
M = len(source)
if n_demo:
    if n_live == 0:
        head = "<b>SIMULATED</b> demo prices - not real market data. Company list is scraped from Wikipedia."
        note = "Live mode is OFF: no live data file yet (needs the TWELVE_DATA_API_KEY secret and the scheduled refresh workflow)."
    else:
        through = meta.get("live_through", "?")
        head = (f"<b>Live prices cover {n_live} of {M} symbols</b> (real daily closes via Twelve Data, through {through}). "
                f"The other {n_demo} use <b>SIMULATED</b> demo prices - not real market data. "
                "Company list is scraped from Wikipedia.")
        note = "Live and simulated symbols are never ranked together: choose a price universe below."
    st.markdown(f'<div class="sim">{head}<br><small>{note}</small></div>', unsafe_allow_html=True)

opts = ([f"Live prices ({n_live} symbols)"] if n_live else []) + ([f"Simulated prices ({n_demo} symbols)"] if n_demo else [])
view = st.radio("Price universe", opts, horizontal=True) if len(opts) > 1 else opts[0]
sim = view.startswith("Simulated")
syms = list(source[source == ("demo" if sim else "live")].index)
px = px_all[syms].dropna(how="all").ffill(limit=3)
cons_all, cons = cons, cons[cons.symbol.isin(syms)].reset_index(drop=True)
ret, sc = an.returns_table(px), an.scores(px)
as_of = str(px.index[-1].date())
tag = ("SIMULATED" if sim else "LIVE") + f", {len(syms)} symbols"
st.markdown(
    f'<span class="pill {"warn" if sim else ""}">{tag}</span><span class="pill">Prices as of {as_of}</span>'
    f'<span class="pill">Reference only · not investment advice</span>', unsafe_allow_html=True)

adv = int((ret["1d"] > 0).sum())
k = st.columns(4)
k[0].metric("Symbols in this view", f"{len(cons)} of {len(cons_all)}")
k[1].metric("Advancers / decliners (1d)", f"{adv} / {len(ret) - adv}")
k[2].metric("Median 1d return", f"{ret['1d'].median():+.2f}%")
k[3].metric("Median YTD return", f"{ret['YTD'].median():+.1f}%")

t_over, t_mov, t_scr, t_cor, t_wl, t_about = st.tabs(
    ["Overview", "Movers", "Screener", "Correlation", "Watchlist", "Sources & limits"])

with t_over:
    wm = st.radio("Sector weighting", ["Cap-weighted (illustrative caps)", "Equal-weighted"], horizontal=True)
    d = an.sector_weights(cons_all, caps, "cap" if wm.startswith("Cap") else "equal")
    wts = d.groupby("sector").weight.sum().sort_values(ascending=False)
    ss = an.sector_summary(d[d.symbol.isin(syms)], ret)
    ss.index = [f"{i} (n={int(n)})" for i, n in zip(ss.index, ss.stocks)]
    a, b = st.columns([5, 6])
    with a:
        st.markdown("### Sector weights (all 503, composition)")
        fig = bar_h(wts.index, wts.values, "w", BLUE if mode == "light" else "#3987e5")
        fig.update_xaxes(ticksuffix="%")
        st.plotly_chart(style(fig, 400, wm="ILLUSTRATIVE CAPS · all 503 symbols"), width="stretch", config={"displayModeBar": False})
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
    st.caption("Blue = up, red = down. Weights are illustrative caps for all 503 names; returns cover only the chosen price universe (n = symbols per sector).")

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
    st.markdown("### Screener: momentum, volatility, 52-week range" + (" · <span class='pill warn'>SIMULATED prices</span>" if sim else ""), unsafe_allow_html=True)
    f1, f2 = st.columns([2, 3])
    secs = f1.multiselect("Sector", sorted(cons.sector.unique()))
    q = f2.text_input("Search symbol or name", placeholder="e.g. NVDA or Visa")
    exp = an.to_export(cons, ret, sc, source[syms])
    exp["data_source"] = exp["data_source"].map({"demo": "SIMULATED", "live": "live"})
    if secs:
        exp = exp[exp.sector.isin(secs)]
    if q:
        exp = exp[exp.symbol.str.contains(q, case=False) | exp["name"].str.contains(q, case=False)]
    st.dataframe(exp.sort_values("momentum", ascending=False), hide_index=True, height=420, width="stretch",
                 column_order=["symbol", "name", "sector", "price", "data_source", "1d", "1w", "1m", "YTD", "momentum", "volatility", "range_pos"],
                 column_config={
                     "momentum": st.column_config.ProgressColumn("Momentum (0-100)", min_value=0, max_value=100, format="%.0f"),
                     "range_pos": st.column_config.ProgressColumn("52w range pos.", min_value=0, max_value=100, format="%.0f"),
                     "volatility": st.column_config.NumberColumn("Vol 60d ann. %", format="%.1f"),
                     "price": st.column_config.NumberColumn("Price", format="%.2f"),
                     "data_source": st.column_config.TextColumn("Prices")})
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
            st.caption("Alerts are computed on SIMULATED demo prices.")
    else:
        st.success("No rules triggered for this watchlist.")
    if st.button("Send to mock webhook", disabled=not alerts):
        st.json(MockWebhook().send(alerts, as_of), expanded=1)
        st.caption("Nothing was sent anywhere: the webhook is a stand-in that only shows the payload.")

with t_about:
    st.markdown(f"""
### Data sources & terms
- **Constituents**: [Wikipedia]({SOURCE_URL}), fetched via the MediaWiki API (HTML fallback honours robots.txt), cached,
  with a breakage detector. {ATTRIBUTION}
- **Prices**: pluggable providers. *Snapshot* (bundled **synthetic demo**, seeded, not real quotes), *Twelve Data*
  (free tier, 8 req/min, 800/day, refreshed by a scheduled GitHub Action) and an optional, off-by-default *Yahoo*
  provider for personal use only.
- **Market caps / weights**: illustrative orders of magnitude, not index-provider weights.

### Limitations
Not investment advice; no trading features. Demo prices are synthetic. Live data covers only the symbols the scheduled
job has refreshed so far; the rest stay SIMULATED, and the two sets are never ranked together (pick a price universe). Scores are descriptive statistics, not forecasts.
Prices as of **{as_of}**.
""")
