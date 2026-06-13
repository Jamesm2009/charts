# Custom Charts

A self-hosted technical analysis charting tool for ETFs, stocks, and mutual funds. Built with Plotly Dash, deployed on Dokku (DigitalOcean), and cached via Upstash Redis.

---

## What it does

Enter any ticker, choose Daily (8 months) or Weekly (3 years), and get a multi-panel chart with a stats sidebar. All price data is pulled live from Yahoo Finance and cached so repeat loads are instant.

---

## Chart layout

The chart is split into fixed and optional panels stacked top to bottom.

**Price panel (always on)**

Candlestick price chart (or line chart for mutual funds) overlaid with:

- **Bridge Bands** — a regime-aware volatility envelope translated from Pine Script (calebsandfort). Starts with a WMA-based Bollinger Band, then interpolates it toward a linear regression price channel by `abs(Hurst × 2 − 1)`. When the Hurst exponent is near 0.5 (random walk), the bands stay at standard width. When the market is trending or mean-reverting strongly, the bands widen toward the raw price channel. The bands and their fill turn **green** when price is above the 63-period Donchian midpoint (bullish trend) and **red** when below.
- **Trend line** — 63-period Donchian channel midpoint, drawn as a solid green/red line matching the band color.
- **Trade line** — 15-period Donchian channel midpoint, drawn as small dots. Green when price is above, red when below.
- **Volume bars** — normalized to the bottom 20% of the price range, green on up days, red on down days.
- **Volume MA21** — 21-period average volume line overlaid on the volume bars.

**Realized Volatility panel (optional toggle)**

Annualized 21-day realized volatility, shown as a filled area chart.

**Matrix Series panel (always on)**

Translated from Pine Script v3 (wisestocktrader.com). Takes a weighted close `(H + L + C×2) / 4`, Z-scores it against its own EMA and standard deviation, then triple-smooths into two lines (`up` and `down`). Bars are drawn spanning `min(up, down)` to `max(up, down)`:

- **Green** when `up > down` (momentum accelerating upward)
- **Red** when `up ≤ down` (momentum decelerating)

Also plots:
- **Dynamic S/R lines** — CCI-16 based resistance (green) and support (red) levels derived from the 50-bar highest/lowest CCI range.
- **OB/OS markers** — teal `+` crosses appear above bars when `up > 200` (overbought) or below when `down < −200` (oversold).

**Momentum panel (always on)**

Bar chart of `(5-day MA / 63-day MA − 1) × 100`. Shows short-term momentum relative to the medium-term trend. Green bars above zero, red below.

**Stochastic RSI panel (optional toggle)**

14-period Stochastic RSI with 3-period smoothing on both %K and %D lines. Overbought line at 80, oversold at 20.

---

## Stats sidebar

Shown to the right of the chart on desktop, collapsible on mobile. Displays:

| Field | What it is |
|---|---|
| Trade (15) | Price vs 15-period Donchian mid — Bullish / Neutral / Bearish |
| Trend (63) | Price vs 63-period Donchian mid — Bullish / Neutral / Bearish |
| Opinion | Both signals agree → Bullish or Bearish; otherwise Neutral |
| 12M High / Low | Rolling 12-month price range |
| vs 12M Low | How far current price is above the 12-month low |
| Bridge Bands Outlook | BULL / BEAR based on close vs 63-period Donchian mid |
| BB Upside / Downside | % distance from current price to upper / lower band |
| Hurst | Current Hurst exponent proxy (>0.5 trending, <0.5 mean-reverting) |
| vs EMA 200 | Price vs 200-period EMA, as a percentage |
| Performance | 1D / 1W / 1M / 3M / 6M price returns |
| RV 1M | Average annualized realized volatility over the past 21 days |
| 12M Z-Score | Standard deviations from the 12-month mean price |

An OVERBOUGHT or OVERSOLD pill appears when the Z-Score exceeds ±2.0.

---

## Toggle controls

| Button | What it shows / hides |
|---|---|
| Realized Vol | Annualized realized volatility panel |
| Stoch RSI | Stochastic RSI panel |

---

## Hover tooltip

On the price panel, hovering shows: Price (OHLC), Volume (actual share count), BB Top, BB Bottom, BB Mid. All other traces on the price panel (Trend line, Trade dots, volume MA) are suppressed from the tooltip to keep it clean. The Matrix panel is fully suppressed from hover.

---

## Indicator parameters

| Indicator | Key parameters |
|---|---|
| Bridge Bands | Length 15, Trend length 63 |
| Trade line | 15-period Donchian mid |
| Trend line | 63-period Donchian mid |
| ATR (for Hurst) | 15-period Wilder RMA |
| Momentum bars | 5-day MA vs 63-day MA |
| Realized vol | 21-day log-return std × √252 |
| Stochastic RSI | RSI 14, Stoch 14, Smooth K×3, Smooth D×3 |
| Matrix Series | Smoother 5 (triple EMA), OB/OS threshold ±200 |
| Matrix S/R | CCI 16, lookback 50 |
| EMA 200 | 200-period EMA (stats sidebar only) |
| Z-Score | 12-month (252-day) rolling mean and std |

---

## Caching

Data is cached at two levels to keep loads fast.

**Local disk** (`/cache/` directory) — fastest, cleared on every server restart or redeploy.

**Upstash Redis** — survives restarts. If local cache is missing, data is fetched from Redis and re-saved to disk.

| Data type | Cache TTL |
|---|---|
| Daily OHLCV | 1 hour |
| Weekly OHLCV | 6 hours |

On a first load of an uncached ticker, Yahoo Finance is queried (~1–2 seconds). Subsequent loads of the same ticker are near-instant from disk. To force a refresh, either wait for the TTL to expire or clear the key from the Upstash dashboard.

> **Note:** The Bridge Bands calculation includes a nested Python loop over the lookback window that runs on every `apply_all()` call. For uncached tickers this is the slowest step. If load times are a concern for frequently-viewed tickers, they will be fast on repeat loads from cache.

---

## Files

```
app.py              Main application — all indicators, chart, layout, callbacks
requirements.txt    Python dependencies
Procfile            gunicorn startup command for Dokku
runtime.txt         Python version (3.11.9)
```

---

## Dependencies

```
dash >= 2.14
plotly >= 5.18
yfinance >= 0.2.36
pandas >= 2.0
numpy >= 1.26
requests >= 2.31
gunicorn >= 21.0
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `UPSTASH_REDIS_REST_URL` | Yes | REST URL from Upstash dashboard |
| `UPSTASH_REDIS_REST_TOKEN` | Yes | Bearer token from Upstash dashboard |
| `PORT` | No | Set automatically by Dokku — do not set manually |

---

## Deployment (Dokku on DigitalOcean)

**First time — run on the server:**

```bash
dokku apps:create aetd-charts

dokku config:set aetd-charts \
  UPSTASH_REDIS_REST_URL=https://YOUR-URL.upstash.io \
  UPSTASH_REDIS_REST_TOKEN=YOUR-TOKEN

dokku domains:add aetd-charts charts.market-dashboards.com

dokku letsencrypt:set aetd-charts email your@email.com
dokku letsencrypt:enable aetd-charts
```

**First deploy — run on your local machine:**

```bash
git init
git add .
git commit -m "Initial commit"
git remote add dokku dokku@YOUR_DROPLET_IP:aetd-charts
git push dokku main
```

Dokku detects Python via `runtime.txt`, installs dependencies from `requirements.txt`, and starts the server using the `Procfile`. Deployment takes 2–3 minutes. Visit `https://charts.market-dashboards.com` when done.

**Subsequent deploys:**

```bash
git add app.py
git commit -m "Describe your change"
git push dokku main
```

Zero-downtime redeploy. The local disk cache is cleared on each redeploy, but Redis retains data.

**Checking logs:**

```bash
dokku logs aetd-charts --tail 50
```

Common issues:
- `No module named X` — add to `requirements.txt` and redeploy
- App not responding — `dokku ps:restart aetd-charts`
- Redis errors — `dokku config aetd-charts` to verify env vars

---

## Indicator credits

- **Bridge Bands** — Pine Script by calebsandfort ([TradingView](https://www.tradingview.com/v/IhUChSph/))
- **Matrix Series** — Pine Script by wisestocktrader.com ([TradingView](http://www.wisestocktrader.com/indicators/2739-flower-indicator))
