"""T+1 短线隔日冲策略回测 — A股全市场。

回测流程:
  1. 扫描 TDX vipdoc 数据目录, 加载所有 A 股日线数据
  2. 对每只股票逐日计算选股信号
  3. T日收盘信号触发 → T日尾盘买入 → T+1日开盘卖出
  4. 记录每笔交易 → 计算收益曲线和绩效指标

信号源 (命令行 --signal 切换):
  - tdx2_final:    公式1 — 牛线突破 + B1底部反转 (你发的第一条公式)
  - tdx_resonance: 公式2 — 双信号共振: 擒龙决 AND 涨停先锋 (你发的第二条公式)
  - tdx2_xg:       公式1子信号 — 涨停突破牛线 + MACD多头
  - tdx2_b1:       公式1子信号 — 底部反转结构 B1

用法:
  python run_t1_backtest.py                        # 默认: 公式1, 2022-2025
  python run_t1_backtest.py --signal tdx_resonance # 公式2: 双信号共振
  python run_t1_backtest.py --start 2023-01-01 --end 2025-12-31
  python run_t1_backtest.py --max-positions 5 --position-pct 0.20
"""

import sys
import os
sys.path.insert(0, r"d:\quant_framework\src")

# Fix encoding for Windows GBK terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import argparse
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals import (
    factor_qlj, factor_ztxf, factor_resonance,
)
from quant_framework.factors.tdx_signals2 import (
    factor_xg_signal, factor_b1_structure, factor_final_pick,
)


# ======================================================================
# Configuration
# ======================================================================

@dataclass
class BacktestConfig:
    """回测配置。"""
    signal_name: str = "tdx2_final"      # 信号因子
    start_date: str = "2022-01-01"       # 回测起始
    end_date: str = "2025-12-31"         # 回测结束
    initial_cash: float = 1_000_000.0    # 初始资金
    max_positions: int = 3               # 最大持仓数
    position_pct: float = 0.30           # 单票仓位 (可用资金%)
    stop_loss: float = -0.03             # 止损 (-3%)
    take_profit: float = 0.05            # 止盈 (+5%)
    commission_rate: float = 0.0003      # 佣金 万三
    stamp_duty: float = 0.001            # 印花税 千一 (仅卖出)
    slippage: float = 0.001              # 滑点 0.1%
    min_days: int = 200                  # 最少历史K线要求
    data_root: str = ""                  # 数据目录


# ======================================================================
# Signal computers
# ======================================================================

SIGNAL_FUNCTIONS = {
    "tdx2_xg":        ("涨停突破牛线(XG)", factor_xg_signal),
    "tdx2_b1":        ("底部反转结构(B1)", factor_b1_structure),
    "tdx2_final":     ("牛线突破+B1反转(公式1)", factor_final_pick),
    "tdx_qlj":        ("擒龙决", factor_qlj),
    "tdx_ztxf":       ("涨停先锋", factor_ztxf),
    "tdx_resonance":  ("双信号共振(公式2)", factor_resonance),
}


# ======================================================================
# Trade record
# ======================================================================

@dataclass
class Trade:
    symbol: str
    buy_date: datetime
    sell_date: datetime
    buy_price: float
    sell_price: float
    volume: int
    return_pct: float
    net_profit: float
    exit_type: str          # "normal" | "stop_loss" | "take_profit" | "limit_down"
    signal_name: str = ""


# ======================================================================
# Backtest Engine
# ======================================================================

class T1BacktestEngine:
    """T+1 短线隔日冲回测引擎。

    逐日遍历全市场K线数据，模拟:
      - T日信号触发 → 以收盘价买入
      - T+1日 → 以开盘价卖出
      - T+1 约束: 当日买入当日不可卖
    """

    def __init__(self, config: BacktestConfig, data_root: str):
        self.cfg = config
        self.data_root = data_root

        # 获取信号函数
        self.signal_label, self.signal_fn = SIGNAL_FUNCTIONS[config.signal_name]

        # 加载数据
        self._provider = THSDayDataProvider(data_root)
        self._provider.connect()

        # 状态
        self.cash = config.initial_cash
        self.holdings: dict[str, dict] = {}          # symbol → holding info
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self.daily_pnl: list[dict] = []

        # 预处理: 加载所有股票数据到内存
        self._stock_data: dict[str, pd.DataFrame] = {}

    # ==================================================================
    # Data loading
    # ==================================================================

    def load_data(self) -> int:
        """加载所有A股日线数据。返回有效股票数。"""
        print(f"\n[1/4] Loading A-share daily data from: {self.data_root}")
        t0 = time.time()

        all_symbols = self._provider.scan_symbols()
        print(f"  Total .day files found: {len(all_symbols)}")

        loaded = 0
        skipped_no_data = 0
        skipped_short = 0
        start_dt = datetime.strptime(self.cfg.start_date, "%Y-%m-%d")
        # 需要回溯数据至少 min_days 天
        min_data_start = start_dt - timedelta(days=self.cfg.min_days * 2)

        for i, sym in enumerate(all_symbols):
            if i % 500 == 0:
                print(f"  Loading... {i}/{len(all_symbols)} ({loaded} valid)")

            data = self._provider._read_day_file(sym)
            if not data:
                skipped_no_data += 1
                continue

            # Convert to DataFrame
            records = []
            for date_int, (o, h, l, c, amt, vol) in data.items():
                dt = _date_to_datetime(date_int)
                if dt is None:
                    continue
                if o <= 0 or c <= 0:
                    continue
                records.append({
                    "date": dt,
                    "open": o, "high": h, "low": l,
                    "close": c, "volume": vol, "amount": amt,
                })

            if not records:
                skipped_no_data += 1
                continue

            df = pd.DataFrame(records).sort_values("date").set_index("date")

            # Check minimum history
            df_in_range = df[df.index >= min_data_start]
            if len(df_in_range) < self.cfg.min_days:
                skipped_short += 1
                continue

            self._stock_data[sym] = df
            loaded += 1

        elapsed = time.time() - t0
        print(f"  Loaded: {loaded} stocks ({skipped_no_data} no-data, {skipped_short} too-short)")
        print(f"  Time: {elapsed:.1f}s")
        return loaded

    # ==================================================================
    # Run backtest
    # ==================================================================

    def run(self):
        """执行回测。"""
        print(f"\n[2/4] Running backtest: {self.cfg.start_date} → {self.cfg.end_date}")
        print(f"  Signal: {self.signal_label}")
        print(f"  Max positions: {self.cfg.max_positions}, Position size: {self.cfg.position_pct:.0%}")
        print(f"  Stop loss: {self.cfg.stop_loss:.0%}, Take profit: {self.cfg.take_profit:.0%}")

        # Generate trading calendar
        start_dt = datetime.strptime(self.cfg.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(self.cfg.end_date, "%Y-%m-%d")

        # Use the data from the stock with most dates to create calendar
        trading_dates = self._build_calendar(start_dt, end_dt)
        print(f"  Trading days: {len(trading_dates)}")

        # State tracking
        self.cash = self.cfg.initial_cash
        self.holdings = {}
        self.trades = []
        self.equity_curve = []
        buy_queue: dict[str, dict] = {}  # T日买入队列 → T+1日卖出

        total_days = len(trading_dates)
        signal_count = 0
        trade_count = 0

        for day_idx, today in enumerate(trading_dates):
            if day_idx % 50 == 0:
                pct = day_idx / total_days * 100
                print(f"  Progress: {day_idx}/{total_days} ({pct:.0f}%) | "
                      f"Trades: {trade_count} | Signals: {signal_count} | "
                      f"Cash: ¥{self.cash:,.0f}")

            # ---- Step 1: Process T+1 sells (from yesterday's buy queue) ----
            sell_proceeds = 0.0
            for symbol, buy_info in list(buy_queue.items()):
                sell_result = self._execute_sell(symbol, buy_info, today)
                if sell_result is not None:
                    sell_proceeds += sell_result
                    trade_count += 1
                    del buy_queue[symbol]

            if sell_proceeds > 0:
                self.cash += sell_proceeds

            # ---- Step 2: Compute signals for today (close of day) ----
            today_signals: list[tuple[str, int, float]] = []  # [(symbol, intensity, close)]

            for symbol, df in self._stock_data.items():
                if symbol in self.holdings or symbol in buy_queue:
                    continue  # Already holding or pending

                # Get data up to today
                df_up_to_today = df[df.index <= today]
                if len(df_up_to_today) < self.cfg.min_days:
                    continue

                try:
                    sig_val = self.signal_fn(df_up_to_today)
                    if isinstance(sig_val, pd.Series):
                        sig = sig_val.iloc[-1]
                        if not pd.isna(sig) and sig > 0:
                            close_price = df_up_to_today["close"].iloc[-1]
                            today_signals.append((symbol, int(sig), close_price))
                except Exception:
                    continue

            # ---- Step 3: Rank signals and buy ----
            if today_signals:
                signal_count += len(today_signals)

                # Sort by intensity (higher = stronger)
                today_signals.sort(key=lambda x: x[1], reverse=True)

                # Available slots
                slots = self.cfg.max_positions - len(buy_queue) - len(self.holdings)
                if slots > 0:
                    for symbol, intensity, close_price in today_signals[:slots]:
                        buy_info = self._execute_buy(symbol, intensity, close_price, today)
                        if buy_info is not None:
                            buy_queue[symbol] = buy_info

            # ---- Step 4: Record equity ----
            market_value = sum(
                h.get("current_value", h["cost"])
                for h in self.holdings.values()
            )
            pending_value = sum(
                b.get("cost", 0) for b in buy_queue.values()
            )
            total_equity = self.cash + market_value + pending_value

            self.equity_curve.append({
                "date": today,
                "equity": total_equity,
                "cash": self.cash,
                "market_value": market_value,
                "positions": len(self.holdings) + len(buy_queue),
            })

        # ---- End of loop ----
        # Force close any remaining positions at last day
        last_day = trading_dates[-1]
        for symbol, buy_info in list(buy_queue.items()):
            self._execute_sell(symbol, buy_info, last_day, force_close=True)
            trade_count += 1

        print(f"\n  Backtest complete: {trade_count} trades, {signal_count} signals")
        self._final_equity = total_equity
        self._trade_count = trade_count
        self._signal_count = signal_count

    # ==================================================================
    # Buy / Sell execution
    # ==================================================================

    def _execute_buy(
        self, symbol: str, intensity: int, close_price: float, date: datetime
    ) -> dict | None:
        """模拟T日尾盘买入。"""
        # Check available cash
        available_cash = self.cash * self.cfg.position_pct
        # Reserve for commission + stamp duty
        estimated_cost_per_share = close_price * (1 + self.cfg.slippage + self.cfg.commission_rate)
        volume = int(available_cash / estimated_cost_per_share / 100) * 100  # Round to lot

        if volume < 100:
            return None  # Not enough cash for 1 lot

        buy_price = close_price * (1 + self.cfg.slippage)
        total_cost = buy_price * volume * (1 + self.cfg.commission_rate)
        total_cost = max(total_cost, buy_price * volume + self.cfg.commission_rate * 10)

        if total_cost > self.cash * 0.35:  # Don't use more than 35% cash
            volume = int(self.cash * 0.35 / buy_price / 100) * 100
            if volume < 100:
                return None
            total_cost = buy_price * volume * (1 + self.cfg.commission_rate)

        if total_cost > self.cash:
            return None

        # Deduct cash
        self.cash -= total_cost

        return {
            "symbol": symbol,
            "buy_price": buy_price,
            "volume": volume,
            "cost": total_cost,
            "buy_date": date,
            "intensity": intensity,
        }

    def _execute_sell(
        self, symbol: str, buy_info: dict, date: datetime, force_close: bool = False
    ) -> float | None:
        """模拟T+1日开盘卖出。返回卖出现金。"""
        # Get today's open price for this symbol
        df = self._stock_data.get(symbol)
        if df is None:
            return buy_info["cost"] * 0.9  # Fallback: assume 10% loss

        df_today = df[df.index == date]
        if df_today.empty:
            if force_close:
                return buy_info["cost"] * 0.95  # Assume 5% loss on force close
            return None

        open_price = df_today["open"].iloc[0]
        buy_price = buy_info["buy_price"]
        volume = buy_info["volume"]

        # Calculate return
        sell_price = open_price * (1 - self.cfg.slippage)
        return_pct = (sell_price - buy_price) / buy_price

        # Exit type
        exit_type = "normal"
        if return_pct <= self.cfg.stop_loss:
            exit_type = "stop_loss"
        elif return_pct >= self.cfg.take_profit:
            exit_type = "take_profit"

        # Check limit-down (approximate: open = prev_close * 0.90)
        prev_close = df.iloc[-1]["close"] if len(df) > 0 else buy_price
        if abs(open_price - prev_close * 0.90) < 0.01 and not force_close:
            # Limit down - can't sell, hold
            return None

        # Calculate proceeds
        gross_proceeds = sell_price * volume
        commission = max(gross_proceeds * self.cfg.commission_rate, 5.0)
        stamp = gross_proceeds * self.cfg.stamp_duty
        net_proceeds = gross_proceeds - commission - stamp
        net_profit = net_proceeds - buy_info["cost"]

        # Record trade
        trade = Trade(
            symbol=symbol,
            buy_date=buy_info["buy_date"],
            sell_date=date,
            buy_price=buy_price,
            sell_price=sell_price,
            volume=volume,
            return_pct=return_pct,
            net_profit=net_profit,
            exit_type=exit_type,
            signal_name=self.cfg.signal_name,
        )
        self.trades.append(trade)

        return net_proceeds

    # ==================================================================
    # Calendar
    # ==================================================================

    def _build_calendar(self, start: datetime, end: datetime) -> list[datetime]:
        """构建交易日历。使用数据中最多日期的股票来推导。"""
        # Find the stock with most trading days
        best_dates = set()
        for df in self._stock_data.values():
            dates_in_range = set(df.index[(df.index >= start) & (df.index <= end)])
            if len(dates_in_range) > len(best_dates):
                best_dates = dates_in_range

        return sorted(best_dates)

    # ==================================================================
    # Report
    # ==================================================================

    def generate_report(self):
        """生成回测报告。"""
        print(f"\n[3/4] Generating performance report...")
        print("=" * 70)
        print(f"  A-Share T+1 Scalp Backtest Report")
        print("=" * 70)
        print(f"  Signal:      {self.signal_label}")
        print(f"  Period:      {self.cfg.start_date} → {self.cfg.end_date}")
        print(f"  Initial:     ¥{self.cfg.initial_cash:,.0f}")
        print(f"  Max Hold:    {self.cfg.max_positions}")
        print(f"  Position:    {self.cfg.position_pct:.0%} per trade")
        print(f"  Stop Loss:   {self.cfg.stop_loss:.0%}")
        print(f"  Take Profit: {self.cfg.take_profit:.0%}")
        print("-" * 70)

        trades = self.trades
        if not trades:
            print("\n  ⚠ No trades executed! Signal may be too rare.")
            print("  Try: --signal tdx2_xg (uses only XG, broader signal)")
            print("       --signal tdx_resonance (different formula)")
            print("       --start 2020-01-01 (longer period)")
            return

        # ---- Trade Statistics ----
        returns = [t.return_pct for t in trades]
        profits = [t.net_profit for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        total_return = sum(profits)
        win_rate = len(wins) / len(returns) if returns else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
        best_trade = max(returns)
        worst_trade = min(returns)

        print(f"\n  ── Trade Summary ──")
        print(f"  Total Trades:     {len(trades):>8d}")
        print(f"  Winning Trades:   {len(wins):>8d}  ({win_rate:.1%})")
        print(f"  Losing Trades:    {len(losses):>8d}  ({1-win_rate:.1%})")
        print(f"  Avg Win:          {avg_win:>8.2%}")
        print(f"  Avg Loss:         {avg_loss:>8.2%}")
        print(f"  Profit Factor:    {profit_factor:>8.2f}")
        print(f"  Best Trade:       {best_trade:>8.2%}")
        print(f"  Worst Trade:      {worst_trade:>8.2%}")

        # ---- Profit & Loss ----
        gross_profit = sum(p for p in profits if p > 0)
        gross_loss = sum(p for p in profits if p < 0)
        net_pnl = sum(profits)
        total_commission = sum(
            t.buy_price * t.volume * self.cfg.commission_rate +
            t.sell_price * t.volume * (self.cfg.commission_rate + self.cfg.stamp_duty)
            for t in trades
        )

        print(f"\n  ── P&L ──")
        print(f"  Gross Profit:     ¥{gross_profit:>10,.0f}")
        print(f"  Gross Loss:       ¥{gross_loss:>10,.0f}")
        print(f"  Net P&L:          ¥{net_pnl:>10,.0f}")
        print(f"  Total Commission: ¥{total_commission:>10,.0f}")
        print(f"  Final Cash:       ¥{self.cash:>10,.0f}")

        # ---- Exit Type Distribution ----
        exit_counts = defaultdict(int)
        for t in trades:
            exit_counts[t.exit_type] += 1
        print(f"\n  ── Exit Types ──")
        for exit_type, count in sorted(exit_counts.items()):
            avg_ret = np.mean([t.return_pct for t in trades if t.exit_type == exit_type])
            print(f"  {exit_type:<15s}: {count:>5d}  (avg return: {avg_ret:>7.2%})")

        # ---- Equity Curve Metrics ----
        if self.equity_curve:
            eq_df = pd.DataFrame(self.equity_curve)
            eq_df.set_index("date", inplace=True)
            equity = eq_df["equity"]

            final_equity = equity.iloc[-1]
            total_return_pct = (final_equity / self.cfg.initial_cash) - 1

            # Annual return
            days = (equity.index[-1] - equity.index[0]).days
            years = days / 365.25
            annual_return = (final_equity / self.cfg.initial_cash) ** (1 / years) - 1 if years > 0 else 0

            # Max drawdown
            peak = equity.expanding().max()
            drawdown = (equity - peak) / peak
            max_dd = drawdown.min()

            # Sharpe (daily)
            daily_ret = equity.pct_change().dropna()
            sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0

            # Calmar
            calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-9 else 0

            # Win days ratio
            win_days = (daily_ret > 0).mean()

            print(f"\n  ── Performance Metrics ──")
            print(f"  Total Return:     {total_return_pct:>10.2%}")
            print(f"  Annual Return:    {annual_return:>10.2%}")
            print(f"  Sharpe Ratio:     {sharpe:>10.2f}")
            print(f"  Max Drawdown:     {max_dd:>10.2%}")
            print(f"  Calmar Ratio:     {calmar:>10.2f}")
            print(f"  Daily Win Rate:   {win_days:>10.1%}")
            print(f"  Trading Days:     {len(daily_ret):>10d}")

            # ---- Monthly Returns ----
            monthly = equity.resample("ME").last().pct_change().dropna()
            if len(monthly) > 0:
                positive_months = (monthly > 0).mean()
                print(f"\n  ── Monthly Returns ──")
                print(f"  Positive Months:  {positive_months:>10.1%}")
                print(f"  Best Month:       {monthly.max():>10.2%}")
                print(f"  Worst Month:      {monthly.min():>10.2%}")
                print(f"  Avg Monthly:      {monthly.mean():>10.2%}")

            # ---- Yearly Returns ----
            yearly = equity.resample("YE").last().pct_change().dropna()
            if len(yearly) > 0:
                print(f"\n  ── Yearly Returns ──")
                for dt, ret in yearly.items():
                    print(f"  {dt.year}:           {ret:>10.2%}")

            # ---- Top/Bottom trades ----
            print(f"\n  ── Top 5 Best Trades ──")
            sorted_trades = sorted(trades, key=lambda t: t.return_pct, reverse=True)
            for t in sorted_trades[:5]:
                print(f"  {t.symbol}  {t.buy_date.date()}→{t.sell_date.date()}  "
                      f"buy=¥{t.buy_price:.2f} sell=¥{t.sell_price:.2f}  "
                      f"return={t.return_pct:+.2%}  [{t.exit_type}]")

            print(f"\n  ── Top 5 Worst Trades ──")
            for t in sorted_trades[-5:]:
                print(f"  {t.symbol}  {t.buy_date.date()}→{t.sell_date.date()}  "
                      f"buy=¥{t.buy_price:.2f} sell=¥{t.sell_price:.2f}  "
                      f"return={t.return_pct:+.2%}  [{t.exit_type}]")

            # ---- Save equity curve ----
            eq_df.to_csv(r"d:\quant_framework\equity_curve.csv", encoding="utf-8-sig")
            print(f"\n  Equity curve saved to: d:\\quant_framework\\equity_curve.csv")

        # ---- Save trade log ----
        trade_records = [{
            "symbol": t.symbol,
            "buy_date": t.buy_date,
            "sell_date": t.sell_date,
            "buy_price": t.buy_price,
            "sell_price": t.sell_price,
            "volume": t.volume,
            "return_pct": t.return_pct,
            "net_profit": t.net_profit,
            "exit_type": t.exit_type,
            "signal": t.signal_name,
        } for t in trades]
        trade_df = pd.DataFrame(trade_records)
        trade_df.to_csv(r"d:\quant_framework\trade_log.csv", encoding="utf-8-sig", index=False)
        print(f"  Trade log saved to: d:\\quant_framework\\trade_log.csv")

        print(f"\n[4/4] ✓ Backtest complete!")
        print("=" * 70)


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="A-Share T+1 Scalp Strategy Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_t1_backtest.py
  python run_t1_backtest.py --signal tdx_resonance
  python run_t1_backtest.py --signal tdx2_xg --start 2023-01-01
  python run_t1_backtest.py --max-positions 5 --position-pct 0.20
        """,
    )
    parser.add_argument("--signal", default="tdx2_final",
                        choices=list(SIGNAL_FUNCTIONS.keys()),
                        help="Signal formula to use")
    parser.add_argument("--start", default="2022-01-01",
                        help="Backtest start date")
    parser.add_argument("--end", default="2025-12-31",
                        help="Backtest end date")
    parser.add_argument("--max-positions", type=int, default=3,
                        help="Max simultaneous holdings")
    parser.add_argument("--position-pct", type=float, default=0.30,
                        help="Position size as % of available cash")
    parser.add_argument("--stop-loss", type=float, default=-0.03,
                        help="Stop loss threshold (e.g. -0.03)")
    parser.add_argument("--take-profit", type=float, default=0.05,
                        help="Take profit threshold (e.g. 0.05)")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0,
                        help="Initial capital")
    parser.add_argument("--data-root", default="",
                        help="TDX vipdoc directory (auto-detect if empty)")

    args = parser.parse_args()

    # Auto-detect data root
    data_root = args.data_root
    if not data_root:
        candidates = [
            r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc",
            r"D:\通信达技术指标\01、散人竞价擒龙V8.59旗舰版（下载解压即可使用）\散人竞价擒龙V8.59旗舰版（无加密）\vipdoc",
            r"d:\同花顺软件\同花顺\history",
        ]
        for c in candidates:
            if os.path.isdir(c):
                data_root = c
                break

    if not data_root:
        print("ERROR: No data directory found! Specify --data-root")
        sys.exit(1)

    # Config
    cfg = BacktestConfig(
        signal_name=args.signal,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        position_pct=args.position_pct,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
    )

    print("=" * 70)
    print("  A-Share T+1 Scalp Strategy Backtest")
    print("=" * 70)
    print(f"  Signal:  {args.signal} ({dict(SIGNAL_FUNCTIONS)[args.signal][0]})")
    print(f"  Data:    {data_root}")
    print(f"  Period:  {args.start} → {args.end}")
    print(f"  Capital: ¥{args.initial_cash:,.0f}")

    # Run
    engine = T1BacktestEngine(cfg, data_root)
    n_stocks = engine.load_data()

    if n_stocks < 10:
        print(f"\n  ⚠ Only {n_stocks} valid stocks! Check data directory.")
        sys.exit(1)

    engine.run()
    engine.generate_report()


if __name__ == "__main__":
    main()
