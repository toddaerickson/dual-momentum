"""
Streamlit Dashboard: Dual Momentum + HY Spread Model Portfolio

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
import backtest
import performance

# ──────────────────────────────────────────────
# Page Config
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="Dual Momentum Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("Dual Momentum + HY Spread Model Portfolio")


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

if st.sidebar.button("Refresh Data", type="primary"):
    st.cache_data.clear()
    st.rerun()

view = st.sidebar.radio(
    "View",
    ["Current Signals", "Signal History", "SPY + Regimes", "Backtest Performance", "Allocation Over Time"],
)


# ──────────────────────────────────────────────
# 1. Current Signals View
# ──────────────────────────────────────────────

if view == "Current Signals":
    try:
        all_data = load_data()
        prices = all_data["prices"]
        hy_spread = all_data["hy_spread"]
    except Exception as e:
        st.error(f"Data fetch failed: {e}")
        st.info("Ensure FRED_API_KEY is set in your environment.")
        st.stop()

    as_of = prices.index[-1]
    all_signals = signals_mod.compute_all_signals(prices, hy_spread, as_of)
    gem = all_signals["gem"]
    hy = all_signals["hy_regime"]
    yc = all_signals["yield_curve"]
    target = portfolio_mod.construct_portfolio(gem, hy)

    st.subheader(f"Signals as of {as_of.strftime('%Y-%m-%d')}")

    # Key metrics row
    col1, col2, col3, col4 = st.columns(4)

    gem_signal = gem.get("gem_signal", "N/A")
    gem_colors = {"SPY": "green", "EFA": "blue", "SHY": "orange"}
    gem_labels = {"SPY": "US Equities", "EFA": "Intl Equities", "SHY": "Short Treasuries"}

    col1.metric("GEM Signal", f"{gem_signal}", gem_labels.get(gem_signal, ""))
    col2.metric(
        "HY Regime",
        hy.get("regime", "N/A"),
        f"{hy.get('hy_spread_current', 0):.0f} bps",
    )
    col3.metric(
        "HY 3M Change",
        f"{hy.get('hy_spread_change_3m', 0):+.0f} bps",
        hy.get("rate_of_change", ""),
    )
    override = hy.get("fast_widen_override", False)
    col4.metric("Override", "ACTIVE" if override else "Inactive")

    st.divider()

    # ── Plain English Summary ──
    gem_signal = gem.get("gem_signal", "N/A")
    regime = hy.get("regime", "N/A")
    spread = hy.get("hy_spread_current", 0)
    spread_change = hy.get("hy_spread_change_3m", 0)
    override = hy.get("fast_widen_override", False)
    spy_ret = gem.get("spy_12m_return", 0)
    efa_ret = gem.get("efa_12m_return", 0)
    bil_ret = gem.get("bil_12m_return", 0)
    abs_pass = gem.get("absolute_pass", False)
    rel_winner = gem.get("relative_winner", "N/A")

    # GEM explanation
    if gem_signal == "SPY":
        gem_english = (
            f"US stocks (SPY) returned **{spy_ret:+.1%}** over the past 12 months, "
            f"beating international stocks (EFA) at **{efa_ret:+.1%}** and comfortably "
            f"clearing the T-bill hurdle of **{bil_ret:+.1%}**. "
            "Momentum favors staying in US equities."
        )
    elif gem_signal == "EFA":
        gem_english = (
            f"International stocks (EFA) returned **{efa_ret:+.1%}** over the past 12 months, "
            f"beating US stocks (SPY) at **{spy_ret:+.1%}** and clearing "
            f"the T-bill hurdle of **{bil_ret:+.1%}**. "
            "Momentum favors international equities."
        )
    else:
        gem_english = (
            f"Neither US stocks (**{spy_ret:+.1%}**) nor international stocks (**{efa_ret:+.1%}**) "
            f"beat T-bills (**{bil_ret:+.1%}**) over the past 12 months. "
            "Momentum says step aside into short-term Treasuries until equities recover."
        )

    # Regime explanation
    regime_descriptions = {
        "TIGHT": (
            f"Credit spreads are **tight at {spread:.0f} bps** — well below 350. "
            "Bond markets are calm and confident. No signs of stress. "
            "This is a green light for risk assets."
        ),
        "NORMAL": (
            f"Credit spreads are **normal at {spread:.0f} bps** (350-500 range). "
            "Markets are functioning normally with typical risk pricing. "
            "No reason to reduce exposure."
        ),
        "STRESSED": (
            f"Credit spreads are **elevated at {spread:.0f} bps** (500-700 range). "
            "The bond market is signaling concern. The model reduces equity exposure "
            "and adds Treasuries as a cushion."
        ),
        "CRISIS": (
            f"Credit spreads are **at crisis levels: {spread:.0f} bps** (above 700). "
            "This has only happened during severe events like 2008 and early 2020. "
            "The model goes heavily defensive and adds fallen angel bonds (ANGL) "
            "to capture the eventual recovery in credit."
        ),
    }
    regime_english = regime_descriptions.get(regime, "")

    # Rate of change
    if override:
        roc_english = (
            f"Spreads widened **{spread_change:+.0f} bps in 3 months**, triggering the "
            "WIDENING_FAST override (threshold: +100 bps). The model forces 100% Treasuries."
        )
    elif spread_change > 100:
        roc_english = f"Spreads widened **{spread_change:+.0f} bps over 3 months**. Override threshold is +100 bps."
    elif spread_change > 50:
        roc_english = f"Spreads widened **{spread_change:+.0f} bps over 3 months** (WIDENING). Override triggers at +100 bps."
    elif spread_change > 0:
        roc_english = f"Spreads widened **{spread_change:+.0f} bps over 3 months**. Override triggers at +100 bps."
    elif spread_change > -50:
        roc_english = f"Spreads moved **{spread_change:+.0f} bps over 3 months**."
    else:
        roc_english = f"Spreads tightened **{spread_change:+.0f} bps over 3 months** (TIGHTENING)."

    # Conviction assessment
    margin = abs(spy_ret - efa_ret)
    abs_margin = max(spy_ret, efa_ret) - bil_ret

    conviction_parts = []
    if gem_signal in ("SPY", "EFA"):
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
    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
        w = target.get(ticker, 0)
        if w > 0:
            alloc_parts.append(f"**{w:.0%} {ticker}**")
    alloc_str = " / ".join(alloc_parts)

    ticker_names = {"SPY": "US stocks", "EFA": "international stocks", "SHY": "short-term Treasuries", "ANGL": "fallen angel bonds"}
    alloc_plain = ", ".join(
        f"{target[t]:.0%} in {ticker_names.get(t, t)}"
        for t in ["SPY", "EFA", "SHY", "ANGL"] if target.get(t, 0) > 0
    )

    # Render the summary
    st.subheader("What This Means")

    st.markdown(f"""
**Momentum Signal (GEM):** {gem_english}

**Credit Conditions (HY Regime):** {regime_english}

**Spread Velocity:** {roc_english}

**Conviction:** {conviction_english}

---

**Bottom line:** The model says hold {alloc_str}. That's {alloc_plain}. This is a mechanical, rules-based allocation — no forecasting, no opinion. Rebalance on the last business day of the month. Between rebalances, do nothing. The system will email you if anything changes.
""")

    st.divider()

    # GEM detail + Target allocation side by side
    left, right = st.columns(2)

    with left:
        st.subheader("GEM Momentum Detail")
        gem_data = {
            "Metric": ["SPY 12M Return", "EFA 12M Return", "BIL 12M Return", "Relative Winner", "Absolute Pass"],
            "Value": [
                f"{gem.get('spy_12m_return', 0):+.1%}",
                f"{gem.get('efa_12m_return', 0):+.1%}",
                f"{gem.get('bil_12m_return', 0):+.1%}",
                gem.get("relative_winner", "N/A"),
                "YES" if gem.get("absolute_pass") else "NO",
            ],
        }
        st.table(pd.DataFrame(gem_data).set_index("Metric"))

        yc_spread = yc.get("t10y2y", "N/A")
        yc_status = yc.get("status", "N/A")
        st.caption(f"Yield Curve: {yc_spread}% ({yc_status}) — monitoring only")

    with right:
        st.subheader("Target Portfolio")
        # Donut chart
        tickers = [t for t in ["SPY", "EFA", "SHY", "ANGL"] if target.get(t, 0) > 0]
        weights = [target[t] for t in tickers]
        colors = {"SPY": "#2ecc71", "EFA": "#3498db", "SHY": "#f39c12", "ANGL": "#e74c3c"}

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
            title="HY OAS Spread (bps) + Regime",
            yaxis_title="OAS (bps)",
            yaxis=dict(range=[y_lower, y_upper]),
            height=450,
            hovermode="x unified",
            margin=dict(t=40, b=20, l=60, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            shapes=shapes,
        )
        st.plotly_chart(fig_hy, use_container_width=True)

        # ── Chart 2: GEM Signal ──
        if not sig_hist.empty and "gem_signal" in sig_hist.columns:
            fig_gem_hist = go.Figure()
            gem_colors_map = {"SPY": "#2ecc71", "EFA": "#3498db", "SHY": "#f39c12"}
            gem_numeric = sig_hist["gem_signal"].map({"SPY": 2, "EFA": 1, "SHY": 0})
            fig_gem_hist.add_trace(
                go.Scatter(
                    x=sig_hist["date"], y=gem_numeric,
                    mode="markers+lines",
                    name="GEM Signal",
                    marker=dict(
                        color=[gem_colors_map.get(s, "#95a5a6") for s in sig_hist["gem_signal"]],
                        size=8,
                    ),
                    line=dict(color="#95a5a6", width=1),
                ),
            )
            fig_gem_hist.update_layout(
                title="GEM Signal History",
                height=200,
                margin=dict(t=40, b=20, l=60, r=60),
                yaxis=dict(tickvals=[0, 1, 2], ticktext=["SHY", "EFA", "SPY"]),
                showlegend=False,
            )
            st.plotly_chart(fig_gem_hist, use_container_width=True)

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
            bt = results["gem_hy"]
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

    # Build a combined regime label from GEM signal + HY regime
    # Map regime for each trading day from the backtest
    regime_map = bt[["gem_signal", "hy_regime"]].copy()
    regime_map["combined"] = regime_map["gem_signal"] + " / " + regime_map["hy_regime"]

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

    # ── Chart 2: SPY colored by GEM Signal ──
    st.markdown("#### SPY by GEM Momentum Signal")
    st.caption("Line color = which asset GEM recommends holding. Green = SPY, Blue = EFA, Orange = SHY (risk-off).")

    gem_line_colors = {"SPY": "#2ecc71", "EFA": "#3498db", "SHY": "#f39c12"}
    gem_bg_colors = {
        "SPY": "rgba(46, 204, 113, 0.12)",
        "EFA": "rgba(52, 152, 219, 0.12)",
        "SHY": "rgba(243, 156, 18, 0.18)",
    }

    fig_gem = go.Figure()

    gem_signals = regime_aligned["gem_signal"]
    gem_changes = gem_signals.ne(gem_signals.shift()).cumsum()

    # 1. Background shading first (behind everything)
    for _, group in regime_aligned.groupby(gem_changes):
        gem_val = group["gem_signal"].iloc[0]
        x0 = group.index[0]
        x1 = group.index[-1]
        bg_color = gem_bg_colors.get(gem_val, "rgba(128,128,128,0.1)")
        fig_gem.add_vrect(x0=x0, x1=x1, fillcolor=bg_color, line_width=0, layer="below")

    # 2. SPY line segments on top
    for _, group in regime_aligned.groupby(gem_changes):
        gem_val = group["gem_signal"].iloc[0]
        idx = group.index
        spy_segment = spy_bt.loc[idx]
        color = gem_line_colors.get(gem_val, "#95a5a6")

        fig_gem.add_trace(go.Scatter(
            x=spy_segment.index,
            y=spy_segment.values,
            mode="lines",
            line=dict(color=color, width=2),
            name=gem_val,
            showlegend=False,
            hovertemplate=f"GEM: {gem_val}<br>SPY: $%{{y:.2f}}<br>%{{x}}<extra></extra>",
        ))

    # Legend entries
    gem_labels = {"SPY": "Hold SPY (US)", "EFA": "Hold EFA (Intl)", "SHY": "Hold SHY (Defensive)"}
    for gem_val, color in gem_line_colors.items():
        fig_gem.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color=color, width=4),
            name=gem_labels.get(gem_val, gem_val), showlegend=True,
        ))

    fig_gem.update_layout(
        title="SPY Price by GEM Signal",
        yaxis_title="SPY Price ($)",
        yaxis_type="log",
        height=500,
        hovermode="x unified",
        margin=dict(t=40, b=20, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title="GEM Signal"),
    )
    st.plotly_chart(fig_gem, use_container_width=True)

    # ── Metrics table: same as Backtest Performance ──
    st.subheader("Strategy Comparison")
    all_metrics = {}
    strategy_labels = {
        "gem_hy": "GEM + HY Overlay",
        "gem_pure": "GEM Pure",
        "gem_floor": "GEM Floor (70%)",
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
        "gem_hy": "#2ecc71",
        "gem_pure": "#3498db",
        "gem_floor": "#9b59b6",
        "sixty_forty": "#e67e22",
        "buy_hold": "#e74c3c",
    }
    strategy_labels = {
        "gem_hy": "GEM + HY Overlay",
        "gem_pure": "GEM Pure",
        "gem_floor": "GEM Floor (70%)",
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
            fill="tozeroy" if name == "gem_hy" else None,
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


# ──────────────────────────────────────────────
# 4. Allocation Over Time View
# ──────────────────────────────────────────────

elif view == "Allocation Over Time":
    st.subheader("Portfolio Allocation History")

    port_hist = load_portfolio_history()

    if port_hist.empty:
        # Fall back to backtest data
        st.info("No live portfolio history. Showing backtest allocation for gem_hy strategy.")
        with st.spinner("Running backtest..."):
            try:
                results = load_backtest_results()
                bt = results["gem_hy"]
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
    ticker_colors = {"SPY": "#2ecc71", "EFA": "#3498db", "SHY": "#f39c12", "ANGL": "#e74c3c"}
    fig = go.Figure()

    for ticker in ["SPY", "EFA", "SHY", "ANGL"]:
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
        bt = results["gem_hy"]
        regime_colors = {
            "TIGHT": "#2ecc71", "NORMAL": "#3498db",
            "STRESSED": "#f39c12", "CRISIS": "#e74c3c",
        }

        fig_regime = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=("GEM Signal", "HY Regime"),
            row_heights=[0.5, 0.5],
        )

        gem_numeric = bt["gem_signal"].map({"SPY": 2, "EFA": 1, "SHY": 0})
        fig_regime.add_trace(
            go.Scatter(
                x=bt.index, y=gem_numeric,
                mode="lines", name="GEM",
                line=dict(color="#3498db", width=1),
            ),
            row=1, col=1,
        )
        fig_regime.update_yaxes(
            tickvals=[0, 1, 2], ticktext=["SHY", "EFA", "SPY"],
            row=1, col=1,
        )

        regime_numeric = bt["hy_regime"].map({
            "TIGHT": 0, "NORMAL": 1, "STRESSED": 2, "CRISIS": 3,
        })
        fig_regime.add_trace(
            go.Scatter(
                x=bt.index, y=regime_numeric,
                mode="lines", name="HY Regime",
                line=dict(color="#e67e22", width=1),
            ),
            row=2, col=1,
        )
        fig_regime.update_yaxes(
            tickvals=[0, 1, 2, 3],
            ticktext=["TIGHT", "NORMAL", "STRESSED", "CRISIS"],
            row=2, col=1,
        )

        fig_regime.update_layout(height=350, showlegend=False, margin=dict(t=40, b=20, l=60, r=20))
        st.plotly_chart(fig_regime, use_container_width=True)


# ──────────────────────────────────────────────
# Footer
# ──────────────────────────────────────────────
st.divider()
st.caption(
    "Dual Momentum + HY Spread Model Portfolio. "
    "Signals are rules-based with no discretion. "
    "Past performance does not guarantee future results."
)
