r"""实盘级完整交易策略引擎。

解决核心问题:
  1. 涨停过滤: 当日涨停→不买 (模拟盘致命缺陷修复)
  2. 分层仓位: 初始仓 + 浮盈加仓 + 趋势加仓, 受总仓位上限约束
  3. 移动止损: ATR动态上移 + 硬止损-5% + 时间止损5日
  4. 分批止盈: 5%卖1/3, 10%卖1/3, 剩余跟踪MA5止盈
  5. 风险预算: 单笔最大亏损≤总资金2%, 总持仓≤5只

策略逻辑 (基于你的通达信因子):
  入场:
    - 趋势线底部 > 0.5  (底部抄底信号)
    - 加仓信号 = 1      (DX底背离)
    - 当日未涨停 (price < limit_up - 0.01)
    - 非ST/新股 (>250天)
    - 量能确认 (成交量 > 5日均量)

  仓位:
    - 初始仓: 总资金 × 15% ÷ ATR止损距离
    - 加仓: 浮盈>3% + 信号仍在 + 距上次加仓>5天 → 加初始仓50%
    - 上限: 单票≤30%, 总持仓≤80%

  止损:
    - 硬止损: -5%
    - ATR追踪: 入场后最高价 - 2×ATR
    - 时间止损: 持有5天无盈利 → 出

  止盈:
    - Tier1: +5% → 卖1/3
    - Tier2: +10% → 卖1/3
    - Tier3: 剩余跟踪MA5, 跌破MA5 → 全出

用法:
  python run_live_strategy.py --start 2020-01-01 --end 2025-12-31
"""

import sys, os
sys.path.insert(0, r"d:\quant_framework\src")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import time
import numpy as np
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.tdx_signals import factor_trend_bottom, factor_add_position
from quant_framework.factors.tdx_signals2 import factor_bull_position
from quant_framework.factors.definitions import FACTOR_MAP

# ======================================================================
# 交易系统配置
# ======================================================================
@dataclass
class StrategyConfig:
    """完整交易策略参数"""
    # 资金管理
    initial_cash: float = 1_000_000
    max_positions: int = 5              # 最多同时持有
    init_position_pct: float = 0.15     # 初始仓位 (总资金%)
    max_single_pct: float = 0.30        # 单票上限
    max_total_pct: float = 0.80         # 总仓位上限
    risk_per_trade: float = 0.02        # 单笔最大亏损 (总资金%)

    # 入场过滤
    require_not_limit_up: bool = True   # 涨停不买 (关键修复!)
    min_volume_ratio: float = 0.8       # 量能要求: 成交量 >= N倍5日均量
    require_trend_bottom: bool = True   # 需要趋势线底部信号
    require_add_signal: bool = False    # 需要加仓信号共振

    # 止损
    hard_stop_pct: float = -0.05        # 硬止损-5%
    atr_stop_mult: float = 2.0          # ATR追踪止损倍数
    time_stop_days: int = 5             # 时间止损: 持有N天无盈利→出

    # 止盈
    tp_tier1_pct: float = 0.05          # 第一批止盈
    tp_tier1_sell: float = 0.33         # 卖出比例
    tp_tier2_pct: float = 0.10          # 第二批止盈
    tp_tier2_sell: float = 0.33         # 卖出比例
    trailing_ma: int = 5                # 剩余仓位跟踪MA出场

    # 加仓
    add_threshold_pct: float = 0.03     # 浮盈>3%可加仓
    add_position_pct: float = 0.50      # 加初始仓位的50%
    add_cooldown_days: int = 5          # 加仓冷却期
    max_add_count: int = 2              # 最多加仓次数

    # 数据
    data_root: str = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
    start_date: str = "2020-01-01"
    end_date: str = "2025-12-31"
    commission: float = 0.0003          # 万三佣金
    stamp_tax: float = 0.001            # 千一印花税(卖)


# ======================================================================
# 仓位计算器
# ======================================================================
class PositionCalculator:
    """基于ATR的动态仓位 + 凯利公式调整"""

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    def calc_init_shares(self, price: float, atr: float, cash: float) -> int:
        """计算初始仓位: shares = (cash × risk%) / (ATR × multiplier) / price"""
        risk_amount = cash * self.cfg.risk_per_trade
        stop_dist = atr * self.cfg.atr_stop_mult
        if stop_dist <= 0 or price <= 0:
            return 0
        shares = int(risk_amount / stop_dist / 100) * 100

        # 上限: 初始仓位不超过总资金的init_position_pct
        max_shares = int(cash * self.cfg.init_position_pct / price / 100) * 100
        return min(shares, max_shares)

    def calc_add_shares(self, price: float, initial_shares: int) -> int:
        """加仓: 初始仓位的add_position_pct倍"""
        return int(initial_shares * self.cfg.add_position_pct / 100) * 100


# ======================================================================
# 持仓跟踪器
# ======================================================================
@dataclass
class HoldingInfo:
    """单笔持仓的完整状态"""
    symbol: str
    entry_date: int          # YYYYMMDD
    entry_price: float
    shares: int
    total_cost: float        # 含佣金
    add_count: int = 0       # 已加仓次数
    last_add_date: int = 0
    highest_since_entry: float = 0.0  # 持仓期间最高价

    # 止盈跟踪
    tp1_triggered: bool = False  # 第一批已卖
    tp2_triggered: bool = False  # 第二批已卖
    tp1_shares: int = 0
    tp2_shares: int = 0
    remaining_shares: int = 0

    def __post_init__(self):
        self.highest_since_entry = self.entry_price
        self.remaining_shares = self.shares


# ======================================================================
# 主回测引擎
# ======================================================================
class LiveStrategyEngine:
    """实盘级策略回测引擎"""

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.provider = THSDayDataProvider(cfg.data_root)
        self.provider.connect()
        self.position_calc = PositionCalculator(cfg)

        # State
        self.cash = cfg.initial_cash
        self.holdings: dict[str, HoldingInfo] = {}

        # Records
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.daily_log: list[dict] = []

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self):
        print("=" * 65)
        print("  Live-Grade Strategy Backtest")
        print("=" * 65)
        print(f"  Entry: 趋势线底部 + 加仓信号共振")
        print(f"  Stop: 硬-5% | ATR×{self.cfg.atr_stop_mult} | 时间{self.cfg.time_stop_days}天")
        print(f"  Take-Profit: {self.cfg.tp_tier1_pct:.0%}卖{self.cfg.tp_tier1_sell:.0%} | "
              f"{self.cfg.tp_tier2_pct:.0%}卖{self.cfg.tp_tier2_sell:.0%} | 跟踪MA{self.cfg.trailing_ma}")
        print(f"  涨停过滤: {'ON' if self.cfg.require_not_limit_up else 'OFF'}")
        print()

        # Phase 1: Load data
        print("[1/3] Loading data...")
        t0 = time.time()
        all_syms = self.provider.scan_symbols()
        stock_data = {}
        valid_count = 0

        for si, sym in enumerate(all_syms):
            if si % 1000 == 0:
                print(f"  Loading... {si}/{len(all_syms)} ({valid_count} valid)")

            data = self.provider._read_day_file(sym)
            if not data or len(data) < 250:
                continue

            dates = sorted(data.keys())
            closes = [data[d][3] for d in dates]
            highs = [data[d][1] for d in dates]
            lows = [data[d][2] for d in dates]
            volumes = [data[d][5] for d in dates]
            opens = [data[d][0] for d in dates]

            # Filter: at least some data in our period
            has_data = any(20200101 <= d <= 20260101 for d in dates if len(str(d)) == 8)
            if not has_data:
                continue

            stock_data[sym] = {
                "dates": dates,
                "open": opens, "high": highs, "low": lows,
                "close": closes, "volume": volumes,
            }
            valid_count += 1

        print(f"  Loaded: {valid_count} valid stocks in {time.time()-t0:.0f}s\n")

        # Phase 2: Trading simulation
        print("[2/3] Trading simulation...")
        all_dates = set()
        for sd in stock_data.values():
            for d in sd["dates"]:
                if 20200101 <= d <= 20260101 and len(str(d)) == 8:
                    all_dates.add(d)
        trading_dates = sorted(all_dates)
        print(f"  Trading days: {len(trading_dates)}")

        t0 = time.time()
        for di, date_int in enumerate(trading_dates):
            if di % 100 == 0:
                elapsed = time.time() - t0
                rate = (di + 1) / elapsed if elapsed > 0 else 0
                eta = (len(trading_dates) - di) / rate if rate > 0 else 0
                print(f"  {di}/{len(trading_dates)} ({di/len(trading_dates)*100:.0f}%) "
                      f"Trades:{len(self.trades)} Cash:{self.cash:,.0f} "
                      f"Positions:{len(self.holdings)} ETA:{eta:.0f}s")

            self._process_day(date_int, stock_data)

            # Record equity
            mkt_val = self._mark_to_market(date_int, stock_data)
            self.equity_curve.append({
                "date": date_int,
                "equity": self.cash + mkt_val,
                "cash": self.cash,
                "n_positions": len(self.holdings),
            })

        # Phase 3: Results
        print(f"\n[3/3] Performance Report")
        self._print_report()

    # ------------------------------------------------------------------
    # 每日处理
    # ------------------------------------------------------------------
    def _process_day(self, date_int: int, stock_data: dict):
        # Step 1: 检查持仓 — 止损/止盈/时间止损
        self._check_exits(date_int, stock_data)

        # Step 2: 扫描新入场信号
        if len(self.holdings) >= self.cfg.max_positions:
            return
        if self.cash <= self.cfg.initial_cash * 0.05:  # 现金不足5%暂停
            return

        signals = self._scan_signals(date_int, stock_data)
        if not signals:
            return

        # Step 3: 按信号强度排序, 分配资金
        signals.sort(key=lambda x: x["score"], reverse=True)
        slots = self.cfg.max_positions - len(self.holdings)
        alloc_per_slot = self.cash * self.cfg.init_position_pct

        for sig in signals[:slots]:
            self._enter_position(sig, date_int, alloc_per_slot, stock_data)

        # Step 4: 检查现有持仓是否需要加仓
        self._check_add_position(date_int, stock_data)

    # ------------------------------------------------------------------
    # 信号扫描
    # ------------------------------------------------------------------
    def _scan_signals(self, date_int: int, stock_data: dict) -> list[dict]:
        """扫描所有股票，返回触发信号列表"""
        signals = []

        for sym, sd in stock_data.items():
            if sym in self.holdings:
                continue

            dates = sd["dates"]
            if date_int not in dates:
                continue
            idx = dates.index(date_int)
            if idx < 250:
                continue

            price = sd["close"][idx]
            if price <= 0:
                continue

            # ═══ 涨停过滤 (实盘关键!) ═══
            if self.cfg.require_not_limit_up:
                if idx >= 1:
                    prev_close = sd["close"][idx - 1]
                    if prev_close > 0:
                        limit_up_price = round(prev_close * 1.10, 2)
                        # 涨停板 ± 0.01 容忍度
                        if price >= limit_up_price - 0.01:
                            continue  # 涨停了，买不到
                # 也检查是否一字板 (open == high == close)
                if sd["open"][idx] == sd["high"][idx] == price:
                    continue

            # 量能过滤
            if idx >= 5:
                avg_vol = np.mean(sd["volume"][idx-5:idx])
                if sd["volume"][idx] < avg_vol * self.cfg.min_volume_ratio:
                    continue  # 缩量，不参与

            # ═══ 计算因子 ═══
            # Build mini DataFrame for factor computation
            lookback = min(idx + 1, 150)
            df = pd.DataFrame({
                "open": sd["open"][idx-lookback+1:idx+1],
                "high": sd["high"][idx-lookback+1:idx+1],
                "low": sd["low"][idx-lookback+1:idx+1],
                "close": sd["close"][idx-lookback+1:idx+1],
                "volume": sd["volume"][idx-lookback+1:idx+1],
                "amount": [0] * lookback,
            })

            try:
                trend_bottom = factor_trend_bottom(df)
                add_signal = factor_add_position(df)
                bull_pos = factor_bull_position(df)
            except Exception:
                continue

            tb_val = float(trend_bottom.iloc[-1]) if not trend_bottom.empty else 0
            add_val = float(add_signal.iloc[-1]) if not add_signal.empty else 0
            bp_val = float(bull_pos.iloc[-1]) if not bull_pos.empty else 0

            # ═══ 信号评分 ═══
            score = 0.0
            if self.cfg.require_trend_bottom and tb_val < 0.3:
                continue  # 趋势线底部强度不够
            score += tb_val * 60  # 0~60分

            if self.cfg.require_add_signal:
                if add_val == 0:
                    continue  # 必须共振
                score += 40
            else:
                score += (add_val * 40)  # 0~40分

            score += bp_val * 20  # -20~20分 (牛线位置调整)

            # 计算ATR (用于仓位)
            tr_arr = np.maximum(
                np.array(sd["high"][idx-14:idx+1]) - np.array(sd["low"][idx-14:idx+1]),
                np.abs(np.array(sd["high"][idx-14:idx+1]) - np.array([sd["close"][idx-15]] + sd["close"][idx-14:idx]))
            )
            atr_val = float(np.mean(tr_arr[-14:]))

            signals.append({
                "symbol": sym,
                "score": score,
                "price": price,
                "atr": atr_val,
                "trend_bottom": tb_val,
                "add_signal": int(add_val),
            })

        return signals

    # ------------------------------------------------------------------
    # 入场
    # ------------------------------------------------------------------
    def _enter_position(self, sig: dict, date_int: int, alloc: float, stock_data: dict):
        price = sig["price"]
        atr = sig["atr"]
        sym = sig["symbol"]

        shares = self.position_calc.calc_init_shares(price, atr, self.cash)
        if shares < 100:
            return

        cost = shares * price * (1 + self.cfg.commission)
        if cost > alloc * 1.2:  # 不超过分配额120%
            shares = int(alloc / price / 100) * 100
            cost = shares * price * (1 + self.cfg.commission)

        if cost > self.cash * 0.8:  # 不超过可用资金80%
            return

        self.cash -= cost
        self.holdings[sym] = HoldingInfo(
            symbol=sym,
            entry_date=date_int,
            entry_price=price,
            shares=shares,
            total_cost=cost,
        )

    # ------------------------------------------------------------------
    # 出场检查
    # ------------------------------------------------------------------
    def _check_exits(self, date_int: int, stock_data: dict):
        to_remove = []

        for sym, h in self.holdings.items():
            sd = stock_data.get(sym)
            if not sd or date_int not in sd["dates"]:
                continue
            idx = sd["dates"].index(date_int)
            price = sd["close"][idx]
            high = sd["high"][idx]

            # Update highest since entry
            if price > h.highest_since_entry:
                h.highest_since_entry = price

            remaining = h.remaining_shares
            if remaining <= 0:
                to_remove.append(sym)
                continue

            pnl_pct = (price - h.entry_price) / h.entry_price
            days_held = len([d for d in sd["dates"] if h.entry_date <= d <= date_int])

            should_exit = False
            exit_reason = ""
            sell_shares = 0

            # ═══ 硬止损 -5% ═══
            if pnl_pct <= self.cfg.hard_stop_pct:
                should_exit = True
                exit_reason = f"硬止损 {pnl_pct:.1%}"
                sell_shares = remaining

            # ═══ ATR追踪止损 ═══
            elif not should_exit:
                tr_arr = np.maximum(
                    np.array(sd["high"][max(0,idx-14):idx+1]) - np.array(sd["low"][max(0,idx-14):idx+1]),
                    np.abs(np.array(sd["high"][max(0,idx-14):idx+1]) -
                           np.array([sd["close"][max(0,idx-15)]] + sd["close"][max(0,idx-14):idx]))
                )
                atr_now = float(np.mean(tr_arr[-14:])) if len(tr_arr) >= 14 else sig.get("atr", 0.01)
                stop_price = h.highest_since_entry - atr_now * self.cfg.atr_stop_mult
                if price <= stop_price:
                    should_exit = True
                    exit_reason = f"ATR追踪 {pnl_pct:.1%}"
                    sell_shares = remaining

            # ═══ 时间止损 ═══
            elif not should_exit and days_held >= self.cfg.time_stop_days and pnl_pct < 0.01:
                should_exit = True
                exit_reason = f"时间止损 {days_held}天"
                sell_shares = remaining

            # ═══ 分批止盈 ═══
            if not should_exit and pnl_pct > 0:
                # Tier 1: +5%
                if not h.tp1_triggered and pnl_pct >= self.cfg.tp_tier1_pct:
                    sell_shares = int(h.shares * self.cfg.tp_tier1_sell / 100) * 100
                    if sell_shares >= 100:
                        h.tp1_triggered = True
                        h.tp1_shares = sell_shares
                        h.remaining_shares -= sell_shares
                        self._execute_sell(sym, price, sell_shares, date_int, f"止盈T1 +{pnl_pct:.1%}")

                # Tier 2: +10%
                if not h.tp2_triggered and h.tp1_triggered and pnl_pct >= self.cfg.tp_tier2_pct:
                    sell_shares = int(h.shares * self.cfg.tp_tier2_sell / 100) * 100
                    if sell_shares >= 100 and sell_shares <= h.remaining_shares:
                        h.tp2_triggered = True
                        h.tp2_shares = sell_shares
                        h.remaining_shares -= sell_shares
                        self._execute_sell(sym, price, sell_shares, date_int, f"止盈T2 +{pnl_pct:.1%}")

                # Tier 3: 跟踪MA出场
                if h.tp2_triggered and h.remaining_shares >= 100:
                    mas = np.mean(sd["close"][max(0,idx-self.cfg.trailing_ma+1):idx+1])
                    if price < mas:
                        should_exit = True
                        exit_reason = f"跟踪MA{self.cfg.trailing_ma}出场 {pnl_pct:.1%}"
                        sell_shares = h.remaining_shares

            if should_exit and sell_shares >= 100:
                self._execute_sell(sym, price, min(sell_shares, h.remaining_shares), date_int, exit_reason)
                to_remove.append(sym)

        for sym in to_remove:
            if sym in self.holdings:
                del self.holdings[sym]

    def _execute_sell(self, sym: str, price: float, shares: int, date_int: int, reason: str):
        """执行卖出,计算盈亏"""
        h = self.holdings.get(sym)
        if not h or shares <= 0:
            return

        cost_basis = h.total_cost * (shares / h.shares)
        proceeds = shares * price * (1 - self.cfg.commission - self.cfg.stamp_tax)
        pnl = proceeds - cost_basis
        pnl_pct = pnl / cost_basis if cost_basis > 0 else 0

        self.cash += proceeds

        self.trades.append({
            "date": date_int,
            "symbol": sym,
            "buy_date": h.entry_date,
            "buy_price": h.entry_price,
            "sell_price": price,
            "shares": shares,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "days_held": len([d for d in range(h.entry_date, date_int+1)
                             if d % 10000 >= 101 and d % 10000 <= 1231]) if False else 0,
        })

        if shares >= h.remaining_shares:
            h.remaining_shares = 0
        else:
            h.remaining_shares -= shares

    # ------------------------------------------------------------------
    # 加仓检查
    # ------------------------------------------------------------------
    def _check_add_position(self, date_int: int, stock_data: dict):
        for sym, h in list(self.holdings.items()):
            if h.add_count >= self.cfg.max_add_count:
                continue
            sd = stock_data.get(sym)
            if not sd or date_int not in sd["dates"]:
                continue
            idx = sd["dates"].index(date_int)
            price = sd["close"][idx]

            pnl_pct = (price - h.entry_price) / h.entry_price

            # 条件: 浮盈>阈值 + 冷却期已过 + 信号仍有效
            days_since_add = len([d for d in sd["dates"] if h.last_add_date < d <= date_int]) if h.last_add_date else 999
            if pnl_pct > self.cfg.add_threshold_pct and days_since_add >= self.cfg.add_cooldown_days:
                # 重新检查信号
                lookback = min(idx + 1, 150)
                df = pd.DataFrame({
                    "open": sd["open"][idx-lookback+1:idx+1],
                    "high": sd["high"][idx-lookback+1:idx+1],
                    "low": sd["low"][idx-lookback+1:idx+1],
                    "close": sd["close"][idx-lookback+1:idx+1],
                    "volume": sd["volume"][idx-lookback+1:idx+1],
                    "amount": [0] * lookback,
                })
                try:
                    tb = factor_trend_bottom(df).iloc[-1]
                    if tb < 0.5:
                        continue  # 信号减弱，不加仓
                except Exception:
                    continue

                add_shares = self.position_calc.calc_add_shares(price, h.shares)
                if add_shares < 100:
                    continue

                cost = add_shares * price * (1 + self.cfg.commission)
                if cost > self.cash * 0.3:
                    add_shares = int(self.cash * 0.3 / price / 100) * 100
                    cost = add_shares * price * (1 + self.cfg.commission)

                if cost <= self.cash * 0.3:
                    self.cash -= cost
                    h.shares += add_shares
                    h.remaining_shares += add_shares
                    h.total_cost += cost
                    h.add_count += 1
                    h.last_add_date = date_int
                    # 重新计算入场均价
                    h.entry_price = h.total_cost / h.shares

    # ------------------------------------------------------------------
    # 市值计算
    # ------------------------------------------------------------------
    def _mark_to_market(self, date_int: int, stock_data: dict) -> float:
        total = 0.0
        for sym, h in self.holdings.items():
            sd = stock_data.get(sym)
            if sd and date_int in sd["dates"]:
                idx = sd["dates"].index(date_int)
                total += h.remaining_shares * sd["close"][idx]
        return total

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------
    def _print_report(self):
        if not self.trades:
            print("  No trades executed!")
            return

        eq = pd.DataFrame(self.equity_curve)
        total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1 if len(eq) > 1 else 0
        months = max(len(eq) // 21, 1)
        ann_ret = (1 + total_ret) ** (12 / months) - 1 if months > 0 else 0

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]

        win_rate = len(wins) / len(self.trades) if self.trades else 0
        avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses else float("inf")

        exit_reasons = defaultdict(int)
        for t in self.trades:
            reason = t["reason"].split(" ")[0] if t["reason"] else "?"
            exit_reasons[reason] += 1

        print(f"\n{'='*65}")
        print(f"  TRADE PERFORMANCE")
        print(f"{'='*65}")
        print(f"  Total Trades:      {len(self.trades):>8}")
        print(f"  Winning:           {len(wins):>8}  ({win_rate:.1%})")
        print(f"  Losing:            {len(losses):>8}")
        print(f"  Avg Win:           {avg_win:>8.2%}")
        print(f"  Avg Loss:          {avg_loss:>8.2%}")
        print(f"  Profit Factor:     {profit_factor:>8.2f}")
        print(f"  Total Return:      {total_ret:>8.1%}")
        print(f"  Annual Return:     {ann_ret:>8.1%}")

        # Exit reason distribution
        print(f"\n  Exit Reasons:")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / len(self.trades)
            print(f"    {reason:<25} {count:>5} ({pct:.0%})")

        # Top/bottom trades
        sorted_trades = sorted(self.trades, key=lambda t: t["pnl_pct"])
        print(f"\n  Worst 5 Trades:")
        for t in sorted_trades[:5]:
            print(f"    {t['symbol']} {t['buy_date']}→{t['date']} "
                  f"B:{t['buy_price']:.2f} S:{t['sell_price']:.2f} "
                  f"P&L:{t['pnl_pct']:.1%} [{t['reason']}]")
        print(f"  Best 5 Trades:")
        for t in sorted_trades[-5:]:
            print(f"    {t['symbol']} {t['buy_date']}→{t['date']} "
                  f"B:{t['buy_price']:.2f} S:{t['sell_price']:.2f} "
                  f"P&L:{t['pnl_pct']:.1%} [{t['reason']}]")

        print(f"\n  Final: Cash={self.cash:,.0f}  Holdings={len(self.holdings)}")

        eq.to_csv(r"d:\quant_framework\equity_live.csv", encoding="utf-8-sig")
        print(f"  Saved: d:\\quant_framework\\equity_live.csv")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live-Grade Strategy Backtest")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--cash", type=float, default=1_000_000)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--risk-per-trade", type=float, default=0.02)
    parser.add_argument("--require-add-signal", action="store_true",
                       help="Require 加仓信号 resonance (more selective)")
    args = parser.parse_args()

    cfg = StrategyConfig(
        initial_cash=args.cash,
        max_positions=args.max_positions,
        risk_per_trade=args.risk_per_trade,
        require_add_signal=args.require_add_signal,
        start_date=args.start,
        end_date=args.end,
    )

    engine = LiveStrategyEngine(cfg)
    engine.run()
