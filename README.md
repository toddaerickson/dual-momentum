# Three-Stage TAA Model Portfolio

A rules-based tactical asset allocation system with three independent stages: absolute momentum filter, cross-asset momentum ranking, and credit stress regime overlay. Fully mechanical, no discretion, no forecasting.

## What It Does

Three stages run every month. Their combination determines how to allocate across nine ETFs.

**Stage 1 -- Absolute Momentum Gate (Crash Avoidance)**

Checks whether the best equity market (SPY or EFA) beats T-bills (BIL) over the trailing 12 months. If neither clears the hurdle, the model moves entirely to short-term Treasuries. This is a binary gate: pass or fail.

| Condition | Output |
|-----------|--------|
| Best equity return > BIL return | PASS -- proceed to Stage 2 |
| Neither beats BIL | FAIL -- 100% SHY, stop |

**Stage 2 -- Cross-Asset Momentum Ranking (Asset Selection)**

Ranks 6 risky assets (SPY, EFA, EEM, VNQ, DBC, GLD) by vol-adjusted 12-1 month momentum. Skips the most recent month to avoid short-term reversal (Jegadeesh & Titman, 1993). Normalizes to 10% target vol before ranking (Blitz & van Vliet, 2008).

Assets are assigned to terciles: top 2 get 30% each, middle 2 get 20% each, bottom 2 get 0%. Hysteresis prevents rebalancing when rank changes by only 1 position (~30-40% turnover reduction).

ANGL is excluded from ranking -- it only appears as a structural crisis allocation in Stage 3.

**Stage 3 -- Dual-Signal Credit Regime Classifier (Risk Budget Sizing)**

Uses two ICE BofA credit spread series from FRED to classify the credit environment:

| Signal | FRED Series | Role |
|--------|-------------|------|
| CCC-BB spread | `BAMLH0A3HYC` minus `BAMLH0A1HYBB` | Primary -- risk appetite measure |
| Single-B OAS | `BAMLH0A2HYB` | Secondary -- absolute stress confirmation |

The CCC-BB spread captures how much extra compensation investors demand for the lowest-quality credits relative to BB. It is a cleaner risk-appetite measure than the composite HY index OAS, which drifts with index composition over time.

**Why percentile ranks instead of fixed basis-point thresholds:** The absolute level of the CCC-BB spread shifts over decades as the index composition changes. Percentile ranks on an expanding window adapt automatically -- a spread at the 80th percentile of its own history means the same thing regardless of whether that maps to 500 bps in 2005 or 600 bps in 2025.

| CCC-BB Percentile | Regime | Meaning |
|-------------------|--------|---------|
| < 25th | TIGHT | Strong risk appetite, green light |
| 25th -- 60th | NORMAL | Typical conditions |
| 60th -- 85th | STRESSED | Elevated caution, model reduces equity |
| >= 85th | CRISIS | Extreme stress, heavily defensive |

The Single-B OAS acts as a confirmation signal. When it is also elevated (>= 75th percentile), it **escalates** the primary regime by one step. It never de-escalates. This prevents the model from dismissing stress that shows up in absolute spread levels even when the CCC-BB differential hasn't moved much.

**WIDENING_FAST override:** If Single-B OAS widens by more than 100 bps in 3 months, the model forces 100% Treasuries regardless of the regime classification. This is keyed off Single-B rather than the composite for the same composition-stability reasons.

## Three-Stage Pipeline

| Stage | Signal | Output |
|-------|--------|--------|
| **Stage 1** | Absolute momentum (best equity 12M vs BIL) | PASS (proceed) or FAIL (100% SHY) |
| **Stage 2** | 6-asset momentum ranking (vol-adjusted 12-1M) | Risky asset weights (sums to 1.0) |
| **Stage 3** | HY regime (CCC-BB percentile + Single-B confirmation) | Risk budget sizing |

### Risk Budget by Regime

| HY Regime | Risky Budget | SHY | ANGL | Effect |
|-----------|-------------|-----|------|--------|
| **TIGHT** | 100% | 0% | 0% | Full allocation to momentum-ranked assets |
| **NORMAL** | 100% | 0% | 0% | Same as TIGHT |
| **STRESSED** | 70% | 30% | 0% | Reduce risky, add Treasuries |
| **CRISIS** | 50% | 30% | 20% | Heavily defensive + fallen angel recovery |
| **WIDENING_FAST** | 0% | 100% | 0% | Override: 100% SHY regardless |

## ETF Universe

| ETF  | Role                        | Stage |
|------|-----------------------------|-------|
| SPY  | US equity (S&P 500)         | Ranked (Stage 2) |
| EFA  | International developed     | Ranked (Stage 2) |
| EEM  | Emerging markets            | Ranked (Stage 2) |
| VNQ  | Real estate (REITs)         | Ranked (Stage 2) |
| DBC  | Broad commodities           | Ranked (Stage 2) |
| GLD  | Gold                        | Ranked (Stage 2) |
| SHY  | Defensive bond (1-3Y Treas) | Structural (Stage 3) |
| BIL  | T-bill proxy (hurdle only)  | Stage 1 hurdle |
| ANGL | Fallen angel HY bonds       | Structural crisis only (Stage 3) |

## Quick Start

```bash
pip install -r requirements.txt
export FRED_API_KEY="your_key"   # Free at https://fred.stlouisfed.org/docs/api/api_key.html
```

### Run signals
```bash
python run_daily.py              # Check current signals
python run_monthly.py --force    # Force a monthly rebalance
python run_weekly.py             # Send weekly summary email
```

### Launch dashboard
```bash
streamlit run dashboard.py
```

The dashboard will prompt for your FRED API key in the sidebar if the environment variable is not set. It also reads from `.streamlit/secrets.toml` if present.

### Run backtest
```bash
python backtest.py               # All strategies from 1980-present
```

## Dashboard

Interactive Streamlit dashboard with six views:

| View | What You See |
|------|-------------|
| **Current Signals** | Three-stage signals: absolute momentum gate, momentum ranking table (terciles, scores, weights), HY regime, target allocation, plain English interpretation |
| **Signal History** | Composite HY OAS time series with regime bands |
| **SPY + Regimes** | SPY price history colored by credit regime |
| **Backtest Performance** | Equity curves, drawdowns, annual returns, monthly heatmap, rolling Sharpe |
| **Allocation Over Time** | Stacked area chart of portfolio weights over the backtest period |
| **Parameter Sensitivity** | Sweep CCC-BB percentile thresholds to test robustness |

## Notifications

Alerts fire when signals change or monthly rebalances occur. Three channels, configured via environment variables:

| Channel | Variables |
|---------|-----------|
| Email (SMTP) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` |
| Slack | `SLACK_WEBHOOK_URL` |
| SMS (Twilio) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `ALERT_SMS_TO` |

Unconfigured channels are silently skipped. Test with `python notifications.py --test`.

## Automation

### GitHub Actions

Both workflows already reference `secrets.FRED_API_KEY` and run automatically:

| Workflow | Schedule | What It Does |
|----------|----------|-------------|
| **Daily** | Mon-Fri 6 PM ET | Signal check, emails on changes |
| **Weekly** | Sunday 6 PM ET | Summary email with current signals and interpretation |

Add `FRED_API_KEY` as a repository secret in **Settings > Secrets and variables > Actions** to enable them. Both have manual trigger buttons for on-demand runs.

### Local (cron / Task Scheduler)

```bash
# Daily at 6 PM CT (weekdays only)
0 23 * * 1-5 cd ~/dual-momentum && python run_daily.py >> logs/daily.log 2>&1

# Monthly: runs every weekday, script checks if last business day
0 0 * * 1-5 cd ~/dual-momentum && python run_monthly.py >> logs/monthly.log 2>&1
```

Windows users can use `run_weekly.bat` with Task Scheduler.

## Project Structure

```
dual-momentum/
├── config.py              # All constants, thresholds, FRED series, paths
├── data.py                # FRED + yfinance fetching with 12-hour cache
├── signals.py             # Three-stage signal computation (abs momentum + momentum ranking + HY)
├── momentum_rank.py       # Cross-asset 12-1 month momentum ranking module
├── portfolio.py           # Portfolio constructor (delegates to portfolio_v2)
├── portfolio_v2.py        # Three-stage portfolio constructor
├── backtest.py            # Historical simulation engine (3 strategies)
├── performance.py         # CAGR, Sharpe, drawdown, comparison tables
├── state.py               # Persistent CSV logging of signals and portfolio
├── notifications.py       # Email, Slack, SMS alert dispatcher
├── dashboard.py           # Streamlit dashboard (6 views)
├── run_daily.py           # Daily signal check + alerts
├── run_monthly.py         # Monthly rebalance + memo generation
├── run_weekly.py          # Weekly summary email
├── test_momentum.py       # Test harness for momentum ranking + portfolio_v2
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
- **Pre-EFA (pre-2001):** Absolute momentum uses SPY only (no EFA comparison)
- **Pre-HY OAS (pre-1997):** Falls back to absolute momentum + ranking only (no HY overlay)
- **Transaction costs:** 5 bps per position change
- **Rebalance:** Last business day of each month

### Strategies Compared

| Strategy | Description |
|----------|-------------|
| `momentum_hy` | Full model: three-stage (abs momentum gate + momentum ranking + HY regime) |
| `sixty_forty` | 60% SPY / 40% SHY, monthly rebalance (control) |
| `buy_hold` | 100% SPY buy-and-hold (control) |

## Configuration Reference

All parameters live in `config.py`. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GEM_LOOKBACK_MONTHS` | 12 | Trailing return window for momentum |
| `HY_CCC_BB_PERCENTILE_THRESHOLDS` | 25/60/85 | CCC-BB percentile cutoffs for TIGHT/NORMAL/STRESSED |
| `HY_B_PERCENTILE_THRESHOLDS` | 75/90 | Single-B percentile for escalation/crisis confirmation |
| `HY_ROC_THRESHOLDS["WIDENING_FAST"]` | 100 bps | Single-B OAS 3-month change override trigger |
| `HY_ROC_LOOKBACK_MONTHS` | 3 | Rate-of-change lookback window |
| `TRANSACTION_COST_BPS` | 5 | Cost per position change in backtest |
| `CACHE_EXPIRY_HOURS` | 12 | How long FRED/yfinance data is cached |

## Dependencies

- **pandas**, **numpy** -- data manipulation
- **yfinance** -- ETF price data (free)
- **fredapi** -- FRED economic data (requires free API key)
- **streamlit** -- interactive dashboard
- **plotly** -- charts

## Known Limitations

1. **Concurrent stock-bond selloff (2022).** Both equities and SHY fall. SHY's short duration limits the damage (~4% loss vs ~13% for AGG) but does not eliminate it.
2. **12-month lookback lag.** The absolute momentum gate is slow to react. The HY overlay partially compensates. A flash crash that resolves within a month won't trigger either signal.
3. **WIDENING_FAST is speculative.** Small historical sample (2008, 2020, possibly 2011). May whipsaw. The Parameter Sensitivity view lets you backtest with and without.
4. **SHY underperforms long bonds in rate cuts.** Explicit tradeoff for term-premium protection.
5. **ANGL pre-2012.** Uses JNK proxy with different credit quality characteristics.
6. **CCC-BB spread percentile requires history.** In the early expanding-window period (first few years of data), percentile ranks are noisy. The legacy fixed-bps classifier acts as fallback for pre-1997 periods.
