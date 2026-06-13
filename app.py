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
    "price":    "#4a9eff",
    "ema21":    "#93c5fd",
    "ema63":    "#f97316",
    # Bridge Bands
    "bb_bull":  "#34d399",              # bullish band outline + fill tint
    "bb_bear":  "#f87171",              # bearish band outline + fill tint
    "bb_fill_bull": "rgba(52,211,153,0.07)",
    "bb_fill_bear": "rgba(248,113,113,0.07)",
    "bb_mid":   "rgba(160,160,170,0.55)",
    # Momentum overlay
    "mom_pos":  "rgba(52,211,153,0.28)",
    "mom_neg":  "rgba(248,113,113,0.28)",
    # Volume
    "vol_up":   "rgba(52,211,153,0.50)",
    "vol_dn":   "rgba(248,113,113,0.50)",
    # Sub-panels
    "rv":       "#a78bfa",
    "stoch_k":  "#60a5fa",
    "stoch_d":  "#f87171",
    "ob":       "#f87171",
    "os_":      "#34d399",
    # Matrix Series
    "ms_bull":  "#34d399",
    "ms_bear":  "#f87171",
    "ms_res":   "#34d399",
    "ms_sup":   "#f87171",
    "ms_ob":    "#22d3ee",
    # Chrome
    "bg":       "#0d1117",
    "panel":    "#161b22",
    "border":   "#30363d",
    "text":     "#e6edf3",
    "muted":    "#8b949e",
}

# ── Redis ─────────────────────────────────────────────────────────────────────
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

# ── Cache ─────────────────────────────────────────────────────────────────────
def _ckey(ticker, is_weekly):
    return f"kcm2_{ticker}_{'w' if is_weekly else 'd'}"

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

# ── Data ──────────────────────────────────────────────────────────────────────
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

def get_asset_name(info):
    return (info.get("longName") or info.get("shortName") or "").strip()

def get_morningstar_url(ticker, is_mf):
    t = ticker.lower()
    if is_mf:
        return f"https://www.morningstar.com/funds/xnas/{t}/quote"
    return f"https://www.morningstar.com/etfs/arcx/{t}/quote"

# ── Indicators ────────────────────────────────────────────────────────────────

def _wilder_rma(series, length):
    """Wilder's RMA (= Pine's rma()): EWM with alpha=1/length, adjust=False."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()

def _wma(series, length):
    """Linearly-weighted moving average matching Pine's wma()."""
    weights = np.arange(1, length + 1, dtype=float)
    return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

def calc_bridge_bands(close, high, low, length=15, trend_length=63):
    """
    Bridge Bands — translated from Pine Script v4 by calebsandfort.

    Core idea: start with a WMA-based Bollinger Band, then interpolate it
    toward a linear-regression price channel by abs(Hurst×2 - 1).
    When Hurst ≈ 0.5 (random walk) the bands stay at standard BB width.
    When the market is strongly trending or mean-reverting they widen toward
    the price channel, giving a volatility-adjusted, regime-aware envelope.

    Returns a dict of Series: top, bottom, mid, top_mid, bottom_mid,
    trend_line, bull (bool Series), and hurst.
    """
    n = len(close)
    lm1 = length - 1  # lengthMinus1

    # ── Slope of the regression line (per-bar, rolling) ───────────────────────
    # slope[t] = (close[t] - close[t - lm1]) / lm1
    slope = (close - close.shift(lm1)) / lm1

    # ── Bridge range: min/max deviation of each bar from the trend line ───────
    # For bar t, trend_line_at_i = close[t] + slope[t] * (t - i)
    # i iterates from t back to t - lm1 (Pine's "0 to n" with shifted indexing)
    min_diff = pd.Series(np.nan, index=close.index)
    max_diff = pd.Series(np.nan, index=close.index)

    close_arr = close.values
    slope_arr = slope.values

    for t in range(lm1, n):
        m_min =  1e9
        m_max = -1e9
        s = slope_arr[t]
        c0 = close_arr[t]
        for i in range(lm1 + 1):          # i = 0 … lm1 (Pine: n - i in reversed order)
            val = close_arr[t - i] - (c0 + s * i)
            if val < m_min:
                m_min = val
            if val > m_max:
                m_max = val
        min_diff.iloc[t] = m_min
        max_diff.iloc[t] = m_max

    bridge_bottom = close + min_diff   # = close + most-negative deviation
    bridge_top    = close + max_diff   # = close + most-positive deviation

    # ── ATR (Wilder) ──────────────────────────────────────────────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = _wilder_rma(tr, length)

    # ── Hurst exponent proxy ──────────────────────────────────────────────────
    # hurst = log(highest_high - lowest_low) - log(ATR)) / log(length)
    hi_lo_range = high.rolling(length).max() - low.rolling(length).min()
    log_len = np.log(length)
    hurst = (np.log(hi_lo_range.clip(lower=1e-10)) - np.log(atr.clip(lower=1e-10))) / log_len

    # ── WMA-based Bollinger Bands ─────────────────────────────────────────────
    wma_c  = _wma(close, length)
    sd     = close.rolling(length).std()
    bb_top    = wma_c + sd * 2
    bb_bottom = wma_c - sd * 2

    # ── Bridge Bands: interpolate BB toward bridge range by |hurst×2 - 1| ────
    h_factor = (hurst * 2 - 1).abs()
    bb_top_final    = bb_top    - (bb_top    - bridge_top)    * h_factor
    bb_bottom_final = bb_bottom + (bridge_bottom - bb_bottom) * h_factor
    bb_mid          = (bb_top_final + bb_bottom_final) / 2
    bb_top_mid      = bb_mid    + (bb_top_final - bb_mid)    / 2
    bb_bottom_mid   = bb_bottom_final + (bb_mid - bb_bottom_final) / 2

    # ── Trend signal: Donchian midpoint of trendLength bars ──────────────────
    trend_line = (low.rolling(trend_length).min() +
                  (high.rolling(trend_length).max() - low.rolling(trend_length).min()) / 2)
    bull = close >= trend_line

    return {
        "top":        bb_top_final,
        "bottom":     bb_bottom_final,
        "mid":        bb_mid,
        "top_mid":    bb_top_mid,
        "bottom_mid": bb_bottom_mid,
        "trend":      trend_line,
        "bull":       bull,
        "hurst":      hurst,
    }

def apply_all(df):
    df = df.copy()
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    # ── Bridge Bands (replaces Bollinger Bands on price chart) ───────────────
    bb = calc_bridge_bands(close, high, low, length=15, trend_length=63)
    df["BB_Upper"]     = bb["top"]
    df["BB_Lower"]     = bb["bottom"]
    df["BB_Mid"]       = bb["mid"]
    df["BB_TopMid"]    = bb["top_mid"]
    df["BB_BottomMid"] = bb["bottom_mid"]
    df["BB_Trend"]     = bb["trend"]       # 63-period Donchian mid (trend line)
    df["BB_Bull"]      = bb["bull"]        # close >= trend line
    df["BB_Hurst"]     = bb["hurst"]

    # Trade line: 15-period Donchian midpoint (Pine's `trade` variable)
    trade_line = (low.rolling(15).min() +
                  (high.rolling(15).max() - low.rolling(15).min()) / 2)
    df["BB_Trade"]     = trade_line        # 15-period trade signal line

    # ── EMAs ─────────────────────────────────────────────────────────────────
    df["EMA21"]  = close.ewm(span=21,  adjust=False).mean()
    df["EMA63"]  = close.ewm(span=63,  adjust=False).mean()
    df["EMA200"] = close.ewm(span=200, adjust=False).mean()

    # ── Momentum overlay: (5D MA / 63D MA - 1) × 100 ────────────────────────
    df["Mom_Pct"] = ((close.rolling(5).mean() / close.rolling(63).mean()) - 1) * 100

    # ── Realized vol annualised ───────────────────────────────────────────────
    lr = np.log(close / close.shift(1))
    df["RV"] = lr.rolling(21).std() * np.sqrt(252) * 100

    # ── Stochastic RSI ────────────────────────────────────────────────────────
    delta  = close.diff()
    ma_up  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    ma_dn  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi    = 100 - (100 / (1 + ma_up / (ma_dn + 1e-10)))
    lo14, hi14 = rsi.rolling(14).min(), rsi.rolling(14).max()
    stoch  = (rsi - lo14) / (hi14 - lo14 + 1e-10) * 100
    df["StochRSI_K"] = stoch.rolling(3).mean()
    df["StochRSI_D"] = df["StochRSI_K"].rolling(3).mean()

    # ── Rate of Change — 12 month YoY (252 trading days) ────────────────────
    df["RoC_YoY"] = close.pct_change(252) * 100

    # ── Matrix Series ─────────────────────────────────────────────────────────
    # Translated from Pine Script v3 by wisestocktrader.com
    nn  = 5
    ys1 = (high + low + close * 2) / 4
    rk3 = ys1.ewm(span=nn, adjust=False).mean()
    rk4 = ys1.rolling(nn).std()
    rk5 = (ys1 - rk3) * 200 / (rk4 + 1e-10)
    rk6 = rk5.ewm(span=nn, adjust=False).mean()
    up   = rk6.ewm(span=nn, adjust=False).mean()
    down = up.ewm(span=nn, adjust=False).mean()
    df["MS_Up"]    = up
    df["MS_Down"]  = down
    df["MS_Open"]  = np.minimum(up, down)
    df["MS_Close"] = np.maximum(up, down)

    # CCI-based dynamic S/R for Matrix panel
    pds     = 16
    tp      = (high + low + close) / 3
    tp_sma  = tp.rolling(pds).mean()
    tp_mad  = tp.rolling(pds).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci     = (tp - tp_sma) / (0.015 * tp_mad + 1e-10)
    hi_cci  = cci.rolling(50).max()
    lo_cci  = cci.rolling(50).min()
    rng_cci = hi_cci - lo_cci
    df["MS_Resist"]  = lo_cci + rng_cci
    df["MS_Support"] = hi_cci - rng_cci

    return df

def calc_zscore(df):
    if len(df) < 126:
        return None
    recent = df["Close"].tail(252)
    std = recent.std()
    if std == 0:
        return None
    return round((df["Close"].iloc[-1] - recent.mean()) / std, 2)

def trade_trend_signal(price, ema):
    """Bullish: price > EMA. Neutral: within 1% below. Bearish: >1% below."""
    if price > ema:
        return "Bullish"
    elif price >= ema * 0.99:
        return "Neutral"
    else:
        return "Bearish"

def opinion_signal(trade, trend):
    if trade == "Bullish" and trend == "Bullish":
        return "Bullish"
    if trade == "Bearish" and trend == "Bearish":
        return "Bearish"
    return "Neutral"

def sig_color(sig):
    return {"Bullish": "#34d399", "Bearish": "#f87171", "Neutral": "#8b949e"}[sig]

def sig_bg(sig):
    return {"Bullish": "rgba(52,211,153,0.12)",
            "Bearish": "rgba(248,113,113,0.12)",
            "Neutral": "rgba(139,148,158,0.12)"}[sig]

# ── Chart ─────────────────────────────────────────────────────────────────────
def build_chart(df, ticker, is_weekly, is_mf, show_rv, show_stoch):
    """
    Panel order (top → bottom):
      1. Price    — always (Bridge Bands, EMAs, volume)
      2. RV       — optional toggle
      3. Matrix   — always (synthetic candle momentum oscillator)
      4. Mom      — always (5D/63D momentum bar chart)
      5. StochRSI — optional toggle
    """
    has_vol = (not is_mf) and ("Volume" in df.columns) and (df["Volume"].sum() > 0)
    display = df.tail(260 if is_weekly else 252)
    idx     = display.index

    row_labels  = ["price"]
    row_heights = [1.0]
    if show_rv:
        row_labels.append("rv")
        row_heights.append(0.20)
    row_labels.append("ms")
    row_heights.append(0.28)
    row_labels.append("mom")
    row_heights.append(0.16)
    if show_stoch:
        row_labels.append("stoch")
        row_heights.append(0.20)

    total   = sum(row_heights)
    heights = [h / total for h in row_heights]
    n_rows  = len(row_labels)
    specs   = [[{"secondary_y": False}]] * n_rows

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.022,
        row_heights=heights,
        specs=specs,
    )

    def row(lbl):
        return row_labels.index(lbl) + 1

    # ── Volume bars + MA21 ────────────────────────────────────────────────────
    if has_vol:
        p_lo    = float(display["Low"].min())
        p_hi    = float(display["High"].max())
        p_range = p_hi - p_lo
        v_max   = float(display["Volume"].max())
        if v_max > 0:
            vol_h = (display["Volume"] / v_max) * (p_range * 0.20)
            vol_colors = [C["vol_up"] if float(c) >= float(o) else C["vol_dn"]
                          for o, c in zip(display["Open"], display["Close"])]
            fig.add_trace(go.Bar(
                x=idx, y=vol_h, name="Volume",
                marker_color=vol_colors, marker_line_width=0,
                base=p_lo, opacity=0.55, showlegend=True,
            ), row=1, col=1, secondary_y=False)
            vol_ma21      = display["Volume"].rolling(21).mean()
            vol_ma21_norm = (vol_ma21 / v_max) * (p_range * 0.20)
            fig.add_trace(go.Scatter(
                x=idx, y=vol_ma21_norm + p_lo, name="Vol MA21",
                line=dict(color="#f97316", width=1), showlegend=True,
            ), row=1, col=1, secondary_y=False)

    # ── Bridge Bands — per-segment color, no fill ────────────────────────────
    # Bull = close >= 63-period Donchian mid (trend line).
    # We split the top/bottom band into contiguous bull/bear segments so each
    # segment can be drawn in its own color without a single-color compromise.
    bull_arr  = display["BB_Bull"].fillna(True).values
    upper_arr = display["BB_Upper"].values
    lower_arr = display["BB_Lower"].values
    idx_arr   = np.array(idx)

    def _split_segments(arr_x, arr_y, bull_mask):
        """Yield (x_list, y_list, is_bull) for each contiguous same-color segment."""
        if len(arr_x) == 0:
            return
        seg_x, seg_y = [arr_x[0]], [arr_y[0]]
        cur = bool(bull_mask[0])
        for i in range(1, len(arr_x)):
            b = bool(bull_mask[i])
            if b != cur:
                yield seg_x, seg_y, cur
                seg_x, seg_y = [arr_x[i-1], arr_x[i]], [arr_y[i-1], arr_y[i]]
                cur = b
            else:
                seg_x.append(arr_x[i])
                seg_y.append(arr_y[i])
        yield seg_x, seg_y, cur

    first_seg = True
    for seg_x, seg_y, is_bull in _split_segments(idx_arr, upper_arr, bull_arr):
        col = C["bb_bull"] if is_bull else C["bb_bear"]
        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            line=dict(color=col, width=2),
            mode="lines",
            name="BB Top" if first_seg else "",
            showlegend=first_seg,
            legendgroup="bb_top",
        ), row=1, col=1)
        first_seg = False

    first_seg = True
    for seg_x, seg_y, is_bull in _split_segments(idx_arr, lower_arr, bull_arr):
        col = C["bb_bull"] if is_bull else C["bb_bear"]
        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            line=dict(color=col, width=2),
            mode="lines",
            name="BB Bot" if first_seg else "",
            showlegend=first_seg,
            legendgroup="bb_bot",
        ), row=1, col=1)
        first_seg = False

    # Mid line — subtle dashed
    fig.add_trace(go.Scatter(
        x=idx, y=display["BB_Mid"], name="BB Mid",
        line=dict(color=C["bb_mid"], width=1, dash="dot"),
        showlegend=False,
    ), row=1, col=1)

    # ── Price candles / line ──────────────────────────────────────────────────
    if not is_mf:
        fig.add_trace(go.Candlestick(
            x=idx,
            open=display["Open"], high=display["High"],
            low=display["Low"],   close=display["Close"],
            name="Price",
            increasing_line_color=C["price"], decreasing_line_color="#ef4444",
            increasing_fillcolor=C["price"],  decreasing_fillcolor="#ef4444",
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=idx, y=display["Close"], name="Price",
            line=dict(color=C["price"], width=2),
        ), row=1, col=1)

    # ── Trend line (63-period Donchian mid) — solid, color = bull/bear ────────
    trend_arr  = display["BB_Trend"].values
    first_seg  = True
    for seg_x, seg_y, is_bull in _split_segments(idx_arr, trend_arr, bull_arr):
        col = C["bb_bull"] if is_bull else C["bb_bear"]
        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            line=dict(color=col, width=2),
            mode="lines",
            name="Trend (63)" if first_seg else "",
            showlegend=first_seg,
            legendgroup="bb_trend",
        ), row=1, col=1)
        first_seg = False

    # ── Trade line (15-period Donchian mid) — bright dots, color = bull/bear ──
    # Pine uses plot.style_circles; we use mode="markers" with circle markers.
    # Color per bar: green if close > trade mid, red otherwise.
    trade_arr  = display["BB_Trade"].values
    close_arr  = display["Close"].values
    trade_bull = close_arr >= trade_arr
    first_seg  = True
    for seg_x, seg_y, is_bull in _split_segments(idx_arr, trade_arr, trade_bull):
        col = C["bb_bull"] if is_bull else C["bb_bear"]
        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            mode="markers",
            marker=dict(color=col, size=4, symbol="circle"),
            name="Trade (15)" if first_seg else "",
            showlegend=first_seg,
            legendgroup="bb_trade",
        ), row=1, col=1)
        first_seg = False

    # Price y-axis: auto-scale
    p_lo_d = float(display["Low"].min())  if "Low"  in display.columns else float(display["Close"].min())
    p_hi_d = float(display["High"].max()) if "High" in display.columns else float(display["Close"].max())
    p_pad  = (p_hi_d - p_lo_d) * 0.04
    fig.update_yaxes(
        range=[p_lo_d - p_pad, p_hi_d + p_pad],
        title_text="Price", title_font=dict(size=9, color=C["muted"]),
        row=1, col=1,
    )

    # ── RV panel ─────────────────────────────────────────────────────────────
    if show_rv:
        r = row("rv")
        fig.add_trace(go.Scatter(
            x=idx, y=display["RV"], name="Realized Vol",
            line=dict(color=C["rv"], width=1.5),
            fill="tozeroy", fillcolor="rgba(167,139,250,0.10)",
        ), row=r, col=1)
        fig.update_yaxes(title_text="RV %", title_font=dict(size=9, color=C["muted"]),
                         row=r, col=1)

    # ── Matrix Series panel — always shown ───────────────────────────────────
    r    = row("ms")
    ms_o = display["MS_Open"].values
    ms_c = display["MS_Close"].values
    ms_h = ms_c
    ms_l = ms_o
    up_v = display["MS_Up"].values
    dn_v = display["MS_Down"].values

    fig.add_trace(go.Candlestick(
        x=idx,
        open=ms_o, high=ms_h, low=ms_l, close=ms_c,
        name="Matrix",
        increasing_line_color=C["ms_bull"],
        decreasing_line_color=C["ms_bear"],
        increasing_fillcolor=C["ms_bull"],
        decreasing_fillcolor=C["ms_bear"],
        whiskerwidth=0,
    ), row=r, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["MS_Resist"], name="MS Resist",
        line=dict(color=C["ms_res"], width=1.2), showlegend=False,
    ), row=r, col=1)
    fig.add_trace(go.Scatter(
        x=idx, y=display["MS_Support"], name="MS Support",
        line=dict(color=C["ms_sup"], width=1.2), showlegend=False,
    ), row=r, col=1)

    ob_thresh, os_thresh = 200, -200
    ob_y = [float(ms_h[i]) + 8 if up_v[i] > ob_thresh else None for i in range(len(idx))]
    os_y = [float(ms_l[i]) - 8 if dn_v[i] < os_thresh else None for i in range(len(idx))]
    for marker_y, mlabel in [(ob_y, "OB"), (os_y, "OS")]:
        valid = [(idx[i], marker_y[i]) for i in range(len(idx)) if marker_y[i] is not None]
        if valid:
            xs, ys = zip(*valid)
            fig.add_trace(go.Scatter(
                x=list(xs), y=list(ys), name=mlabel, mode="markers",
                marker=dict(symbol="x", size=8, color=C["ms_ob"], line_width=2),
                showlegend=False,
            ), row=r, col=1)

    ms_all = np.concatenate([ms_h[~np.isnan(ms_h)], ms_l[~np.isnan(ms_l)]])
    if len(ms_all) > 0:
        ms_pad = (ms_all.max() - ms_all.min()) * 0.15
        fig.update_yaxes(
            range=[ms_all.min() - ms_pad, ms_all.max() + ms_pad],
            title_text="Matrix", title_font=dict(size=9, color=C["muted"]),
            row=r, col=1,
        )
    else:
        fig.update_yaxes(title_text="Matrix", title_font=dict(size=9, color=C["muted"]),
                         row=r, col=1)

    # ── Momentum bar panel — always shown below Matrix ────────────────────────
    r = row("mom")
    mom      = display["Mom_Pct"].fillna(0)
    mom_cols = [C["mom_pos"] if v >= 0 else C["mom_neg"] for v in mom]
    fig.add_trace(go.Bar(
        x=idx, y=mom, name="Momentum",
        marker_color=mom_cols, marker_line_width=0,
    ), row=r, col=1)
    fig.add_hline(y=0, line_color=C["muted"], line_width=0.8, row=r, col=1)
    fig.update_yaxes(title_text="Mom %", title_font=dict(size=9, color=C["muted"]),
                     row=r, col=1)

    # ── StochRSI panel — optional ─────────────────────────────────────────────
    if show_stoch:
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

    # X-axis: show ticks on price panel and bottom panel only
    for r_idx in range(1, n_rows + 1):
        show = (r_idx == 1 or r_idx == n_rows)
        fig.update_xaxes(showticklabels=show,
                         tickfont=dict(size=9, color=C["muted"]),
                         row=r_idx, col=1)

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
        showlegend=False,
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

# ── Stats ─────────────────────────────────────────────────────────────────────
def pct_n(df, n):
    if len(df) <= n:
        return None
    return round((df["Close"].iloc[-1] - df["Close"].iloc[-1-n]) / df["Close"].iloc[-1-n] * 100, 2)

def build_stats(df_daily, ticker, is_mf):
    """Always uses daily df for all stats calculations."""
    z    = calc_zscore(df_daily)
    last = round(float(df_daily["Close"].iloc[-1]), 2)

    # 12M Hi/Lo
    win  = df_daily["Close"].tail(252)
    hi12 = round(float(win.max()), 2)
    lo12 = round(float(win.min()), 2)
    vs_lo = round((last - lo12) / lo12 * 100, 1) if lo12 else None

    # Bridge Bands distance from current price
    bb_upper = round(float(df_daily["BB_Upper"].iloc[-1]), 2)
    bb_lower = round(float(df_daily["BB_Lower"].iloc[-1]), 2)
    bb_up  = round((bb_upper - last) / last * 100, 1) if last else None
    bb_dn  = round((bb_lower - last) / last * 100, 1) if last else None
    bb_bull = bool(df_daily["BB_Bull"].iloc[-1])
    hurst  = round(float(df_daily["BB_Hurst"].iloc[-1]), 2) if not np.isnan(df_daily["BB_Hurst"].iloc[-1]) else None

    # Trade/Trend signals — now use Bridge Bands Donchian lines, not EMAs
    bb_trade_val = float(df_daily["BB_Trade"].iloc[-1])
    bb_trend_val = float(df_daily["BB_Trend"].iloc[-1])
    ema200       = float(df_daily["EMA200"].iloc[-1])
    trade  = trade_trend_signal(last, bb_trade_val)
    trend  = trade_trend_signal(last, bb_trend_val)
    opinion = opinion_signal(trade, trend)
    sma200_pct = round((last - ema200) / ema200 * 100, 1)

    # RV 1M
    rv1m = round(float(df_daily["RV"].tail(21).mean()), 1)

    # Performance
    perf = {k: pct_n(df_daily, n) for k, n in [("1D",1),("1W",5),("1M",21),("3M",63),("6M",126)]}

    return dict(ticker=ticker, last=last, z=z,
                trade=trade, trend=trend, opinion=opinion,
                hi12=hi12, lo12=lo12, vs_lo=vs_lo,
                bb_up=bb_up, bb_dn=bb_dn, bb_bull=bb_bull, hurst=hurst,
                sma200=sma200_pct, rv1m=rv1m, perf=perf)

def _pc(v):
    return "#34d399" if v and v >= 0 else "#f87171"

def _row(label, val, color=None):
    return html.Div([
        html.Span(label, style={"color": C["muted"], "fontSize": "11px"}),
        html.Span(val,   style={"color": color or C["text"],
                                 "fontSize": "12px", "fontWeight": "600"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": "5px"})

def _sig_row(label, sig):
    col = sig_color(sig)
    bg  = sig_bg(sig)
    return html.Div([
        html.Span(label, style={"color": C["muted"], "fontSize": "11px"}),
        html.Span(sig,   style={"color": col, "background": bg,
                                 "padding": "2px 7px", "borderRadius": "4px",
                                 "fontSize": "11px", "fontWeight": "700"}),
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

    z     = s["z"]
    z_col = "#f87171" if z and z > 2.1 else "#34d399" if z and z < -2.0 else C["text"]

    ob_pill = []
    if z is not None and abs(z) >= 2.0:
        lbl_ob = "OVERBOUGHT" if z > 0 else "OVERSOLD"
        col_ob = "#f87171"   if z > 0 else "#34d399"
        ob_pill = [html.Div(
            html.Span(lbl_ob, style={
                "color": col_ob, "background": "rgba(13,17,23,0.95)",
                "padding": "3px 12px", "borderRadius": "4px",
                "fontSize": "11px", "fontWeight": "700",
                "border": f"2px solid {col_ob}",
                "display": "inline-block", "marginTop": "4px",
            }),
            style={"textAlign": "center"}
        )]

    # Bridge Bands outlook pill
    bb_bull = s.get("bb_bull", True)
    hurst   = s.get("hurst")
    bb_out_col = C["bb_bull"] if bb_bull else C["bb_bear"]
    bb_out_bg  = "rgba(52,211,153,0.12)" if bb_bull else "rgba(248,113,113,0.12)"
    bb_out_lbl = "BULL" if bb_bull else "BEAR"

    return html.Div([
        html.Div([
            html.Span(s["ticker"], style={"fontSize": "18px", "fontWeight": "800",
                                           "color": C["price"], "letterSpacing": "1px"}),
            html.Span(f"  ${s['last']}", style={"fontSize": "13px", "color": C["text"]}),
        ], style={"marginBottom": "10px"}),

        _sig_row("Trade  (15)",   s["trade"]),
        _sig_row("Trend  (63)",   s["trend"]),
        _sig_row("Opinion",          s["opinion"]),
        html.Div(style=div),

        _row("12M High", f"${s['hi12']}"),
        _row("12M Low",  f"${s['lo12']}"),
        _row("vs 12M Low", f"{s['vs_lo']:+.1f}%" if s["vs_lo"] is not None else "—"),
        html.Div(style=div),

        # Bridge Bands section
        html.Div("Bridge Bands", style={"fontSize": "10px", "color": C["muted"], "marginBottom": "5px"}),
        html.Div([
            html.Span("Outlook", style={"color": C["muted"], "fontSize": "11px"}),
            html.Span(bb_out_lbl, style={"color": bb_out_col, "background": bb_out_bg,
                                          "padding": "2px 7px", "borderRadius": "4px",
                                          "fontSize": "11px", "fontWeight": "700"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "5px"}),
        _row("BB Upside",   f"{s['bb_up']:+.1f}%"  if s["bb_up"]  is not None else "—", color=C["bb_bull"]),
        _row("BB Downside", f"{s['bb_dn']:+.1f}%"  if s["bb_dn"]  is not None else "—", color=C["bb_bear"]),
        _row("Hurst", f"{hurst:.2f}" if hurst is not None else "—"),
        html.Div(style=div),

        _row("vs EMA 200", f"{s['sma200']:+.1f}%"),
        html.Div(style=div),

        html.Div("Performance", style={"fontSize": "10px", "color": C["muted"], "marginBottom": "5px"}),
        html.Div(cells, style={"display": "flex", "border": "1px solid #21262d",
                                "borderRadius": "5px", "overflow": "hidden", "marginBottom": "10px"}),
        _row("RV 1M", f"{s['rv1m']:.1f}%"),
        html.Div(style=div),
        _row("12M Z-Score", f"{z:+.2f}" if z is not None else "—", color=z_col),
        *ob_pill,
    ], style={"background": C["panel"], "border": f"1px solid {C['border']}",
              "borderRadius": "8px", "padding": "12px 14px",
              "fontFamily": "IBM Plex Mono", "color": C["text"]})

# ── Legend items ──────────────────────────────────────────────────────────────
def _leg_item(color, label, shape="square"):
    if shape == "line":
        marker = html.Div(style={"width": "14px", "height": "2px",
                                  "background": color, "borderRadius": "1px"})
    else:
        marker = html.Div(style={"width": "10px", "height": "10px",
                                  "background": color, "borderRadius": "2px"})
    return html.Div([marker, html.Span(label, style={"color": C["muted"], "fontSize": "10px"})],
                    style={"display": "flex", "alignItems": "center", "gap": "4px"})

LEGEND_ITEMS = [
    _leg_item(C["price"],    "Price",       "square"),
    _leg_item(C["bb_bull"],  "Bull band",   "line"),
    _leg_item(C["bb_bear"],  "Bear band",   "line"),
    _leg_item(C["vol_up"],   "Vol+",        "square"),
    _leg_item(C["vol_dn"],   "Vol-",        "square"),
]

def _tog_style(active):
    return {"fontFamily": "IBM Plex Mono", "fontSize": "11px", "fontWeight": "600",
            "padding": "3px 12px", "borderRadius": "4px", "cursor": "pointer",
            "marginRight": "6px",
            "background": "#4a9eff22" if active else "transparent",
            "color": C["price"] if active else C["muted"],
            "border": f"1px solid {'#4a9eff55' if active else C['border']}"}

# ── Layout ────────────────────────────────────────────────────────────────────
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

    # Toggle row + legend
    html.Div([
        html.Span("Show:", style={"color": C["muted"], "fontSize": "11px", "marginRight": "8px"}),
        html.Button("Realized Vol",   id="toggle-rv",    n_clicks=0, style=_tog_style(False)),
        html.Button("Stoch RSI",      id="toggle-stoch", n_clicks=0, style=_tog_style(False)),
        html.Div(style={"width": "20px"}),
        html.Div(LEGEND_ITEMS,
                 style={"display": "flex", "alignItems": "center", "gap": "12px",
                        "flexWrap": "wrap"}),
    ], style={"padding": "5px 14px", "background": C["bg"],
              "borderBottom": f"1px solid {C['border']}",
              "display": "flex", "alignItems": "center", "flexWrap": "wrap", "gap": "4px"}),

    # Status bar — name link + signals
    html.Div(id="status-bar", style={"padding": "4px 14px", "fontSize": "11px",
             "minHeight": "24px", "background": C["bg"],
             "borderBottom": f"1px solid {C['border']}",
             "display": "flex", "alignItems": "center", "gap": "12px", "flexWrap": "wrap"}),

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

# ── Callbacks ─────────────────────────────────────────────────────────────────
@app.callback(
    Output("main-chart",         "figure"),
    Output("stats-sidebar",      "children"),
    Output("mobile-stats-panel", "children"),
    Output("status-bar",         "children"),
    Input("load-btn",            "n_clicks"),
    Input("timeframe",           "value"),
    Input("toggle-rv",           "n_clicks"),
    Input("toggle-stoch",        "n_clicks"),
    State("ticker-input",        "value"),
    State("asset-type",          "value"),
    prevent_initial_call=False,
)
def update_chart(n_load, tf, n_rv, n_stoch, ticker, asset_type):
    ticker     = (ticker or "SPY").upper().strip()
    is_weekly  = (tf == "weekly")
    is_mf      = (asset_type == "mf")
    show_rv    = (n_rv    % 2 == 1)
    show_stoch = (n_stoch % 2 == 1)

    df_daily, info = fetch_data(ticker, is_weekly=False)
    if df_daily is None or len(df_daily) < 50:
        return go.Figure(), [], [], [html.Span(f"No data for {ticker}",
                                               style={"color": C["ob"]})]

    if is_weekly:
        df_chart, _ = fetch_data(ticker, is_weekly=True)
        if df_chart is None:
            df_chart = df_daily
    else:
        df_chart = df_daily

    df_daily = apply_all(df_daily)
    df_chart = apply_all(df_chart)

    fig   = build_chart(df_chart, ticker, is_weekly, is_mf, show_rv, show_stoch)
    s     = build_stats(df_daily, ticker, is_mf)
    panel = build_stats_panel(s)

    name    = get_asset_name(info)
    ms_url  = get_morningstar_url(ticker, is_mf)
    name_el = html.A(name or ticker,
                     href=ms_url, target="_blank",
                     style={"color": C["price"], "textDecoration": "none",
                            "fontWeight": "600", "fontSize": "12px"})

    def sig_span(label, sig):
        return html.Span([
            html.Span(f"{label}: ", style={"color": C["muted"]}),
            html.Span(sig, style={"color": sig_color(sig), "fontWeight": "700"}),
        ], style={"fontSize": "11px"})

    # Bridge Bands outlook in status bar
    bb_bull = s.get("bb_bull", True)
    bb_col  = C["bb_bull"] if bb_bull else C["bb_bear"]
    bb_lbl  = "BULL" if bb_bull else "BEAR"

    status_children = [
        name_el,
        html.Span("|", style={"color": C["border"]}),
        sig_span("Trade",   s["trade"]),
        sig_span("Trend",   s["trend"]),
        sig_span("Opinion", s["opinion"]),
        html.Span("|", style={"color": C["border"]}),
        html.Span([
            html.Span("BB: ", style={"color": C["muted"]}),
            html.Span(bb_lbl, style={"color": bb_col, "fontWeight": "700"}),
        ], style={"fontSize": "11px"}),
        html.Span(f"${s['last']}", style={"color": C["text"], "fontSize": "11px"}),
    ]

    return fig, panel, panel, status_children


@app.callback(
    Output("toggle-rv",    "style"),
    Output("toggle-stoch", "style"),
    Input("toggle-rv",     "n_clicks"),
    Input("toggle-stoch",  "n_clicks"),
)
def toggle_styles(n_rv, n_stoch):
    return (_tog_style(n_rv    % 2 == 1),
            _tog_style(n_stoch % 2 == 1))


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
