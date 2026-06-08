"""量化策略分析平台 — Streamlit Web 仪表盘。

启动: streamlit run app.py
浏览器访问: http://localhost:8501
"""

import sys, os
sys.path.insert(0, r"d:\quant_framework\src")

import numpy as np
import pandas as pd
from datetime import datetime
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

st.set_page_config(
    page_title="A股量化策略分析平台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== Dark Theme CSS ====================
st.markdown("""
<style>
    .stApp { background: #0d1117; }
    section[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
    section[data-testid="stSidebar"] * { color: #c9d1d9 !important; }
    div[data-testid="stMetric"] { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px; }
    div[data-testid="stMetric"] label { color: #8b949e !important; font-size: 12px !important; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] { color: #c9d1d9 !important; font-size: 24px !important; font-weight: 700 !important; }
    h1, h2, h3 { color: #58a6ff !important; }
    .stDataFrame { background: #161b22; border: 1px solid #30363d; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ==================== Data Loading (P2#6: 统一用 BacktestStore) ====================
from quant_framework.data.backtest_store import BacktestStore

_app_store = BacktestStore(r"d:\quant_framework")

@st.cache_data(ttl=10)  # P2#9: 从60s降到10s
def load_data():
    return _app_store.load_latest()

# ==================== Sidebar ====================
with st.sidebar:
    st.title("📊 量化策略平台")
    st.markdown("---")
    page = st.radio("导航", ["📈 策略仪表盘", "📋 交易复盘", "🌡 市场情绪", "⚙️ 策略配置"])

    st.markdown("---")
    st.markdown(f"<span style='color:#484f58;font-size:12px;'>通达信日线数据<br/>v1.0 | {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>",
                unsafe_allow_html=True)

# ==================== Load ====================
data = load_data()
trades = data["trades"]
equity = data["equity"]
sentiment = data["sentiment"]

# ==================== PAGE 1: Dashboard ====================
if page == "📈 策略仪表盘":
    st.title("策略绩效仪表盘")

    if equity.empty:
        st.warning("暂无回测数据。请先运行回测或检查数据文件。")
        st.stop()

    eq_series = equity["equity"] if "equity" in equity.columns else equity.iloc[:, 0]
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0] - 1) if eq_series.iloc[0] > 0 else 0
    days = max((eq_series.index[-1] - eq_series.index[0]).days, 1)
    years = max(days / 365.25, 0.1)
    annual_ret = (1 + total_ret) ** (1 / years) - 1
    daily_ret = eq_series.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    peak = eq_series.expanding().max()
    dd = (eq_series - peak) / peak
    max_dd = dd.min()
    calmar = annual_ret / abs(max_dd) if abs(max_dd) > 1e-9 else 0

    n_trades = len(trades)
    if not trades.empty:
        rets = trades["return_pct"].values
        wr = (rets > 0).mean()
        best_t, worst_t = rets.max(), rets.min()
        wins = rets[rets > 0]; losses = rets[rets < 0]
        pf = abs(wins.mean() / losses.mean()) if len(losses) > 0 and losses.mean() != 0 else 0
        total_pnl = trades["net_profit"].sum() if "net_profit" in trades.columns else 0
    else:
        wr = best_t = worst_t = pf = total_pnl = 0

    # KPI Cards
    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
    c1.metric("总收益率", f"{total_ret:+.2%}")
    c2.metric("年化收益", f"{annual_ret:+.2%}")
    c3.metric("夏普比率", f"{sharpe:.2f}")
    c4.metric("最大回撤", f"{max_dd:.2%}")
    c5.metric("卡玛比率", f"{calmar:.2f}")
    c6.metric("胜率", f"{wr:.1%}")
    c7.metric("盈亏比", f"{pf:.2f}")
    c8.metric("交易次数", str(n_trades))

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("最佳交易", f"{best_t:+.2%}")
    c2.metric("最差交易", f"{worst_t:+.2%}")
    c3.metric("总盈亏(元)", f"{total_pnl:+,.0f}")
    c4.metric("年化波动", f"{daily_ret.std() * np.sqrt(252):.2%}")
    c5.metric("交易天数", str(len(daily_ret)))
    c6.metric("回测年限", f"{years:.1f}年")

    # Charts
    tab1, tab2, tab3 = st.tabs(["收益曲线", "回撤分析", "月度收益"])

    with tab1:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                            row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(x=eq_series.index, y=eq_series.values, mode="lines",
                                 name="策略权益", line=dict(color="#58a6ff", width=1.5),
                                 fill="tozeroy", fillcolor="rgba(88,166,255,0.1)"), row=1, col=1)
        fig.add_hline(y=eq_series.iloc[0], line_dash="dash", line_color="#484f58",
                      annotation_text="初始资金", row=1, col=1)
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, mode="lines",
                                 name="回撤", line=dict(color="#f85149", width=1),
                                 fill="tozeroy", fillcolor="rgba(248,81,73,0.15)"), row=2, col=1)
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=500, hovermode="x unified",
                          margin=dict(l=20, r=20, t=10, b=10))
        fig.update_yaxes(title_text="权益 (元)", row=1, col=1, gridcolor="#21262d")
        fig.update_yaxes(title_text="回撤 %", row=2, col=1, gridcolor="#21262d")
        st.plotly_chart(fig, width='stretch')

    with tab2:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, mode="lines",
                                 fill="tozeroy", fillcolor="rgba(248,81,73,0.2)",
                                 line=dict(color="#f85149", width=1.5)))
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=350, yaxis_title="回撤 %", margin=dict(l=20, r=20, t=10, b=10))
        fig.update_yaxes(gridcolor="#21262d", tickformat=".1f")
        st.plotly_chart(fig, width='stretch')

    with tab3:
        monthly = eq_series.resample("ME").last().pct_change().dropna()
        colors = ["#3fb950" if v > 0 else "#f85149" for v in monthly.values]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=monthly.index, y=monthly.values * 100, marker_color=colors,
                             text=[f"{v*100:+.2f}%" for v in monthly.values],
                             textposition="outside", textfont=dict(size=10)))
        fig.add_hline(y=0, line_color="#484f58")
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=350, yaxis_title="月度收益 %", margin=dict(l=20, r=20, t=10, b=10))
        fig.update_yaxes(gridcolor="#21262d")
        st.plotly_chart(fig, width='stretch')

# ==================== PAGE 2: Trade Review ====================
elif page == "📋 交易复盘":
    st.title("交易复盘分析")

    if trades.empty:
        st.warning("暂无交易数据。请先运行回测。")
        st.stop()

    rets = trades["return_pct"].values

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总交易", str(len(trades)))
    c2.metric("胜率", f"{(rets > 0).mean():.1%}")
    c3.metric("平均收益", f"{rets.mean():+.2%}")
    c4.metric("盈亏比", f"{abs(rets[rets>0].mean()/rets[rets<0].mean()) if (rets<0).any() else 0:.2f}")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("收益分布")
        fig = go.Figure()
        fig.add_trace(go.Histogram(x=rets * 100, nbinsx=30,
                                   marker_color=["#3fb950" if r > 0 else "#f85149" for r in rets],
                                   opacity=0.8))
        fig.add_vline(x=rets.mean() * 100, line_dash="dash", line_color="#58a6ff",
                      annotation_text=f"均值: {rets.mean()*100:+.2f}%")
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=350, xaxis_title="收益率 %", yaxis_title="次数")
        st.plotly_chart(fig, width='stretch')

    with col2:
        st.subheader("退出方式")
        exit_counts = trades["exit_type"].value_counts()
        fig = go.Figure(go.Pie(labels=exit_counts.index, values=exit_counts.values,
                                hole=0.5, marker_colors=["#58a6ff", "#f85149", "#d29922"]))
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", height=350)
        st.plotly_chart(fig, width='stretch')

    # Cumulative P&L
    st.subheader("累计盈亏曲线")
    trades_sorted = trades.sort_values("buy_date")
    cum_pnl = trades_sorted["net_profit"].cumsum() if "net_profit" in trades.columns else (trades_sorted["return_pct"] * 10000).cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=trades_sorted["buy_date"], y=cum_pnl.values, mode="lines",
                             fill="tozeroy", line=dict(color="#58a6ff", width=1.5),
                             fillcolor="rgba(88,166,255,0.15)"))
    fig.add_hline(y=0, line_color="#484f58")
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                      height=300, yaxis_title="累计盈亏 (元)")
    st.plotly_chart(fig, width='stretch')

    # Trade table
    st.subheader("交易明细")
    display_cols = ["symbol", "buy_date", "sell_date", "buy_price", "sell_price", "return_pct", "net_profit", "exit_type"]
    display_cols = [c for c in display_cols if c in trades.columns]
    display_df = trades[display_cols].sort_values("buy_date", ascending=False).head(100)
    display_df.columns = ["代码", "买入日", "卖出日", "买入价", "卖出价", "收益率", "盈亏", "退出方式"]
    if "收益率" in display_df.columns:
        display_df["收益率"] = display_df["收益率"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
    if "盈亏" in display_df.columns:
        display_df["盈亏"] = display_df["盈亏"].apply(lambda x: f"{x:+,.0f}" if pd.notna(x) else "")
    st.dataframe(display_df, width='stretch', hide_index=True)

# ==================== PAGE 3: Sentiment ====================
elif page == "🌡 市场情绪":
    st.title("市场情绪分析")

    if sentiment.empty:
        st.warning("暂无情绪数据。请先运行 run_sentiment.py 生成。")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("日均涨停", f"{sentiment['limit_up'].mean():.0f} 家")
    c2.metric("最高涨停", f"{sentiment['limit_up'].max():.0f} 家")
    c3.metric("日均跌停", f"{sentiment['limit_down'].mean():.0f} 家")
    c4.metric("冰点天数", f"{((sentiment['limit_up'] <= 15) & (sentiment['limit_down'] > sentiment['limit_up'])).sum()} 天")

    tab1, tab2, tab3 = st.tabs(["涨跌停统计", "市场宽度", "成交额"])

    with tab1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sentiment.index, y=sentiment["limit_up"].values,
                                 mode="lines", name="涨停", line=dict(color="#3fb950", width=1),
                                 fill="tozeroy", fillcolor="rgba(63,185,80,0.15)"))
        fig.add_trace(go.Scatter(x=sentiment.index, y=-sentiment["limit_down"].values,
                                 mode="lines", name="跌停", line=dict(color="#f85149", width=1),
                                 fill="tozeroy", fillcolor="rgba(248,81,73,0.15)"))
        fig.add_hline(y=0, line_color="#484f58")
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=400, hovermode="x unified", yaxis_title="家数")
        st.plotly_chart(fig, width='stretch')

    with tab2:
        breadth = (sentiment["up_count"] - sentiment["down_count"]) / sentiment["valid_stocks"]
        colors = ["#3fb950" if v > 0 else "#f85149" for v in breadth]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=sentiment.index, y=breadth.values, marker_color=colors,
                             width=1.5))
        fig.add_hline(y=0, line_color="#484f58")
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          height=400, yaxis_title="(上涨-下跌)/总数")
        st.plotly_chart(fig, width='stretch')

    with tab3:
        if "total_amount" in sentiment.columns:
            amount = sentiment["total_amount"] / 1e8
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=sentiment.index, y=amount.values,
                                     mode="lines", name="成交额",
                                     line=dict(color="#58a6ff", width=1),
                                     fill="tozeroy", fillcolor="rgba(88,166,255,0.1)"))
            fig.add_trace(go.Scatter(x=sentiment.index, y=amount.rolling(20).mean().values,
                                     mode="lines", name="20日均值",
                                     line=dict(color="#d29922", width=1.5)))
            fig.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                              height=400, yaxis_title="成交额 (亿元)")
            st.plotly_chart(fig, width='stretch')

# ==================== PAGE 4: Config ====================
elif page == "⚙️ 策略配置":
    st.title("策略配置")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("选股公式")
        signal = st.selectbox("公式", [
            "tdx2_final (公式1: 牛线突破+B1反转)",
            "tdx_resonance (公式2: 双信号共振)",
            "tdx2_xg (涨停突破牛线)",
            "tdx2_b1 (底部反转B1)",
            "tdx_qlj (擒龙决)",
            "tdx_ztxf (涨停先锋)",
        ])
        st.subheader("资金管理")
        max_pos = st.slider("最大持仓数", 1, 10, 3)
        pos_pct = st.slider("单票仓位 %", 5, 100, 30) / 100
        stop_loss = st.slider("止损 %", -20, 0, -3) / 100
        take_profit = st.slider("止盈 %", 1, 20, 5) / 100

    with col2:
        st.subheader("回测区间")
        start_date = st.date_input("起始日期", datetime(2022, 1, 1))
        end_date = st.date_input("结束日期", datetime(2025, 12, 31))
        st.metric("初始资金", "1,000,000 元")

        if st.button("🚀 开始回测", width='stretch', type="primary"):
            # P3#11: 不再只提示手动运行，实际执行回测
            import subprocess, json as _json
            config = _json.dumps({
                "strategy": "tdx2_final",
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "max_pos": 3, "stop_loss": -0.05, "take_profit": 0.08,
            })
            with st.spinner("正在运行回测..."):
                try:
                    subprocess.run([sys.executable, r"d:\quant_framework\run_backtest_fast.py",
                                    "--config", config], capture_output=True, timeout=600)
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"回测失败: {e}")

    st.markdown("---")
    st.subheader("📊 当前回测结果")
    if not equity.empty:
        st.success(f"已加载 {len(trades)} 笔交易的回测数据")
    else:
        st.warning("暂无回测数据")

# ==================== Footer ====================
st.markdown("---")
st.markdown("<div style='text-align:center;color:#484f58;font-size:12px;'>A股量化策略分析平台 v1.0 | 通达信日线数据 | T+1短线策略</div>",
            unsafe_allow_html=True)
