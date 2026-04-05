# Dual Momentum + HY Spread Model Portfolio

A rules-based tactical asset allocation system. Two independent signals — equity momentum and credit stress — combine into a single monthly portfolio allocation. Fully mechanical, no discretion.

## How It Works

**Signal 1: GEM (Global Equity Momentum)**
- Compare 12-month total return: SPY vs EFA
- Winner must also beat T-bills (BIL) — absolute momentum filter
- Output: hold SPY, EFA, or SHY (defensive)

**Signal 2: HY Spread Regime**
- ICE BofA High-Yield OAS from FRED
- TIGHT (<350 bps), NORMAL (350-500), STRESSED (500-700), CRISIS (>=700)
- WIDENING_FAST override (>100 bps widening in 3 months) forces 100% Treasuries

These two signals feed a decision matrix that outputs target weights across SPY, EFA, SHY, and ANGL.

### Decision Matrix

|                    | GEM = SPY       | GEM = EFA       | GEM = SHY    |
|--------------------|-----------------|-----------------|--------------|
| TIGHT (<350)       | 100% SPY        | 100% EFA        | 100% SHY     |
| NORMAL (350-500)   | 100% SPY        | 100% EFA        | 100% SHY     |
| STRESSED (500-700) | 70/30 SPY/SHY   | 70/30 EFA/SHY   | 100% SHY     |
| CRISIS (>=700)     | 50/30/20 SPY/SHY/ANGL | 50/30/20 EFA/SHY/ANGL | 80/20 SHY/ANGL |
| WIDENING_FAST      | -> 100% SHY     | -> 100% SHY     | No change    |

## Quick Start

```bash
pip install -r requirements.txt
export FRED_API_KEY="your_key"  # Free at https://fred.stlouisfed.org/docs/api/api_key.html
```

### Run signals
```bash
python run_daily.py          # Check current signals
python run_monthly.py --force # Force a monthly rebalance
python run_weekly.py         # Send weekly summary email
```

### Launch dashboard
```bash
streamlit run dashboard.py
```

### Run backtest
```bash
python backtest.py           # All strategies from 1980-present
```

## Dashboard

Interactive Streamlit dashboard with six views:

| View | Description |
|------|-------------|
| **Current Signals** | Live GEM signal, HY regime, target allocation, plain English interpretation |
| **Signal History** | HY spread time series with regime shading, GEM signal timeline |
| **SPY + Regimes** | SPY price history colored by HY regime and GEM signal |
| **Backtest Performance** | Equity curves, drawdowns, annual returns, monthly heatmap, rolling Sharpe |
| **Allocation Over Time** | Stacked area chart of portfolio weights over backtest period |
| **Parameter Sensitivity** | Sweep GEM lookback (3-18M) and HY thresholds to test robustness |

## Notifications

Alerts via email, Slack, or SMS when signals change or rebalances occur. Configure via environment variables:

| Channel | Environment Variables |
|---------|----------------------|
| Email (SMTP) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` |
| Slack | `SLACK_WEBHOOK_URL` |
| SMS (Twilio) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `ALERT_SMS_TO` |

Unconfigured channels are silently skipped. Test with:
```bash
python notifications.py --test
```

## Automation

### GitHub Actions (cloud, runs even when your PC is off)
- **Daily** (Mon-Fri 6 PM ET) — signal check, emails on changes
- **Weekly** (Sunday 6 PM ET) — summary email with current signals and interpretation

Secrets are configured in repo settings. Both workflows have manual trigger buttons.

### Windows Task Scheduler (local)
- Weekly summary email via `run_weekly.bat`

## Project Structure

```
dual-momentum/
├── config.py              # All constants, thresholds, tickers, paths
├── data.py                # FRED + yfinance fetching with 12-hour cache
├── signals.py             # GEM signal + HY regime computation
├── portfolio.py           # Decision matrix -> target weights
├── backtest.py            # Historical simulation engine (5 strategies)
├── performance.py         # CAGR, Sharpe, drawdown, comparison tables
├── state.py               # Persistent CSV logging of signals/portfolio
├── notifications.py       # Email, Slack, SMS alert dispatcher
├── dashboard.py           # Streamlit dashboard (6 views)
├── run_daily.py           # Daily signal check + alerts
├── run_monthly.py         # Monthly rebalance + memo generation
├── run_weekly.py          # Weekly summary email
├── run_weekly.bat          # Windows Task Scheduler wrapper
├── requirements.txt
├── .github/workflows/     # GitHub Actions (daily + weekly)
├── data/
│   ├── signals_history.csv
│   ├── portfolio_history.csv
│   └── cache/
├── reports/               # Monthly markdown memos
└── logs/
```

## Backtest Details

- **Period:** 1980-present (extended via index proxies)
- **Pre-ETF substitutions:** ^GSPC for SPY (pre-1993), FRED DTB3 for BIL (pre-2007), FRED GS1 for SHY (pre-2002), JNK for ANGL (pre-2012)
- **Pre-EFA (pre-2001):** GEM runs absolute momentum only (SPY vs T-bills, no international comparison)
- **Pre-HY OAS (pre-1997):** Strategies using HY overlay fall back to GEM-only
- **Transaction costs:** 5 bps per position change
- **Rebalance:** Last business day of each month

### Strategies Compared

| Strategy | Description |
|----------|-------------|
| gem_hy | Full model: GEM + HY spread overlay |
| gem_pure | GEM only, no HY overlay |
| gem_floor | GEM + HY overlay with 70% minimum equity floor |
| sixty_forty | 60% SPY / 40% SHY, monthly rebalance |
| buy_hold | 100% SPY buy-and-hold |

## Dependencies

- pandas, numpy — data manipulation
- yfinance — ETF price data
- fredapi — FRED economic data (requires free API key)
- streamlit — dashboard
- plotly — interactive charts

## Known Limitations

1. **Concurrent stock-bond selloff (2022):** Both equities and SHY fall. Short duration limits damage (~4% vs ~13% for AGG) but doesn't eliminate it.
2. **12-month lookback lag:** GEM is slow to react. HY overlay partially compensates. Flash crashes within a month won't trigger either signal.
3. **WIDENING_FAST:** Small historical sample (2008, 2020). May whipsaw.
4. **SHY underperforms long bonds in rate cuts.** Explicit tradeoff for term premium protection.
5. **ANGL pre-2012:** Uses JNK proxy with different characteristics.
