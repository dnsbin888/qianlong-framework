"""生成独立 HTML 回测报告 — 双击即可在浏览器打开，无需服务器。

P3#12: 统一使用 BacktestStore.compute_metrics() 计算指标。
"""
import sys, os, numpy as np, pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio

sys.path.insert(0, r"d:\quant_framework\src")

print("Generating standalone HTML report...")

# ---- Load data (P2#6: 用 BacktestStore) ----
from quant_framework.data.backtest_store import BacktestStore

store = BacktestStore(r"d:\quant_framework")
data = store.load_latest()

equity = data["equity"]
trades = data["trades"]
sentiment = data.get("sentiment", pd.DataFrame())
meta = data.get("meta", {})

eq_series = equity["equity"] if "equity" in equity.columns else (equity.iloc[:, 0] if not equity.empty else pd.Series())

# ---- Compute Metrics (P3#12: 统一) ----
m = store.compute_metrics(equity, trades)
total_ret = m["total_return"]
annual_ret = m["annual_return"]
sharpe = m["sharpe"]
max_dd = m["max_drawdown"]
calmar = m["calmar"]
wr = m["win_rate"]
best_t = m["best_trade"]
worst_t = m["worst_trade"]
pf = m["profit_factor"]
total_pnl = m["total_pnl"]
n_trades = m["n_trades"]

# Drawdown series for chart
dd = pd.Series()
if not eq_series.empty:
    peak = eq_series.expanding().max()
    dd = (eq_series - peak) / peak

# ---- Chart 1: Equity + Drawdown ----
fig1 = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
if not eq_series.empty:
    fig1.add_trace(go.Scatter(x=eq_series.index, y=eq_series.values, mode="lines",
        name="策略权益", line=dict(color="#58a6ff", width=1.5),
        fill="tozeroy", fillcolor="rgba(88,166,255,0.1)"), row=1, col=1)
    fig1.add_hline(y=eq_series.iloc[0], line_dash="dash", line_color="#484f58", row=1, col=1)
    if not dd.empty:
        fig1.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, mode="lines",
            name="回撤%", line=dict(color="#f85149", width=1),
            fill="tozeroy", fillcolor="rgba(248,81,73,0.15)"), row=2, col=1)
fig1.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
    height=450, hovermode="x unified", margin=dict(l=20, r=20, t=10, b=10))
fig1.update_yaxes(title_text="权益(元)", row=1, col=1, gridcolor="#21262d")
fig1.update_yaxes(title_text="回撤%", row=2, col=1, gridcolor="#21262d")
chart1 = pio.to_html(fig1, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})

# ---- Chart 2: Monthly ----
chart2 = ""
if not eq_series.empty:
    monthly = eq_series.resample("ME").last().pct_change().dropna()
    colors = ["#3fb950" if v > 0 else "#f85149" for v in monthly.values]
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=monthly.index, y=monthly.values * 100, marker_color=colors,
        text=[f"{v*100:+.2f}%" for v in monthly.values], textposition="outside", textfont=dict(size=9)))
    fig2.add_hline(y=0, line_color="#484f58")
    fig2.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=300, yaxis_title="月度收益%", margin=dict(l=20, r=20, t=10, b=10))
    fig2.update_yaxes(gridcolor="#21262d")
    chart2 = pio.to_html(fig2, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})

# ---- Chart 3: Distribution ----
chart3 = ""
if not trades.empty and "return_pct" in trades.columns:
    fig3 = go.Figure()
    fig3.add_trace(go.Histogram(x=rets * 100, nbinsx=30,
        marker_color=["#3fb950" if r > 0 else "#f85149" for r in rets], opacity=0.8))
    fig3.add_vline(x=rets.mean() * 100, line_dash="dash", line_color="#58a6ff",
        annotation_text=f"均值:{rets.mean()*100:+.2f}%")
    fig3.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=300, xaxis_title="收益率%", yaxis_title="次数", margin=dict(l=20, r=20, t=10, b=10))
    chart3 = pio.to_html(fig3, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})

# ---- Chart 4: Cumulative P&L ----
chart4 = ""
if not trades.empty and "net_profit" in trades.columns:
    t_sort = trades.sort_values("buy_date")
    cum = t_sort["net_profit"].cumsum()
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(x=t_sort["buy_date"], y=cum.values, mode="lines",
        fill="tozeroy", line=dict(color="#58a6ff", width=1.5), fillcolor="rgba(88,166,255,0.15)"))
    fig4.add_hline(y=0, line_color="#484f58")
    fig4.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=280, yaxis_title="累计盈亏(元)", margin=dict(l=20, r=20, t=10, b=10))
    chart4 = pio.to_html(fig4, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})

# ---- Chart 5: Sentiment ----
chart5 = ""
if not sentiment.empty and "limit_up" in sentiment.columns:
    fig5 = go.Figure()
    fig5.add_trace(go.Scatter(x=sentiment.index, y=sentiment["limit_up"].values,
        mode="lines", name="涨停", line=dict(color="#3fb950", width=1),
        fill="tozeroy", fillcolor="rgba(63,185,80,0.15)"))
    fig5.add_trace(go.Scatter(x=sentiment.index, y=-sentiment["limit_down"].values,
        mode="lines", name="跌停", line=dict(color="#f85149", width=1),
        fill="tozeroy", fillcolor="rgba(248,81,73,0.15)"))
    fig5.add_hline(y=0, line_color="#484f58")
    fig5.update_layout(template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        height=350, yaxis_title="家数", margin=dict(l=20, r=20, t=10, b=10), hovermode="x unified")
    chart5 = pio.to_html(fig5, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})

# ---- Trade Rows ----
trade_rows = ""
if not trades.empty:
    for _, row in trades.sort_values("buy_date", ascending=False).head(50).iterrows():
        ret_val = row.get("return_pct", 0)
        ret_str = f"{ret_val:+.2%}" if pd.notna(ret_val) else ""
        pnl_val = row.get("net_profit", 0)
        pnl_str = f"{pnl_val:+,.0f}" if pd.notna(pnl_val) else ""
        color = "#3fb950" if (isinstance(ret_val, (int, float)) and ret_val > 0) else "#f85149"
        trade_rows += f"""
        <tr>
            <td>{row.get("symbol", "")}</td>
            <td>{str(row.get("buy_date", ""))[:10]}</td>
            <td>{str(row.get("sell_date", ""))[:10]}</td>
            <td>{row.get("buy_price", "")}</td>
            <td style="color:{color}">{ret_str}</td>
            <td style="color:{color}">{pnl_str}</td>
            <td>{row.get("exit_type", "")}</td>
        </tr>"""

# ---- Build Full HTML ----
pos_class = lambda v: "pos" if v > 0 else "neg"

html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股量化策略分析报告</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Microsoft YaHei', -apple-system, sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }}
.header {{ text-align:center; padding:25px 0; border-bottom:2px solid #1e3a5f; margin-bottom:25px; }}
.header h1 {{ color:#58a6ff; font-size:26px; }}
.header p {{ color:#6b7280; font-size:13px; margin-top:5px; }}
.grid-8 {{ display:grid; grid-template-columns:repeat(8, 1fr); gap:10px; margin-bottom:20px; }}
.grid-4 {{ display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-bottom:20px; }}
.grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:15px; margin-bottom:20px; }}
.card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:15px; text-align:center; }}
.card .label {{ color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:1px; }}
.card .value {{ font-size:24px; font-weight:700; margin-top:5px; }}
.card .value.pos {{ color:#3fb950; }}
.card .value.neg {{ color:#f85149; }}
.chart-box {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px; margin-bottom:15px; }}
.chart-box h3 {{ color:#58a6ff; font-size:15px; margin-bottom:5px; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; font-size:13px; }}
th {{ background:#1e3a5f; color:#58a6ff; padding:10px; text-align:left; font-weight:600; }}
td {{ padding:8px 10px; border-bottom:1px solid #1a2733; }}
tr:hover {{ background:#1a2733; }}
.footer {{ text-align:center; padding:20px; color:#484f58; font-size:12px; border-top:1px solid #1e3a5f; margin-top:30px; }}
.nav {{ display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }}
.nav a {{ background:#161b22; color:#8b949e; border:1px solid #30363d; border-radius:6px; padding:8px 16px; text-decoration:none; font-size:13px; }}
.nav a:hover {{ background:#1f6feb; color:#fff; border-color:#1f6feb; }}
</style>
</head>
<body>

<div class="header">
    <h1>A股 T+1 短线量化策略 回测报告</h1>
    <p>公式1: 牛线突破 + B1底部反转 | 通达信日线数据 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</div>

<div class="nav">
    <a href="#overview">绩效概览</a>
    <a href="#charts">收益图表</a>
    <a href="#sentiment">市场情绪</a>
    <a href="#trades">交易明细</a>
</div>

<h2 id="overview" style="color:#58a6ff;margin-bottom:15px;">绩效概览</h2>
<div class="grid-8">
    <div class="card"><div class="label">总收益率</div><div class="value {pos_class(total_ret)}">{total_ret:+.2%}</div></div>
    <div class="card"><div class="label">年化收益</div><div class="value {pos_class(annual_ret)}">{annual_ret:+.2%}</div></div>
    <div class="card"><div class="label">夏普比率</div><div class="value">{sharpe:.2f}</div></div>
    <div class="card"><div class="label">最大回撤</div><div class="value neg">{max_dd:.2%}</div></div>
    <div class="card"><div class="label">卡玛比率</div><div class="value">{calmar:.2f}</div></div>
    <div class="card"><div class="label">胜率</div><div class="value {pos_class(wr-0.5)}">{wr:.1%}</div></div>
    <div class="card"><div class="label">盈亏比</div><div class="value">{pf:.2f}</div></div>
    <div class="card"><div class="label">交易次数</div><div class="value">{n_trades}</div></div>
</div>

<div class="grid-4">
    <div class="card"><div class="label">总盈亏</div><div class="value {pos_class(total_pnl)}">{total_pnl:+,.0f} 元</div></div>
    <div class="card"><div class="label">最佳单笔</div><div class="value pos">{best_t:+.2%}</div></div>
    <div class="card"><div class="label">最差单笔</div><div class="value neg">{worst_t:+.2%}</div></div>
    <div class="card"><div class="label">回测年限</div><div class="value">{years:.1f} 年</div></div>
</div>

<h2 id="charts" style="color:#58a6ff;margin-bottom:15px;">收益图表</h2>
<div class="chart-box"><h3>收益曲线 & 回撤</h3>{chart1}</div>
<div class="grid-2">
    <div class="chart-box"><h3>月度收益</h3>{chart2}</div>
    <div class="chart-box"><h3>收益分布</h3>{chart3}</div>
</div>
<div class="chart-box"><h3>累计盈亏曲线</h3>{chart4}</div>

<h2 id="sentiment" style="color:#58a6ff;margin-bottom:15px;">市场情绪</h2>
<div class="chart-box"><h3>每日涨停 / 跌停家数</h3>{chart5}</div>

<h2 id="trades" style="color:#58a6ff;margin-bottom:15px;">交易明细 (最近50笔)</h2>
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-x:auto;">
<table>
    <thead><tr><th>代码</th><th>买入日</th><th>卖出日</th><th>价格</th><th>收益率</th><th>盈亏</th><th>退出</th></tr></thead>
    <tbody>{trade_rows}</tbody>
</table>
</div>

<div class="footer">
    A股量化策略分析平台 v1.0 | 通达信日线数据 | T+1短线策略 | 公式1: 牛线突破+B1底部反转
</div>

</body>
</html>"""

output_path = r"d:\quant_framework\回测报告.html"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"Report generated: {output_path}")
print(f"Size: {len(html_content):,} bytes")
print(f"Trades: {n_trades}, Win Rate: {wr:.1%}, Total Return: {total_ret:+.2%}")
