# Dual Momentum + HY Spread Model Portfolio
## Claude Code Build Instructions

This is a rules-based tactical asset allocation model. Two signals, monthly rebalance, fully mechanical.

## Quick Start

```bash
cd dual-momentum
pip install -r requirements.txt
export FRED_API_KEY="your_key_here"    # Get free at https://fred.stlouisfed.org/docs/api/fred/
python run_daily.py                     # Test current signals
python run_monthly.py --force           # Force a monthly rebalance run
python backtest.py                      # Run full backtest suite
```

---

## System Architecture

### Two Signals → One Portfolio

**Signal 1: GEM (Global Equity Momentum)**
- Compare 12-month total return: SPY vs EFA
- Winner must also beat BIL (T-bill proxy) → absolute momentum filter
- Output: `SPY`, `EFA`, or `SHY`

**Signal 2: HY Spread Regime**
- ICE BofA HY OAS (FRED: BAMLH0A0HYM2)
- Regime: TIGHT (<350 bps), NORMAL (350-500), STRESSED (500-700), CRISIS (≥700)
- Rate of change: 3-month delta. WIDENING_FAST (>100 bps) overrides to SHY.

### Decision Matrix

```
┌──────────────────┬──────────────┬──────────────┬──────────────┐
│                  │ GEM = SPY    │ GEM = EFA    │ GEM = SHY    │
├──────────────────┼──────────────┼──────────────┼──────────────┤
│ TIGHT (<350)     │ 100% SPY     │ 100% EFA     │ 100% SHY     │
│ NORMAL (350-500) │ 100% SPY     │ 100% EFA     │ 100% SHY     │
│ STRESSED (500-700)│ 70/30 SPY/SHY│ 70/30 EFA/SHY│ 100% SHY    │
│ CRISIS (≥700)    │ 50/30/20     │ 50/30/20     │ 80/20        │
│                  │ SPY/SHY/ANGL │ EFA/SHY/ANGL │ SHY/ANGL     │
│ WIDENING_FAST    │ → 100% SHY   │ → 100% SHY   │ No change    │
└──────────────────┴──────────────┴──────────────┴──────────────┘
```

### ETF Universe

| ETF  | Role                  | Duration |
|------|-----------------------|----------|
| SPY  | US equity             | N/A      |
| EFA  | Intl developed equity | N/A      |
| SHY  | Defensive bond (1-3Y) | ~1.9 yr  |
| BIL  | T-bill proxy (hurdle) | ~0.1 yr  |
| ANGL | Crisis credit (fallen angels) | ~5.5 yr |

---

## File Structure

```
dual-momentum/
├── config.py          # All constants, thresholds, tickers, paths
├── data.py            # FRED + yfinance fetching with cache
├── signals.py         # GEM signal + HY regime computation
├── portfolio.py       # Decision matrix → target weights
├── backtest.py        # Historical simulation engine
├── performance.py     # CAGR, Sharpe, drawdown, comparison tables
├── state.py           # Persistent signal/portfolio logging
├── run_daily.py       # Daily: fetch, compute, alert
├── run_monthly.py     # Monthly: full rebalance + memo
├── requirements.txt
├── data/
│   ├── signals_history.csv
│   ├── portfolio_history.csv
│   └── cache/
├── reports/
│   └── YYYY-MM_memo.md
└── logs/
```

---

## Build Sequence for Claude Code

### Phase 1: Validate Data Pipeline

```
Run `python data.py` and verify:
1. FRED HY OAS data fetches correctly (expect ~6000+ daily observations since 1996)
2. All 5 ETF price series download (SPY, EFA, SHY, BIL, ANGL)
3. Cache files are created in data/cache/
4. Trailing return computation works: check SPY 12M return is reasonable
5. T-bill return computation works for dates before BIL inception (2007)

Fix any errors before proceeding.
```

### Phase 2: Validate Signals

```
Run `python signals.py` and verify:
1. GEM signal output contains all expected fields
2. HY regime classification matches current spread level
   - Current spread (March 2026) should be in TIGHT or NORMAL range
3. Yield curve status populates correctly
4. No NaN values in signal output for recent dates

Test edge case: compute signals for 2008-10-15 (should be CRISIS regime, GEM = SHY)
Test edge case: compute signals for 2021-06-30 (should be TIGHT regime, GEM = SPY)
```

### Phase 3: Validate Portfolio Constructor

```
Run `python portfolio.py` and verify:
1. All 15 matrix cells produce correct weights
2. Weights sum to exactly 1.0 for every cell
3. WIDENING_FAST override correctly forces SHY when GEM = equity
4. WIDENING_FAST does NOT override when GEM = SHY (already defensive)

Run the trade computation test and verify output.
```

### Phase 4: Run Backtest

```
Run `python backtest.py` and verify:
1. GEM Pure backtest 2003-present: expect 6-10% CAGR, max DD around -20% to -25%
2. GEM+HY backtest: expect similar or slightly lower CAGR, reduced max DD
3. 60/40 control: expect 5-7% CAGR
4. SPY buy-hold: expect 9-11% CAGR, max DD -50%+

Key validation dates:
- 2008 Q4: HY overlay should move to defensive BEFORE pure GEM does
- Mar 2020: WIDENING_FAST should fire (spreads went from ~350 to 1100 in weeks)
- 2022: Both strategies should be in SHY (but SHY lost ~4%, not ~13% like AGG)

If GEM Pure CAGR is outside 5-12% range, debug the signal computation.
The HY overlay's main value-add is drawdown reduction, not return enhancement.
```

### Phase 5: Performance Report

```
After backtest validates, run comparison table:

from backtest import run_all_strategies
from performance import comparison_table

results = run_all_strategies()
print(comparison_table(results))

This should produce a side-by-side table of all four strategies.
Key question: Does GEM+HY have a better Sharpe than GEM Pure?
Key question: Is max drawdown materially lower with the HY overlay?
```

### Phase 6: Live System

```
1. Run `python run_daily.py` — verify clean output with current signals
2. Run `python run_monthly.py --force` — verify memo generation
3. Check that data/signals_history.csv and data/portfolio_history.csv are populated
4. Verify the markdown memo in reports/ is well-formatted

Set up cron:
  # Daily at 6 PM CT (weekdays only)
  0 23 * * 1-5 cd ~/dual-momentum && python run_daily.py >> logs/daily.log 2>&1

  # Monthly: run every weekday, script checks if last business day
  0 0 * * 1-5 cd ~/dual-momentum && python run_monthly.py >> logs/monthly.log 2>&1
```

---

## Backtest Substitutions

Before certain ETF inception dates, the backtest uses proxy data:

| ETF  | Inception   | Pre-inception substitute |
|------|-------------|--------------------------|
| BIL  | 2007-05-25  | FRED DTB3 (3-month T-bill rate) |
| ANGL | 2012-04-10  | JNK (SPDR HY bond ETF) |
| SHY  | 2002-07-22  | FRED GS1 (1-year Treasury rate) |

These are implemented in `data.py` and referenced in `config.py`.

---

## Historical Regime Map (Backtest Validation)

Use this to sanity-check backtest output. If the model classifies these periods differently, investigate.

| Period      | HY Spread  | Expected Regime | GEM Signal | Expected Portfolio      |
|-------------|-----------|-----------------|------------|------------------------|
| 2003-2004   | 400→300   | NORMAL→TIGHT    | SPY        | 100% SPY               |
| 2005-2006   | 300-350   | TIGHT           | SPY        | 100% SPY               |
| 2007 Q4     | 400→500   | NORMAL→STRESSED | SPY fading | 70% SPY / 30% SHY     |
| 2008 H1     | 500→700   | STRESSED        | GEM→SHY    | 100% SHY               |
| 2008 Q4     | 700→2000  | CRISIS          | SHY        | 80% SHY / 20% ANGL    |
| 2009 H1     | 2000→700  | CRISIS→STRESSED | SHY→equity | Transition             |
| Mar 2020    | 350→1100  | TIGHT→CRISIS    | SHY (delayed)| WIDENING_FAST fires  |
| 2021        | 300-350   | TIGHT           | SPY        | 100% SPY               |
| 2022        | 350→500   | NORMAL→STRESSED | SHY        | 100% SHY               |
| 2023-2024   | 300-400   | TIGHT/NORMAL    | SPY        | 100% SPY               |

---

## Known Limitations and Failure Modes

1. **Concurrent stock-bond selloff (2022):** Both equities and SHY fall. SHY's short duration limits the damage (~4% loss vs ~13% for AGG) but does not eliminate it. This is a known failure mode of all momentum systems.

2. **12-month lookback lag:** GEM is slow to react. The HY overlay partially compensates. A fast crash that resolves within a month (e.g., Aug 2015 flash crash) will not trigger either signal.

3. **WIDENING_FAST override is speculative.** It has a very small sample size (would fire in 2008, 2020, possibly 2011). Backtest with and without to assess whether it adds value or just whipsaw.

4. **SHY underperforms long bonds in rate-cutting cycles.** This is the explicit tradeoff for deficit/term-premium protection. The backtest will show lower returns during 2008-2009 and 2019-2020 recovery phases vs. an AGG-based model.

5. **ANGL inception 2012.** Pre-2012 crisis credit allocation uses JNK, which has different characteristics (lower quality, more defaults). Results before 2012 for the crisis credit sleeve are approximate.

---

## Extensions (post-validation only)

Do NOT implement these until the base model is running and validated.

| Extension              | What                                          | Complexity |
|------------------------|-----------------------------------------------|------------|
| Multi-lookback GEM     | Average 6/9/12-month signals                 | Medium     |
| Volatility scaling     | Reduce equity when VIX > 30                  | Low        |
| Real yield overlay     | 10Y TIPS yield > 2.5% → shift to TIP         | Low        |
| Sector rotation        | Replace broad equity with top-4 sectors       | High       |
| Term premium monitor   | NY Fed ACM model, log for regime awareness    | Low        |
