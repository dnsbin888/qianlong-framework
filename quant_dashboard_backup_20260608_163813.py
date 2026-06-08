"""A股量化策略专业分析面板 — Streamlit Web Dashboard.

对标专业量化平台 (聚宽/米筐/优矿), 提供:
  1. 策略绩效仪表盘 — 收益/风险/交易指标卡片
  2. 收益曲线 + 回撤曲线 (双轴联动)
  3. 月度收益热力图
  4. 收益分布直方图 + QQ图
  5. 交易分析 — 胜率/盈亏比/持仓时间/周内表现
  6. 滚动指标 — 滚动夏普/滚动收益
  7. 情绪叠加 — 市场情绪 vs 策略表现
  8. 交易明细 — 可筛选/排序的交易记录

启动:
  streamlit run quant_dashboard.py
"""

import sys
import os

sys.path.insert(0, r"d:\quant_framework\src")

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# ---- Streamlit & Plotly ----
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ======================================================================
# Page Config
# ======================================================================

st.set_page_config(
    page_title="策略回测 — 潜龙",
    page_icon="🐉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- Custom CSS — 完全克隆 localhost:5000 潜龙主页风格 ----
# 颜色体系: #409EFF蓝 #87174C深红 #FF4051红 #FF40B3粉 #F1A100琥珀
# 背景体系: #000黑底 #141619面板 #191B1F表头 #2a2c30边框 #323337深边框
st.markdown("""
<style>
/* ================================================================
   潜龙 Dashboard — 完全克隆主页风格
   颜色: #409EFF蓝 #87174C深红 #FF4051红 #FF40B3粉 #F1A100琥珀
   ================================================================ */

/* ---- 全局重置 & 滚动条 ---- */
* { outline: none !important; box-sizing: border-box; }
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-thumb { border-radius: 4px; background: rgba(144,147,153,.3); }
*::-webkit-scrollbar-track { background: transparent; }

/* ---- 基础背景 & 字体 ---- */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"],
.stApp, .main, .block-container {
    background: #000 !important;
    color: #e3e3e3 !important;
    font-size: 14px !important;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif !important;
}

/* ---- 隐藏 Streamlit 原生顶栏 / 工具栏 / 装饰 ---- */
header[data-testid="stHeader"]  { display: none !important; }
div[data-testid="stToolbar"]    { display: none !important; }
div[data-testid="stDecoration"] { display: none !important; }
#MainMenu                       { display: none !important; }
footer                          { display: none !important; }

/* ---- Streamlit 侧边栏 → 克隆主页 sidebar 风格 ---- */
/* 外层 section 和内层 div 都强制 300px */
section[data-testid="stSidebar"] {
    width: 300px !important;
    min-width: 300px !important;
    max-width: 300px !important;
    flex-shrink: 0 !important;
}
/* 覆盖 Streamlit 内联 style 的宽度 */
section[data-testid="stSidebar"][style],
section[data-testid="stSidebar"] > div:first-child,
div[data-testid="stSidebarContent"] {
    width: 300px !important;
    min-width: 300px !important;
    max-width: 300px !important;
}
div[data-testid="stSidebar"] {
    background: #141619 !important;
    border-right: 1px solid #2a2c30 !important;
    padding: 20px 18px !important;
    width: 300px !important;
    min-width: 300px !important;
}
/* 调整主内容区左边距，防止被侧边栏盖住 */
section[data-testid="stMain"] > div {
    margin-left: 300px !important;
}
div[data-testid="stSidebar"] .stMarkdown p,
div[data-testid="stSidebar"] label,
div[data-testid="stSidebar"] span {
    color: #b4b6b8 !important;
    font-size: 13px !important;
}
div[data-testid="stSidebar"] h1,
div[data-testid="stSidebar"] h2,
div[data-testid="stSidebar"] h3 {
    color: #e3e3e3 !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    border-bottom: 1px solid #2a2c30;
    padding-bottom: 6px;
    margin-bottom: 8px;
}

/* 侧边栏 select / selectbox */
div[data-testid="stSidebar"] .stSelectbox > div > div {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    border-radius: 4px !important;
    color: #b4b6b8 !important;
    font-size: 12px !important;
}
div[data-testid="stSidebar"] .stSelectbox > div > div:hover {
    border-color: #409eff !important;
}
/* selectbox 下拉菜单 */
div[data-baseweb="select"] ul,
div[data-baseweb="popover"] {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
}
div[data-baseweb="select"] li:hover,
div[data-baseweb="menu"] li:hover {
    background: rgba(64,158,255,.1) !important;
    color: #409eff !important;
}

/* 侧边栏 radio 按钮 */
div[data-testid="stSidebar"] .stRadio > div {
    gap: 4px !important;
}
div[data-testid="stSidebar"] .stRadio label {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    border-radius: 4px !important;
    padding: 6px 14px !important;
    color: #b4b6b8 !important;
    cursor: pointer;
    transition: all .15s;
}
div[data-testid="stSidebar"] .stRadio label:hover {
    border-color: #409eff !important;
    color: #e3e3e3 !important;
}
div[data-testid="stSidebar"] .stRadio label[data-checked="true"],
div[data-testid="stSidebar"] [aria-checked="true"] {
    background: rgba(64,158,255,.1) !important;
    border-color: rgba(64,158,255,.3) !important;
    color: #409eff !important;
}

/* 侧边栏分割线 */
div[data-testid="stSidebar"] hr {
    border-color: #2a2c30 !important;
    margin: 8px 0 !important;
}

/* 侧边栏按钮 */
div[data-testid="stSidebar"] button {
    width: 100% !important;
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    color: #b4b6b8 !important;
    border-radius: 4px !important;
    font-size: 13px !important;
    padding: 8px 0 !important;
    cursor: pointer;
    transition: all .15s;
}
div[data-testid="stSidebar"] button:hover {
    border-color: #409eff !important;
    color: #e3e3e3 !important;
    background: rgba(64,158,255,.06) !important;
}

/* ---- 顶部自定义导航栏 ---- */
.ql-topbar {
    position: fixed; top: 0; left: 0; right: 0; z-index: 999;
    height: 44px; background: #141619; border-bottom: 1px solid #2a2c30;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 16px;
}
.ql-topbar-left { display: flex; align-items: center; gap: 24px; }
.ql-logo {
    font-size: 18px; font-weight: 700; color: #409eff !important;
    letter-spacing: 2px; text-decoration: none;
}
.ql-nav { display: flex; gap: 2px; }
.ql-nav a {
    padding: 6px 14px; border-radius: 4px; font-size: 13px;
    color: #b4b6b8 !important; text-decoration: none; transition: all .2s;
}
.ql-nav a:hover { color: #e3e3e3 !important; background: rgba(255,255,255,.05); }
.ql-nav a.ql-active { color: #409eff !important; background: rgba(64,158,255,.1); }
.ql-topbar-right {
    display: flex; align-items: center; gap: 10px;
    color: #888; font-size: 12px;
}
.ql-clock { color: #b4b6b8; font-variant-numeric: tabular-nums; }

/* ---- 主内容区偏移 44px 让出顶栏空间 ---- */
.stApp { margin-top: 44px !important; }
.main .block-container { padding: 0 !important; max-width: 100% !important; }

/* ── 左/右独立滚动 ── */
.ql-left-scroll {
    height: calc(100vh - 44px); overflow-y: auto;
    background: #141619; border-right: 1px solid #2a2c30;
    padding: 20px 18px;
}
.ql-right-scroll {
    height: calc(100vh - 44px); overflow-y: auto;
    background: #0d0e10; padding: 16px 20px;
}

/* ---- 标题 ---- */
h1 { font-size: 20px !important; color: #e3e3e3 !important; font-weight: 700 !important; }
h2 { font-size: 17px !important; color: #e3e3e3 !important; font-weight: 600 !important; }
h3 { font-size: 14px !important; color: #e3e3e3 !important; font-weight: 500 !important; }
p  { color: #b4b6b8 !important; }

/* ---- section 标题带下边框（克隆 sidebar-title 风格）---- */
.section-header {
    color: #e3e3e3 !important; font-size: 15px; font-weight: 600;
    border-bottom: 1px solid #2a2c30; padding-bottom: 8px; margin: 16px 0 12px 0;
}

/* ---- Metric 卡片 — 克隆主页 stat-item ---- */
div[data-testid="stMetric"] {
    background: #141619 !important;
    border: 1px solid #2a2c30 !important;
    border-radius: 5px !important;
    padding: 8px 12px !important;
    transition: border-color .15s;
}
div[data-testid="stMetric"]:hover {
    border-color: rgba(64,158,255,.3) !important;
}
div[data-testid="stMetric"] label,
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    color: #888 !important; font-size: 11px !important; font-weight: 400 !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #e3e3e3 !important; font-size: 18px !important; font-weight: 700 !important;
}
div[data-testid="stMetric"] [data-testid="stMetricDelta"] { font-size: 11px !important; }
div[data-testid="stMetric"] [data-testid="stMetricDelta"] .positive { color: #FF4051 !important; }
div[data-testid="stMetric"] [data-testid="stMetricDelta"] .negative { color: #00b96b !important; }

/* ---- DataFrame / 表格 — 克隆主页 stock-table ---- */
div[data-testid="stDataFrame"],
div[data-testid="stTable"] {
    background: #141619 !important;
    border: 1px solid #2a2c30 !important;
    border-radius: 6px !important;
    overflow: hidden !important;
}
div[data-testid="stDataFrame"] table,
div[data-testid="stTable"] table {
    border-collapse: collapse !important;
    width: 100% !important;
}
div[data-testid="stDataFrame"] th,
div[data-testid="stTable"] th {
    background: #191B1F !important;
    color: #999 !important;
    font-size: 11px !important; font-weight: 500 !important;
    border-bottom: 1px solid #2a2c30 !important;
    padding: 8px 10px !important;
    white-space: nowrap !important;
}
div[data-testid="stDataFrame"] td,
div[data-testid="stTable"] td {
    color: #b4b6b8 !important;
    font-size: 12px !important;
    border-bottom: 1px solid rgba(42,44,48,.6) !important;
    padding: 6px 10px !important;
}
div[data-testid="stDataFrame"] tr:hover td {
    background: rgba(255,255,255,.03) !important;
    color: #e3e3e3 !important;
}

/* ---- 主内容区普通 container / expander ---- */
div[data-testid="stExpander"] {
    background: #141619 !important;
    border: 1px solid #2a2c30 !important;
    border-radius: 6px !important;
}
div[data-testid="stExpander"] summary {
    color: #b4b6b8 !important; font-size: 13px !important;
    padding: 10px 14px !important;
}
div[data-testid="stExpander"] summary:hover { color: #e3e3e3 !important; }
div[data-testid="stExpander"] summary svg { fill: #666 !important; }

/* ---- 按钮 ---- */
button[data-testid="stBaseButton-primary"],
button[kind="primary"] {
    background: #409eff !important;
    border: 1px solid #409eff !important;
    color: #fff !important; border-radius: 4px !important;
}
button[data-testid="stBaseButton-secondary"],
button[kind="secondary"] {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    color: #b4b6b8 !important; border-radius: 4px !important;
    transition: all .15s;
}
button[data-testid="stBaseButton-secondary"]:hover,
button[kind="secondary"]:hover {
    border-color: #409eff !important;
    color: #e3e3e3 !important;
    background: rgba(64,158,255,.06) !important;
}

/* ---- Input / Selectbox / Slider (主内容区) ---- */
div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    color: #e3e3e3 !important;
    border-radius: 4px !important;
}
div[data-baseweb="input"]:focus-within,
div[data-baseweb="textarea"]:focus-within {
    border-color: #409eff !important;
}
div[data-baseweb="select"] > div {
    background: #1d1f23 !important;
    border: 1px solid #323337 !important;
    color: #b4b6b8 !important;
    border-radius: 4px !important;
}
div[data-baseweb="select"]:focus-within > div {
    border-color: #409eff !important;
}

/* Slider */
div[data-testid="stSlider"] > div > div > div {
    background: #409eff !important;
}
div[data-testid="stSlider"] [role="slider"] {
    background: #409eff !important;
    border-color: #409eff !important;
}

/* Checkbox */
input[type="checkbox"] { accent-color: #409eff !important; }

/* ---- Plotly 图表容器背景 ---- */
div[data-testid="stPlotlyChart"] > div {
    border: 1px solid #2a2c30 !important;
    border-radius: 6px !important;
    overflow: hidden !important;
    background: #141619 !important;
}

/* ---- Tab 组件 ---- */
div[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid #2a2c30 !important;
    gap: 2px !important;
}
div[data-testid="stTabs"] [role="tab"] {
    background: transparent !important;
    color: #b4b6b8 !important;
    border: 1px solid transparent !important;
    border-radius: 4px 4px 0 0 !important;
    font-size: 13px !important;
    padding: 6px 16px !important;
    transition: all .15s !important;
}
div[data-testid="stTabs"] [role="tab"]:hover {
    color: #e3e3e3 !important;
    background: rgba(255,255,255,.04) !important;
}
div[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: #409eff !important;
    background: rgba(64,158,255,.08) !important;
    border-color: rgba(64,158,255,.3) !important;
    border-bottom-color: transparent !important;
}

/* ---- Divider ---- */
hr { border-color: #2a2c30 !important; margin: 10px 0 !important; }

/* ---- 色彩助手类 (供 st.markdown 使用) ---- */
.up   { color: #FF4051 !important; }
.down { color: #00b96b !important; }
.flat { color: #999 !important; }
.text-blue   { color: #409eff !important; }
.text-amber  { color: #F1A100 !important; }
.text-pink   { color: #FF40B3 !important; }
.text-dim    { color: #666 !important; }

/* ---- 信号标签 ---- */
.signal-tag {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 11px; font-weight: 500;
}
.signal-tag.triple { background: rgba(255,64,81,.15); color: #FF4051; }
.signal-tag.double { background: rgba(241,161,0,.15); color: #F1A100; }
.signal-tag.single { background: rgba(64,158,255,.12); color: #409eff; }

/* ---- 因子图标 ---- */
.factor-icons { display: flex; gap: 4px; align-items: center; }
.factor-icon {
    width: 20px; height: 20px; border-radius: 3px; font-size: 10px;
    display: inline-flex; align-items: center; justify-content: center;
    font-weight: 700; line-height: 1;
}
.factor-icon.d  { background: rgba(255,64,81,.15); color: #FF4051; }
.factor-icon.zg { background: rgba(255,64,81,.2);  color: #FF40B3; }
.factor-icon.z  { background: rgba(241,161,0,.15); color: #F1A100; }
.factor-icon.g  { background: rgba(64,158,255,.15); color: #409eff; }

/* ---- 资金列 ---- */
.capital-cell {
    background: rgba(135,23,76,.5); color: #FFF;
    display: block; padding: 3px 10px; margin: -4px -6px;
    text-align: right;
}

/* ---- 垂直间距压缩 ---- */
div[data-testid="stVerticalBlock"] > div + div { margin-top: 0.15rem !important; }
div[data-testid="column"] { padding: 0 4px !important; }
</style>
""", unsafe_allow_html=True)



# ======================================================================
# Data Loading (P2#6/9: 统一用 BacktestStore, TTL降至10s)
# ======================================================================

from quant_framework.data.backtest_store import BacktestStore

_store = BacktestStore(r"d:\quant_framework")


@st.cache_data(ttl=10)  # P2#9: 从120s降到10s
def load_data(signal_name="tdx2_final"):
    """统一通过 BacktestStore 加载数据。"""
    return _store.load_latest()

    return data


def generate_sample_data():
    """Generate sample data for demo when real data is unavailable."""
    np.random.seed(42)
    dates = pd.date_range("2022-01-01", "2025-06-01", freq="B")
    n = len(dates)

    # Simulated equity curve with some realistic patterns
    returns = np.random.normal(0.0008, 0.018, n)  # Mean slightly positive
    # Add some autocorrelation and trends
    for i in range(1, len(returns)):
        returns[i] += returns[i-1] * 0.1  # Slight momentum

    equity = 1_000_000 * np.cumprod(1 + returns)
    equity = pd.Series(equity, index=dates, name="equity")

    # Drawdown
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak

    # 50 simulated trades
    n_trades = 80
    trade_dates = np.random.choice(dates[100:], n_trades, replace=False)
    trade_dates = sorted(trade_dates)
    trades = []
    for i, d in enumerate(trade_dates):
        ret = np.random.normal(0.008, 0.035)
        ret = np.clip(ret, -0.08, 0.10)
        trades.append({
            "symbol": f"{np.random.choice(['600','000','300'])}{np.random.randint(100,999):03d}",
            "buy_date": d,
            "sell_date": d + timedelta(days=1),
            "buy_price": np.random.uniform(10, 80),
            "sell_price": 0,  # Will be computed
            "volume": np.random.randint(100, 5000) * 100,
            "return_pct": ret,
            "net_profit": ret * 50000,
            "exit_type": np.random.choice(
                ["normal", "stop_loss", "take_profit"],
                p=[0.6, 0.25, 0.15]
            ),
            "signal": "tdx2_final",
        })
        trades[-1]["sell_price"] = trades[-1]["buy_price"] * (1 + ret)

    trades_df = pd.DataFrame(trades)

    return {
        "equity": equity.to_frame("equity"),
        "drawdown": drawdown.to_frame("drawdown"),
        "trades": trades_df,
        "sentiment": pd.DataFrame(),
    }


# ======================================================================
# Metric computation
# ======================================================================

def compute_metrics(equity: pd.Series, trades: pd.DataFrame):
    """Compute all performance metrics."""
    if equity.empty:
        return {}

    total_ret = (equity.iloc[-1] / equity.iloc[0] - 1) if equity.iloc[0] > 0 else 0
    days = (equity.index[-1] - equity.index[0]).days
    years = max(days / 365.25, 0.1)
    annual_ret = (1 + total_ret) ** (1 / years) - 1

    daily_ret = equity.pct_change().dropna()
    annual_vol = daily_ret.std() * np.sqrt(252)
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    sortino_std = daily_ret[daily_ret < 0].std()
    sortino = (daily_ret.mean() / sortino_std * np.sqrt(252)) if sortino_std and sortino_std > 0 else 0

    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak
    max_dd = drawdown.min()
    calmar = annual_ret / abs(max_dd) if abs(max_dd) > 1e-9 else 0

    # Trade metrics
    if not trades.empty:
        returns = trades["return_pct"].values
        win_rate = (returns > 0).mean()
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = losses.mean() if len(losses) > 0 else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        best = returns.max()
        worst = returns.min()
        n_trades = len(trades)
        total_pnl = trades["net_profit"].sum() if "net_profit" in trades.columns else 0
    else:
        win_rate = avg_win = avg_loss = profit_factor = best = worst = n_trades = total_pnl = 0

    monthly_ret = equity.resample("ME").last().pct_change().dropna()
    positive_months = (monthly_ret > 0).mean() if len(monthly_ret) > 0 else 0

    return {
        "total_return": total_ret,
        "annual_return": annual_ret,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "best_trade": best,
        "worst_trade": worst,
        "n_trades": n_trades,
        "total_pnl": total_pnl,
        "positive_months": positive_months,
        "trading_days": len(daily_ret),
        "years": years,
    }


# ======================================================================
# Chart builders
# ======================================================================

def build_equity_chart(equity: pd.Series, drawdown: pd.Series | None = None):
    """Build equity curve + drawdown subchart."""
    if drawdown is None:
        peak = equity.expanding().max()
        drawdown = (equity - peak) / peak

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=("Equity Curve", "Drawdown"),
    )

    # Equity curve
    fig.add_trace(
        go.Scatter(
            x=equity.index, y=equity.values,
            mode="lines",
            name="Strategy",
            line=dict(color="#409eff", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(88,166,255,0.1)",
        ),
        row=1, col=1,
    )

    # Initial capital line
    fig.add_hline(
        y=equity.iloc[0], line_dash="dash", line_color="#484f58",
        annotation_text="Initial Capital", row=1, col=1,
    )

    # Drawdown
    fig.add_trace(
        go.Scatter(
            x=drawdown.index, y=drawdown.values * 100,
            mode="lines",
            name="Drawdown",
            line=dict(color="#FF4051", width=1),
            fill="tozeroy",
            fillcolor="rgba(248,81,73,0.15)",
        ),
        row=2, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=500,
        margin=dict(l=20, r=20, t=30, b=20),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )
    fig.update_yaxes(title_text="Equity (CNY)", row=1, col=1, gridcolor="#2a2c30")
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1, gridcolor="#2a2c30", tickformat=".1%")

    return fig


def build_monthly_heatmap(equity: pd.Series):
    """Build monthly returns heatmap table."""
    monthly = equity.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        return None

    # Build pivot: year x month
    df = monthly.to_frame("return")
    df["year"] = df.index.year
    df["month"] = df.index.month
    pivot = df.pivot(index="year", columns="month", values="return")

    month_names = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    pivot.columns = [month_names.get(c, c) for c in pivot.columns]

    # Plotly heatmap
    fig = go.Figure(data=go.Heatmap(
        z=pivot.values * 100,
        x=list(pivot.columns),
        y=[str(y) for y in pivot.index],
        colorscale=[[0, "#FF4051"], [0.5, "#141619"], [1, "#00b96b"]],
        zmid=0,
        text=[[f"{v*100:.1f}%" if not np.isnan(v) else "" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        textfont={"color": "#b4b6b8", "size": 11},
        hoverongaps=False,
        showscale=True,
        colorbar=dict(title="Return %", tickformat=".1f"),
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=300,
        margin=dict(l=20, r=20, t=10, b=10),
        xaxis=dict(title="", side="top"),
        yaxis=dict(title="", autorange="reversed"),
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )

    return fig


def build_returns_distribution(trades: pd.DataFrame):
    """Build returns distribution histogram."""
    if trades.empty:
        return go.Figure()

    returns = trades["return_pct"].values * 100

    fig = go.Figure()

    # Histogram
    fig.add_trace(go.Histogram(
        x=returns,
        nbinsx=40,
        marker=dict(
            color=["#00b96b" if r > 0 else "#FF4051" for r in returns],
        ),
        opacity=0.8,
        name="Return Distribution",
    ))

    # Mean line
    mean_ret = returns.mean()
    fig.add_vline(
        x=mean_ret, line_dash="dash", line_color="#409eff",
        annotation_text=f"Mean: {mean_ret:.2f}%",
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=350,
        margin=dict(l=20, r=20, t=10, b=10),
        xaxis_title="Return (%)",
        yaxis_title="Count",
        bargap=0.05,
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )

    return fig


def build_rolling_metrics(equity: pd.Series, window: int = 60):
    """Build rolling Sharpe and rolling returns chart."""
    if len(equity) < window:
        return go.Figure()

    daily_ret = equity.pct_change().dropna()
    rolling_ret = daily_ret.rolling(window).mean() * 252 * 100  # Annualized %
    rolling_std = daily_ret.rolling(window).std() * np.sqrt(252) * 100
    rolling_sharpe = rolling_ret / rolling_std

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
        subplot_titles=("Rolling Annual Return (60-Day)", "Rolling Sharpe Ratio (60-Day)"),
    )

    fig.add_trace(
        go.Scatter(
            x=rolling_ret.index, y=rolling_ret.values,
            mode="lines", name="Rolling Return",
            line=dict(color="#409eff", width=1.2),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.1)",
        ),
        row=1, col=1,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#484f58", row=1, col=1)

    fig.add_trace(
        go.Scatter(
            x=rolling_sharpe.index, y=rolling_sharpe.values,
            mode="lines", name="Rolling Sharpe",
            line=dict(color="#d2a8ff", width=1.2),
            fill="tozeroy", fillcolor="rgba(210,168,255,0.1)",
        ),
        row=2, col=1,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#484f58", row=2, col=1)
    fig.add_hline(y=1, line_dash="dot", line_color="#00b96b", row=2, col=1, annotation_text="Sharpe=1")

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=400,
        margin=dict(l=20, r=20, t=30, b=10),
        showlegend=False,
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )
    fig.update_yaxes(title_text="Return %", row=1, col=1, gridcolor="#2a2c30", tickformat=".1f")
    fig.update_yaxes(title_text="Sharpe", row=2, col=1, gridcolor="#2a2c30")

    return fig


def build_weekly_analysis(trades: pd.DataFrame):
    """Build weekday analysis bar chart."""
    if trades.empty:
        return go.Figure()

    t = trades.copy()
    t["buy_date_dt"] = pd.to_datetime(t["buy_date"])
    t["weekday"] = t["buy_date_dt"].dt.dayofweek
    weekday_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

    grouped = t.groupby("weekday").agg(
        count=("return_pct", "count"),
        win_rate=("return_pct", lambda x: (x > 0).mean() * 100),
        avg_return=("return_pct", lambda x: x.mean() * 100),
    )
    grouped.index = grouped.index.map(weekday_names)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped.index, y=grouped["avg_return"],
        marker=dict(
            color=["#00b96b" if v > 0 else "#FF4051" for v in grouped["avg_return"]],
        ),
        text=[f"{v:+.2f}%" for v in grouped["avg_return"]],
        textposition="outside",
        name="Avg Return",
    ))

    fig.add_trace(go.Scatter(
        x=grouped.index, y=grouped["win_rate"],
        mode="lines+markers",
        name="Win Rate %",
        yaxis="y2",
        line=dict(color="#409eff", width=2),
        marker=dict(size=10),
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=300,
        margin=dict(l=20, r=20, t=10, b=10),
        yaxis=dict(title="Avg Return %", gridcolor="#2a2c30"),
        yaxis2=dict(title="Win Rate %", overlaying="y", side="right", range=[0, 100]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )

    return fig


def build_drawdown_chart(equity: pd.Series):
    """Build underwater/drawdown plot."""
    peak = equity.expanding().max()
    dd = ((equity - peak) / peak * 100)

    fig = go.Figure()

    # Top 5 drawdown periods
    drawdown_start = None
    drawdown_periods = []
    in_dd = False

    for i in range(len(dd)):
        if dd.iloc[i] < 0 and not in_dd:
            drawdown_start = i
            in_dd = True
        elif dd.iloc[i] >= 0 and in_dd:
            drawdown_periods.append((drawdown_start, i))
            in_dd = False

    if in_dd:
        drawdown_periods.append((drawdown_start, len(dd) - 1))

    # Sort by depth
    drawdown_periods.sort(key=lambda x: dd.iloc[x[0]:x[1]+1].min())
    top_drawdowns = drawdown_periods[:5]

    colors = ["#FF4051", "#F1A100", "#FF40B3", "#8b949e", "#484f58"]
    # Convert hex to rgba since Plotly doesn't support 8-char hex
    def _hex_to_rgba(hex_color, alpha=0.25):
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    for rank, (start, end) in enumerate(top_drawdowns):
        fig.add_trace(go.Scatter(
            x=dd.index[start:end+1],
            y=dd.values[start:end+1],
            mode="lines",
            fill="tozeroy",
            fillcolor=_hex_to_rgba(colors[rank % len(colors)]),
            line=dict(color=colors[rank % len(colors)], width=1.5),
            name=f"DD{rank+1}: {dd.iloc[start:end+1].min():.1f}%",
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=300,
        margin=dict(l=20, r=20, t=10, b=10),
        yaxis=dict(title="Drawdown %", gridcolor="#2a2c30", tickformat=".1f"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )

    return fig


def build_sentiment_overlay(equity: pd.Series, sentiment: pd.DataFrame):
    """Overlay strategy performance with market sentiment."""
    if sentiment.empty or equity.empty:
        return go.Figure()

    # Align dates
    common_dates = equity.index.intersection(sentiment.index)
    if len(common_dates) < 10:
        return go.Figure()

    sentiment_aligned = sentiment.loc[common_dates]
    # Get monthly strategy returns
    monthly_strategy = equity.resample("ME").last().pct_change().dropna()
    monthly_sentiment = sentiment_aligned["sentiment_score"].resample("ME").mean()

    common_months = monthly_strategy.index.intersection(monthly_sentiment.index)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=monthly_strategy.loc[common_months].index,
        y=monthly_strategy.loc[common_months].values * 100,
        name="Strategy Monthly Return",
        marker=dict(
            color=["#00b96b" if v > 0 else "#FF4051" for v in monthly_strategy.loc[common_months].values],
            opacity=0.8,
        ),
    ))

    fig.add_trace(go.Scatter(
        x=monthly_sentiment.loc[common_months].index,
        y=monthly_sentiment.loc[common_months].values,
        mode="lines+markers",
        name="Market Sentiment",
        yaxis="y2",
        line=dict(color="#FF40B3", width=2),
        marker=dict(size=6),
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141619",
        plot_bgcolor="#141619",
        height=350,
        margin=dict(l=20, r=20, t=10, b=10),
        yaxis=dict(title="Strategy Return %", gridcolor="#2a2c30"),
        yaxis2=dict(title="Sentiment Score", overlaying="y", side="right"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif")
    )

    return fig


# ======================================================================
# Main Dashboard
# ======================================================================


def main():
    # ==================================================================
    # 潜龙全局顶栏 (与 localhost:5000 完全一致)
    # ==================================================================
    # Topbar HTML
    st.markdown(f"""
    <div class="ql-topbar">
      <div class="ql-topbar-left">
        <a class="ql-logo" href="http://localhost:5000/">🐉 潜龙</a>
        <nav class="ql-nav">
          <a href="http://localhost:5000/">智能狙击大模型</a>
          <a href="http://localhost:8501/" class="ql-active">策略回测</a>
        </nav>
      </div>
      <div class="ql-topbar-right">
        <span style="color:#b4b6b8">{datetime.now().strftime('%H:%M:%S')}</span>
        <span style="color:#666;font-size:11px">| 通达信日线数据</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 左右分栏：左侧配置，右侧结果 ----
    left_col, right_col = st.columns([1, 3])

    with left_col:
        st.markdown('<div class="ql-left-scroll">', unsafe_allow_html=True)
        st.markdown('<div class="section-header">📋 功能页面</div>', unsafe_allow_html=True)

        page = st.radio("选择页面", ["📊 策略回测", "🔬 WFA 分析"], label_visibility="collapsed", key="main_page")

        if page == "🔬 WFA 分析":
            # ─────────────── WFA 分析页面 ───────────────
            st.markdown('<div class="section-header" style="margin-top:20px">🔬 WFA 参数配置</div>', unsafe_allow_html=True)
            wfa_stock = st.text_input("股票代码", "600519", key="wfa_stock")
            wfa_train = st.number_input("训练窗口 (交易日)", 60, 504, 252, 21, key="wfa_train")
            wfa_test = st.number_input("测试窗口 (交易日)", 21, 252, 63, 21, key="wfa_test")
            wfa_folds = st.slider("折叠数", 2, 8, 4, key="wfa_folds")

            st.markdown('<div style="margin-top:10px;font-size:12px;color:#8b949e">参数搜索范围:</div>', unsafe_allow_html=True)
            wfa_stop_losses = st.multiselect("止损%", [-0.03, -0.05, -0.07, -0.10], default=[-0.03, -0.05, -0.07], key="wfa_sl")
            wfa_take_profits = st.multiselect("止盈%", [0.05, 0.08, 0.10, 0.15], default=[0.05, 0.08, 0.10], key="wfa_tp")
            wfa_max_pos = st.multiselect("最大持仓", [3, 5, 7], default=[3, 5], key="wfa_mp")

            if st.button("🔬 开始 WFA 分析", width="stretch", type="primary", key="wfa_btn"):
                import subprocess, json as _json
                param_grid = {
                    "stop_loss": wfa_stop_losses,
                    "take_profit": wfa_take_profits,
                }
                cmd = [
                    sys.executable,
                    r"d:\quant_framework\run_wfa.py",
                    "--stock", wfa_stock,
                    "--train", str(wfa_train),
                    "--test", str(wfa_test),
                    "--n-folds", str(wfa_folds),
                    "--param-grid", _json.dumps(param_grid),
                    "--output", r"d:\quant_framework\wfa_result.json",
                ]
                with st.spinner("正在运行 WFA 分析 (网格搜索 × 滚动窗口, 约需1-5分钟)..."):
                    try:
                        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                        if proc.returncode == 0:
                            st.success("✅ WFA 分析完成！")
                        else:
                            st.error(f"WFA 失败 (exit {proc.returncode})")
                            if proc.stderr:
                                st.text(proc.stderr[-500:])
                    except subprocess.TimeoutExpired:
                        st.error("WFA 超时 (10分钟)")
                    except Exception as e:
                        st.error(f"WFA 启动失败: {e}")

        st.markdown('</div>', unsafe_allow_html=True)
    # end of left_col

    # ─────────────── WFA 结果渲染 (右侧) ───────────────
    if page == "🔬 WFA 分析":
        with right_col:
            st.markdown('<div class="ql-right-scroll">', unsafe_allow_html=True)
            wfa_path = r"d:\quant_framework\wfa_result.json"
            if not os.path.exists(wfa_path):
                st.info("🔍 尚未运行 WFA 分析。请在左侧配置参数后点击「开始 WFA 分析」。")
            else:
                import json as _json
                with open(wfa_path, "r", encoding="utf-8") as f:
                    wfa_data = _json.load(f)

                if "error" in wfa_data:
                    st.error(f"WFA 运行失败: {wfa_data['error']}")
                else:
                    folds = wfa_data.get("folds", [])
                    summary = wfa_data.get("summary", {})
                    params = wfa_data.get("params", {})

                    st.markdown(f"### 🔬 WFA 分析结果 — {wfa_data.get('stock', '?')}")
                    st.caption(f"{params.get('n_folds', '?')} folds · train={params.get('train_days','?')}d · test={params.get('test_days','?')}d · "
                               f"param_grid: {params.get('param_grid', {})} · {params.get('elapsed_seconds','?')}s")

                    if folds:
                        import plotly.graph_objects as go
                        from plotly.subplots import make_subplots

                        # -- Chart 1: Train vs Test Sharpe bars --
                        fig_bars = go.Figure()
                        fold_labels = [f"Fold {f['fold']}" for f in folds]
                        train_sharpes = [f.get("train_sharpe", 0) for f in folds]
                        test_sharpes = [f.get("test_sharpe", 0) for f in folds]
                        decays = [f.get("sharpe_decay", 0) for f in folds]

                        fig_bars.add_trace(go.Bar(name="训练 Sharpe", x=fold_labels, y=train_sharpes,
                            marker_color="#58a6ff", text=[f"{v:.2f}" for v in train_sharpes], textposition="outside"))
                        fig_bars.add_trace(go.Bar(name="测试 Sharpe", x=fold_labels, y=test_sharpes,
                            marker_color="#FF4051", text=[f"{v:.2f}" for v in test_sharpes], textposition="outside"))
                        fig_bars.add_hline(y=0, line_dash="solid", line_color="#484f58")
                        fig_bars.update_layout(
                            template="plotly_dark", paper_bgcolor="#141619", plot_bgcolor="#141619",
                            height=350, margin=dict(l=20, r=20, t=10, b=10),
                            barmode="group", yaxis=dict(title="Sharpe Ratio", gridcolor="#2a2c30"),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02),
                            font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif"),
                        )
                        st.plotly_chart(fig_bars, width="stretch")

                        # -- Chart 2: Sharpe decay line --
                        fig_decay = go.Figure()
                        fig_decay.add_trace(go.Scatter(x=fold_labels, y=decays, mode="lines+markers",
                            line=dict(color="#F1A100", width=2), marker=dict(size=10),
                            fill="tozeroy", fillcolor="rgba(241,161,0,0.1)"))
                        fig_decay.add_hline(y=0, line_dash="dash", line_color="#484f58")
                        avg_decay = summary.get("avg_sharpe_decay", 0)
                        fig_decay.add_hline(y=avg_decay, line_dash="dot", line_color="#FF4051",
                            annotation_text=f"Avg decay: {avg_decay:.2f}")
                        fig_decay.update_layout(
                            template="plotly_dark", paper_bgcolor="#141619", plot_bgcolor="#141619",
                            height=250, margin=dict(l=20, r=20, t=10, b=10),
                            yaxis=dict(title="Sharpe Decay (test - train)", gridcolor="#2a2c30"),
                            font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif"),
                        )
                        st.plotly_chart(fig_decay, width="stretch")

                        # -- Fold detail table --
                        fold_rows = []
                        for f in folds:
                            bp = f.get("best_params", {})
                            fold_rows.append({
                                "Fold": f["fold"],
                                "训练期": f.get("train_period", ""),
                                "测试期": f.get("test_period", ""),
                                "最优参数": ", ".join(f"{k}={v}" for k, v in bp.items()),
                                "训练Sharpe": f"{f.get('train_sharpe', 0):.2f}",
                                "测试Sharpe": f"{f.get('test_sharpe', 0):.2f}",
                                "Sharpe衰减": f"{f.get('sharpe_decay', 0):.2f}",
                                "测试收益": f"{f.get('test_return', 0):.2%}",
                                "测试胜率": f"{f.get('test_win_rate', 0):.1%}",
                            })
                        st.dataframe(pd.DataFrame(fold_rows), width="stretch", hide_index=True)

                    # -- Conclusion box --
                    conclusion = summary.get("conclusion", "")
                    avg_decay = summary.get("avg_sharpe_decay", 0)
                    param_stab = summary.get("param_stability", {})
                    overall_score = param_stab.get("overall_score", 0)

                    is_overfit = abs(avg_decay) > 1.0 or overall_score < 0.4
                    if is_overfit:
                        st.error(f"⚠️ **过拟合警告**: {conclusion}")
                        st.metric("Sharpe 衰减", f"{avg_decay:.2f}", delta=f"{avg_decay:.2f}", delta_color="inverse")
                        st.metric("参数稳定性", f"{overall_score:.0%}", delta=f"{overall_score:.0%}", delta_color="off")
                    else:
                        st.success(f"✅ **参数稳健**: {conclusion}")
                        st.metric("Sharpe 衰减", f"{avg_decay:.2f}")
                        st.metric("参数稳定性", f"{overall_score:.0%}")

            st.markdown('</div>', unsafe_allow_html=True)
        return  # WFA page done

    # ─────────────── 策略回测页面 (原有逻辑) ───────────────
    with left_col:
        st.markdown('<div class="ql-left-scroll">', unsafe_allow_html=True)  # reopen for backtest config
        st.markdown('<div class="section-header">⚙️ 策略配置</div>', unsafe_allow_html=True)

        signal = st.selectbox(
            "选股公式",
            ["tdx2_final (牛线突破+B1反转)", "tdx_resonance (双信号共振)",
             "tdx2_xg (涨停突破牛线)", "tdx2_b1 (底部反转B1)"],
            index=0,
        )

        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input("回测起始", datetime(2022, 1, 1))
        with d2:
            end_date = st.date_input("回测结束", datetime(2025, 12, 31))
        max_positions = st.slider("最大持仓", 1, 10, 3)
        position_pct = st.slider("仓位%", 10, 100, 30) / 100
        stop_loss = st.slider("止损%", -10.0, 0.0, -3.0) / 100
        take_profit = st.slider("止盈%", 1.0, 20.0, 5.0) / 100

        if st.button("🔄 开始回测", width='stretch', type="primary"):
            with st.spinner("正在运行回测，请稍候..."):
                # P1修复: 按钮实际执行回测，不再是只清缓存
                import subprocess, json as _json
                config = {
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "max_pos": max_positions,
                    "position_pct": position_pct,
                    "strategy": signal.split(" ")[0] if signal else "tdx2_final",
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d"),
                }
                cmd = [
                    sys.executable,
                    r"d:\quant_framework\run_backtest_fast.py",
                    "--config", _json.dumps(config, ensure_ascii=False),
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if result.returncode == 0:
                        st.success("✅ 回测完成！结果已保存到 trade_log.csv / equity_curve.csv")
                    else:
                        st.error(f"回测失败 (exit {result.returncode})")
                        if result.stderr:
                            st.text(result.stderr[-500:])
                except subprocess.TimeoutExpired:
                    st.error("回测超时 (10分钟)，请减少股票数量后重试")
                except Exception as e:
                    st.error(f"回测启动失败: {e}")
                st.cache_data.clear()
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

    # ---- Load Data (P2#6: 统一用 BacktestStore) ----
    sig_key = signal.split(" ")[0] if signal else "tdx2_final"
    data = load_data(signal_name=sig_key)

    if not data["available"] or data["equity"].empty:
        # P2#7: 不再回退到假数据，显示清晰指引
        st.warning("⚠️ 未找到回测数据。请点击左侧「🔄 开始回测」按钮生成，或在终端运行: python run_backtest_fast.py")
        st.stop()

    equity = data["equity"]["equity"] if "equity" in data["equity"].columns else data["equity"].iloc[:, 0]
    peak = equity.expanding().max()
    drawdown_series = (equity - peak) / peak
    trades = data["trades"]
    sentiment = data.get("sentiment", pd.DataFrame())

    # P3#12: 统一用 BacktestStore 的指标计算
    metrics = _store.compute_metrics(data["equity"], trades)

    # P2#8: 显示 cache 同步状态
    if not data.get("cache_synced", True):
        st.info("ℹ️ 因子缓存已更新，建议重新运行回测以同步数据。")

    with right_col:
        st.markdown('<div class="ql-right-scroll">', unsafe_allow_html=True)
        meta = data.get("meta", {})
        run_info = f"run: {meta.get('run_id', 'unknown')}" if meta.get('run_id') else "实时数据"
        st.success(f"✅ 已加载回测数据 ({run_info})")

        # ROW 1
        perf_col1, perf_col2 = st.columns([2, 3])
        with perf_col1:
            st.markdown('<div class="section-header">📊 策略回测分析</div>', unsafe_allow_html=True)
        with perf_col2:
            sig_label = signal.split("(")[-1].replace(")", "") if "(" in signal else signal
            st.markdown(f"<div style='color:#8b949e;font-size:12px;text-align:right;padding-top:12px'>通达信选股公式驱动 · T+1策略 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>", unsafe_allow_html=True)

        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
        with col1:
            st.metric("总收益率", f"{metrics.get('total_return', 0):+.2%}")
        with col2:
            st.metric("年化收益", f"{metrics.get('annual_return', 0):+.2%}")
        with col3:
            st.metric("夏普比率", f"{metrics.get('sharpe', 0):.2f}")
        with col4:
            st.metric("最大回撤", f"{metrics.get('max_drawdown', 0):.2%}")
        with col5:
            st.metric("卡玛比率", f"{metrics.get('calmar', 0):.2f}")
        with col6:
            st.metric("胜率", f"{metrics.get('win_rate', 0):.1%}")
        with col7:
            st.metric("盈亏比", f"{metrics.get('profit_factor', 0):.2f}")
        with col8:
            st.metric("交易次数", str(metrics.get("n_trades", 0)))

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("年化波动", f"{metrics.get('annual_volatility', 0):.2%}")
        with col2:
            st.metric("索提诺比率", f"{metrics.get('sortino', 0):.2f}")
        with col3:
            st.metric("最佳交易", f"{metrics.get('best_trade', 0):+.2%}")
        with col4:
            st.metric("最差交易", f"{metrics.get('worst_trade', 0):+.2%}")
        with col5:
            st.metric("盈利月份", f"{metrics.get('positive_months', 0):.0%}")
        with col6:
            st.metric("总盈亏(¥)", f"{metrics.get('total_pnl', 0):+,.0f}")

        # ROW 3
        st.markdown('<div class="section-header">💰 收益曲线 & 回撤</div>', unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["收益曲线", "回撤分析"])
        with tab1:
            fig_equity = build_equity_chart(equity, drawdown_series)
            st.plotly_chart(fig_equity, width='stretch')
        with tab2:
            col_a, col_b = st.columns([2, 1])
            with col_a:
                fig_dd = build_drawdown_chart(equity)
                st.plotly_chart(fig_dd, width='stretch')
            with col_b:
                if not trades.empty:
                    st.markdown("**最大回撤区间**")
                    peak_vals = equity.expanding().max()
                    dd_vals = (equity - peak_vals) / peak_vals
                    max_dd_idx = dd_vals.idxmin()
                    st.metric("最大回撤", f"{dd_vals.min():.2%}")
                    st.metric("最大回撤日", str(max_dd_idx.date()))
                    recovery = (dd_vals == 0) & (dd_vals.shift(1) < 0)
                    recovery_dates = dd_vals[recovery].index
                    st.metric("创新高次数", len(recovery_dates))

        # ROW 4
        st.markdown('<div class="section-header">📅 月度收益 & 分布分析</div>', unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("**月度收益热力图**")
            fig_monthly = build_monthly_heatmap(equity)
            if fig_monthly:
                st.plotly_chart(fig_monthly, width='stretch')
            else:
                st.info("数据不足，无法生成月度热力图")
        with col2:
            st.markdown("**收益分布直方图**")
            fig_dist = build_returns_distribution(trades)
            st.plotly_chart(fig_dist, width='stretch')

        # ROW 5
        st.markdown('<div class="section-header">📉 滚动指标 & 情绪叠加</div>', unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("**60日滚动夏普 & 滚动收益**")
            fig_rolling = build_rolling_metrics(equity)
            st.plotly_chart(fig_rolling, width='stretch')
        with col2:
            st.markdown("**策略收益 vs 市场情绪**")
            fig_sentiment = build_sentiment_overlay(equity, sentiment)
            if fig_sentiment:
                st.plotly_chart(fig_sentiment, width='stretch')
            else:
                st.info("情绪数据未加载，请先运行 run_sentiment.py")

        # ROW 6
        st.markdown('<div class="section-header">📋 交易分析</div>', unsafe_allow_html=True)
        if not trades.empty:
            tab1, tab2, tab3 = st.tabs(["周内分析", "交易明细", "退出方式"])
            with tab1:
                col_a, col_b = st.columns([1, 1])
                with col_a:
                    fig_weekly = build_weekly_analysis(trades)
                    st.plotly_chart(fig_weekly, width='stretch')
                with col_b:
                    st.markdown("**累计盈亏曲线**")
                    trades_sorted = trades.sort_values("buy_date")
                    cum_pnl = trades_sorted["net_profit"].cumsum() if "net_profit" in trades_sorted.columns else trades_sorted["return_pct"].cumsum() * 10000
                    fig_cum = go.Figure()
                    fig_cum.add_trace(go.Scatter(
                        x=trades_sorted["buy_date"], y=cum_pnl.values, mode="lines",
                        fill="tozeroy", line=dict(color="#409eff", width=1.5),
                        fillcolor="rgba(88,166,255,0.15)",
                    ))
                    fig_cum.update_layout(
                        template="plotly_dark", paper_bgcolor="#141619", plot_bgcolor="#141619",
                        height=300, margin=dict(l=20, r=20, t=10, b=10),
                        yaxis=dict(title="Cumulative P&L (CNY)", gridcolor="#2a2c30"),
                        font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif"),
                    )
                    st.plotly_chart(fig_cum, width='stretch')
            with tab2:
                st.dataframe(
                    trades.sort_values("buy_date", ascending=False).head(50),
                    column_config={
                        "symbol": "代码", "buy_date": "买入日",
                        "return_pct": st.column_config.NumberColumn("收益率", format="+.2%"),
                        "net_profit": st.column_config.NumberColumn("净盈亏", format="¥,.0f"),
                        "exit_type": "退出方式",
                    },
                    width='stretch', hide_index=True,
                )
            with tab3:
                exit_counts = trades["exit_type"].value_counts()
                fig_exit = go.Figure(data=[go.Pie(
                    labels=exit_counts.index, values=exit_counts.values, hole=0.5,
                    marker=dict(colors=["#409eff", "#FF4051", "#F1A100"]),
                )])
                fig_exit.update_layout(
                    template="plotly_dark", paper_bgcolor="#141619", plot_bgcolor="#141619",
                    height=350, margin=dict(l=20, r=20, t=10, b=10),
                    font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif"),
                )
                st.plotly_chart(fig_exit, width='stretch')
        else:
            st.info("暂无交易记录数据")

    # ROW 7: 参数对比 (对比功能 — 任务卡 #1)
    st.markdown('<div class="section-header">📊 参数对比</div>', unsafe_allow_html=True)
    runs = _store.list_runs()

    if len(runs) < 2:
        st.info(f"🔍 需要至少 2 次回测才能对比。当前 session 内已完成 {len(runs)} 次回测。请先运行回测（修改参数后多次点击「开始回测」）。")
    else:
        # 构建选择器选项
        run_options = {}
        for r in runs:
            cfg = r.get("config", {})
            label = (
                f"{r['run_id']} | {r.get('strategy', '?')} | "
                f"止损{cfg.get('stop_loss', cfg.get('stop_loss', '?'))} "
                f"止盈{cfg.get('take_profit', cfg.get('take_profit', '?'))} | "
                f"{r.get('n_trades', 0)}笔"
            )
            run_options[label] = r["run_id"]

        run_labels = list(run_options.keys())
        run_ids = list(run_options.values())

        col_a, col_b = st.columns(2)
        with col_a:
            idx_a = st.selectbox("回测 A", range(len(run_labels)), format_func=lambda i: run_labels[i], key="cmp_a")
        with col_b:
            default_b = min(1, len(run_labels) - 1)
            idx_b = st.selectbox("回测 B", range(len(run_labels)), format_func=lambda i: run_labels[i], index=default_b, key="cmp_b")

        run_a = _store.load_run(run_ids[idx_a]) if idx_a < len(run_ids) else None
        run_b = _store.load_run(run_ids[idx_b]) if idx_b < len(run_ids) else None
        meta_a = runs[idx_a] if idx_a < len(runs) else {}
        meta_b = runs[idx_b] if idx_b < len(runs) else {}

        if run_a and run_b and not run_a["equity"].empty and not run_b["equity"].empty:
            eq_a = run_a["equity"]["equity"] if "equity" in run_a["equity"].columns else run_a["equity"].iloc[:, 0]
            eq_b = run_b["equity"]["equity"] if "equity" in run_b["equity"].columns else run_b["equity"].iloc[:, 0]

            # -- 权益曲线叠加图 --
            from plotly.subplots import make_subplots
            fig_cmp = go.Figure()
            fig_cmp.add_trace(go.Scatter(
                x=eq_a.index, y=eq_a.values, mode="lines",
                name=f"A: {meta_a.get('run_id', '')}", line=dict(color="#58a6ff", width=2),
            ))
            fig_cmp.add_trace(go.Scatter(
                x=eq_b.index, y=eq_b.values, mode="lines",
                name=f"B: {meta_b.get('run_id', '')}", line=dict(color="#FF4051", width=2),
            ))
            fig_cmp.add_hline(y=1_000_000, line_dash="dash", line_color="#484f58", annotation_text="初始资金")
            fig_cmp.update_layout(
                template="plotly_dark", paper_bgcolor="#141619", plot_bgcolor="#141619",
                height=400, margin=dict(l=20, r=20, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis=dict(title="权益 (元)", gridcolor="#2a2c30"),
                font=dict(color="#b4b6b8", size=12, family="PingFang SC, Microsoft YaHei, sans-serif"),
            )
            st.plotly_chart(fig_cmp, width="stretch")

            # -- 指标对比表格 --
            ma = _store.compute_metrics(run_a["equity"], run_a.get("trades", pd.DataFrame()))
            mb = _store.compute_metrics(run_b["equity"], run_b.get("trades", pd.DataFrame()))

            cmp_rows = []
            metric_keys = [
                ("total_return", "总收益率"), ("annual_return", "年化收益"),
                ("max_drawdown", "最大回撤"), ("sharpe", "夏普比率"),
                ("win_rate", "胜率"), ("n_trades", "交易次数"),
                ("profit_factor", "盈亏比"), ("total_pnl", "总盈亏(¥)"),
            ]
            for key, label in metric_keys:
                va = ma.get(key, 0)
                vb = mb.get(key, 0)
                if key in ("total_return", "annual_return", "max_drawdown", "win_rate"):
                    va_str = f"{va:.2%}"
                    vb_str = f"{vb:.2%}"
                elif key == "total_pnl":
                    va_str = f"{va:+,.0f}"
                    vb_str = f"{vb:+,.0f}"
                elif key == "sharpe":
                    va_str = f"{va:.2f}"
                    vb_str = f"{vb:.2f}"
                else:
                    va_str = f"{va:.2f}" if isinstance(va, float) else str(va)
                    vb_str = f"{vb:.2f}" if isinstance(vb, float) else str(vb)

                # 高亮更优者
                if key in ("max_drawdown",):
                    better_a = va > vb  # drawdown: less negative is better
                elif key in ("total_pnl", "sharpe", "win_rate", "profit_factor"):
                    better_a = va > vb
                else:
                    better_a = va > vb

                style_a = "color:#27ae60;font-weight:600" if better_a and va != vb else ""
                style_b = "color:#27ae60;font-weight:600" if (not better_a) and va != vb else ""

                cmp_rows.append({
                    "指标": label,
                    "回测 A": va_str, "A_style": style_a,
                    "回测 B": vb_str, "B_style": style_b,
                })

            cmp_df = pd.DataFrame(cmp_rows)
            # Render as HTML table for colored cells
            cmp_html = "<table style='width:100%;border-collapse:collapse;color:#b4b6b8;font-size:13px'>"
            cmp_html += "<tr style='border-bottom:1px solid #30363d'><th style='text-align:left;padding:6px 12px'>指标</th><th style='text-align:right;padding:6px 12px'>回测 A</th><th style='text-align:right;padding:6px 12px'>回测 B</th></tr>"
            for _, row in cmp_df.iterrows():
                cmp_html += (
                    f"<tr style='border-bottom:1px solid #21262d'>"
                    f"<td style='padding:6px 12px'>{row['指标']}</td>"
                    f"<td style='text-align:right;padding:6px 12px;{row['A_style']}'>{row['回测 A']}</td>"
                    f"<td style='text-align:right;padding:6px 12px;{row['B_style']}'>{row['回测 B']}</td>"
                    f"</tr>"
                )
            cmp_html += "</table>"
            st.markdown(cmp_html, unsafe_allow_html=True)

            # 参数差异
            cfg_a = meta_a.get("config", {})
            cfg_b = meta_b.get("config", {})
            all_cfg_keys = set(list(cfg_a.keys()) + list(cfg_b.keys()))
            diffs = []
            for k in sorted(all_cfg_keys):
                va = cfg_a.get(k, "-")
                vb = cfg_b.get(k, "-")
                if va != vb:
                    diffs.append(f"**{k}**: {va} → {vb}")
            if diffs:
                st.caption("参数差异: " + " | ".join(diffs))
        else:
            st.warning("选中的回测数据无法加载，请确认对应文件存在。")

    st.divider()
    st.markdown(
        f"<div style='text-align:center; color:#484f58; font-size:12px;'>"
        f"Quant Framework v1.0 · A-Share T+1 Strategy · Data: 通达信日线 · Powered by Streamlit"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
