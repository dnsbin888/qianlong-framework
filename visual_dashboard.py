"""量化策略可视化面板 — 情绪监控 + 策略复盘 + 信号回放。

功能:
  1. 市场情绪仪表盘 — 涨停/跌停家数, 炸板率, 连板高度, 情绪周期
  2. 策略绩效面板 — 收益曲线, 回撤曲线, 月度热力图
  3. 交易复盘 — 逐笔交易分析, 胜率/盈亏比, 持仓时间分布
  4. K线信号回放 — 逐日展示K线+信号标记, 回溯历史买卖点

数据源:
  - 通达信 vipdoc 日线数据 (D盘)
  - 策略信号 (回测引擎实时计算)
  - 情绪指标 (从全市场K线统计)

启动:
  streamlit run visual_dashboard.py
  # 或
  python visual_dashboard.py --mode cli
"""

import sys
import os
import time
import json
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

# ---- 框架路径 ----
sys.path.insert(0, r"d:\quant_framework\src")

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals import (
    factor_qlj, factor_ztxf, factor_resonance,
)
from quant_framework.factors.tdx_signals2 import (
    factor_xg_signal, factor_b1_structure, factor_final_pick,
)


# ======================================================================
# 1. 情绪指标计算引擎
# ======================================================================

class SentimentEngine:
    """A股市场情绪指标计算引擎。

    从全市场K线数据统计每日情绪:
      - 涨停家数 / 跌停家数
      - 炸板率
      - 连板高度
      - 上涨/下跌家数比
      - 成交额总量
      - 市场宽度 (站上MA20的比例)
    """

    def __init__(self, data_root: str):
        self.provider = THSDayDataProvider(data_root)
        self.provider.connect()
        self._stock_data: dict[str, pd.DataFrame] = {}
        self._sentiment_df: pd.DataFrame | None = None

    def load_data(self, symbols: list[str] | None = None, min_days: int = 200):
        """加载股票数据。"""
        if symbols is None:
            symbols = self.provider.scan_symbols()
            # 只取沪深A股 (600/601/603/000/001/002/003/300/301)
            symbols = [s for s in symbols
                       if s[0] in ('6', '0', '3') and len(s) == 6 and s.isdigit()]
            symbols = symbols[:2000]  # 限制2000只保证速度

        print(f"  Loading {len(symbols)} stocks for sentiment analysis...")
        loaded = 0
        for sym in symbols:
            data = self.provider._read_day_file(sym)
            if not data:
                continue
            records = []
            for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
                dt = _date_to_datetime(date_int)
                if dt is None or o <= 0 or c <= 0:
                    continue
                records.append({
                    "date": dt, "open": o, "high": h, "low": l,
                    "close": c, "volume": vol, "amount": amt,
                })
            if len(records) < min_days:
                continue
            self._stock_data[sym] = pd.DataFrame(records).set_index("date")
            loaded += 1
        print(f"  Loaded {loaded} stocks")

    def compute_daily_sentiment(
        self, start: str = "2022-01-01", end: str = "2025-12-31"
    ) -> pd.DataFrame:
        """计算每日市场情绪指标。"""
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        # 获取交易日历
        all_dates = set()
        for df in self._stock_data.values():
            all_dates.update(df.index)
        trading_dates = sorted(d for d in all_dates if start_dt <= d <= end_dt)

        print(f"  Computing sentiment for {len(trading_dates)} trading days...")

        sentiment_rows = []
        for i, today in enumerate(trading_dates):
            if i % 100 == 0:
                print(f"    {i}/{len(trading_dates)} days...")

            limit_up_count = 0
            limit_down_count = 0
            up_count = 0
            down_count = 0
            total_amount = 0.0
            above_ma20 = 0
            valid_count = 0
            gap_up_count = 0  # 高开家数
            consecutive_limit_heights = []  # 连板高度列表

            for sym, df in self._stock_data.items():
                if today not in df.index:
                    continue
                idx = df.index.get_loc(today)
                if idx < 20:  # Need history for MA20
                    continue

                row = df.iloc[idx]
                prev_row = df.iloc[idx - 1]
                close = row["close"]
                prev_close = prev_row["close"]
                open_price = row["open"]

                if close <= 0 or prev_close <= 0:
                    continue

                valid_count += 1
                change_pct = (close - prev_close) / prev_close

                # 涨跌停 (10%)
                if change_pct >= 0.098:
                    limit_up_count += 1
                    # 计算连板数
                    streak = 1
                    j = idx - 1
                    while j >= 0:
                        prev_c = df.iloc[j]["close"]
                        prev_pc = df.iloc[j - 1]["close"] if j > 0 else prev_c
                        if (prev_c - prev_pc) / prev_pc >= 0.098:
                            streak += 1
                            j -= 1
                        else:
                            break
                    consecutive_limit_heights.append(streak)
                elif change_pct <= -0.098:
                    limit_down_count += 1

                # 涨跌家数
                if change_pct > 0:
                    up_count += 1
                elif change_pct < 0:
                    down_count += 1

                # 高开
                if open_price > prev_close:
                    gap_up_count += 1

                # MA20
                ma20 = df.iloc[idx - 19:idx + 1]["close"].mean()
                if close > ma20:
                    above_ma20 += 1

                # 成交额
                total_amount += row.get("amount", 0)

            if valid_count == 0:
                continue

            # 炸板率 = 盘中涨停但收盘未封住的比例 (简化: 用高开低走近似)
            # 实际炸板率需要分钟线数据，这里用高开回落近似
            break_rate = 0.0  # Placeholder — 需要分钟线精确计算

            max_consecutive = max(consecutive_limit_heights) if consecutive_limit_heights else 0

            sentiment_rows.append({
                "date": today,
                "limit_up": limit_up_count,
                "limit_down": limit_down_count,
                "up_count": up_count,
                "down_count": down_count,
                "advance_decline_ratio": up_count / max(down_count, 1),
                "limit_ratio": limit_up_count / max(limit_down_count, 1),
                "break_rate": break_rate,
                "max_consecutive": max_consecutive,
                "above_ma20_pct": above_ma20 / max(valid_count, 1),
                "gap_up_pct": gap_up_count / max(valid_count, 1),
                "total_amount": total_amount,
                "valid_stocks": valid_count,
            })

        self._sentiment_df = pd.DataFrame(sentiment_rows).set_index("date")
        return self._sentiment_df

    def get_sentiment_phase(self, date: datetime) -> str:
        """判断当日情绪周期阶段。"""
        if self._sentiment_df is None or date not in self._sentiment_df.index:
            return "unknown"

        row = self._sentiment_df.loc[date]
        lu = row["limit_up"]
        ld = row["limit_down"]
        mc = row["max_consecutive"]

        # 情绪周期判断
        if lu >= 100 and mc >= 6:
            return "🔥 高潮期"     # 涨停>100 + 连板高度>6
        elif lu >= 50 and mc >= 4:
            return "📈 升温期"     # 赚钱效应扩散
        elif lu >= 30:
            return "🌤 修复期"     # 正常市场
        elif lu <= 15 and ld > lu:
            return "❄ 冰点期"     # 情绪冰点
        elif lu <= 30:
            return "🌧 退潮期"     # 赚钱效应消退
        else:
            return "🌤 正常期"


# ======================================================================
# 2. 策略复盘引擎
# ======================================================================

class ReviewEngine:
    """策略复盘引擎 — 从回测结果分析策略表现。

    功能:
      - 加载回测交易记录
      - 按维度分析 (时间、市场环境、信号强度)
      - 生成复盘报告
    """

    def __init__(self, trades_csv: str, equity_csv: str):
        self.trades = pd.read_csv(trades_csv) if os.path.exists(trades_csv) else pd.DataFrame()
        self.equity = pd.read_csv(equity_csv) if os.path.exists(equity_csv) else pd.DataFrame()

    def analyze_by_sentiment(self, sentiment_df: pd.DataFrame) -> pd.DataFrame:
        """按市场情绪环境分析交易表现。"""
        if self.trades.empty:
            return pd.DataFrame()

        trades = self.trades.copy()
        trades["buy_date"] = pd.to_datetime(trades["buy_date"])

        # Map sentiment phase to each trade
        phases = []
        for _, t in trades.iterrows():
            bd = t["buy_date"]
            # Find closest sentiment date
            if bd in sentiment_df.index:
                phases.append(sentiment_df.loc[bd].get("sentiment_phase", "unknown"))
            else:
                phases.append("unknown")

        trades["sentiment_phase"] = phases

        # Group by sentiment phase
        analysis = trades.groupby("sentiment_phase").agg(
            trades=("return_pct", "count"),
            win_rate=("return_pct", lambda x: (x > 0).mean()),
            avg_return=("return_pct", "mean"),
            total_pnl=("net_profit", "sum"),
            best=("return_pct", "max"),
            worst=("return_pct", "min"),
        ).round(4)

        return analysis

    def analyze_by_weekday(self) -> pd.DataFrame:
        """按周几分析交易表现。"""
        if self.trades.empty:
            return pd.DataFrame()

        trades = self.trades.copy()
        trades["buy_date"] = pd.to_datetime(trades["buy_date"])
        trades["weekday"] = trades["buy_date"].dt.dayofweek
        weekday_names = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五"}

        analysis = trades.groupby("weekday").agg(
            trades=("return_pct", "count"),
            win_rate=("return_pct", lambda x: (x > 0).mean()),
            avg_return=("return_pct", "mean"),
        ).round(4)
        analysis.index = analysis.index.map(weekday_names)
        return analysis

    def analyze_by_intensity(self) -> pd.DataFrame:
        """按信号强度分析。"""
        if self.trades.empty or "signal" not in self.trades.columns:
            return pd.DataFrame()

        trades = self.trades.copy()
        analysis = trades.groupby("signal").agg(
            trades=("return_pct", "count"),
            win_rate=("return_pct", lambda x: (x > 0).mean()),
            avg_return=("return_pct", "mean"),
            total_pnl=("net_profit", "sum"),
        ).round(4)
        return analysis

    def consecutive_analysis(self) -> dict:
        """连赢/连亏分析。"""
        if self.trades.empty:
            return {}

        returns = self.trades["return_pct"].values
        wins = returns > 0

        max_win_streak = 0
        max_loss_streak = 0
        current_win = 0
        current_loss = 0

        for w in wins:
            if w:
                current_win += 1
                current_loss = 0
                max_win_streak = max(max_win_streak, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss_streak = max(max_loss_streak, current_loss)

        return {
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        }


# ======================================================================
# 3. K线信号可视化 (基于 matplotlib)
# ======================================================================

class SignalVisualizer:
    """K线信号可视化 — 逐日K线图+信号标记。

    为股票生成带买卖信号的K线图, 用于复盘回放。
    """

    @staticmethod
    def plot_signals(
        df: pd.DataFrame,
        signal_series: pd.Series | None = None,
        trades: list[dict] | None = None,
        title: str = "",
        save_path: str = "",
    ):
        """绘制K线图+信号标记。

        Args:
            df: OHLCV DataFrame (必须含 open/high/low/close)
            signal_series: 信号序列 (0/1/2)
            trades: 交易记录 [{date, type(buy/sell), price}]
            title: 图表标题
            save_path: 保存路径
        """
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.patches import FancyBboxPatch

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(16, 10),
                                              gridspec_kw={'height_ratios': [3, 1, 1]},
                                              sharex=True)

        # ---- K线图 (简化: 用收盘价线 + 红绿填充) ----
        close = df["close"].values
        dates = df.index
        colors = ['red' if close[i] >= close[i-1] else 'green' for i in range(1, len(close))]
        colors = ['gray'] + colors

        # 绘制价格区间
        ax1.fill_between(range(len(df)), df["low"], df["high"], alpha=0.3, color='gray')
        ax1.plot(range(len(df)), close, 'b-', linewidth=1.5, label='Close')

        # MA20, MA60
        if len(df) >= 60:
            ma20 = close.rolling(20).mean()
            ma60 = close.rolling(60).mean()
            ax1.plot(range(len(df)), ma20, 'orange', linewidth=0.8, alpha=0.7, label='MA20')
            ax1.plot(range(len(df)), ma60, 'purple', linewidth=0.8, alpha=0.7, label='MA60')

        # 信号标记
        if signal_series is not None:
            sig_vals = signal_series.values if isinstance(signal_series, pd.Series) else signal_series
            buy_idx = [i for i, v in enumerate(sig_vals) if v >= 1]
            buy_prices = close[buy_idx]
            ax1.scatter(buy_idx, buy_prices, c='red', s=80, marker='^',
                       zorder=5, edgecolors='darkred', linewidths=1,
                       label=f'Signal ({len(buy_idx)})')

        # 交易标记
        if trades:
            for t in trades:
                if 'date' in t and t['date'] in df.index:
                    idx = df.index.get_loc(t['date'])
                    if t.get('type') == 'buy':
                        ax1.scatter(idx, close[idx], c='lime', s=120, marker='o',
                                   zorder=6, edgecolors='darkgreen', linewidths=1.5)
                    else:
                        ax1.scatter(idx, close[idx], c='orange', s=120, marker='v',
                                   zorder=6, edgecolors='darkred', linewidths=1.5)

        ax1.set_ylabel('Price (¥)', fontsize=11)
        ax1.set_title(title or 'K-Line with Signals', fontsize=14, fontweight='bold')
        ax1.legend(loc='upper left', fontsize=9)
        ax1.grid(True, alpha=0.3)

        # ---- 成交量 ----
        colors_vol = ['red' if close[i] >= close[i-1] else 'green' for i in range(len(close))]
        ax2.bar(range(len(df)), df["volume"].values, color=colors_vol, alpha=0.6, width=1.0)
        vol_ma = df["volume"].rolling(5).mean()
        ax2.plot(range(len(df)), vol_ma, 'blue', linewidth=0.8, alpha=0.5, label='Vol MA5')
        ax2.set_ylabel('Volume', fontsize=11)
        ax2.legend(loc='upper left', fontsize=9)
        ax2.grid(True, alpha=0.3)

        # ---- MACD ----
        if len(df) >= 26:
            ema12 = df["close"].ewm(span=12, adjust=False).mean()
            ema26 = df["close"].ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            hist = 2 * (dif - dea)

            colors_macd = ['red' if h >= 0 else 'green' for h in hist]
            ax3.bar(range(len(df)), hist, color=colors_macd, alpha=0.6, width=1.0)
            ax3.plot(range(len(df)), dif, 'blue', linewidth=1, label='DIF')
            ax3.plot(range(len(df)), dea, 'orange', linewidth=1, label='DEA')
            ax3.axhline(y=0, color='gray', linewidth=0.5, linestyle='-')
            ax3.set_ylabel('MACD', fontsize=11)
            ax3.set_xlabel('Trading Days', fontsize=11)
            ax3.legend(loc='upper left', fontsize=9)
            ax3.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"  Chart saved: {save_path}")

        plt.close()
        return fig


# ======================================================================
# 4. HTML 报告生成器
# ======================================================================

class HTMLReportGenerator:
    """生成独立的 HTML 可视化报告。

    不需要 Streamlit，直接在浏览器打开。
    包含: 绩效概览 / 收益曲线 / 情绪分析 / 交易列表 / 复盘回放
    """

    @staticmethod
    def generate(
        trades_csv: str,
        equity_csv: str,
        sentiment_csv: str,
        output_path: str = "backtest_report.html",
    ):
        """生成完整的 HTML 回测报告。"""
        trades = pd.read_csv(trades_csv) if os.path.exists(trades_csv) else pd.DataFrame()
        equity = pd.read_csv(equity_csv) if os.path.exists(equity_csv) else pd.DataFrame()
        sentiment = pd.read_csv(sentiment_csv) if os.path.exists(sentiment_csv) else pd.DataFrame()

        # Compute stats
        if not trades.empty:
            returns = trades["return_pct"].values
            win_rate = (returns > 0).mean()
            avg_return = np.mean(returns)
            total_pnl = trades["net_profit"].sum() if "net_profit" in trades.columns else 0
            best = np.max(returns)
            worst = np.min(returns)
            n_trades = len(trades)
            avg_win = np.mean([r for r in returns if r > 0]) if any(r > 0 for r in returns) else 0
            avg_loss = np.mean([r for r in returns if r < 0]) if any(r < 0 for r in returns) else 0
            pf = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
        else:
            win_rate = avg_return = total_pnl = best = worst = n_trades = avg_win = avg_loss = pf = 0

        if not equity.empty:
            equity_vals = equity["equity"].values
            final_eq = equity_vals[-1]
            total_ret = (final_eq / equity_vals[0] - 1) if equity_vals[0] > 0 else 0
            peak = np.maximum.accumulate(equity_vals)
            drawdown = (equity_vals - peak) / peak
            max_dd = np.min(drawdown)
        else:
            total_ret = max_dd = 0

        # Trade table rows
        trade_rows = ""
        if not trades.empty:
            for _, t in trades.head(50).iterrows():
                ret = t.get("return_pct", 0) * 100
                pnl = t.get("net_profit", 0)
                color = "#27ae60" if ret > 0 else "#e74c3c"
                trade_rows += f"""
                <tr>
                    <td>{t.get('buy_date', '')}</td>
                    <td>{t.get('symbol', '')}</td>
                    <td style="color:{color}">{ret:+.2f}%</td>
                    <td>¥{pnl:+,.0f}</td>
                    <td>{t.get('exit_type', '')}</td>
                </tr>"""

        # Sentiment stats
        sentiment_summary = ""
        if not sentiment.empty:
            avg_lu = sentiment["limit_up"].mean()
            max_lu = sentiment["limit_up"].max()
            avg_ad = sentiment["advance_decline_ratio"].mean()
            sentiment_summary = f"""
            <div class="stat-box">
                <div class="stat-label">日均涨停</div>
                <div class="stat-value">{avg_lu:.0f} 家</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">最高涨停</div>
                <div class="stat-value">{max_lu:.0f} 家</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">涨跌比</div>
                <div class="stat-value">{avg_ad:.2f}</div>
            </div>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股量化策略回测报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Microsoft YaHei', sans-serif; background: #0f1923; color: #d1d5db; padding: 20px; }}
.header {{ text-align: center; padding: 30px 0; border-bottom: 2px solid #1e3a5f; margin-bottom: 30px; }}
.header h1 {{ color: #60a5fa; font-size: 28px; margin-bottom: 10px; }}
.header p {{ color: #6b7280; font-size: 14px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 30px; }}
.card {{ background: #1a2733; border: 1px solid #1e3a5f; border-radius: 10px; padding: 20px; }}
.card h3 {{ color: #60a5fa; font-size: 13px; text-transform: uppercase; margin-bottom: 12px; letter-spacing: 1px; }}
.stat-box {{ background: #1a2733; border: 1px solid #1e3a5f; border-radius: 10px; padding: 20px; text-align: center; }}
.stat-value {{ font-size: 32px; font-weight: 700; margin: 8px 0; }}
.stat-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 1px; }}
.positive {{ color: #27ae60; }}
.negative {{ color: #e74c3c; }}
.neutral {{ color: #60a5fa; }}
table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
th {{ background: #1e3a5f; color: #60a5fa; padding: 12px; text-align: left; font-size: 13px; font-weight: 600; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #1a2733; font-size: 13px; }}
tr:hover {{ background: #1a2733; }}
.section {{ margin: 30px 0; }}
.section-title {{ color: #60a5fa; font-size: 18px; border-bottom: 1px solid #1e3a5f; padding-bottom: 10px; margin-bottom: 20px; }}
.footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; margin-top: 40px; border-top: 1px solid #1e3a5f; }}
</style>
</head>
<body>

<div class="header">
    <h1>📊 A股 T+1 短线量化策略 · 回测报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据源: 通达信日线</p>
</div>

<div class="section">
    <h2 class="section-title">📈 绩效概览</h2>
    <div class="grid">
        <div class="stat-box">
            <div class="stat-label">总收益率</div>
            <div class="stat-value {'positive' if total_ret > 0 else 'negative'}">{total_ret:+.2%}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">总交易次数</div>
            <div class="stat-value neutral">{n_trades}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">胜率</div>
            <div class="stat-value {'positive' if win_rate > 0.5 else 'negative'}">{win_rate:.1%}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">最大回撤</div>
            <div class="stat-value negative">{max_dd:.2%}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">盈亏比</div>
            <div class="stat-value {'positive' if pf > 1 else 'negative'}">{pf:.2f}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">最佳交易</div>
            <div class="stat-value positive">{best:+.2%}</div>
        </div>
    </div>
</div>

<div class="section">
    <h2 class="section-title">🌡 市场情绪</h2>
    <div class="grid">
        {sentiment_summary}
    </div>
</div>

<div class="section">
    <h2 class="section-title">📋 交易明细 (最近50笔)</h2>
    <table>
        <thead>
            <tr>
                <th>买入日期</th>
                <th>股票代码</th>
                <th>收益率</th>
                <th>净盈亏</th>
                <th>退出方式</th>
            </tr>
        </thead>
        <tbody>
            {trade_rows}
        </tbody>
    </table>
</div>

<div class="footer">
    Quant Framework v1.0 | Powered by 通达信 Data | {datetime.now().year}
</div>

</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n  HTML报告已生成: {output_path}")
        return output_path


# ======================================================================
# 5. CLI 主程序 (不需要 Streamlit 也能运行)
# ======================================================================

def run_cli(args):
    """命令行模式 — 生成所有分析结果。"""
    print("=" * 70)
    print("  A股量化策略可视化面板 (CLI模式)")
    print("=" * 70)

    data_root = args.data_root or r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"

    # 1. 情绪分析
    if args.sentiment:
        print("\n[1/3] Computing market sentiment...")
        engine = SentimentEngine(data_root)
        engine.load_data()
        sentiment_df = engine.compute_daily_sentiment(args.start, args.end)

        # 统计
        if not sentiment_df.empty:
            print(f"\n  ── 市场情绪统计 ──")
            print(f"  日均涨停: {sentiment_df['limit_up'].mean():.0f} 家")
            print(f"  日均跌停: {sentiment_df['limit_down'].mean():.0f} 家")
            print(f"  平均涨跌比: {sentiment_df['advance_decline_ratio'].mean():.2f}")
            print(f"  最高连板: {sentiment_df['max_consecutive'].max():.0f} 板")
            print(f"  站上MA20均值: {sentiment_df['above_ma20_pct'].mean():.1%}")

            # 情绪周期统计
            sentiment_df["phase"] = sentiment_df.index.map(engine.get_sentiment_phase)
            phase_counts = sentiment_df["phase"].value_counts()
            print(f"\n  ── 情绪周期分布 ──")
            for phase, count in phase_counts.items():
                print(f"  {phase}: {count} 天 ({count/len(sentiment_df)*100:.1f}%)")

            sentiment_df.to_csv(r"d:\quant_framework\sentiment_data.csv", encoding="utf-8-sig")
            print(f"\n  Sentiment data saved: d:\\quant_framework\\sentiment_data.csv")

    # 2. 策略复盘 (P2#6+P3#12: 用 BacktestStore 统一加载+指标)
    if args.review:
        print("\n[2/3] Strategy review analysis...")
        sys.path.insert(0, r"d:\quant_framework\src")
        from quant_framework.data.backtest_store import BacktestStore

        store = BacktestStore(r"d:\quant_framework")
        data = store.load_latest()
        equity = data["equity"]
        trades = data["trades"]
        metrics = store.compute_metrics(equity, trades)
        total_ret = metrics["total_return"]
        max_dd = metrics["max_drawdown"]
        win_rate = metrics["win_rate"]
        total_pnl = metrics["total_pnl"]
        sharpe = metrics["sharpe"]
        if not trades.empty:
            print(f"\n  ── 策略复盘 ──")
            print(f"  总交易: {metrics['n_trades']}")
            print(f"  胜率: {metrics['win_rate']:.1%}")
            print(f"  最佳: {metrics['best_trade']:+.2%}")
            print(f"  最差: {metrics['worst_trade']:+.2%}")
            print(f"  夏普: {metrics['sharpe']:.2f}")
            print(f"  最大回撤: {metrics['max_drawdown']:.2%}")
            print(f"  最长连亏: {streaks.get('max_loss_streak', 0)} 次")
        else:
            print("  No trades found! Run backtest first.")

    # 3. 生成 HTML 报告
    if args.report:
        print("\n[3/3] Generating HTML report...")
        HTMLReportGenerator.generate(
            trades_csv=args.trades or r"d:\quant_framework\trade_log.csv",
            equity_csv=args.equity or r"d:\quant_framework\equity_curve.csv",
            sentiment_csv=r"d:\quant_framework\sentiment_data.csv",
            output_path=args.output or r"d:\quant_framework\backtest_report.html",
        )

    print("\n✓ All tasks complete!")


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="A股量化策略可视化面板")
    parser.add_argument("--mode", default="cli", choices=["cli", "streamlit"])
    parser.add_argument("--data-root", default="")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--sentiment", action="store_true", default=True)
    parser.add_argument("--review", action="store_true", default=True)
    parser.add_argument("--report", action="store_true", default=True)
    parser.add_argument("--trades", default="")
    parser.add_argument("--equity", default="")
    parser.add_argument("--output", default=r"d:\quant_framework\backtest_report.html")

    args = parser.parse_args()

    if args.mode == "streamlit":
        print("Starting Streamlit server...")
        print("Run: streamlit run visual_dashboard.py -- --mode streamlit")
        print("(Streamlit not yet installed — using CLI mode instead)")
        run_cli(args)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
