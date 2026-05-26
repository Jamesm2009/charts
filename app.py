"""
AETD Custom Charting Tool
Dash + Plotly | yfinance | Upstash Redis + local pickle cache
Deploy: Dokku on DigitalOcean | charts.market-dashboards.com
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

# ── Server (gunicorn needs app.server) ───────────────────────────────────────
app = dash.Dash(
    __name__,
    title="AETD Custom Chart Tool",
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap"
    ],
)
server = app.server   # gunicorn entry point

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_DIR         = "cache"
REDIS_URL         = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN       = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
LOCAL_TTL         = 3600    # 1 hour  — local pickle
REDIS_TTL_DAILY   = 3600    # 1 hour  — Redis daily
REDIS_TTL_WEEKLY  = 21600   # 6 hours — Redis weekly
os.makedirs(CACHE_DIR, exist_ok=True)


# ── Upstash Redis helpers ─────────────────────────────────────────────────────

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
    return f"aetd_{ticker}_{'w' if is_weekly else 'd'}"


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

    # 1. Local pickle (fastest)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            if datetime.now().timestamp() - data.get("ts", 0) < LOCAL_TTL:
                return data["df"], data.get("info", {})
        except Exception:
            pass

    # 2. Upstash Redis (survives restarts)
    raw = redis_get(key)
    if raw:
        try:
            payload = json.loads(raw)
            df      = _json_to_df(payload["df"])
            info    = payload.get("info", {})
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

    days  = 1825 if is_weekly else 400
    end   = datetime.now()
    start = end - timedelta(days=days + 100)

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None, None

    if is_weekly:
        df = df.resample("W").agg({
            "Open": "first", "High": "max",
            "Low": "min", "Close": "last", "Volume": "sum",
        })

    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}

# yfinance >= 0.2.38 returns MultiIndex columns — flatten to simple names\n   
    if isinstance(df.columns, pd.MultiIndex):\n        
      df.columns = df.columns.get_level_values(0)\n    
      df = df.dropna()\n    
     save_cache(ticker, is_weekly, df, info)\n    
    return df, 
    info


# ── Asset-type detection ──────────────────────────────────────────────────────

def is_mutual_fund(info):
    if not info:
        return False
    qt = info.get("quoteType", "").upper()
    if qt in ("MUTUALFUND", "FUND"):
        return True
    if info.get("fundFamily"):
        return True
    for name in (info.get("longName", ""), info.get("shortName", "")):
        name = name.upper()
        if ("MUTUAL" in name or "FUND" in name) and "ETF" not in name:
            return True
    return False


# ── Indicators ────────────────────────────────────────────────────────────────

def calc_vol_bb(df, period=21, mult=2.0):
    df = df.copy()
    df["BB_Mid"]   = df["Close"].rolling(period).mean()
    df["BB_Std"]   = df["Close"].rolling(period).std()
    df["Vol_MA20"] = df["Volume"].rolling(20).mean()
    vol_ratio      = (df["Volume"] / df["Vol_MA20"]).rolling(5).mean().fillna(1)
    adj            = df["BB_Std"] * vol_ratio
    df["BB_Upper"] = df["BB_Mid"] + mult * adj
    df["BB_Lower"] = df["BB_Mid"] - mult * adj
    return df


def calc_stoch_rsi(df, period=14, sk=3, sd=3):
    df = df.copy()
    delta   = df["Close"].diff()
    ma_up   = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    ma_down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rsi     = 100 - (100 / (1 + ma_up / ma_down))
    lo, hi  = rsi.rolling(period).min(), rsi.rolling(period).max()
    stoch   = (rsi - lo) / (hi - lo) * 100
    df["StochRSI_K"] = stoch.rolling(sk).mean()
    df["StochRSI_D"] = df["StochRSI_K"].rolling(sd).mean()
    return df


def calc_zscore(df):
    if len(df) < 50:
        return None
    recent = df["Close"].tail(252)
    std    = recent.std()
    if std == 0:
        return None
    return round((df["Close"].iloc[-1] - recent.mean()) / std, 2)


def calc_momentum(df):
    df = df.copy()
    df["Ret_5d"]       = df["Close"].pct_change(5) * 100
    df["Ret_63d_norm"] = df["Close"].pct_change(63) * 100 / 12
    return df


def apply_all(df):
    df = calc_vol_bb(df)
    df = calc_stoch_rsi(df)
    df = calc_momentum(df)
    df["MA63"] = df["Close"].rolling(63).mean()
    return df


# ── Styles ────────────────────────────────────────────────────────────────────

_DARK  = {"backgroundColor": "#0f172a", "color": "#e2e8f0",
          "fontFamily": "'Inter', system-ui, sans-serif", "minHeight": "100vh"}
_LIGHT = {"backgroundColor": "#f1f5f9", "color": "#1e293b",
          "fontFamily": "'Inter', system-ui, sans-serif", "minHeight": "100vh"}

_BTN = {"border": "none", "borderRadius": "6px", "padding": "8px 18px",
        "fontSize": "13px", "fontWeight": "600", "cursor": "pointer"}


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div(id="app-shell", style=_DARK, children=[

    dcc.Store(id="theme-store", data="dark"),

    # Header
    html.Div(style={
        "background": "linear-gradient(135deg,#1e3a5f,#0f2240)",
        "padding": "12px 24px", "display": "flex",
        "alignItems": "center", "justifyContent": "space-between",
        "flexWrap": "wrap", "gap": "10px",
        "boxShadow": "0 2px 10px rgba(0,0,0,0.4)",
    }, children=[
        html.Div([
            html.H1("AETD Custom Charting Tool",
                    style={"fontSize": "19px", "fontWeight": "700",
                           "color": "#f8fafc", "margin": 0}),
            html.Div(
                "Price + Volume + Momentum | Vol-Adjusted Bollinger Bands | "
                "charts.market-dashboards.com",
                style={"fontSize": "11px", "color": "#93c5fd", "marginTop": "3px"},
            ),
        ]),
        html.Button("Light Mode", id="theme-btn", n_clicks=0, style={
            **_BTN,
            "background": "rgba(255,255,255,0.1)", "color": "#e2e8f0",
            "border": "1px solid rgba(255,255,255,0.2)", "fontSize": "12px",
            "padding": "5px 12px",
        }),
    ]),

    # Controls
    html.Div(style={
        "padding": "10px 24px", "display": "flex", "gap": "12px",
        "alignItems": "center", "flexWrap": "wrap",
        "borderBottom": "1px solid rgba(148,163,184,0.15)",
    }, children=[
        dcc.Input(
            id="ticker-input", value="SPY", type="text",
            placeholder="Ticker  e.g. SPY, AAPL, VFIAX",
            debounce=False,
            style={
                "fontSize": "15px", "fontWeight": "600",
                "textTransform": "uppercase", "width": "230px",
                "padding": "7px 12px", "borderRadius": "6px",
                "border": "1px solid #334155",
                "background": "#1e293b", "color": "#f1f5f9",
            },
        ),
        dcc.RadioItems(
            id="timeframe",
            options=[
                {"label": "  Daily  (~12 mo)", "value": "daily"},
                {"label": "  Weekly (5 yr)",   "value": "weekly"},
            ],
            value="daily", inline=True,
            style={"fontSize": "13px", "color": "#cbd5e1"},
            inputStyle={"marginRight": "5px"},
            labelStyle={"marginRight": "18px"},
        ),
        html.Button("Load Chart", id="load-btn", n_clicks=0,
                    style={**_BTN, "background": "#2563eb", "color": "#fff"}),
        html.Button("Export CSV", id="export-btn", n_clicks=0,
                    style={**_BTN, "background": "#0f766e", "color": "#fff"}),
        dcc.Download(id="download-csv"),
    ]),

    # Indicator toggles
    html.Div(style={
        "padding": "7px 24px", "fontSize": "12px",
        "borderBottom": "1px solid rgba(148,163,184,0.12)",
        "display": "flex", "gap": "8px", "alignItems": "center", "flexWrap": "wrap",
    }, children=[
        html.Span("Overlays:", style={"color": "#94a3b8", "fontWeight": "600"}),
        dcc.Checklist(
            id="show-indicators",
            options=[
                {"label": " Bollinger Bands", "value": "bb"},
                {"label": " MA 63",           "value": "ma63"},
                {"label": " Volume MA",       "value": "volma"},
                {"label": " Stoch RSI",       "value": "stoch"},
                {"label": " Momentum Bars",   "value": "momentum"},
            ],
            value=["bb", "ma63", "stoch", "momentum"],
            inline=True,
            style={"fontSize": "12px", "color": "#cbd5e1"},
            inputStyle={"marginRight": "4px"},
            labelStyle={"marginRight": "14px"},
        ),
    ]),

    # Status bar
    html.Div(id="status-bar", style={
        "padding": "6px 24px", "fontSize": "12px",
        "color": "#fbbf24", "fontWeight": "600", "minHeight": "28px",
    }),

    # Chart
    dcc.Graph(
        id="main-chart",
        style={"height": "920px"},
        config={
            "toImageButtonOptions": {
                "format": "png", "filename": "aetd_chart", "scale": 2,
            },
            "displayModeBar": True,
        },
    ),

    html.Footer(
        "Data: Yahoo Finance (yfinance)  |  Cache: Upstash Redis + local  |  "
        "AETD Custom Chart Tool",
        style={
            "textAlign": "center", "padding": "12px", "fontSize": "11px",
            "color": "#475569", "borderTop": "1px solid rgba(148,163,184,0.1)",
        },
    ),
])


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-shell",   "style"),
    Output("theme-store", "data"),
    Output("theme-btn",   "children"),
    Input("theme-btn",    "n_clicks"),
)
def toggle_theme(n):
    if n % 2 == 1:
        return _LIGHT, "light", "Dark Mode"
    return _DARK, "dark", "Light Mode"


@app.callback(
    Output("main-chart",  "figure"),
    Output("status-bar",  "children"),
    Input("load-btn",     "n_clicks"),
    Input("theme-store",  "data"),
    State("ticker-input", "value"),
    State("timeframe",    "value"),
    State("show-indicators", "value"),
)
def update_chart(n_clicks, theme, ticker, tf, indicators):
    ticker    = (ticker or "SPY").upper().strip()
    is_weekly = (tf == "weekly")
    ind       = indicators or []
    tmpl      = "plotly_dark" if theme == "dark" else "plotly_white"

    df, info = fetch_data(ticker, is_weekly)

    if df is None or len(df) < 50:
        fig = go.Figure()
        fig.update_layout(template=tmpl, height=920)
        return fig, f"No data found for {ticker} — check the ticker symbol."

    df    = apply_all(df)
    z     = calc_zscore(df)
    is_mf = is_mutual_fund(info)
    use_cs = not (is_mf and not is_weekly)

    show_bb       = "bb"       in ind
    show_ma63     = "ma63"     in ind
    show_volma    = "volma"    in ind
    show_stoch    = "stoch"    in ind
    show_momentum = "momentum" in ind

    # Build subplot config dynamically
    rows_cfg    = [{"label": "price",    "h": 0.52}]
    if show_momentum:
        rows_cfg.append({"label": "momentum", "h": 0.13})
    rows_cfg.append({"label": "volume",  "h": 0.18})
    if show_stoch:
        rows_cfg.append({"label": "stoch",    "h": 0.17})

    total = sum(r["h"] for r in rows_cfg)
    heights = [r["h"] / total for r in rows_cfg]
    labels  = [r["label"] for r in rows_cfg]
    n_rows  = len(rows_cfg)

    titles_map = {
        "price":    "Price & Indicators",
        "momentum": "5-Day Momentum vs 63-Day Baseline",
        "volume":   "Volume",
        "stoch":    "Stochastic RSI (14)",
    }
    subplot_titles = [titles_map[l] for l in labels]

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=heights,
        subplot_titles=subplot_titles,
    )

    def row(label):
        return labels.index(label) + 1

    # ── Price ──
    if use_cs:
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], name="Price",
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
        ), row=row("price"), col=1)
    else:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["Close"], name="Close",
            line=dict(color="#60a5fa", width=2),
        ), row=row("price"), col=1)

    if show_bb:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Upper"], name="BB Upper",
            line=dict(color="#f59e0b", dash="dash", width=1),
        ), row=row("price"), col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Lower"], name="BB Lower",
            line=dict(color="#f59e0b", dash="dash", width=1),
            fill="tonexty", fillcolor="rgba(245,158,11,0.07)",
        ), row=row("price"), col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Mid"], name="BB Mid",
            line=dict(color="#f59e0b", dash="dot", width=1),
        ), row=row("price"), col=1)

    if show_ma63:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MA63"], name="MA 63",
            line=dict(color="#818cf8", width=2),
        ), row=row("price"), col=1)

    # ── Momentum ──
    if show_momentum:
        colors_m = ["#22c55e" if v >= 0 else "#ef4444"
                    for v in df["Ret_5d"].fillna(0)]
        fig.add_trace(go.Bar(
            x=df.index, y=df["Ret_5d"],
            name="5-Day Return %", marker_color=colors_m, opacity=0.85,
        ), row=row("momentum"), col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["Ret_63d_norm"],
            name="63-Day Baseline (scaled)",
            line=dict(color="#a78bfa", width=1.5),
        ), row=row("momentum"), col=1)
        fig.add_hline(y=0, line_color="#64748b", line_width=1,
                      row=row("momentum"), col=1)

    # ── Volume ──
    vol_colors = [
        "#22c55e" if float(c) >= float(o) else "#ef4444"
        for o, c in zip(df["Open"], df["Close"])
    ]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"],
        name="Volume", marker_color=vol_colors, opacity=0.75,
    ), row=row("volume"), col=1)
    if show_volma:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["Vol_MA20"], name="Vol MA20",
            line=dict(color="#a78bfa", width=1.5),
        ), row=row("volume"), col=1)

    # ── Stochastic RSI ──
    if show_stoch:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["StochRSI_K"], name="%K",
            line=dict(color="#60a5fa", width=1.5),
        ), row=row("stoch"), col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["StochRSI_D"], name="%D",
            line=dict(color="#f87171", width=1.5),
        ), row=row("stoch"), col=1)
        fig.add_hline(y=80, line_dash="dot", line_color="#ef4444",
                      annotation_text="OB", row=row("stoch"), col=1)
        fig.add_hline(y=20, line_dash="dot", line_color="#22c55e",
                      annotation_text="OS", row=row("stoch"), col=1)

    # ── Z-Score annotation ──
    if z is not None and abs(z) > 2.0:
        label_txt = "OVERBOUGHT" if z > 2.1 else "OVERSOLD"
        col_hex   = "#ef4444"    if z > 2.1 else "#22c55e"
        fig.add_annotation(
            text=label_txt, x=0.98, y=0.97,
            xref="paper", yref="paper", showarrow=False,
            font=dict(color=col_hex, size=13, family="Inter"),
            bgcolor=f"rgba({'239,68,68' if z > 2.1 else '34,197,94'},0.15)",
            bordercolor=col_hex, borderwidth=1, borderpad=6,
        )

    # ── Layout ──
    label   = "Weekly" if is_weekly else "Daily"
    atype   = "Mutual Fund" if is_mf else "Stock / ETF"
    z_str   = f"{z:+.2f}" if z is not None else "n/a"
    txt_col = "#f1f5f9" if theme == "dark" else "#1e293b"

    fig.update_layout(
        title=dict(
            text=f"{ticker}  |  {atype}  |  {label}  |  12-Mo Z-Score: {z_str}",
            font=dict(size=14, color=txt_col),
        ),
        height=920, template=tmpl, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="center", x=0.5, font=dict(size=11)),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=60, t=60, b=40),
    )

    ob_str = ""
    if z is not None:
        if z > 2.1:
            ob_str = "  OVERBOUGHT"
        elif z < -2.0:
            ob_str = "  OVERSOLD"

    status = (
        f"{ticker}  |  {atype}  |  {label} view  |  "
        f"Z-Score: {z_str}{ob_str}  |  {len(df):,} bars loaded"
    )
    return fig, status


@app.callback(
    Output("download-csv", "data"),
    Input("export-btn",    "n_clicks"),
    State("ticker-input",  "value"),
    State("timeframe",     "value"),
    prevent_initial_call=True,
)
def export_csv(n_clicks, ticker, tf):
    if not ticker:
        return None
    ticker = ticker.upper().strip()
    df, _  = fetch_data(ticker, tf == "weekly")
    if df is None:
        return None
    df     = apply_all(df)
    fname  = f"{ticker}_{'weekly' if tf == 'weekly' else 'daily'}.csv"
    return dcc.send_data_frame(df.to_csv, fname)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run_server(debug=False, host="0.0.0.0", port=port)
