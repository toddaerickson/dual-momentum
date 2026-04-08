"""
Streamlit Dashboard: Three-Stage TAA Model Portfolio

Run:
  streamlit run dashboard.py
"""

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import config
import data as data_mod
import signals as signals_mod
import portfolio as portfolio_mod
import portfolio_v2
import momentum_rank
import backtest
import performance

# ──────────────────────────────────────────────
# Page Config
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="Three-Stage TAA Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("Three-Stage TAA Model Portfolio")


# ──────────────────────────────────────────────
# Data Loading (cached)
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_data():
    """Fetch all market data (cached 1 hour)."""
    return data_mod.fetch_all()


@st.cache_data(ttl=3600)
def load_backtest_results():
    """Run all backtests (cached 1 hour)."""
    return backtest.run_all_strategies()


def load_signal_history():
    """Load signal history CSV."""
    if not config.SIGNALS_HISTORY_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(config.SIGNALS_HISTORY_FILE, parse_dates=["date"])
    return df


def load_portfolio_history():
    """Load portfolio history CSV."""
    if not config.PORTFOLIO_HISTORY_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(config.PORTFOLIO_HISTORY_FILE, parse_dates=["date"])
    return df


# ──────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────
st.sidebar.header("Controls")

# FRED API Key input — checks env var, Streamlit secrets, then sidebar input
if not config.FRED_API_KEY:
    # Try Streamlit secrets (set via .streamlit/secrets.toml or Streamlit Cloud)
    _secrets_key = st.secrets.get("FRED_API_KEY", "") if hasattr(st, "secrets") else ""
    if _secrets_key:
        config.FRED_API_KEY = _secrets_key
        import os
        os.environ["FRED_API_KEY"] = _secrets_key

if not config.FRED_API_KEY:
    st.sidebar.warning("FRED API key not set")
    _input_key = st.sidebar.text_input(
        "FRED API Key",
        type="password",
        help="Get a free key at https://fred.stlouisfed.org/docs/api/fred/",
    )
    if _input_key:
        config.FRED_API_KEY = _input_key
        import os
        os.environ["FRED_API_KEY"] = _input_key
        st.cache_data.clear()
        st.rerun()
    else:
        st.sidebar.info("Enter your FRED API key to load data.")
        st.stop()

if st.sidebar.button("Refresh Data", type="primary"):
    st.cache_data.clear()
    st.rerun()

view = st.sidebar.radio(
    "View",
    ["Current Signals", "Signal History", "SPY + Regimes", "Backtest Performance", "Allocation Over Time", "Parameter Sensitivity"],
)


# ──────────────────────────────────────────────
# 1. Current Signals View
# ──────────────────────────────────────────────

if view == "Current Signals":
    try:
        all_data = load_data()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
        ccc_bb_spread = all_data["ccc_bb_spread"]
        hy_b_spread = all_data["hy_b_spread"]
    except Exception as e:
        st.error(f"Data fetch failed: {e}")
        st.info("Ensure FRED_API_KEY is set in your environment.")
        st.stop()

    as_of = prices.index[-1]
    all_signals = signals_mod.compute_all_signals(
        prices, hy_spread, as_of,
        ccc_bb_spread=ccc_bb_spread, hy_b_spread=hy_b_spread,
    )
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    mom = all_signals["momentum"]
    yc = all_signals["yield_curve"]
    target_full = portfolio_mod.construct_portfolio(gem, hy, mom)
    target = {k: v for k, v in target_full.items() if k != "_metadata" and isinstance(v, (int, float))}

    st.subheader(f"Signals as of {as_of.strftime('%Y-%m-%d')}")

    # Key metrics row
    col1, col2, col3, col4 = st.columns(4)

    abs_pass = gem.get("absolute_pass", False)
    col1.metric("Abs Momentum", "PASS" if abs_pass else "FAIL")
    col2.metric(
        "HY Regime",
        hy.get("regime", "N/A"),
        f"CCC-BB pctl: {hy.get('ccc_bb_percentile', 0):.0f}",
    )
    col3.metric(
        "Single-B 3M Δ",
        f"{hy.get('hy_b_change_3m', 0) or 0:+.0f} bps",
        hy.get("rate_of_change", ""),
    )
    override = hy.get("fast_widen_override", False)
    col4.metric("Override", "ACTIVE" if override else "Inactive")

    st.divider()

    # ── Plain English Summary ──
    regime = hy.get("regime", "N/A")
    regime_primary = hy.get("regime_primary", "N/A")
    ccc_bb = hy.get("ccc_bb_spread_current", 0) or 0
    ccc_bb_pctl = hy.get("ccc_bb_percentile", 0) or 0
    b_oas = hy.get("hy_b_current", 0) or 0
    b_pctl = hy.get("hy_b_percentile", 0) or 0
    b_change = hy.get("hy_b_change_3m", 0) or 0
    spread = hy.get("hy_spread_current", 0) or 0
    override = hy.get("fast_widen_override", False)
    spy_ret = gem.get("spy_12m_return", 0)
    efa_ret = gem.get("efa_12m_return", 0)
    bil_ret = gem.get("bil_12m_return", 0)
    abs_pass = gem.get("absolute_pass", False)

    # Absolute momentum explanation
    if abs_pass:
        gem_english = (
            f"US stocks (SPY) returned **{spy_ret:+.1%}** and international stocks (EFA) "
            f"returned **{efa_ret:+.1%}** over the past 12 months, "
            f"with the best clearing the T-bill hurdle of **{bil_ret:+.1%}**. "
            "Absolute momentum passes — equities are favored."
        )
    else:
        gem_english = (
            f"Neither US stocks (**{spy_ret:+.1%}**) nor international stocks (**{efa_ret:+.1%}**) "
            f"beat T-bills (**{bil_ret:+.1%}**) over the past 12 months. "
            "Absolute momentum fails — step aside into short-term Treasuries until equities recover."
        )

    # Regime explanation (dual-signal)
    regime_descriptions = {
        "TIGHT": (
            f"CCC-BB spread is **{ccc_bb:.0f} bps** ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at **{b_oas:.0f} bps** ({b_pctl:.0f}th pctl). "
            "Risk appetite is healthy. Green light for risk assets."
        ),
        "NORMAL": (
            f"CCC-BB spread is **{ccc_bb:.0f} bps** ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at **{b_oas:.0f} bps** ({b_pctl:.0f}th pctl). "
            "Normal risk pricing. No reason to reduce exposure."
        ),
        "STRESSED": (
            f"CCC-BB spread is **{ccc_bb:.0f} bps** ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at **{b_oas:.0f} bps** ({b_pctl:.0f}th pctl). "
            "Elevated stress — the model reduces equity and adds Treasuries."
        ),
        "CRISIS": (
            f"CCC-BB spread is **{ccc_bb:.0f} bps** ({ccc_bb_pctl:.0f}th percentile), "
            f"Single-B OAS at **{b_oas:.0f} bps** ({b_pctl:.0f}th pctl). "
            "Crisis-level stress. The model goes heavily defensive with ANGL "
            "to capture eventual credit recovery."
        ),
    }
    regime_english = regime_descriptions.get(regime, "")

    # Rate of change (keyed off Single-B OAS)
    if override:
        roc_english = (
            f"Single-B OAS widened **{b_change:+.0f} bps in 3 months**, triggering the "
            "WIDENING_FAST override (threshold: +100 bps). The model forces 100% Treasuries."
        )
    elif b_change > 100:
        roc_english = f"Single-B OAS widened **{b_change:+.0f} bps over 3 months**. Override threshold is +100 bps."
    elif b_change > 50:
        roc_english = f"Single-B OAS widened **{b_change:+.0f} bps over 3 months** (WIDENING). Override triggers at +100 bps."
    elif b_change > 0:
        roc_english = f"Single-B OAS widened **{b_change:+.0f} bps over 3 months**. Override triggers at +100 bps."
    elif b_change > -50:
        roc_english = f"Single-B OAS moved **{b_change:+.0f} bps over 3 months**."
    else:
        roc_english = f"Single-B OAS tightened **{b_change:+.0f} bps over 3 months** (TIGHTENING)."

    # Conviction assessment
    margin = abs(spy_ret - efa_ret)
    abs_margin = max(spy_ret, efa_ret) - bil_ret

    conviction_parts = []
    if abs_pass:
        if margin > 0.10:
            conviction_parts.append("The relative momentum gap is wide — high conviction in the equity pick.")
        elif margin > 0.03:
            conviction_parts.append("Moderate gap between US and international — reasonably clear winner.")
        else:
            conviction_parts.append("US and international returns are very close — the winner could flip next month.")

        if abs_margin > 0.10:
            conviction_parts.append("Equities are well ahead of T-bills — strong case to stay invested.")
        elif abs_margin > 0.03:
            conviction_parts.append("Equities are ahead of T-bills but the margin is moderate.")
        else:
            conviction_parts.append("Equities barely beat T-bills — a small move could push the model to defensive.")
    else:
        if abs_margin < -0.05:
            conviction_parts.append("Equities are significantly underperforming T-bills — clear risk-off signal.")
        else:
            conviction_parts.append("Equities just barely missed the T-bill hurdle — could flip back next month.")

    if regime in ("TIGHT", "NORMAL") and not override:
        conviction_parts.append("Credit markets confirm no stress — the environment supports the momentum signal.")
    elif regime == "STRESSED":
        conviction_parts.append("Credit stress adds caution — the model hedges even if momentum is positive.")

    conviction_english = " ".join(conviction_parts)

    # Target portfolio in plain english
    alloc_parts = []
    for ticker in portfolio_v2.ALL_TICKERS:
        w = target.get(ticker, 0)
        if w > 0.001:
            alloc_parts.append(f"**{w:.0%} {ticker}**")
    alloc_str = " / ".join(alloc_parts)

    ticker_names = {
        "SPY": "US stocks", "EFA": "international stocks", "EEM": "emerging markets",
        "VNQ": "REITs", "DBC": "commodities", "GLD": "gold",
        "SHY": "short-term Treasuries", "ANGL": "fallen angel bonds",
    }
    alloc_plain = ", ".join(
        f"{target[t]:.0%} in {ticker_names.get(t, t)}"
        for t in portfolio_v2.ALL_TICKERS if target.get(t, 0) > 0.001
    )

    # Render the summary
    st.subheader("What This Means")

    st.markdown(f"""
**Stage 1 — Absolute Momentum:** {gem_english}

**Stage 2 — Momentum Ranking:** The model ranks 6 risky assets (SPY, EFA, EEM, VNQ, DBC, GLD) by vol-adjusted 12-1 month momentum and allocates to the top and middle terciles.

**Stage 3 — Credit Conditions (HY Regime):** {regime_english}

**Spread Velocity:** {roc_english}

**Conviction:** {conviction_english}

---

**Bottom line:** The model says hold {alloc_str}. That's {alloc_plain}. This is a mechanical, rules-based allocation — no forecasting, no opinion. Rebalance on the last business day of the month. Between rebalances, do nothing. The system will email you if anything changes.
""")

    st.divider()

    # Three-column layout: Abs Momentum, Momentum Ranking, Target Portfolio
    col_gem, col_mom, col_target = st.columns(3)

    with col_gem:
        st.subheader("Stage 1: Absolute Momentum")
        gem_data = {
            "Metric": ["SPY 12M Return", "EFA 12M Return", "BIL 12M Return", "Absolute Pass"],
            "Value": [
                f"{gem.get('spy_12m_return', 0):+.1%}",
                f"{gem.get('efa_12m_return', 0):+.1%}",
                f"{gem.get('bil_12m_return', 0):+.1%}",
                "YES" if gem.get("absolute_pass") else "NO",
            ],
        }
        st.table(pd.DataFrame(gem_data).set_index("Metric"))

        yc_spread = yc.get("t10y2y", "N/A")
        yc_status = yc.get("status", "N/A")
        st.caption(f"Yield Curve: {yc_spread}% ({yc_status}) — monitoring only")

    with col_mom:
        st.subheader("Stage 2: Momentum")
        mom_rows = []
        for ticker in sorted(mom.ranks.keys(), key=lambda t: mom.ranks.get(t, 99)):
            raw = mom.raw_momentum.get(ticker, np.nan)
            raw_s = f"{raw:+.1%}" if not np.isnan(raw) else "N/A"
            terc = mom.terciles.get(ticker, "N/A")
            rw = mom.risky_weights.get(ticker, 0.0)
            mom_rows.append({
                "Ticker": ticker,
                "12-1M": raw_s,
                "Tercile": terc,
                "Wt": f"{rw:.0%}",
            })
        st.table(pd.DataFrame(mom_rows).set_index("Ticker"))
        st.caption(f"Metric: {mom.ranking_metric} | Assets: {mom.available_assets}")

    with col_target:
        st.subheader("Target Portfolio")
        # Donut chart
        tickers = [t for t in portfolio_v2.ALL_TICKERS if target.get(t, 0) > 0.001]
        weights = [target[t] for t in tickers]
        colors = {
            "SPY": "#2ecc71", "EFA": "#3498db", "EEM": "#1abc9c",
            "VNQ": "#8e44ad", "DBC": "#d35400", "GLD": "#f1c40f",
            "SHY": "#f39c12", "ANGL": "#e74c3c",
        }

        fig = go.Figure(
            data=[go.Pie(
                labels=tickers,
                values=weights,
                hole=0.5,
                marker_colors=[colors.get(t, "#95a5a6") for t in tickers],
                textinfo="label+percent",
                textfont_size=16,
            )]
        )
        fig.update_layout(
            showlegend=False,
            margin=dict(t=10, b=10, l=10, r=10),
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)


# ──────────────────────────────────────────────
# 2. Signal History View
# ──────────────────────────────────────────────

elif view == "Signal History":
    sig_hist = load_signal_history()

    if sig_hist.empty:
        st.warning("No signal history found. Run `python run_daily.py` first.")
        st.stop()

    st.subheader("Signal History")

    # HY Spread over time
    try:
        all_data = load_data()
        hy_spread = all_data["hy_spread"]

        # ── Chart 1: HY OAS Spread + Regime ──
        fig_hy = go.Figure()

        # Classify regime
        thresholds = config.HY_REGIME_THRESHOLDS
        def _classify_regime_label(val):
            if val < thresholds["TIGHT"]:
                return "TIGHT"
            elif val < thresholds["NORMAL"]:
                return "NORMAL"
            elif val < thresholds["STRESSED"]:
                return "STRESSED"
            else:
                return "CRISIS"

        hy_regimes = hy_spread.apply(_classify_regime_label)
        regime_changes = hy_regimes.ne(hy_regimes.shift()).cumsum()

        regime_fill_colors = {
            "TIGHT": "rgba(46, 204, 113, 0.25)",
            "NORMAL": "rgba(52, 152, 219, 0.25)",
            "STRESSED": "rgba(243, 156, 18, 0.35)",
            "CRISIS": "rgba(231, 76, 60, 0.35)",
        }
        regime_line_colors = {
            "TIGHT": "#2ecc71", "NORMAL": "#3498db",
            "STRESSED": "#f39c12", "CRISIS": "#e74c3c",
        }

        # 1. Regime shading via layout shapes (always behind traces)
        shapes = []
        for _, group_idx in hy_regimes.groupby(regime_changes):
            regime_val = group_idx.iloc[0]
            x0 = group_idx.index[0]
            x1 = group_idx.index[-1]
            shapes.append(dict(
                type="rect",
                xref="x", yref="paper",
                x0=x0, x1=x1,
                y0=0, y1=1,
                fillcolor=regime_fill_colors.get(regime_val, "rgba(128,128,128,0.1)"),
                line_width=0,
                layer="below",
            ))

        # 2. HY OAS spread line (always in front of shapes)
        fig_hy.add_trace(go.Scatter(
            x=hy_spread.index, y=hy_spread.values,
            name="HY OAS (bps)",
            line=dict(color="white", width=2.5),
        ))

        # Y-axis range
        spread_max = float(hy_spread.max())
        spread_min = float(hy_spread.min())
        y_padding = (spread_max - spread_min) * 0.1
        y_upper = spread_max + y_padding
        y_lower = max(0, spread_min - y_padding)

        # Threshold lines
        for label, val in config.HY_REGIME_THRESHOLDS.items():
            if val <= y_upper:
                fig_hy.add_hline(
                    y=val, line_dash="dash", line_color="rgba(255,255,255,0.4)",
                    annotation_text=label, annotation_position="right",
                    annotation_font_color="rgba(255,255,255,0.7)",
                )
                y_upper = max(y_upper, val + y_padding)

        # 3. Legend entries for regimes
        for regime_val, color in regime_line_colors.items():
            fig_hy.add_trace(go.Scatter(
                x=[None], y=[None], mode="lines",
                line=dict(color=color, width=4),
                name=regime_val, showlegend=True,
            ))

        fig_hy.update_layout(
            title="Composite HY OAS Spread (bps) + Legacy Regime Bands",
            yaxis_title="OAS (bps)",
            yaxis=dict(range=[y_lower, y_upper]),
            height=450,
            hovermode="x unified",
            margin=dict(t=40, b=20, l=60, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            shapes=shapes,
        )
        st.plotly_chart(fig_hy, use_container_width=True)

    except Exception as e:
        st.warning(f"Could not load market data for chart: {e}")

    # Raw data table
    st.subheader("Raw Signal Log")
    st.dataframe(sig_hist.sort_values("date", ascending=False), use_container_width=True)


# ──────────────────────────────────────────────
# 3. SPY + Regimes View
# ──────────────────────────────────────────────

elif view == "SPY + Regimes":
    st.subheader("SPY Price History with Dual Momentum Regimes")

    with st.spinner("Running backtest to extract regime history..."):
        try:
            results = load_backtest_results()
            bt = results["momentum_hy"]
        except Exception as e:
            st.error(f"Backtest failed: {e}")
            st.info("Ensure FRED_API_KEY is set and you have internet access.")
            st.stop()

    # Load SPY prices aligned to backtest dates
    try:
        all_data = load_data()
        spy_prices = all_data["prices"]["SPY"].dropna()
    except Exception as e:
        st.error(f"Data fetch failed: {e}")
        st.stop()

    # Align SPY prices to backtest range
    spy_bt = spy_prices[spy_prices.index >= bt.index[0]]
    spy_bt = spy_bt[spy_bt.index <= bt.index[-1]]

    # Build regime map from backtest — use absolute_pass and hy_regime
    regime_cols = ["hy_regime"]
    if "absolute_pass" in bt.columns:
        regime_cols.insert(0, "absolute_pass")
    regime_map = bt[regime_cols].copy()

    # Reindex to match SPY prices (forward-fill regime for non-rebalance days)
    regime_aligned = regime_map.reindex(spy_bt.index, method="ffill")

    # ── Chart 1: SPY colored by HY Regime ──
    st.markdown("#### SPY by Credit Regime")
    st.caption("Background color = HY spread regime. Line = SPY adjusted close.")

    hy_regime_colors = {
        "TIGHT": "rgba(46, 204, 113, 0.15)",
        "NORMAL": "rgba(52, 152, 219, 0.15)",
        "STRESSED": "rgba(243, 156, 18, 0.25)",
        "CRISIS": "rgba(231, 76, 60, 0.25)",
    }
    hy_regime_line_colors = {
        "TIGHT": "#2ecc71",
        "NORMAL": "#3498db",
        "STRESSED": "#f39c12",
        "CRISIS": "#e74c3c",
    }

    fig_hy = go.Figure()

    hy_regimes = regime_aligned["hy_regime"]
    regime_changes = hy_regimes.ne(hy_regimes.shift()).cumsum()

    # 1. Background shading first (behind everything)
    for _, group in regime_aligned.groupby(regime_changes):
        regime_val = group["hy_regime"].iloc[0]
        x0 = group.index[0]
        x1 = group.index[-1]
        bg_color = hy_regime_colors.get(regime_val, "rgba(128,128,128,0.1)")
        fig_hy.add_vrect(x0=x0, x1=x1, fillcolor=bg_color, line_width=0, layer="below")

    # 2. SPY line segments on top
    for _, group in regime_aligned.groupby(regime_changes):
        regime_val = group["hy_regime"].iloc[0]
        idx = group.index
        spy_segment = spy_bt.loc[idx]
        color = hy_regime_line_colors.get(regime_val, "#95a5a6")

        fig_hy.add_trace(go.Scatter(
            x=spy_segment.index,
            y=spy_segment.values,
            mode="lines",
            line=dict(color=color, width=2),
            name=regime_val,
            showlegend=False,
            hovertemplate=f"HY: {regime_val}<br>SPY: $%{{y:.2f}}<br>%{{x}}<extra></extra>",
        ))

    # 3. Legend entries
    for regime_val, color in hy_regime_line_colors.items():
        fig_hy.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color=color, width=4),
            name=regime_val, showlegend=True,
        ))

    fig_hy.update_layout(
        title="SPY Price by HY Spread Regime",
        yaxis_title="SPY Price ($)",
        yaxis_type="log",
        height=500,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title="HY Regime"),
    )
    st.plotly_chart(fig_hy, use_container_width=True)

    # ── Metrics table: same as Backtest Performance ──
    st.subheader("Strategy Comparison")
    all_metrics = {}
    strategy_labels = {
        "momentum_hy": "Momentum + HY (3-Stage)",
        "sixty_forty": "60/40",
        "buy_hold": "Buy & Hold SPY",
    }
    for name, df in results.items():
        m = performance.compute_metrics(df)
        all_metrics[strategy_labels.get(name, name)] = {
            "CAGR": f"{m['cagr']:+.1%}",
            "Total Return": f"{m['total_return']:.0%}",
            "Max Drawdown": f"{m['max_drawdown']:.1%}",
            "Volatility": f"{m['ann_volatility']:.1%}",
            "Sharpe": f"{m['sharpe']:.2f}",
            "Sortino": f"{m['sortino']:.2f}",
            "Calmar": f"{m['calmar']:.2f}",
            "Best Year": f"{m['best_year']:+.1%} ({m['best_year_label']})",
            "Worst Year": f"{m['worst_year']:+.1%} ({m['worst_year_label']})",
            "% Positive Years": f"{m['pct_positive_years']:.0%}",
        }

    metrics_df = pd.DataFrame(all_metrics)
    st.dataframe(metrics_df, use_container_width=True)


# ──────────────────────────────────────────────
# 4. Backtest Performance View
# ──────────────────────────────────────────────

elif view == "Backtest Performance":
    st.subheader("Backtest: Strategy Comparison")

    with st.spinner("Running backtests (this may take a minute on first load)..."):
        try:
            results = load_backtest_results()
        except Exception as e:
            st.error(f"Backtest failed: {e}")
            st.info("Ensure FRED_API_KEY is set and you have internet access.")
            st.stop()

    # Equity curves
    fig = go.Figure()
    strategy_colors = {
        "momentum_hy": "#1abc9c",
        "sixty_forty": "#e67e22",
        "buy_hold": "#e74c3c",
    }
    strategy_labels = {
        "momentum_hy": "Momentum + HY (3-Stage)",
        "sixty_forty": "60/40",
        "buy_hold": "Buy & Hold SPY",
    }

    for name, df in results.items():
        fig.add_trace(go.Scatter(
            x=df.index, y=df["cumulative"],
            name=strategy_labels.get(name, name),
            line=dict(color=strategy_colors.get(name, "#95a5a6"), width=2),
        ))

    fig.update_layout(
        title=f"Growth of $1 (since {config.BACKTEST_START})",
        yaxis_title="Cumulative Value ($)",
        yaxis_type="log",
        height=500,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Drawdowns
    st.subheader("Drawdowns")
    fig_dd = go.Figure()
    for name, df in results.items():
        dd = performance.drawdown_series(df)
        fig_dd.add_trace(go.Scatter(
            x=dd.index, y=dd.values * 100,
            name=strategy_labels.get(name, name),
            line=dict(color=strategy_colors.get(name, "#95a5a6"), width=1.5),
            fill="tozeroy" if name == "momentum_hy" else None,
        ))

    fig_dd.update_layout(
        title="Drawdown (%)",
        yaxis_title="Drawdown %",
        height=350,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # Metrics table
    st.subheader("Performance Metrics")
    all_metrics = {}
    for name, df in results.items():
        m = performance.compute_metrics(df)
        all_metrics[strategy_labels.get(name, name)] = {
            "CAGR": f"{m['cagr']:+.1%}",
            "Total Return": f"{m['total_return']:.0%}",
            "Max Drawdown": f"{m['max_drawdown']:.1%}",
            "Volatility": f"{m['ann_volatility']:.1%}",
            "Sharpe": f"{m['sharpe']:.2f}",
            "Sortino": f"{m['sortino']:.2f}",
            "Calmar": f"{m['calmar']:.2f}",
            "Best Year": f"{m['best_year']:+.1%} ({m['best_year_label']})",
            "Worst Year": f"{m['worst_year']:+.1%} ({m['worst_year_label']})",
            "% Positive Years": f"{m['pct_positive_years']:.0%}",
        }

    metrics_df = pd.DataFrame(all_metrics)
    st.dataframe(metrics_df, use_container_width=True)

    # ── Annual Returns Bar Chart ──
    st.subheader("Annual Returns by Strategy")
    annual_data = {}
    for name, df in results.items():
        df_copy = df.copy()
        df_copy["year"] = df_copy.index.year
        annual = df_copy.groupby("year")["daily_return"].apply(
            lambda x: (1 + x).prod() - 1
        )
        annual_data[strategy_labels.get(name, name)] = annual

    annual_df = pd.DataFrame(annual_data)
    fig_annual = go.Figure()
    for col in annual_df.columns:
        sname = [k for k, v in strategy_labels.items() if v == col]
        color = strategy_colors.get(sname[0], "#95a5a6") if sname else "#95a5a6"
        fig_annual.add_trace(go.Bar(
            x=annual_df.index, y=annual_df[col] * 100,
            name=col, marker_color=color,
        ))

    fig_annual.update_layout(
        barmode="group",
        yaxis_title="Return (%)",
        height=400,
        hovermode="x unified",
        margin=dict(t=20, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_annual, use_container_width=True)

    # ── Monthly Returns Heatmap ──
    heatmap_strategy = "momentum_hy"
    heatmap_label = strategy_labels.get(heatmap_strategy, heatmap_strategy)
    st.subheader(f"Monthly Returns Heatmap ({heatmap_label})")
    heatmap_df = results[heatmap_strategy].copy()
    heatmap_df["year"] = heatmap_df.index.year
    heatmap_df["month"] = heatmap_df.index.month
    monthly = heatmap_df.groupby(["year", "month"])["daily_return"].apply(
        lambda x: (1 + x).prod() - 1
    ).unstack(level="month")
    monthly.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    fig_heatmap = go.Figure(data=go.Heatmap(
        z=monthly.values * 100,
        x=monthly.columns,
        y=monthly.index,
        colorscale=[
            [0, "#e74c3c"],
            [0.5, "#f5f5f5"],
            [1, "#2ecc71"],
        ],
        zmid=0,
        text=[[f"{v:.1f}%" if not np.isnan(v) else "" for v in row] for row in monthly.values * 100],
        texttemplate="%{text}",
        textfont=dict(size=10),
        colorbar=dict(title="Return %"),
    ))
    fig_heatmap.update_layout(
        yaxis=dict(autorange="reversed", dtick=1),
        height=max(300, len(monthly) * 22),
        margin=dict(t=20, b=20, l=60, r=20),
    )
    st.plotly_chart(fig_heatmap, use_container_width=True)

    # ── Rolling 3-Year Sharpe ──
    st.subheader("Rolling 3-Year Sharpe Ratio")
    fig_sharpe = go.Figure()
    window = 252 * 3  # 3 years of trading days
    for name, df in results.items():
        if len(df) < window:
            continue
        rolling_mean = df["daily_return"].rolling(window).mean() * 252
        rolling_std = df["daily_return"].rolling(window).std() * np.sqrt(252)
        rolling_sharpe = rolling_mean / rolling_std
        rolling_sharpe = rolling_sharpe.dropna()
        fig_sharpe.add_trace(go.Scatter(
            x=rolling_sharpe.index, y=rolling_sharpe.values,
            name=strategy_labels.get(name, name),
            line=dict(color=strategy_colors.get(name, "#95a5a6"), width=1.5),
        ))

    fig_sharpe.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_sharpe.update_layout(
        yaxis_title="Sharpe Ratio",
        height=400,
        hovermode="x unified",
        margin=dict(t=20, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_sharpe, use_container_width=True)


# ──────────────────────────────────────────────
# 5. Allocation Over Time View
# ──────────────────────────────────────────────

elif view == "Allocation Over Time":
    st.subheader("Portfolio Allocation History")

    port_hist = load_portfolio_history()

    if port_hist.empty:
        # Fall back to backtest data
        st.info("No live portfolio history. Showing backtest allocation for momentum_hy strategy.")
        with st.spinner("Running backtest..."):
            try:
                results = load_backtest_results()
                bt = results["momentum_hy"]
            except Exception as e:
                st.error(f"Backtest failed: {e}")
                st.stop()

        # Parse weights from backtest
        weight_records = []
        for date, row in bt.iterrows():
            try:
                w = json.loads(row["weights"])
                w["date"] = date
                weight_records.append(w)
            except (json.JSONDecodeError, TypeError):
                pass

        if not weight_records:
            st.warning("No weight data available.")
            st.stop()

        weights_df = pd.DataFrame(weight_records).set_index("date").fillna(0)
    else:
        # Parse live portfolio history
        weight_records = []
        for _, row in port_hist.iterrows():
            try:
                w = json.loads(row["target_weights"])
                w["date"] = row["date"]
                weight_records.append(w)
            except (json.JSONDecodeError, TypeError):
                pass

        if not weight_records:
            st.warning("No weight data available.")
            st.stop()

        weights_df = pd.DataFrame(weight_records).set_index("date").fillna(0)

    # Stacked area chart
    ticker_colors = {
        "SPY": "#2ecc71", "EFA": "#3498db", "EEM": "#1abc9c",
        "VNQ": "#8e44ad", "DBC": "#d35400", "GLD": "#f1c40f",
        "SHY": "#f39c12", "ANGL": "#e74c3c",
    }
    fig = go.Figure()

    for ticker in portfolio_v2.ALL_TICKERS:
        if ticker in weights_df.columns:
            fig.add_trace(go.Scatter(
                x=weights_df.index,
                y=weights_df[ticker] * 100,
                name=ticker,
                stackgroup="one",
                line=dict(width=0),
                fillcolor=ticker_colors.get(ticker, "#95a5a6"),
            ))

    fig.update_layout(
        title="Portfolio Allocation Over Time",
        yaxis_title="Weight (%)",
        yaxis=dict(range=[0, 100]),
        height=450,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Regime history from backtest
    if port_hist.empty:
        bt = results["momentum_hy"]

        fig_regime = go.Figure()

        regime_numeric = bt["hy_regime"].map({
            "TIGHT": 0, "NORMAL": 1, "STRESSED": 2, "CRISIS": 3,
        })
        fig_regime.add_trace(
            go.Scatter(
                x=bt.index, y=regime_numeric,
                mode="lines", name="HY Regime",
                line=dict(color="#e67e22", width=1),
            ),
        )
        fig_regime.update_yaxes(
            tickvals=[0, 1, 2, 3],
            ticktext=["TIGHT", "NORMAL", "STRESSED", "CRISIS"],
        )

        fig_regime.update_layout(
            title="HY Regime Over Time",
            height=300, showlegend=False,
            margin=dict(t=40, b=20, l=60, r=20),
        )
        st.plotly_chart(fig_regime, use_container_width=True)


# ──────────────────────────────────────────────
# 6. Parameter Sensitivity View
# ──────────────────────────────────────────────

elif view == "Parameter Sensitivity":
    st.subheader("Parameter Sensitivity Analysis")
    st.caption("How robust are the current parameters? Sweep lookback periods and HY thresholds to find out.")

    # ── Absolute Momentum Lookback ──
    st.markdown("#### Absolute Momentum Lookback")
    st.info(
        "The absolute momentum filter uses a fixed **12-month** lookback (12-1 month excess return over T-bills). "
        "This is the standard setting from the academic literature and is not swept here."
    )

    # ── HY Threshold Sweep ──
    st.markdown("#### HY Regime Percentile Thresholds")
    st.caption("Current: TIGHT < 25th, NORMAL < 60th, STRESSED < 85th pctl of CCC-BB spread. Testing tighter and wider bands.")

    @st.cache_data(ttl=3600)
    def run_threshold_sweep():
        """Run momentum_hy backtest with different CCC-BB percentile threshold sets."""
        all_data = data_mod.fetch_all()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
        ccc_bb_spread = all_data["ccc_bb_spread"]
        hy_b_spread = all_data["hy_b_spread"]
        daily_returns = prices.pct_change().fillna(0)

        start_date = config.BACKTEST_START
        end_date = prices.index[-1].strftime("%Y-%m-%d")

        mask = (prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))
        filtered = prices[mask]
        month_ends = filtered.groupby(filtered.index.to_period("M")).apply(lambda x: x.index[-1])
        rebalance_dates = set(month_ends)

        threshold_sets = {
            "Tight (15/50/75)": {"TIGHT": 15, "NORMAL": 50, "STRESSED": 75},
            "Current (25/60/85)": {"TIGHT": 25, "NORMAL": 60, "STRESSED": 85},
            "Wide (35/70/90)": {"TIGHT": 35, "NORMAL": 70, "STRESSED": 90},
            "Very Wide (40/75/95)": {"TIGHT": 40, "NORMAL": 75, "STRESSED": 95},
        }

        sweep_results = {}
        for label, pctl_thresholds in threshold_sets.items():
            current_weights = {"SPY": 0.0, "EFA": 0.0, "SHY": 1.0, "ANGL": 0.0}
            results_list = []
            tc_bps = config.TRANSACTION_COST_BPS / 10000

            for date in daily_returns[mask].index:
                if date in rebalance_dates:
                    gem_sig = signals_mod.compute_absolute_momentum(prices, date)

                    hy_available = (len(ccc_bb_spread) > 0 and len(hy_b_spread) > 0
                                    and date >= pd.Timestamp(config.HY_OAS_AVAILABLE_FROM))
                    if hy_available:
                        as_of = pd.Timestamp(date)
                        # Compute CCC-BB percentile rank on expanding window
                        ccc_bb_avail = ccc_bb_spread[ccc_bb_spread.index <= as_of].dropna()
                        b_avail = hy_b_spread[hy_b_spread.index <= as_of].dropna()
                        if len(ccc_bb_avail) >= 2 and len(b_avail) >= 2:
                            ccc_bb_pctl = (ccc_bb_avail < ccc_bb_avail.iloc[-1]).sum() / len(ccc_bb_avail) * 100

                            if ccc_bb_pctl < pctl_thresholds["TIGHT"]:
                                regime = "TIGHT"
                            elif ccc_bb_pctl < pctl_thresholds["NORMAL"]:
                                regime = "NORMAL"
                            elif ccc_bb_pctl < pctl_thresholds["STRESSED"]:
                                regime = "STRESSED"
                            else:
                                regime = "CRISIS"

                            # Single-B ROC for WIDENING_FAST
                            roc_start = as_of - pd.DateOffset(months=3)
                            b_past = hy_b_spread[hy_b_spread.index <= roc_start]
                            b_change = float(b_avail.iloc[-1]) - float(b_past.iloc[-1]) if len(b_past) > 0 else 0
                            fast_widen = b_change > config.HY_ROC_THRESHOLDS["WIDENING_FAST"]
                            hy_reg = {"regime": regime, "fast_widen_override": fast_widen}
                        else:
                            hy_reg = {"regime": "TIGHT", "fast_widen_override": False}
                    else:
                        hy_reg = {"regime": "TIGHT", "fast_widen_override": False}

                    target_weights = portfolio_mod.construct_portfolio(gem_sig, hy_reg)
                    tc = sum(tc_bps for t in config.ALL_TICKERS
                             if abs(target_weights.get(t, 0) - current_weights.get(t, 0)) > 1e-6)
                    current_weights = target_weights

                day_ret = sum(
                    current_weights.get(t, 0) * daily_returns.loc[date].get(t, 0)
                    for t in config.ALL_TICKERS if t in daily_returns.columns
                )
                if date in rebalance_dates:
                    day_ret -= tc
                results_list.append({"date": date, "daily_return": day_ret})

            df = pd.DataFrame(results_list).set_index("date")
            df["cumulative"] = (1 + df["daily_return"]).cumprod()
            sweep_results[label] = df

        return sweep_results

    with st.spinner("Running threshold sweep..."):
        try:
            threshold_results = run_threshold_sweep()
        except Exception as e:
            st.error(f"Threshold sweep failed: {e}")
            st.stop()

    # Equity curves
    fig_th = go.Figure()
    th_colors = {
        "Tight (15/50/75)": "#e74c3c",
        "Current (25/60/85)": "#2ecc71",
        "Wide (35/70/90)": "#3498db",
        "Very Wide (40/75/95)": "#9b59b6",
    }
    for label, df in threshold_results.items():
        width = 2.5 if "Current" in label else 1.5
        fig_th.add_trace(go.Scatter(
            x=df.index, y=df["cumulative"],
            name=label,
            line=dict(color=th_colors.get(label, "#95a5a6"), width=width),
        ))

    fig_th.update_layout(
        title="Growth of $1 by HY Regime Thresholds",
        yaxis_title="Cumulative Value ($)",
        yaxis_type="log",
        height=450,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_th, use_container_width=True)

    # Metrics comparison
    th_metrics = {}
    for label, df in threshold_results.items():
        m = performance.compute_metrics(df)
        th_metrics[label] = {
            "CAGR": f"{m['cagr']:+.1%}",
            "Max Drawdown": f"{m['max_drawdown']:.1%}",
            "Volatility": f"{m['ann_volatility']:.1%}",
            "Sharpe": f"{m['sharpe']:.2f}",
            "Sortino": f"{m['sortino']:.2f}",
        }
    st.dataframe(pd.DataFrame(th_metrics), use_container_width=True)

    st.markdown("""
**How to read this:** If the current parameters (highlighted) perform similarly to nearby alternatives,
the model is robust — small changes in thresholds don't materially change outcomes.
If performance is highly sensitive to a specific parameter, that's a fragility worth monitoring.
""")


# ──────────────────────────────────────────────
# Footer
# ──────────────────────────────────────────────
st.divider()
st.caption(
    "Three-Stage TAA Model Portfolio: Absolute Momentum + Cross-Asset Ranking + HY Regime. "
    "Signals are rules-based with no discretion. "
    "Past performance does not guarantee future results."
)
