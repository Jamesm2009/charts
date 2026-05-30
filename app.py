"""
Custom Charting Tool
Dash + Plotly | yfinance | Upstash Redis + local pickle cache
"""

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os, pickle, json
import requests as req_lib

app = dash.Dash(
    __name__,
    title="Custom Charts",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server

app.index_string = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>{%title%}</title>
{%favicon%}
{%css%}
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&display=swap');
* { box-sizing: border-box; }
body { margin: 0; padding: 0; background: #0d1117; font-family: 'IBM Plex Mono', monospace; }
label { color: #e6edf3 !important; cursor: pointer; }
input[type=radio] { accent-color: #4a9eff; cursor: pointer; }
@media (max-width: 768px) {
    #stats-sidebar { display: none !important; }
    #mobile-stats-container { display: block !important; }
}
@media (min-width: 769px) {
    #mobile-stats-container { display: none !important; }
}
.js-plotly-plot .plotly .modebar { background: transparent !important; }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

CACHE_DIR        = "cache"
REDIS_URL        = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN      = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
LOCAL_TTL        = 3600
REDIS_TTL_DAILY  = 3600
REDIS_TTL_WEEKLY = 21600
os.makedirs(CACHE_DIR, exist_ok=True)

C = {
    "price":   "#4a9eff",
    "ema21":   "#93c5fd",
    "ema63":   "#f97316",
    "bb_band": "rgba(160,160,170,0.40)",
    "bb_fill": "rgba(160,160,170,0.06)",
    "mom_pos": "rgba(52,211,153,0.28)",
    "mom_neg": "rgba(248,113,113,0.28)",
    "vol_up":  "rgba(52,211,153,0.50)",
    "vol_dn":  "rgba(248,113,113,0.50)",
    "rv":      "#a78bfa",
    "stoch_k": "#60a5fa",
    "stoch_d": "#f87171",
    "ob":      "#f87171",
    "os_":     "#34d399",
    "bg":      "#0d1117",
    "panel":   "#161b22",
    "border":  "#30363d",
    "text":    "#e6edf3",
    "muted":   "#8b949e",
}

def redis_set(key, value, ex=3600):
    if not REDIS_URL or not REDIS_TOKEN:
        return False
    try:
        r = req_lib.post(f"{REDIS_URL}/set/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            json={"value": value, "ex": ex}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def redis_get(key):
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    try:
        r = req_lib.get(f"{REDIS_URL}/get/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"}, timeout=8)
        if r.status_code != 200:
            return None
        return r.json().get("result")
    except Exception:
        return None

def _ckey(ticker, is_weekly):
    return f"kcm_{ticker}_{'w' if is_weekly else 'd'}"

def _df_to_json(df):
    return df.reset_index().to_json(orient="records", date_format="iso")

def _json_to_df(s):
    recs = json.loads(s)
    df = pd.DataFrame(recs)
    dc = df.columns[0]
    df[dc] = pd.to_datetime(df[dc])
    return df.set_index(dc).sort_index()

def _save_local(path, df, info):
    try:
        with open(path, "wb") as f:
            pickle.dump({"df": df, "info": info, "ts": datetime.now().timestamp()}, f)
    except Exception:
        pass

def load_cache(ticker, is_weekly):
    key  = _ckey(ticker, is_weekly)
    path = os.path.join(CACHE_DIR, f"{key}.pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                d = pickle.load(f)
            if datetime.now().timestamp() - d.get("ts", 0) < LOCAL_TTL:
                return d["df"], d.get("info", {})
        except Exception:
            pass
    raw = redis_get(key)
    if raw:
        try:
            p  = json.loads(raw)
            df = _json_to_df(p["df"])
            info = p.get("info", {})
            _save_local(path, df, info)
            return df, info
        except Exception:
            pass
    return None, None

def save_cache(ticker, is_weekly, df, info):
    key = _ckey(ticker, is_weekly)
    _save_local(os.path.join(CACHE_DIR, f"{key}.pkl"), df, info)
    ttl = REDIS_TTL_WEEKLY if is_weekly else REDIS_TTL_DAILY
    try:
        payload = json.dumps({"df": _df_to_json(df), "info": info or {}})
        redis_set(key, payload, ex=ttl)
    except Exception:
        pass

def fetch_data(ticker, is_weekly=False):
    ticker = ticker.upper().strip()
    df, info = load_cache(ticker, is_weekly)
    if df is not None:
        return df, info
    days  = 1925 if is_weekly else 485
    end   = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None, None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if is_weekly:
        agg = {"Close": "last", "Open": "first", "High": "max", "Low": "min"}
        if "Volume" in df.columns:
            agg["Volume"] = "sum"
        df = df.resample("W").agg(agg)
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}
    df = df.dropna(subset=["Close"])
    save_cache(ticker, is_weekly, df, info)
    return df, info

def apply_all(df):
    df = df.copy()
    close = df["Close"]
    # BB bands (volume-adjusted if available)
    mid = close.rolling(21).mean()
    std = close.rolling(21).std()
    has_vol = "Volume" in df.columns and df["Volume"].sum() > 0
    if has_vol:
        vol_rat = (df["Volume"] / df["Volume"].rolling(20).mean()).rolling(5).mean().fillna(1)
        adj = std * vol_rat
    else:
        adj = std
    df["BB_Mid"]   = mid
    df["BB_Upper"] = mid + 2.0 * adj
    df["BB_Lower"] = mid - 2.0 * adj
    # EMAs
    df["EMA21"]  = close.ewm(span=21,  adjust=False).mean()
    df["EMA63"]  = close.ewm(span=63,  adjust=False).mean()
    df["EMA200"] = close.ewm(span=200, adjust=False).mean()
    # Momentum: (5D MA / 63D MA - 1) * 100
    df["Mom_Pct"] = ((close.rolling(5).mean() / close.rolling(63).mean()) - 1) * 100
    # Realized vol annualised
    lr = np.log(close / close.shift(1))
    df["RV"] = lr.rolling(21).std() * np.sqrt(252) * 100
    # Stochastic RSI
    delta  = close.diff()
    ma_up  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    ma_dn  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi    = 100 - (100 / (1 + ma_up / (ma_dn + 1e-10)))
    lo14, hi14 = rsi.rolling(14).min(), rsi.rolling(14).max()
    stoch  = (rsi - lo14) / (hi14 - lo14 + 1e-10) * 100
    df["StochRSI_K"] = stoch.rolling(3).mean()
    df["StochRSI_D"] = df["StochRSI_K"].rolling(3).mean()
    # Rate of Change 63D
    df["RoC63"] = close.pct_change(63) * 100
    return df

def calc_zscore(df):
    if len(df) < 126:
        return None
    recent = df["Close"].tail(252)
    std = recent.std()
    if std == 0:
        return None
    return round((df["Close"].iloc[-1] - recent.mean()) / std, 2)

def build_chart(df, ticker, is_weekly, is_mf, show_rv, show_roc):
    has_vol = (not is_mf) and ("Volume" in df.columns) and (df["Volume"].sum() > 0)
    display = df.tail(260 if is_weekly else 252)
    idx     = display.index

    row_labels  = ["price"]
    row_heights = [1.0]
    if show_rv:
        row_labels.append("rv")
        row_heights.append(0.28)
    row_labels.append("stoch")
    row_heights.append(0.28)
    if show_roc:
        row_labels.append("roc")
        row_heights.append(0.22)

    total   = sum(row_heights)
    heights = [h / total for h in row_heights]
    n_rows  = len(row_labels)
    specs   = [[{"secondary_y": True}]] + [[{"secondary_y": False}]] * (n_rows - 1)

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.022,
        row_heights=heights,
        specs=specs,
    )

    def row(lbl):
        return row_labels.index(lbl) + 1

    # Momentum bars on secondary y — behind everything else
    mom = display["Mom_Pct"].fillna(0)
    mom_colors = [C["mom_pos"] if v >= 0 else C["mom_neg"] for v in mom]
    fig.add_trace(go.Bar(
        x=idx, y=mom,
        name="Momentum",
        marker_color=mom_colors,
        marker_line_width=0,
        showlegend=True,
    ), row=1, col=1, secondary_y=True)

    # Volume — normalised to bottom 10% of price range, overlaid on price panel
    if has_vol:
        p_lo    = float(display["Low"].min())
        p_hi    = float(display["High"].max())
        p_range = p_hi - p_lo
        v_max   = float(display["Volume"].max())
        if v_max > 0:
            # Scale volume height to 10% of price range, base at p_lo
            vol_h  = (display["Volume"] / v_max) * (p_range * 0.20)
            vol_colors = [C["vol_up"] if float(c) >= float(o) else C["vol_dn"]
                          for o, c in zip(display["Open"], display["Close"])]
            fig.add_trace(go.Bar(
                x=idx, y=vol_h,
                name="Volume",
                marker_color=vol_colors,
                marker_line_width=0,
                base=p_lo,
                opacity=0.55,
                showlegend=True,
            ), row=1, col=1, secondary_y=False)
            # Volume 21D MA — normalized to same scale
            vol_ma21 = display["Volume"].rolling(21).mean()
            vol_ma21_norm = (vol_ma21 / v_max) * (p_range * 0.20)
            fig.add_trace(go.Scatter(
                x=idx, y=vol_ma21_norm + p_lo,
                name="Vol MA21",
                line=dict(color="#f97316", width=1),
                showlegend=True,
            ), row=1, col=1, secondary_y=False)

    # BB bands
    fig.add_trace(go.Scatter(
        x=idx, y=display["BB_Upper"],
        name="BB", line=dict(color=C["bb_band"], width=1, dash="dot"),
        showlegend=True,
    ), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(
        x=idx, y=display["BB_Lower"],
        name="BB Lower", line=dict(color=C["bb_band"], width=1, dash="dot"),
        fill="tonexty", fillcolor=C["bb_fill"], showlegend=False,
    ), row=1, col=1, secondary_y=False)

    # Price — candlestick for ETF/stock, line for mutual fund
    if not is_mf:
        fig.add_trace(go.Candlestick(
            x=idx,
            open=display["Open"], high=display["High"],
            low=display["Low"],   close=display["Close"],
            name="Price",
            increasing_line_color=C["price"], decreasing_line_color="#ef4444",
            increasing_fillcolor=C["price"],  decreasing_fillcolor="#ef4444",
        ), row=1, col=1, secondary_y=False)
    else:
        fig.add_trace(go.Scatter(
            x=idx, y=display["Close"], name="Price",
            line=dict(color=C["price"], width=2),
        ), row=1, col=1, secondary_y=False)

    # EMAs
    fig.add_trace(go.Scatter(
        x=idx, y=display["EMA21"], name="EMA 21",
        line=dict(color="#93c5fd", width=1.5),
    ), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(
        x=idx, y=display["EMA63"], name="EMA 63",
        line=dict(color="#f97316", width=2),
    ), row=1, col=1, secondary_y=False)

    # Price y-axis: auto-scale around actual data, not from 0
    p_lo_d = float(display["Low"].min())  if "Low"  in display.columns else float(display["Close"].min())
    p_hi_d = float(display["High"].max()) if "High" in display.columns else float(display["Close"].max())
    p_pad  = (p_hi_d - p_lo_d) * 0.04
    fig.update_yaxes(
        range=[p_lo_d - p_pad, p_hi_d + p_pad],
        title_text="Price", title_font=dict(size=9, color=C["muted"]),
        row=1, col=1, secondary_y=False,
    )

    # Momentum secondary y: heavily compressed so bars are subtle
    mom_abs = float(mom.abs().max()) if mom.abs().max() > 0 else 5
    fig.update_yaxes(
        range=[-mom_abs * 1.4, mom_abs * 1.4],
        showticklabels=False, showgrid=False, zeroline=False,
        row=1, col=1, secondary_y=True,
    )

    # RV panel
    if show_rv:
        r = row("rv")
        fig.add_trace(go.Scatter(
            x=idx, y=display["RV"], name="Realized Vol",
            line=dict(color=C["rv"], width=1.5),
            fill="tozeroy", fillcolor="rgba(167,139,250,0.10)",
        ), row=r, col=1)
        fig.update_yaxes(title_text="RV %", title_font=dict(size=9, color=C["muted"]),
                         row=r, col=1)

    # StochRSI panel
    r = row("stoch")
    fig.add_trace(go.Scatter(
        x=idx, y=display["StochRSI_K"], name="%K",
        line=dict(color=C["stoch_k"], width=1.5),
    ), row=r, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["StochRSI_D"], name="%D",
        line=dict(color=C["stoch_d"], width=1.5),
    ), row=r, col=1)
    fig.add_hline(y=80, line_dash="dot", line_color=C["ob"],  line_width=1, row=r, col=1)
    fig.add_hline(y=20, line_dash="dot", line_color=C["os_"], line_width=1, row=r, col=1)
    fig.update_yaxes(title_text="StochRSI", title_font=dict(size=9, color=C["muted"]),
                     range=[0, 100], row=r, col=1)

    # RoC panel
    if show_roc:
        r = row("roc")
        roc_vals  = display["RoC63"].fillna(0)
        roc_colors = [C["mom_pos"] if v >= 0 else C["mom_neg"] for v in roc_vals]
        fig.add_trace(go.Bar(
            x=idx, y=roc_vals, name="RoC 63D",
            marker_color=roc_colors, marker_line_width=0,
        ), row=r, col=1)
        fig.add_hline(y=0, line_color=C["muted"], line_width=0.8, row=r, col=1)
        fig.update_yaxes(title_text="RoC %", title_font=dict(size=9, color=C["muted"]),
                         row=r, col=1)

    # X-axis: show dates on price panel and bottom panel only
    for r_idx in range(1, n_rows + 1):
        show = (r_idx == 1 or r_idx == n_rows)
        fig.update_xaxes(showticklabels=show,
                         tickfont=dict(size=9, color=C["muted"]),
                         row=r_idx, col=1)

    # Global axis style
    fig.update_yaxes(showgrid=True, gridcolor="#21262d", zeroline=False,
                     tickfont=dict(size=9, color=C["muted"]))
    fig.update_xaxes(showgrid=False, rangeslider_visible=False)

    label = "Weekly 5Y" if is_weekly else "Daily 12M"
    fig.update_layout(
        height=720,
        template="plotly_dark",
        paper_bgcolor=C["bg"],
        plot_bgcolor="#0d1117",
        font=dict(family="IBM Plex Mono", color=C["text"]),
        margin=dict(l=58, r=12, t=36, b=8),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.04,
            xanchor="left", x=0,
            font=dict(size=9, color=C["text"]),
            bgcolor="rgba(0,0,0,0)",
            itemsizing="constant",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=C["panel"], font_size=11),
        title=dict(
            text=f"<b>{ticker}</b>  {label}",
            font=dict(size=12, color=C["text"]),
            x=0.01, xanchor="left", pad=dict(t=4),
        ),
        barmode="overlay",
        xaxis_rangeslider_visible=False,
    )
    return fig

def pct_n(df, n):
    if len(df) <= n:
        return None
    return round((df["Close"].iloc[-1] - df["Close"].iloc[-1-n]) / df["Close"].iloc[-1-n] * 100, 2)

def build_stats(df, ticker, is_mf):
    z     = calc_zscore(df)
    last  = round(float(df["Close"].iloc[-1]), 2)
    win   = df["Close"].tail(260)
    hi    = round(float(win.max()), 2)
    lo    = round(float(win.min()), 2)
    pfl   = round((last - lo) / lo * 100, 1)   if lo   else None
    pu    = round((hi - last)  / last * 100, 1) if last else None
    pd_   = round((lo - last)  / last * 100, 1) if last else None
    trade = "Bullish" if float(df["EMA21"].iloc[-1]) > float(df["EMA63"].iloc[-1]) else "Bearish"
    trend = "Bullish" if last > float(df["EMA200"].iloc[-1]) else "Bearish"
    roc   = round(float(df["RoC63"].iloc[-1]), 2) if not pd.isna(df["RoC63"].iloc[-1]) else None
    s200  = round((last - float(df["EMA200"].iloc[-1])) / float(df["EMA200"].iloc[-1]) * 100, 1)
    rv1m  = round(float(df["RV"].tail(21).mean()), 1)
    rv3m  = round(float(df["RV"].tail(63).mean()), 1)
    rvr   = round(rv1m / rv3m, 2) if rv3m else None
    perf  = {k: pct_n(df, n) for k, n in [("1D",1),("1W",5),("1M",21),("3M",63),("6M",126)]}
    return dict(ticker=ticker, last=last, z=z, trade=trade, trend=trend,
                hi=hi, lo=lo, pfl=pfl, pu=pu, pd_=pd_,
                roc=roc, sma200=s200, rv1m=rv1m, rv3m=rv3m, rvr=rvr, perf=perf)

def _pc(v):
    return "#34d399" if v and v >= 0 else "#f87171"

def _row(label, val, color=None):
    return html.Div([
        html.Span(label, style={"color": C["muted"], "fontSize": "11px"}),
        html.Span(val,   style={"color": color or C["text"], "fontSize": "12px", "fontWeight": "600"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": "5px"})

def _sig(label, sig):
    col = "#34d399" if sig == "Bullish" else "#f87171"
    bg  = "rgba(52,211,153,0.12)" if sig == "Bullish" else "rgba(248,113,113,0.12)"
    return html.Div([
        html.Span(label, style={"color": C["muted"], "fontSize": "11px"}),
        html.Span(sig,   style={"color": col, "background": bg, "padding": "2px 7px",
                                 "borderRadius": "4px", "fontSize": "11px", "fontWeight": "700"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": "5px"})

def build_stats_panel(s):
    div = {"borderTop": "1px solid #21262d", "margin": "8px 0"}
    p   = s["perf"]
    cells = []
    for lbl, key in [("1D","1D"),("1W","1W"),("1M","1M"),("3M","3M"),("6M","6M")]:
        v   = p.get(key)
        txt = f"{v:+.2f}%" if v is not None else "—"
        cells.append(html.Div([
            html.Div(lbl, style={"fontSize": "9px",  "color": C["muted"], "textAlign": "center"}),
            html.Div(txt, style={"fontSize": "11px", "fontWeight": "700",
                                  "color": _pc(v), "textAlign": "center"}),
        ], style={"flex": "1", "padding": "3px 2px", "borderRight": "1px solid #21262d"}))
    z_col  = "#f87171" if s["z"] and s["z"] > 2.1 else "#34d399" if s["z"] and s["z"] < -2.0 else C["text"]
    rv_col = "#f97316" if s["rvr"] and s["rvr"] > 1.2 else "#34d399" if s["rvr"] and s["rvr"] < 0.8 else C["text"]
    return html.Div([
        html.Div([
            html.Span(s["ticker"], style={"fontSize": "18px", "fontWeight": "800",
                                           "color": C["price"], "letterSpacing": "1px"}),
            html.Span(f"  ${s['last']}", style={"fontSize": "13px", "color": C["text"]}),
        ], style={"marginBottom": "10px"}),
        _sig("Trade  EMA 21/63",  s["trade"]),
        _sig("Trend  vs EMA 200", s["trend"]),
        html.Div(style=div),
        _row("8M High",      f"${s['hi']}"),
        _row("8M Low",       f"${s['lo']}"),
        _row("vs Low",       f"{s['pfl']:+.1f}%" if s["pfl"] is not None else "—"),
        _row("Pot Upside",   f"{s['pu']:+.1f}%"  if s["pu"]  is not None else "—", color="#34d399"),
        _row("Pot Downside", f"{s['pd_']:+.1f}%" if s["pd_"] is not None else "—", color="#f87171"),
        html.Div(style=div),
        _row("RoC 63D",    f"{s['roc']:+.2f}%" if s["roc"] is not None else "—"),
        _row("vs EMA 200", f"{s['sma200']:+.1f}%"),
        html.Div(style=div),
        html.Div("Performance", style={"fontSize": "10px", "color": C["muted"], "marginBottom": "5px"}),
        html.Div(cells, style={"display": "flex", "border": "1px solid #21262d",
                                "borderRadius": "5px", "overflow": "hidden", "marginBottom": "10px"}),
        _row("RV 1M",    f"{s['rv1m']:.1f}%"),
        _row("RV 3M",    f"{s['rv3m']:.1f}%"),
        _row("1M/3M RV", f"{s['rvr']:.2f}" if s["rvr"] else "—", color=rv_col),
        html.Div(style=div),
        _row("12M Z-Score", f"{s['z']:+.2f}" if s["z"] is not None else "—", color=z_col),
        (html.Div(
            html.Span(
                f"{'OVERBOUGHT' if s['z'] > 2.1 else 'OVERSOLD'}",
                style={"color": "#f87171" if s["z"] and s["z"] > 2.1 else "#34d399",
                       "background": "rgba(30,30,35,0.95)",
                       "padding": "3px 12px", "borderRadius": "4px",
                       "fontSize": "11px", "fontWeight": "700",
                       "border": f"2px solid {'#f87171' if s['z'] and s['z'] > 2.1 else '#34d399'}",
                       "display": "inline-block", "marginTop": "4px"}
            ), style={"textAlign": "center"}
        ) if s["z"] is not None and abs(s["z"]) >= 2.0 else html.Div()),
    ], style={"background": C["panel"], "border": f"1px solid {C['border']}",
              "borderRadius": "8px", "padding": "12px 14px",
              "fontFamily": "IBM Plex Mono", "color": C["text"]})

def _tog_style(active):
    return {"fontFamily": "IBM Plex Mono", "fontSize": "11px", "fontWeight": "600",
            "padding": "3px 12px", "borderRadius": "4px", "cursor": "pointer",
            "marginRight": "6px",
            "background": "#4a9eff22" if active else "transparent",
            "color": C["price"] if active else C["muted"],
            "border": f"1px solid {'#4a9eff55' if active else C['border']}"}

app.layout = html.Div(style={"background": C["bg"], "minHeight": "100vh"}, children=[
    dcc.Store(id="theme-store", data="dark"),

    # Top bar
    html.Div([
        html.Div([
            html.Span("Custom", style={"color": C["text"],  "fontWeight": "400", "fontSize": "15px"}),
            html.Span("Charts", style={"color": C["price"], "fontWeight": "800", "fontSize": "15px",
                                        "marginLeft": "4px", "letterSpacing": "1px"}),
        ]),
        html.Div([
            dcc.Input(id="ticker-input", value="SPY", type="text",
                      placeholder="Ticker", debounce=False,
                      style={"fontFamily": "IBM Plex Mono", "fontSize": "13px", "fontWeight": "600",
                             "textTransform": "uppercase", "width": "90px",
                             "padding": "5px 10px", "borderRadius": "5px",
                             "border": f"1px solid {C['border']}",
                             "background": "#0d1117", "color": C["text"], "outline": "none"}),
            dcc.RadioItems(id="asset-type",
                options=[{"label": " ETF/Stock", "value": "etf"},
                         {"label": " Mut. Fund",  "value": "mf"}],
                value="etf", inline=True,
                inputStyle={"marginRight": "4px"},
                labelStyle={"marginRight": "12px", "color": C["text"], "fontSize": "12px"}),
            dcc.RadioItems(id="timeframe",
                options=[{"label": " Daily",  "value": "daily"},
                         {"label": " Weekly", "value": "weekly"}],
                value="daily", inline=True,
                inputStyle={"marginRight": "4px"},
                labelStyle={"marginRight": "12px", "color": C["text"], "fontSize": "12px"}),
            html.Button("Load", id="load-btn", n_clicks=0,
                style={"fontFamily": "IBM Plex Mono", "fontSize": "12px", "fontWeight": "700",
                       "padding": "5px 16px", "background": C["price"], "color": "#0d1117",
                       "border": "none", "borderRadius": "5px", "cursor": "pointer"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "12px", "flexWrap": "wrap"}),
    ], style={"display": "flex", "alignItems": "center", "justifyContent": "space-between",
              "padding": "8px 14px", "flexWrap": "wrap", "gap": "8px",
              "background": C["panel"], "borderBottom": f"1px solid {C['border']}"}),

    # Sub-panel toggles
    html.Div([
        html.Span("Show:", style={"color": C["muted"], "fontSize": "11px", "marginRight": "8px"}),
        html.Button("Realized Vol",    id="toggle-rv",  n_clicks=1, style=_tog_style(True)),
        html.Button("Rate of Change",  id="toggle-roc", n_clicks=1, style=_tog_style(True)),
    ], style={"padding": "5px 14px", "background": C["bg"],
              "borderBottom": f"1px solid {C['border']}",
              "display": "flex", "alignItems": "center"}),

    # Status bar — ticker info + OB/OS signal
    html.Div(id="status-bar", style={"padding": "3px 14px", "fontSize": "11px",
             "minHeight": "20px", "color": C["muted"],
             "background": C["bg"], "fontFamily": "IBM Plex Mono",
             "borderBottom": f"1px solid {C['border']}"}),

    # Chart title row
    html.Div(id="chart-title-row", style={"padding": "3px 14px", "fontSize": "12px",
             "fontWeight": "700", "color": C["text"], "background": C["bg"],
             "fontFamily": "IBM Plex Mono", "display": "flex",
             "justifyContent": "space-between", "alignItems": "center"}),

    # Chart + sidebar
    html.Div([
        html.Div(
            dcc.Graph(id="main-chart",
                config={"toImageButtonOptions": {"format": "png", "filename": "chart", "scale": 2},
                        "displayModeBar": True,
                        "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
                style={"height": "720px"}),
            style={"flex": "1", "minWidth": "0"}),
        html.Div(id="stats-sidebar",
            style={"width": "210px", "flexShrink": "0",
                   "padding": "8px 8px 8px 0",
                   "display": "flex", "flexDirection": "column"}),
    ], style={"display": "flex", "alignItems": "stretch", "background": C["bg"]}),

    # Mobile stats
    html.Div([
        html.Button("Stats", id="mobile-stats-btn", n_clicks=0,
            style={"fontFamily": "IBM Plex Mono", "fontSize": "11px", "fontWeight": "600",
                   "padding": "6px 16px", "width": "100%",
                   "background": C["panel"], "color": C["muted"],
                   "border": "none", "borderTop": f"1px solid {C['border']}",
                   "cursor": "pointer"}),
        html.Div(id="mobile-stats-panel", style={"display": "none", "padding": "10px 12px"}),
    ], id="mobile-stats-container"),
])

@app.callback(
    Output("main-chart",          "figure"),
    Output("stats-sidebar",       "children"),
    Output("mobile-stats-panel",  "children"),
    Output("status-bar",          "children"),
    Output("chart-title-row",     "children"),
    Input("load-btn",             "n_clicks"),
    Input("timeframe",            "value"),
    Input("toggle-rv",            "n_clicks"),
    Input("toggle-roc",           "n_clicks"),
    State("ticker-input",         "value"),
    State("asset-type",           "value"),
    prevent_initial_call=False,
)
def update_chart(n_load, tf, n_rv, n_roc, ticker, asset_type):
    ticker    = (ticker or "SPY").upper().strip()
    is_weekly = (tf == "weekly")
    is_mf     = (asset_type == "mf")
    show_rv   = (n_rv  % 2 == 1)
    show_roc  = (n_roc % 2 == 1)

    df, info = fetch_data(ticker, is_weekly)
    if df is None or len(df) < 50:
        return go.Figure(), [], [], f"No data for {ticker}", ""

    df    = apply_all(df)
    fig   = build_chart(df, ticker, is_weekly, is_mf, show_rv, show_roc)
    s     = build_stats(df, ticker, is_mf)
    panel = build_stats_panel(s)

    # Status bar: trade/trend signals
    status = (f"{ticker}  |  {'Mutual Fund' if is_mf else 'ETF/Stock'}  |  "
              f"${s['last']}  |  Trade: {s['trade']}  |  Trend: {s['trend']}")

    # Chart title row: ticker + timeframe + OB/OS alert
    z     = s["z"]
    label = "Weekly 5Y" if is_weekly else "Daily 12M"
    ob_el = []
    if z is not None and abs(z) >= 2.0:
        lbl    = "OVERBOUGHT" if z > 0 else "OVERSOLD"
        col_ob = "#f87171" if z > 0 else "#34d399"
        bg_ob  = "rgba(248,113,113,0.12)" if z > 0 else "rgba(52,211,153,0.12)"
        ob_el  = [html.Span(f"⚠ {lbl}  Z={z:+.2f}",
                            style={"color": col_ob,
                                   "background": "rgba(30,30,35,0.95)",
                                   "padding": "3px 12px", "borderRadius": "4px",
                                   "fontSize": "11px", "fontWeight": "700",
                                   "border": f"2px solid {col_ob}",
                                   "boxShadow": f"0 0 8px {col_ob}55",
                                   "letterSpacing": "0.5px"})]
    title_children = [
        html.Span(f"{ticker}  ·  {label}"),
        html.Div(ob_el),
    ]

    return fig, panel, panel, status, title_children


@app.callback(
    Output("toggle-rv",  "style"),
    Output("toggle-roc", "style"),
    Input("toggle-rv",   "n_clicks"),
    Input("toggle-roc",  "n_clicks"),
)
def toggle_styles(n_rv, n_roc):
    return _tog_style(n_rv % 2 == 1), _tog_style(n_roc % 2 == 1)


@app.callback(
    Output("mobile-stats-panel", "style"),
    Output("mobile-stats-btn",   "children"),
    Input("mobile-stats-btn",    "n_clicks"),
)
def toggle_mobile(n):
    if n % 2 == 1:
        return {"display": "block", "padding": "10px 12px"}, "Hide Stats"
    return {"display": "none", "padding": "10px 12px"}, "Stats"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run(debug=False, host="0.0.0.0", port=port)
