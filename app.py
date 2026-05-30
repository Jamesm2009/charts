"""
KCM Custom Charting Tool
Dash + Plotly | yfinance | Upstash Redis + local pickle cache
Deploy: Dokku on DigitalOcean | charts.market-dashboards.com
"""

import dash
from dash import dcc, html, Input, Output, State, callback_context
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os, pickle, json
import requests as req_lib

# ── App ───────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    title="KCM Custom Chart Tool",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
app.index_string = """
<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>{%title%}</title>
{%favicon%}
{%css%}
<style>
@media (max-width: 768px) {
    #stats-sidebar { display: none !important; }
    #mobile-stats-container { display: block !important; }
}
@media (min-width: 769px) {
    #mobile-stats-container { display: none !important; }
}
body { margin: 0; padding: 0; background: #0d1117; }
.js-plotly-plot .plotly .modebar { background: transparent !important; }
</style>
</head>
<body>
{%app_entry%}
<footer>
{%config%}
{%scripts%}
{%renderer%}
</footer>
</body>
</html>
"""
server = app.server

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_DIR        = "cache"
REDIS_URL        = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN      = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
LOCAL_TTL        = 3600
REDIS_TTL_DAILY  = 3600
REDIS_TTL_WEEKLY = 21600
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Colors ────────────────────────────────────────────────────────────────────
C = {
    "price":       "#4a9eff",
    "ema21":       "#93c5fd",
    "ema63":       "#f97316",
    "bb_band":     "rgba(180,180,190,0.35)",
    "bb_fill":     "rgba(180,180,190,0.08)",
    "mom_pos":     "rgba(52,211,153,0.30)",
    "mom_neg":     "rgba(248,113,113,0.30)",
    "vol_up":      "rgba(52,211,153,0.65)",
    "vol_down":    "rgba(248,113,113,0.65)",
    "rv":          "#a78bfa",
    "stoch_k":     "#60a5fa",
    "stoch_d":     "#f87171",
    "roc":         "#fbbf24",
    "ob":          "#f87171",
    "os":          "#34d399",
    "bg_dark":     "#0d1117",
    "bg_panel":    "#161b22",
    "border":      "#30363d",
    "text":        "#e6edf3",
    "muted":       "#8b949e",
}

# ── Redis helpers ─────────────────────────────────────────────────────────────
def redis_set(key, value, ex=3600):
    if not REDIS_URL or not REDIS_TOKEN:
        return False
    try:
        r = req_lib.post(
            f"{REDIS_URL}/set/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            json={"value": value, "ex": ex},
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False

def redis_get(key):
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    try:
        r = req_lib.get(
            f"{REDIS_URL}/get/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        return r.json().get("result")
    except Exception:
        return None

# ── Cache helpers ─────────────────────────────────────────────────────────────
def _ckey(ticker, is_weekly):
    return f"kcm_{ticker}_{'w' if is_weekly else 'd'}"

def _df_to_json(df):
    return df.reset_index().to_json(orient="records", date_format="iso")

def _json_to_df(s):
    recs = json.loads(s)
    df = pd.DataFrame(recs)
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    return df.set_index(date_col).sort_index()

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
                data = pickle.load(f)
            if datetime.now().timestamp() - data.get("ts", 0) < LOCAL_TTL:
                return data["df"], data.get("info", {})
        except Exception:
            pass
    raw = redis_get(key)
    if raw:
        try:
            payload = json.loads(raw)
            df = _json_to_df(payload["df"])
            info = payload.get("info", {})
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

# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_data(ticker, is_weekly=False):
    ticker = ticker.upper().strip()
    df, info = load_cache(ticker, is_weekly)
    if df is not None:
        return df, info

    days  = 1825 + 100 if is_weekly else 365 + 120
    end   = datetime.now()
    start = end - timedelta(days=days)

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None, None

    # Flatten MultiIndex columns (yfinance >= 0.2.38)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if is_weekly:
        agg = {"Close": "last", "Open": "first", "High": "max", "Low": "min"}
        has_vol = "Volume" in df.columns and df["Volume"].sum() > 0
        if has_vol:
            agg["Volume"] = "sum"
        df = df.resample("W").agg(agg)

    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}

    df = df.dropna(subset=["Close"])
    save_cache(ticker, is_weekly, df, info)
    return df, info

# ── Indicators ────────────────────────────────────────────────────────────────
def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_vol_bb(df, period=21, mult=2.0):
    df = df.copy()
    close = df["Close"]
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    has_vol = "Volume" in df.columns and df["Volume"].sum() > 0
    if has_vol:
        vol_ma  = df["Volume"].rolling(20).mean()
        vol_rat = (df["Volume"] / vol_ma).rolling(5).mean().fillna(1)
        adj     = std * vol_rat
    else:
        adj = std
    df["BB_Mid"]   = mid
    df["BB_Upper"] = mid + mult * adj
    df["BB_Lower"] = mid - mult * adj
    return df

def calc_emas(df):
    df = df.copy()
    df["EMA21"] = calc_ema(df["Close"], 21)
    df["EMA63"] = calc_ema(df["Close"], 63)
    df["EMA200"] = calc_ema(df["Close"], 200)
    return df

def calc_momentum(df):
    df = df.copy()
    ma5  = df["Close"].rolling(5).mean()
    ma63 = df["Close"].rolling(63).mean()
    df["Mom_Pct"] = ((ma5 - ma63) / ma63 * 100).fillna(0)
    return df

def calc_realized_vol(df, window=21):
    df = df.copy()
    log_ret = np.log(df["Close"] / df["Close"].shift(1))
    df["RV"] = log_ret.rolling(window).std() * np.sqrt(252) * 100
    return df

def calc_stoch_rsi(df, rsi_period=14, stoch_period=14, sk=3, sd=3):
    df = df.copy()
    delta   = df["Close"].diff()
    ma_up   = delta.clip(lower=0).ewm(alpha=1/rsi_period, adjust=False).mean()
    ma_down = (-delta.clip(upper=0)).ewm(alpha=1/rsi_period, adjust=False).mean()
    rs      = ma_up / (ma_down + 1e-10)
    rsi     = 100 - (100 / (1 + rs))
    lo      = rsi.rolling(stoch_period).min()
    hi      = rsi.rolling(stoch_period).max()
    stoch   = (rsi - lo) / (hi - lo + 1e-10) * 100
    df["StochRSI_K"] = stoch.rolling(sk).mean()
    df["StochRSI_D"] = df["StochRSI_K"].rolling(sd).mean()
    return df

def calc_roc(df, period=63):
    df = df.copy()
    df["RoC"] = df["Close"].pct_change(period) * 100
    return df

def calc_zscore(df, window=252):
    if len(df) < window // 2:
        return None
    recent = df["Close"].tail(window)
    std    = recent.std()
    if std == 0:
        return None
    return round((df["Close"].iloc[-1] - recent.mean()) / std, 2)

def apply_all(df, is_mf=False):
    df = calc_vol_bb(df)
    df = calc_emas(df)
    df = calc_momentum(df)
    df = calc_realized_vol(df)
    df = calc_stoch_rsi(df)
    df = calc_roc(df)
    return df

# ── Stats helpers ─────────────────────────────────────────────────────────────
def pct_change_n(df, n):
    if len(df) <= n:
        return None
    v = (df["Close"].iloc[-1] - df["Close"].iloc[-1-n]) / df["Close"].iloc[-1-n] * 100
    return round(v, 2)

def build_stats(df, ticker, is_mf, is_weekly):
    z  = calc_zscore(df)
    last_px = round(df["Close"].iloc[-1], 2)

    window = min(len(df), 260)
    hi8m   = round(df["Close"].tail(window).max(), 2)
    lo8m   = round(df["Close"].tail(window).min(), 2)
    pct_from_low  = round((last_px - lo8m) / lo8m * 100, 1) if lo8m else None
    pot_upside    = round((hi8m - last_px) / last_px * 100, 1) if last_px else None
    pot_downside  = round((lo8m - last_px) / last_px * 100, 1) if last_px else None

    trade_sig = "Bullish" if df["EMA21"].iloc[-1] > df["EMA63"].iloc[-1] else "Bearish"
    trend_sig = "Bullish" if df["Close"].iloc[-1] > df["EMA200"].iloc[-1] else "Bearish"

    roc_val = round(df["RoC"].iloc[-1], 2) if not pd.isna(df["RoC"].iloc[-1]) else None
    sma200_pct = round((last_px - df["EMA200"].iloc[-1]) / df["EMA200"].iloc[-1] * 100, 1)

    rv_1m  = round(df["RV"].tail(21).mean(), 2)
    rv_3m  = round(df["RV"].tail(63).mean(), 2)
    rv_rat = round(rv_1m / rv_3m, 2) if rv_3m else None

    perf = {
        "1D":  pct_change_n(df, 1),
        "1W":  pct_change_n(df, 5),
        "1M":  pct_change_n(df, 21),
        "3M":  pct_change_n(df, 63),
        "6M":  pct_change_n(df, 126),
    }

    return {
        "ticker": ticker, "last_px": last_px,
        "zscore": z, "trade_sig": trade_sig, "trend_sig": trend_sig,
        "hi8m": hi8m, "lo8m": lo8m,
        "pct_from_low": pct_from_low,
        "pot_upside": pot_upside, "pot_downside": pot_downside,
        "roc": roc_val, "sma200_pct": sma200_pct,
        "rv_1m": rv_1m, "rv_3m": rv_3m, "rv_ratio": rv_rat,
        "perf": perf,
    }

# ── Chart builder ─────────────────────────────────────────────────────────────
def build_chart(df, ticker, is_weekly, is_mf, theme):
    has_vol = (not is_mf) and ("Volume" in df.columns) and (df["Volume"].sum() > 0)
    tmpl    = "plotly_dark" if theme == "dark" else "plotly_white"
    txt_col = C["text"] if theme == "dark" else "#1e293b"
    bg_col  = C["bg_dark"] if theme == "dark" else "#f8fafc"
    panel_col = C["bg_panel"] if theme == "dark" else "#ffffff"
    border_col = C["border"] if theme == "dark" else "#e2e8f0"
    muted   = C["muted"] if theme == "dark" else "#64748b"

    # Trim to display window
    if is_weekly:
        display = df.tail(260)   # 5 years weekly
    else:
        display = df.tail(252)   # 12 months daily

    # Subplot rows: price | RV | StochRSI | RoC | [Volume]
    n_rows     = 5 if has_vol else 4
    row_heights = [0.50, 0.13, 0.13, 0.13, 0.11] if has_vol else [0.54, 0.15, 0.15, 0.16]

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=row_heights,
    )

    idx = display.index

    # ── Momentum overlay bars (behind price) ──────────────────────────────
    mom = display["Mom_Pct"]
    mom_colors = [C["mom_pos"] if v >= 0 else C["mom_neg"] for v in mom]
    fig.add_trace(go.Bar(
        x=idx, y=mom,
        name="Momentum (5D/63D %)",
        marker_color=mom_colors,
        marker_line_width=0,
        opacity=1,
        showlegend=True,
        yaxis="y",
    ), row=1, col=1)

    # ── BB bands ─────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=idx, y=display["BB_Upper"],
        name="BB Upper", line=dict(color=C["bb_band"], width=1, dash="dot"),
        showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["BB_Lower"],
        name="BB Bands", line=dict(color=C["bb_band"], width=1, dash="dot"),
        fill="tonexty", fillcolor=C["bb_fill"],
    ), row=1, col=1)

    # ── Price ────────────────────────────────────────────────────────────
    use_cs = not is_mf
    if use_cs:
        fig.add_trace(go.Candlestick(
            x=idx,
            open=display["Open"], high=display["High"],
            low=display["Low"], close=display["Close"],
            name="Price",
            increasing_line_color=C["price"],
            decreasing_line_color="#ef4444",
            increasing_fillcolor=C["price"],
            decreasing_fillcolor="#ef4444",
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=idx, y=display["Close"],
            name="Price", line=dict(color=C["price"], width=2),
        ), row=1, col=1)

    # ── EMAs ──────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=idx, y=display["EMA21"],
        name="EMA 21", line=dict(color=C["ema21"], width=1.5),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["EMA63"],
        name="EMA 63", line=dict(color=C["ema63"], width=2),
    ), row=1, col=1)

    # ── Realized Volatility ───────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=idx, y=display["RV"],
        name="Realized Vol", line=dict(color=C["rv"], width=1.5),
        fill="tozeroy", fillcolor="rgba(167,139,250,0.10)",
    ), row=2, col=1)

    # ── Stochastic RSI ────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=idx, y=display["StochRSI_K"],
        name="%K", line=dict(color=C["stoch_k"], width=1.5),
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["StochRSI_D"],
        name="%D", line=dict(color=C["stoch_d"], width=1.5),
    ), row=3, col=1)
    fig.add_hline(y=80, line_dash="dot", line_color=C["ob"],   line_width=1, row=3, col=1)
    fig.add_hline(y=20, line_dash="dot", line_color=C["os"],   line_width=1, row=3, col=1)

    # ── Rate of Change ────────────────────────────────────────────────────
    roc_colors = [C["mom_pos"] if v >= 0 else C["mom_neg"] for v in display["RoC"].fillna(0)]
    fig.add_trace(go.Bar(
        x=idx, y=display["RoC"],
        name="RoC 63", marker_color=roc_colors, marker_line_width=0,
    ), row=4, col=1)
    fig.add_hline(y=0, line_color=muted, line_width=0.8, row=4, col=1)

    # ── Volume ────────────────────────────────────────────────────────────
    if has_vol:
        vol_col = [C["vol_up"] if c >= o else C["vol_down"]
                   for o, c in zip(display["Open"], display["Close"])]
        fig.add_trace(go.Bar(
            x=idx, y=display["Volume"],
            name="Volume", marker_color=vol_col, marker_line_width=0,
        ), row=5, col=1)

    # ── Z-Score annotation (top-right of price panel, above BB) ──────────
    z = calc_zscore(df)
    if z is not None and abs(z) >= 2.0:
        label   = "OVERBOUGHT" if z > 0 else "OVERSOLD"
        col_ann = C["ob"] if z > 0 else C["os"]
        fig.add_annotation(
            text=f"<b>{label}</b><br>Z={z:+.2f}",
            x=0.99, y=0.99, xref="paper", yref="paper",
            xanchor="right", yanchor="top",
            showarrow=False,
            font=dict(color=col_ann, size=11),
            bgcolor="rgba(13,17,23,0.75)" if theme == "dark" else "rgba(255,255,255,0.85)",
            bordercolor=col_ann, borderwidth=1, borderpad=5,
        )

    # ── Axis labels ───────────────────────────────────────────────────────
    axis_style = dict(showgrid=True, gridcolor=border_col, zeroline=False,
                      tickfont=dict(size=10, color=muted))
    fig.update_yaxes(**axis_style)
    fig.update_xaxes(showgrid=False, tickfont=dict(size=10, color=muted))
    fig.update_xaxes(rangeslider_visible=False)

    # Panel labels (right side y-axis titles)
    fig.update_yaxes(title_text="Price", title_font=dict(size=9, color=muted), row=1, col=1)
    fig.update_yaxes(title_text="RV %",  title_font=dict(size=9, color=muted), row=2, col=1)
    fig.update_yaxes(title_text="StochRSI", title_font=dict(size=9, color=muted), row=3, col=1)
    fig.update_yaxes(title_text="RoC %", title_font=dict(size=9, color=muted), row=4, col=1)
    if has_vol:
        fig.update_yaxes(title_text="Vol",  title_font=dict(size=9, color=muted), row=5, col=1)

    label = "Weekly (5Y)" if is_weekly else "Daily (12M)"
    fig.update_layout(
        height=720,
        template=tmpl,
        paper_bgcolor=bg_col,
        plot_bgcolor=panel_col,
        font=dict(family="'IBM Plex Mono', monospace", color=txt_col),
        margin=dict(l=55, r=10, t=38, b=10),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="left",   x=0,
            font=dict(size=10, color=txt_col),
            bgcolor="rgba(0,0,0,0)",
            itemsizing="constant",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=panel_col, font_size=11),
        title=dict(
            text=f"<b>{ticker}</b>  ·  {label}",
            font=dict(size=13, color=txt_col),
            x=0, xanchor="left", pad=dict(l=4, t=4),
        ),
        barmode="overlay",
        xaxis_rangeslider_visible=False,
    )

    return fig

# ── Stats panel builder ───────────────────────────────────────────────────────
def _sig_badge(label, sig, hide_mobile=False):
    color   = "#34d399" if sig == "Bullish" else "#f87171"
    bg      = "rgba(52,211,153,0.15)" if sig == "Bullish" else "rgba(248,113,113,0.15)"
    style   = {"display": "flex", "justifyContent": "space-between",
                "alignItems": "center", "marginBottom": "6px"}
    return html.Div([
        html.Span(label, style={"color": "#8b949e", "fontSize": "11px"}),
        html.Span(sig,   style={"color": color, "background": bg,
                                 "padding": "2px 8px", "borderRadius": "4px",
                                 "fontSize": "11px", "fontWeight": "700"}),
    ], style=style)

def _stat_row(label, value, color=None):
    style = {"display": "flex", "justifyContent": "space-between",
             "alignItems": "center", "marginBottom": "6px"}
    val_style = {"fontSize": "12px", "fontWeight": "600",
                 "color": color if color else "#e6edf3"}
    return html.Div([
        html.Span(label, style={"color": "#8b949e", "fontSize": "11px"}),
        html.Span(str(value), style=val_style),
    ], style=style)

def _perf_color(v):
    if v is None: return "#8b949e"
    return "#34d399" if v >= 0 else "#f87171"

def build_stats_panel(stats):
    p = stats["perf"]

    perf_items = []
    for lbl, key in [("1D","1D"),("1W","1W"),("1M","1M"),("3M","3M"),("6M","6M")]:
        v = p.get(key)
        txt = f"{v:+.2f}%" if v is not None else "—"
        perf_items.append(html.Div([
            html.Div(lbl,  style={"fontSize": "9px", "color": "#8b949e", "textAlign": "center"}),
            html.Div(txt,  style={"fontSize": "11px", "fontWeight": "700",
                                   "color": _perf_color(v), "textAlign": "center"}),
        ], style={"flex": "1", "padding": "4px 2px",
                  "borderRight": "1px solid #30363d"}))

    z    = stats["zscore"]
    z_col = "#f87171" if z and z > 2.1 else "#34d399" if z and z < -2.0 else "#e6edf3"
    z_txt = f"{z:+.2f}" if z is not None else "—"

    ru   = stats["rv_ratio"]
    ru_col = "#f97316" if ru and ru > 1.2 else "#34d399" if ru and ru < 0.8 else "#e6edf3"

    card = {
        "background":   "#161b22",
        "border":       "1px solid #30363d",
        "borderRadius": "8px",
        "padding":      "14px 16px",
        "fontFamily":   "'IBM Plex Mono', monospace",
        "color":        "#e6edf3",
    }
    divider = {"borderTop": "1px solid #30363d", "margin": "10px 0"}

    return html.Div([
        # Header
        html.Div([
            html.Span(stats["ticker"], style={"fontSize": "20px", "fontWeight": "800",
                                               "color": C["price"], "letterSpacing": "1px"}),
            html.Span(f"  ${stats['last_px']}", style={"fontSize": "14px",
                                                        "color": "#e6edf3", "marginLeft": "8px"}),
        ], style={"marginBottom": "12px"}),

        # Signals
        _sig_badge("Trade  (EMA 21/63)", stats["trade_sig"]),
        _sig_badge("Trend  (vs EMA 200)", stats["trend_sig"]),
        html.Div(style=divider),

        # Range
        _stat_row("8M High",  f"${stats['hi8m']}"),
        _stat_row("8M Low",   f"${stats['lo8m']}"),
        _stat_row("± Low",    f"{stats['pct_from_low']:+.1f}%" if stats['pct_from_low'] is not None else "—"),
        _stat_row("Pot. Upside",   f"{stats['pot_upside']:+.1f}%" if stats['pot_upside'] is not None else "—",
                  color="#34d399"),
        _stat_row("Pot. Downside", f"{stats['pot_downside']:+.1f}%" if stats['pot_downside'] is not None else "—",
                  color="#f87171"),
        html.Div(style=divider),

        # Momentum metrics
        _stat_row("RoC 63D",    f"{stats['roc']:+.2f}%" if stats['roc'] is not None else "—"),
        _stat_row("vs EMA 200", f"{stats['sma200_pct']:+.1f}%"),
        html.Div(style=divider),

        # Performance
        html.Div("Recent Performance",
                 style={"fontSize": "10px", "color": "#8b949e", "marginBottom": "6px"}),
        html.Div(perf_items,
                 style={"display": "flex", "border": "1px solid #30363d",
                        "borderRadius": "6px", "overflow": "hidden", "marginBottom": "12px"}),

        # Volatility & Z-Score
        _stat_row("RV 1M",    f"{stats['rv_1m']:.1f}%" if stats['rv_1m'] else "—"),
        _stat_row("RV 3M",    f"{stats['rv_3m']:.1f}%" if stats['rv_3m'] else "—"),
        _stat_row("1M/3M RV", f"{ru:.2f}" if ru else "—", color=ru_col),
        html.Div(style=divider),
        _stat_row("12M Z-Score", z_txt, color=z_col),

    ], style=card)

# ── Layout ────────────────────────────────────────────────────────────────────
_FONT_LINK = html.Link(
    rel="stylesheet",
    href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&display=swap",
)

app.layout = html.Div(id="root", children=[
    _FONT_LINK,
    dcc.Store(id="theme-store", data="dark"),
    dcc.Store(id="chart-data-store"),

    # ── Top bar ──────────────────────────────────────────────────────────
    html.Div(id="topbar", children=[
        html.Div([
            html.Span("KCM", style={"color": C["price"], "fontWeight": "800",
                                     "fontSize": "15px", "letterSpacing": "2px"}),
            html.Span(" CHART", style={"color": "#e6edf3", "fontWeight": "400",
                                        "fontSize": "15px", "letterSpacing": "2px"}),
        ], style={"display": "flex", "alignItems": "center"}),

        # Controls
        html.Div([
            dcc.Input(
                id="ticker-input", value="SPY", type="text",
                placeholder="Ticker…",
                debounce=False,
                style={
                    "fontFamily": "'IBM Plex Mono', monospace",
                    "fontSize": "13px", "fontWeight": "600",
                    "textTransform": "uppercase",
                    "width": "100px",
                    "padding": "5px 10px",
                    "borderRadius": "5px",
                    "border": "1px solid #30363d",
                    "background": "#0d1117",
                    "color": "#e6edf3",
                    "outline": "none",
                },
            ),
            dcc.RadioItems(
                id="asset-type",
                options=[
                    {"label": " ETF / Stock", "value": "etf"},
                    {"label": " Mutual Fund", "value": "mf"},
                ],
                value="etf",
                inline=True,
                style={"fontSize": "12px", "color": "#e6edf3"},
                inputStyle={"marginRight": "4px", "accentColor": C["price"]},
                labelStyle={"marginRight": "14px", "cursor": "pointer"},
            ),
            dcc.RadioItems(
                id="timeframe",
                options=[
                    {"label": " Daily", "value": "daily"},
                    {"label": " Weekly", "value": "weekly"},
                ],
                value="daily",
                inline=True,
                style={"fontSize": "12px", "color": "#e6edf3"},
                inputStyle={"marginRight": "4px", "accentColor": C["price"]},
                labelStyle={"marginRight": "14px", "cursor": "pointer"},
            ),
            html.Button("Load", id="load-btn", n_clicks=0, style={
                "fontFamily": "'IBM Plex Mono', monospace",
                "fontSize": "12px", "fontWeight": "700",
                "padding": "5px 16px",
                "background": C["price"], "color": "#0d1117",
                "border": "none", "borderRadius": "5px", "cursor": "pointer",
            }),
            html.Button("☀", id="theme-btn", n_clicks=0, style={
                "fontFamily": "'IBM Plex Mono', monospace",
                "fontSize": "14px", "fontWeight": "700",
                "padding": "4px 10px",
                "background": "transparent", "color": "#e6edf3",
                "border": "1px solid #30363d", "borderRadius": "5px",
                "cursor": "pointer", "marginLeft": "6px",
            }),
        ], style={
            "display": "flex", "alignItems": "center", "gap": "12px", "flexWrap": "wrap",
        }),
    ], style={
        "display": "flex", "alignItems": "center", "justifyContent": "space-between",
        "padding": "8px 16px", "flexWrap": "wrap", "gap": "8px",
        "background": "#161b22", "borderBottom": "1px solid #30363d",
        "fontFamily": "'IBM Plex Mono', monospace",
    }),

    # ── Status bar ────────────────────────────────────────────────────────
    html.Div(id="status-bar", style={
        "padding": "4px 16px", "fontSize": "11px", "minHeight": "22px",
        "color": "#f87171", "fontFamily": "'IBM Plex Mono', monospace",
        "background": "#0d1117",
    }),

    # ── Main content: chart + sidebar ─────────────────────────────────────
    html.Div([
        # Chart column
        html.Div(
            dcc.Graph(
                id="main-chart",
                config={
                    "toImageButtonOptions": {
                        "format": "png", "filename": "kcm_chart", "scale": 2,
                    },
                    "displayModeBar": True,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                },
                style={"height": "720px"},
            ),
            style={"flex": "1", "minWidth": "0"},
        ),

        # Stats sidebar (hidden on mobile via CSS)
        html.Div(
            id="stats-sidebar",
            style={
                "width": "220px", "flexShrink": "0",
                "padding": "10px 10px 10px 0",
                "display": "flex", "flexDirection": "column",
            },
        ),
    ], style={
        "display": "flex", "alignItems": "stretch",
        "background": "#0d1117", "minHeight": "720px",
    }),

    # ── Mobile stats toggle ───────────────────────────────────────────────
    html.Div([
        html.Button("▼ Stats", id="mobile-stats-btn", n_clicks=0, style={
            "fontFamily": "'IBM Plex Mono', monospace",
            "fontSize": "11px", "fontWeight": "600",
            "padding": "5px 16px", "width": "100%",
            "background": "#161b22", "color": "#8b949e",
            "border": "none", "borderTop": "1px solid #30363d",
            "cursor": "pointer",
        }),
        html.Div(id="mobile-stats-panel", style={"display": "none", "padding": "10px 12px"}),
    ], id="mobile-stats-container", style={}),

    # Global CSS injected via dangerouslySetInnerHTML
    html.Div(id="global-css-holder", style={"display":"none"}),
])

# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("root",          "style"),
    Output("topbar",        "style"),
    Output("theme-store",   "data"),
    Output("theme-btn",     "children"),
    Output("theme-btn",     "style"),
    Output("ticker-input",  "style"),
    Input("theme-btn",      "n_clicks"),
    State("theme-store",    "data"),
)
def toggle_theme(n, theme):
    dark = (n % 2 == 0)
    new_theme = "dark" if dark else "light"
    icon = "☀" if dark else "🌙"

    root_style = {
        "fontFamily": "'IBM Plex Mono', monospace",
        "background": "#0d1117" if dark else "#f1f5f9",
        "minHeight": "100vh",
    }
    topbar_style = {
        "display": "flex", "alignItems": "center", "justifyContent": "space-between",
        "padding": "8px 16px", "flexWrap": "wrap", "gap": "8px",
        "background": "#161b22" if dark else "#ffffff",
        "borderBottom": f"1px solid {'#30363d' if dark else '#e2e8f0'}",
        "fontFamily": "'IBM Plex Mono', monospace",
    }
    btn_style = {
        "fontFamily": "'IBM Plex Mono', monospace",
        "fontSize": "14px", "fontWeight": "700",
        "padding": "4px 10px",
        "background": "transparent",
        "color": "#e6edf3" if dark else "#1e293b",
        "border": f"1px solid {'#30363d' if dark else '#cbd5e1'}",
        "borderRadius": "5px", "cursor": "pointer", "marginLeft": "6px",
    }
    input_style = {
        "fontFamily": "'IBM Plex Mono', monospace",
        "fontSize": "13px", "fontWeight": "600",
        "textTransform": "uppercase", "width": "100px",
        "padding": "5px 10px", "borderRadius": "5px",
        "border": f"1px solid {'#30363d' if dark else '#cbd5e1'}",
        "background": "#0d1117" if dark else "#ffffff",
        "color": "#e6edf3" if dark else "#1e293b",
        "outline": "none",
    }
    return root_style, topbar_style, new_theme, icon, btn_style, input_style


@app.callback(
    Output("main-chart",         "figure"),
    Output("stats-sidebar",      "children"),
    Output("mobile-stats-panel", "children"),
    Output("status-bar",         "children"),
    Input("load-btn",            "n_clicks"),
    Input("timeframe",           "value"),
    State("ticker-input",        "value"),
    State("asset-type",          "value"),
    State("theme-store",         "data"),
    prevent_initial_call=False,
)
def update_chart(n_clicks, tf, ticker, asset_type, theme):
    ctx = callback_context
    if not ticker:
        return go.Figure(), [], [], ""

    ticker    = ticker.upper().strip()
    is_weekly = (tf == "weekly")
    is_mf     = (asset_type == "mf")

    df, info = fetch_data(ticker, is_weekly)

    if df is None or len(df) < 50:
        msg = f"No data found for {ticker} — check the ticker symbol."
        return go.Figure(), [], [], msg

    df    = apply_all(df, is_mf)
    fig   = build_chart(df, ticker, is_weekly, is_mf, theme)
    stats = build_stats(df, ticker, is_mf, is_weekly)
    panel = build_stats_panel(stats)

    z     = stats["zscore"]
    z_str = f"Z-Score: {z:+.2f}" if z is not None else ""
    sig   = f"  |  Trade: {stats['trade_sig']}  |  Trend: {stats['trend_sig']}"
    status = f"{ticker}  ·  {'Mutual Fund' if is_mf else 'ETF/Stock'}  ·  ${stats['last_px']}  {sig}  {z_str}"

    return fig, panel, panel, status


@app.callback(
    Output("mobile-stats-panel",  "style"),
    Output("mobile-stats-btn",    "children"),
    Input("mobile-stats-btn",     "n_clicks"),
)
def toggle_mobile_stats(n):
    if n % 2 == 1:
        return {"display": "block", "padding": "10px 12px"}, "▲ Stats"
    return {"display": "none", "padding": "10px 12px"}, "▼ Stats"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run(debug=False, host="0.0.0.0", port=port)
