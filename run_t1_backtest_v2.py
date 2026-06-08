"""T+1 短线隔日冲策略 V2 回测 — A股全市场。

V2 相比 V1 的升级 (回测可量化对比):
  1. 市场环境过滤 — 熊市/高波动自动降仓
  2. 自适应止损止盈 — ATR动态计算
  3. 信号质量评分 — 多维度打分，只做高分信号
  4. 量能确认 — 量比+量价配合
  5. 智能持仓 — 强势股可持有T+2/T+3, 跟踪止盈
  6. 缺口过滤 — 回避未回补向下跳空
  7. 波动率自适应仓位 — 高波小仓、低波大仓
  8. 连续信号冷却期 — 同股票5日不重复

用法:
  python run_t1_backtest_v2.py
  python run_t1_backtest_v2.py --signal tdx_resonance
  python run_t1_backtest_v2.py --no-smart-hold        # 关闭智能持仓(V1模式)
  python run_t1_backtest_v2.py --no-quality-filter     # 关闭质量评分过滤
  python run_t1_backtest_v2.py --min-quality 0.75      # 提高质量门槛
  python run_t1_backtest_v2.py --compare-v1            # 同时跑V1做对比
"""

import sys
import os
sys.path.insert(0, r"d:\quant_framework\src")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import argparse
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
class BacktestV2Config:
    """V2 回测配置。"""
    signal_name: str = "tdx2_final"
    start_date: str = "2022-01-01"
    end_date: str = "2025-12-31"
    initial_cash: float = 1_000_000.0
    max_positions: int = 3
    base_position_pct: float = 0.30
    max_position_pct: float = 0.40
    min_position_pct: float = 0.10

    # 风控
    atr_stop_mult: float = 2.0
    atr_profit_mult: float = 3.0
    atr_period: int = 14
    max_stop_loss_pct: float = -0.05
    min_take_profit_pct: float = 0.03

    # 智能持仓
    enable_smart_hold: bool = True
    trailing_stop_atr: float = 1.5
    max_hold_days: int = 5
    momentum_threshold: float = 0.02

    # 市场环境
    enable_regime_filter: bool = True
    index_symbol: str = "999999"
    bear_market_position_pct: float = 0.50
    high_vol_position_pct: float = 0.70
    regime_ma_short: int = 20
    regime_ma_long: int = 60

    # 量能
    enable_volume_filter: bool = True
    min_vol_ratio: float = 1.2
    require_price_volume_match: bool = True

    # 信号质量
    enable_quality_scoring: bool = True
    min_quality_score: float = 0.55

    # 缺口
    enable_gap_filter: bool = True
    max_gap_down_pct: float = -0.02

    # 冷却
    cooldown_days: int = 5

    # 成本
    commission_rate: float = 0.0003
    stamp_duty: float = 0.001
    slippage: float = 0.001
    min_days: int = 200
    data_root: str = ""


# ======================================================================
# Signal Registry
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
# Market Regime
# ======================================================================

class MarketRegime:
    BULL = "bull"
    BULL_VOLATILE = "bull_volatile"
    SIDEWAYS = "sideways"
    BEAR = "bear"
    BEAR_RALLY = "bear_rally"
    HIGH_VOLATILITY = "high_vol"

    @classmethod
    def classify(cls, price, ma_short, ma_long, vol_ratio, vol_threshold=1.5):
        above_short = price > ma_short
        above_long = price > ma_long
        short_above_long = ma_short > ma_long
        high_vol = vol_ratio > vol_threshold

        if high_vol:
            return cls.HIGH_VOLATILITY
        if above_short and above_long and short_above_long:
            return cls.BULL
        elif above_short and above_long and not short_above_long:
            return cls.BULL_VOLATILE
        elif not above_short and not above_long:
            return cls.BEAR
        elif above_short and not above_long:
            return cls.BEAR_RALLY
        else:
            return cls.SIDEWAYS


# ======================================================================
# Trade Record
# ======================================================================

@dataclass
class TradeV2:
    symbol: str
    buy_date: datetime
    sell_date: datetime
    buy_price: float
    sell_price: float
    volume: int
    return_pct: float
    net_profit: float
    exit_type: str
    hold_days: int = 1
    quality_score: float = 0.0
    regime: str = ""
    signal_name: str = ""


# ======================================================================
# V2 Backtest Engine
# ======================================================================

class T1V2BacktestEngine:
    """T+1 短线隔日冲 V2 回测引擎。

    逐日遍历全市场K线，模拟:
      - T日: 信号触发+多维度评分 → 以收盘价买入
      - T+1~T+N: 智能持仓 — 跟踪止盈/止损/超时退出
      - 市场环境自适应仓位
    """

    def __init__(self, config: BacktestV2Config, data_root: str):
        self.cfg = config
        self.data_root = data_root

        self.signal_label, self.signal_fn = SIGNAL_FUNCTIONS[config.signal_name]

        self._provider = THSDayDataProvider(data_root)
        self._provider.connect()

        # 状态
        self.cash = config.initial_cash
        self.holdings: dict[str, dict] = {}          # symbol → holding info
        self.trades: list[TradeV2] = []
        self.equity_curve: list[dict] = []
        self.last_trade_date: dict[str, datetime] = {}  # 冷却期

        self._stock_data: dict[str, pd.DataFrame] = {}
        self._index_data: pd.DataFrame | None = None    # 大盘数据
        self._current_regime: str = MarketRegime.SIDEWAYS

        # 统计
        self._signal_scores: list[float] = []           # 信号分记录
        self._regime_history: list[dict] = []

    # ==================================================================
    # Data Loading
    # ==================================================================

    def load_data(self) -> int:
        """加载所有A股日线数据。"""
        print(f"\n[1/5] Loading A-share daily data from: {self.data_root}")
        t0 = time.time()

        all_symbols = self._provider.scan_symbols()
        print(f"  Total .day files found: {len(all_symbols)}")

        loaded = 0
        skipped_no_data = 0
        skipped_short = 0
        start_dt = datetime.strptime(self.cfg.start_date, "%Y-%m-%d")
        min_data_start = start_dt - timedelta(days=self.cfg.min_days * 2)

        for i, sym in enumerate(all_symbols):
            if i % 500 == 0:
                print(f"  Loading... {i}/{len(all_symbols)} ({loaded} valid)")

            data = self._provider._read_day_file(sym)
            if not data:
                skipped_no_data += 1
                continue

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
            df_in_range = df[df.index >= min_data_start]
            if len(df_in_range) < self.cfg.min_days:
                skipped_short += 1
                continue

            self._stock_data[sym] = df
            loaded += 1

        elapsed = time.time() - t0
        print(f"  Loaded: {loaded} stocks ({skipped_no_data} no-data, {skipped_short} too-short)")
        print(f"  Time: {elapsed:.1f}s")

        # 尝试加载大盘数据
        self._load_index_data(start_dt, min_data_start)

        return loaded

    def _load_index_data(self, start_dt, min_data_start):
        """加载大盘指数数据用于市场环境判断。"""
        index_symbol = self.cfg.index_symbol
        # 尝试几种命名
        for sym in [index_symbol, f"sh{index_symbol}", f"sz{index_symbol}", "999999", "sh999999"]:
            if sym in self._stock_data:
                self._index_data = self._stock_data[sym]
                print(f"  Index data loaded: {sym} ({len(self._index_data)} days)")
                return

        # 如果找不到，用所有股票的均价当指数
        if not self._index_data and len(self._stock_data) > 10:
            print("  ⚠ Index data not found, using average of all stocks as proxy")
            self._index_data = None  # will compute on the fly

    # ==================================================================
    # Run
    # ==================================================================

    def run(self):
        """执行回测。"""
        print(f"\n[2/5] Running V2 backtest: {self.cfg.start_date} → {self.cfg.end_date}")
        print(f"  Signal: {self.signal_label}")
        print(f"  Smart Hold: {'ON' if self.cfg.enable_smart_hold else 'OFF'} "
              f"(max {self.cfg.max_hold_days} days)")
        print(f"  Quality Filter: {'ON' if self.cfg.enable_quality_scoring else 'OFF'} "
              f"(min={self.cfg.min_quality_score:.0%})")
        print(f"  Regime Filter: {'ON' if self.cfg.enable_regime_filter else 'OFF'}")
        print(f"  Volume Filter: {'ON' if self.cfg.enable_volume_filter else 'OFF'}")
        print(f"  Cooldown: {self.cfg.cooldown_days} days")

        start_dt = datetime.strptime(self.cfg.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(self.cfg.end_date, "%Y-%m-%d")
        trading_dates = self._build_calendar(start_dt, end_dt)
        print(f"  Trading days: {len(trading_dates)}")

        # Reset
        self.cash = self.cfg.initial_cash
        self.holdings = {}
        self.trades = []
        self.equity_curve = []
        self.last_trade_date = {}
        self._signal_scores = []
        self._regime_history = []

        hold_queue: dict[str, dict] = {}  # T日买入 → 持有中
        total_days = len(trading_dates)
        trade_count = 0
        signal_count = 0
        signal_passed = 0  # 通过质量过滤的信号

        for day_idx, today in enumerate(trading_dates):
            if day_idx % 50 == 0:
                pct = day_idx / total_days * 100
                print(f"  Progress: {day_idx}/{total_days} ({pct:.0f}%) | "
                      f"Trades: {trade_count} | Signals: {signal_count} ({signal_passed} passed) | "
                      f"Cash: ¥{self.cash:,.0f} | Holdings: {len(hold_queue)}")

            # ── 更新市场环境 ──
            if self.cfg.enable_regime_filter:
                self._update_regime(today)

            # ── Step 1: 处理持仓卖出 ──
            sell_proceeds = 0.0
            for symbol, hold_info in list(hold_queue.items()):
                if hold_info["buy_date"].date() == today.date():
                    continue  # T+1约束: 今天买入的不能卖

                sell_result = self._process_hold_v2(hold_info, today)
                if sell_result is not None:
                    sell_proceeds += sell_result["proceeds"]
                    trade_count += 1
                    del hold_queue[symbol]
                else:
                    # 还在持有中，更新状态
                    hold_info["hold_days"] += 1
                    # 更新持仓期间最高价
                    df_sym = self._stock_data.get(symbol)
                    if df_sym is not None:
                        df_today = df_sym[df_sym.index == today]
                        if not df_today.empty:
                            today_high = df_today["high"].iloc[0]
                            if today_high > hold_info.get("max_price", hold_info["buy_price"]):
                                hold_info["max_price"] = today_high

            if sell_proceeds > 0:
                self.cash += sell_proceeds

            # ── Step 2: 计算今日信号 ──
            today_signals: list[dict] = []

            for symbol, df in self._stock_data.items():
                if symbol in hold_queue:
                    continue

                df_up_to_today = df[df.index <= today]
                if len(df_up_to_today) < self.cfg.min_days:
                    continue

                sig_result = self._compute_signal_v2(symbol, df_up_to_today, today)
                if sig_result:
                    today_signals.append(sig_result)
                    signal_count += 1

                    if sig_result["quality_score"] >= self.cfg.min_quality_score:
                        signal_passed += 1
                        self._signal_scores.append(sig_result["quality_score"])

            # ── Step 3: 排序+买入 ──
            if today_signals:
                slots = self.cfg.max_positions - len(hold_queue)
                if slots > 0:
                    # 按质量评分排序
                    today_signals.sort(key=lambda x: x["quality_score"], reverse=True)

                    for sig in today_signals[:slots]:
                        symbol = sig["symbol"]

                        # 质量过滤
                        if self.cfg.enable_quality_scoring and sig["quality_score"] < self.cfg.min_quality_score:
                            continue

                        # 冷却期
                        if symbol in self.last_trade_date:
                            days_since = (today - self.last_trade_date[symbol]).days
                            if days_since < self.cfg.cooldown_days:
                                continue

                        # 缺口过滤
                        if self.cfg.enable_gap_filter and not self._check_gap(symbol, df_up_to_today=None):
                            continue

                        buy_result = self._execute_buy_v2(sig, today)
                        if buy_result:
                            hold_queue[symbol] = buy_result
                            self.last_trade_date[symbol] = today

            # ── Step 4: 记录权益曲线 ──
            market_value = sum(
                h.get("cost", 0) * (1 + self._get_unrealized_pnl(h, today))
                for h in hold_queue.values()
            )
            total_equity = self.cash + market_value

            self.equity_curve.append({
                "date": today,
                "equity": total_equity,
                "cash": self.cash,
                "market_value": market_value,
                "positions": len(hold_queue),
                "regime": self._current_regime,
            })

        # ── 强制清仓 ──
        last_day = trading_dates[-1]
        for symbol, hold_info in list(hold_queue.items()):
            result = self._process_hold_v2(hold_info, last_day, force_close=True)
            if result:
                self.cash += result["proceeds"]
                trade_count += 1

        print(f"\n  Backtest complete: {trade_count} trades, {signal_count} raw signals "
              f"({signal_passed} passed quality filter)")
        self._final_equity = self.cash
        self._trade_count = trade_count
        self._signal_count = signal_count
        self._signal_passed = signal_passed

    # ==================================================================
    # V2 Signal Computation
    # ==================================================================

    def _compute_signal_v2(self, symbol: str, df: pd.DataFrame, today: datetime) -> dict | None:
        """V2 信号计算 + 多维度评分。"""
        try:
            sig_val = self.signal_fn(df)
            if isinstance(sig_val, pd.Series):
                raw = sig_val.iloc[-1]
                if pd.isna(raw) or raw <= 0:
                    return None
                raw_signal = int(raw)
            else:
                return None
        except Exception:
            return None

        # 多维度评分
        scores = self._score_quality(df, raw_signal)
        quality = scores["total"]

        # 量能过滤
        if self.cfg.enable_volume_filter:
            if not self._check_volume_filter(df):
                return None

        return {
            "symbol": symbol,
            "signal": raw_signal,
            "quality_score": quality,
            "close": df["close"].iloc[-1],
            "atr_pct": scores.get("atr", 0.02),
            "scores": scores,
        }

    # ==================================================================
    # Quality Scoring
    # ==================================================================

    def _score_quality(self, df: pd.DataFrame, raw_signal: int) -> dict:
        """多维度信号质量评分。"""
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
        scores = {}

        # 1. 信号强度 (0~1)
        scores["intensity"] = 1.0 if raw_signal >= 2 else (0.6 if raw_signal == 1 else 0.3)

        # 2. 趋势质量 (0~1)
        ma5 = c.iloc[-5:].mean()
        ma10 = c.iloc[-10:].mean()
        ma20 = c.iloc[-20:].mean()
        current = c.iloc[-1]

        if current > ma5 > ma10 > ma20:
            trend_score = 0.9
        elif current > ma5 and current > ma10:
            trend_score = 0.7
        elif current > ma20:
            trend_score = 0.5
        elif current > ma5:
            trend_score = 0.3
        else:
            trend_score = 0.1

        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0:
            trend_score = min(1.0, trend_score + 0.1)

        scores["trend"] = trend_score

        # 3. 量能质量 (0~1)
        vol_current = v.iloc[-1]
        vol_ma5 = v.iloc[-6:-1].mean() if len(v) >= 6 else v.iloc[:-1].mean()
        vol_ratio = vol_current / vol_ma5 if vol_ma5 > 0 else 1.0

        if 1.5 <= vol_ratio <= 3.0:
            volume_score = 1.0
        elif 1.2 <= vol_ratio < 1.5:
            volume_score = 0.7
        elif vol_ratio > 3.0:
            volume_score = 0.6
        elif vol_ratio >= 0.8:
            volume_score = 0.4
        else:
            volume_score = 0.2

        price_change = c.iloc[-1] - c.iloc[-2] if len(c) >= 2 else 0
        if price_change > 0 and vol_ratio > 1.0:
            volume_score = min(1.0, volume_score + 0.1)
        elif price_change < 0 and vol_ratio < 1.0:
            volume_score = min(1.0, volume_score + 0.05)

        scores["volume"] = volume_score

        # 4. 位置质量 (0~1)
        high_20 = h.iloc[-20:].max()
        low_20 = l.iloc[-20:].min()
        range_20 = high_20 - low_20
        pos_in_range = (current - low_20) / range_20 if range_20 > 0 else 0.5

        if 0.2 <= pos_in_range <= 0.5:
            position_score = 1.0
        elif 0.5 < pos_in_range <= 0.7:
            position_score = 0.8
        elif 0.7 < pos_in_range <= 0.85:
            position_score = 0.5
        elif pos_in_range > 0.85:
            position_score = 0.2
        else:
            position_score = 0.6

        if current > ma5 * 1.05:
            position_score *= 0.7

        scores["position"] = position_score

        # ATR
        tr = pd.concat([
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.iloc[-self.cfg.atr_period:].mean()
        scores["atr"] = atr / current if current > 0 else 0.02

        # 总分
        scores["total"] = (
            scores["intensity"] * 0.25 +
            scores["trend"] * 0.30 +
            scores["volume"] * 0.25 +
            scores["position"] * 0.20
        )

        return scores

    # ==================================================================
    # Filters
    # ==================================================================

    def _check_volume_filter(self, df: pd.DataFrame) -> bool:
        """量能过滤。"""
        v = df["volume"]
        c = df["close"]
        if len(v) < 6:
            return True

        vol_current = v.iloc[-1]
        vol_ma5 = v.iloc[-6:-1].mean()
        vol_ratio = vol_current / vol_ma5 if vol_ma5 > 0 else 0

        if vol_ratio < self.cfg.min_vol_ratio:
            return False

        if self.cfg.require_price_volume_match and len(c) >= 2:
            if c.iloc[-1] > c.iloc[-2] and vol_ratio < 1.0:
                return False

        return True

    def _check_gap(self, symbol: str, df_up_to_today=None) -> bool:
        """缺口过滤。"""
        df = self._stock_data.get(symbol)
        if df is None or len(df) < 5:
            return True

        for i in range(-3, 0):
            if abs(i) > len(df):
                break
            today_open = df["open"].iloc[i]
            prev_close = df["close"].iloc[i - 1]
            gap = (today_open - prev_close) / prev_close
            if gap < self.cfg.max_gap_down_pct:
                today_high = df["high"].iloc[i]
                if today_high < prev_close:
                    return False

        return True

    # ==================================================================
    # Market Regime
    # ==================================================================

    def _update_regime(self, today: datetime):
        """更新市场环境判断。"""
        if self._index_data is not None:
            df = self._index_data[self._index_data.index <= today]
            if len(df) < self.cfg.regime_ma_long + 10:
                self._current_regime = MarketRegime.SIDEWAYS
                return
            c = df["close"]
            price = c.iloc[-1]
            ma_short = c.iloc[-self.cfg.regime_ma_short:].mean()
            ma_long = c.iloc[-self.cfg.regime_ma_long:].mean()
            returns = c.pct_change().dropna()
            current_vol = returns.iloc[-20:].std()
            historical_vol = returns.iloc[-120:].std() if len(returns) >= 120 else current_vol
            vol_ratio = current_vol / historical_vol if historical_vol > 0 else 1.0
        else:
            # 用所有股票均价当代理
            all_closes = []
            for df in self._stock_data.values():
                df_t = df[df.index <= today]
                if not df_t.empty and len(df_t) >= self.cfg.regime_ma_long + 10:
                    all_closes.append(df_t["close"])
                if len(all_closes) >= 20:
                    break
            if not all_closes:
                self._current_regime = MarketRegime.SIDEWAYS
                return
            avg_close = pd.concat(all_closes, axis=1).mean(axis=1)
            if len(avg_close) < self.cfg.regime_ma_long + 10:
                self._current_regime = MarketRegime.SIDEWAYS
                return
            price = avg_close.iloc[-1]
            ma_short = avg_close.iloc[-self.cfg.regime_ma_short:].mean()
            ma_long = avg_close.iloc[-self.cfg.regime_ma_long:].mean()
            returns = avg_close.pct_change().dropna()
            current_vol = returns.iloc[-20:].std()
            historical_vol = returns.iloc[-120:].std() if len(returns) >= 120 else current_vol
            vol_ratio = current_vol / historical_vol if historical_vol > 0 else 1.0

        self._current_regime = MarketRegime.classify(price, ma_short, ma_long, vol_ratio)
        self._regime_history.append({"date": today, "regime": self._current_regime})

    # ==================================================================
    # Buy / Sell V2
    # ==================================================================

    def _execute_buy_v2(self, sig: dict, date: datetime) -> dict | None:
        """V2 买入 — 波动率自适应仓位 + 市场环境调整。"""
        close_price = sig["close"]
        atr_pct = sig.get("atr_pct", 0.02)
        quality = sig["quality_score"]

        # 仓位计算
        position_pct = self._calc_position(atr_pct)

        # 市场环境调整
        position_pct = self._adjust_for_regime(position_pct)

        available_cash = self.cash * position_pct
        buy_price = close_price * (1 + self.cfg.slippage)
        estimated_cost = buy_price * (1 + self.cfg.commission_rate)
        volume = int(available_cash / estimated_cost / 100) * 100

        if volume < 100:
            return None

        total_cost = buy_price * volume * (1 + self.cfg.commission_rate)
        total_cost = max(total_cost, buy_price * volume + 5.0)

        if total_cost > self.cash * 0.4:
            volume = int(self.cash * 0.4 / buy_price / 100) * 100
            if volume < 100:
                return None
            total_cost = buy_price * volume * (1 + self.cfg.commission_rate)

        if total_cost > self.cash:
            return None

        self.cash -= total_cost

        return {
            "symbol": sig["symbol"],
            "buy_price": buy_price,
            "volume": volume,
            "cost": total_cost,
            "buy_date": date,
            "hold_days": 0,
            "max_price": buy_price,
            "quality_score": quality,
            "atr_pct": atr_pct,
            "regime": self._current_regime,
        }

    def _process_hold_v2(self, hold_info: dict, today: datetime, force_close=False) -> dict | None:
        """V2 智能卖出判断。"""
        symbol = hold_info["symbol"]
        df = self._stock_data.get(symbol)
        if df is None:
            return {"proceeds": hold_info["cost"] * 0.9} if force_close else None

        df_today = df[df.index == today]
        if df_today.empty:
            if force_close:
                return {"proceeds": hold_info["cost"] * 0.95}
            return None

        open_price = df_today["open"].iloc[0]
        close_price = df_today["close"].iloc[0]
        high_price = df_today["high"].iloc[0]
        low_price = df_today["low"].iloc[0]
        buy_price = hold_info["buy_price"]
        volume = hold_info["volume"]
        atr_pct = hold_info.get("atr_pct", 0.02)
        max_price = hold_info.get("max_price", buy_price)

        # 更新最高价
        if high_price > max_price:
            max_price = high_price
            hold_info["max_price"] = max_price

        # 收益率
        current_price = close_price  # T日收盘价
        return_pct = (current_price - buy_price) / buy_price if buy_price > 0 else 0
        atr_price = atr_pct * buy_price if atr_pct > 0 else buy_price * 0.02

        should_sell = False
        exit_type = "normal"
        sell_price = current_price
        reason = ""

        # ── 强制平仓 ──
        if force_close:
            should_sell = True
            exit_type = "force_close"
            reason = "强制平仓"

        # ── 硬止损 ──
        if not should_sell and return_pct <= self.cfg.max_stop_loss_pct:
            should_sell = True
            exit_type = "stop_loss"
            reason = f"硬止损"

        # ── 自适应止盈 ──
        if not should_sell:
            adaptive_tp = self.cfg.atr_profit_mult * atr_pct
            adaptive_tp = max(adaptive_tp, self.cfg.min_take_profit_pct)
            if return_pct >= adaptive_tp:
                should_sell = True
                exit_type = "take_profit"
                reason = f"自适应止盈(ATR×{self.cfg.atr_profit_mult})"

        # ── 跟踪止盈 ──
        if not should_sell and self.cfg.enable_smart_hold and return_pct > 0:
            pullback = (current_price - max_price) / max_price
            trailing_limit = -self.cfg.trailing_stop_atr * atr_pct
            if pullback < trailing_limit:
                should_sell = True
                exit_type = "trailing_stop"
                reason = f"跟踪止盈(最高={max_price:.2f}回撤={pullback:.2%})"

        # ── 超时 ──
        if not should_sell and hold_info["hold_days"] >= self.cfg.max_hold_days:
            should_sell = True
            exit_type = "timeout"
            reason = f"持仓超时({hold_info['hold_days']}天)"

        # ── T+1 弱势退出 ──
        if not should_sell and self.cfg.enable_smart_hold and hold_info["hold_days"] >= 1:
            day_return = (close_price - open_price) / open_price if open_price > 0 else 0
            if day_return < self.cfg.momentum_threshold:
                should_sell = True
                exit_type = "weak_exit"
                reason = f"T+1弱势(当日涨幅={day_return:.2%})"

        # ── 非智能模式: T+1无条件卖 ──
        if not should_sell and not self.cfg.enable_smart_hold and hold_info["hold_days"] >= 1:
            should_sell = True
            exit_type = "t1_normal"
            reason = "T+1隔日冲"

        if not should_sell:
            return None  # 继续持有

        # 执行卖出
        sell_price_adj = sell_price * (1 - self.cfg.slippage)
        gross = sell_price_adj * volume
        commission = max(gross * self.cfg.commission_rate, 5.0)
        stamp = gross * self.cfg.stamp_duty
        net_proceeds = gross - commission - stamp
        net_profit = net_proceeds - hold_info["cost"]

        trade = TradeV2(
            symbol=symbol,
            buy_date=hold_info["buy_date"],
            sell_date=today,
            buy_price=buy_price,
            sell_price=sell_price_adj,
            volume=volume,
            return_pct=return_pct,
            net_profit=net_profit,
            exit_type=exit_type,
            hold_days=hold_info["hold_days"] + 1,
            quality_score=hold_info.get("quality_score", 0),
            regime=self._current_regime,
            signal_name=self.cfg.signal_name,
        )
        self.trades.append(trade)

        return {"proceeds": net_proceeds, "trade": trade}

    def _calc_position(self, atr_pct: float) -> float:
        """波动率自适应仓位。"""
        if atr_pct <= 0:
            return self.cfg.base_position_pct
        vol_adjust = max(0.5, min(1.5, 0.02 / atr_pct))
        pct = self.cfg.base_position_pct * vol_adjust
        return max(self.cfg.min_position_pct, min(self.cfg.max_position_pct, pct))

    def _adjust_for_regime(self, pct: float) -> float:
        """市场环境仓位调整。"""
        if not self.cfg.enable_regime_filter:
            return pct
        multipliers = {
            MarketRegime.BULL: 1.0,
            MarketRegime.BULL_VOLATILE: self.cfg.high_vol_position_pct,
            MarketRegime.SIDEWAYS: 0.85,
            MarketRegime.BEAR: self.cfg.bear_market_position_pct,
            MarketRegime.BEAR_RALLY: 0.60,
            MarketRegime.HIGH_VOLATILITY: self.cfg.high_vol_position_pct,
        }
        mult = multipliers.get(self._current_regime, 0.85)
        return max(self.cfg.min_position_pct, pct * mult)

    def _get_unrealized_pnl(self, hold_info: dict, today: datetime) -> float:
        """估算未实现盈亏%。"""
        df = self._stock_data.get(hold_info["symbol"])
        if df is None:
            return 0.0
        df_today = df[df.index == today]
        if df_today.empty:
            return 0.0
        current = df_today["close"].iloc[0]
        return (current - hold_info["buy_price"]) / hold_info["buy_price"] if hold_info["buy_price"] > 0 else 0.0

    # ==================================================================
    # Calendar
    # ==================================================================

    def _build_calendar(self, start: datetime, end: datetime) -> list[datetime]:
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
        print(f"\n[3/5] Generating V2 performance report...")
        print("=" * 70)
        print(f"  A-Share T+1 Scalp V2 Backtest Report")
        print("=" * 70)
        print(f"  Signal:      {self.signal_label}")
        print(f"  Period:      {self.cfg.start_date} → {self.cfg.end_date}")
        print(f"  Features:    SmartHold={self.cfg.enable_smart_hold}, "
              f"Quality={self.cfg.enable_quality_scoring}, "
              f"Regime={self.cfg.enable_regime_filter}, "
              f"Volume={self.cfg.enable_volume_filter}")
        print(f"  Initial:     ¥{self.cfg.initial_cash:,.0f}")
        print(f"  Max Hold:    {self.cfg.max_positions} (max {self.cfg.max_hold_days} days)")
        print("-" * 70)

        trades = self.trades
        if not trades:
            print("\n  ⚠ No trades executed!")
            return

        # ── Trade Stats ──
        returns = [t.return_pct for t in trades]
        profits = [t.net_profit for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        win_rate = len(wins) / len(returns) if returns else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        print(f"\n  ── Trade Summary ──")
        print(f"  Total Trades:     {len(trades):>8d}")
        print(f"  Winning Trades:   {len(wins):>8d}  ({win_rate:.1%})")
        print(f"  Losing Trades:    {len(losses):>8d}  ({1-win_rate:.1%})")
        print(f"  Avg Win:          {avg_win:>8.2%}")
        print(f"  Avg Loss:         {avg_loss:>8.2%}")
        print(f"  Profit Factor:    {profit_factor:>8.2f}")
        print(f"  Best Trade:       {max(returns):>8.2%}")
        print(f"  Worst Trade:      {min(returns):>8.2%}")

        # ── P&L ──
        gross_profit = sum(p for p in profits if p > 0)
        gross_loss = sum(p for p in profits if p < 0)
        net_pnl = sum(profits)

        print(f"\n  ── P&L ──")
        print(f"  Gross Profit:     ¥{gross_profit:>10,.0f}")
        print(f"  Gross Loss:       ¥{gross_loss:>10,.0f}")
        print(f"  Net P&L:          ¥{net_pnl:>10,.0f}")
        print(f"  Final Cash:       ¥{self.cash:>10,.0f}")

        # ── Exit Type ──
        exit_counts = defaultdict(lambda: {"count": 0, "returns": []})
        for t in trades:
            exit_counts[t.exit_type]["count"] += 1
            exit_counts[t.exit_type]["returns"].append(t.return_pct)

        print(f"\n  ── Exit Types (V2) ──")
        for et, data in sorted(exit_counts.items()):
            avg_ret = np.mean(data["returns"])
            print(f"  {et:<18s}: {data['count']:>5d}  (avg: {avg_ret:>+7.2%})")

        # ── Hold Days ──
        hold_days_list = [t.hold_days for t in trades]
        print(f"\n  ── Hold Days Distribution ──")
        for d in range(1, max(hold_days_list) + 1):
            cnt = hold_days_list.count(d)
            if cnt > 0:
                day_trades = [t for t in trades if t.hold_days == d]
                day_returns = [t.return_pct for t in day_trades]
                print(f"  {d} day(s):  {cnt:>5d} trades  (avg return: {np.mean(day_returns):>+7.2%})")

        # ── Quality Score Impact ──
        if self._signal_scores:
            print(f"\n  ── Signal Quality ──")
            print(f"  Avg Score:        {np.mean(self._signal_scores):.2%}")
            print(f"  Median Score:     {np.median(self._signal_scores):.2%}")
            print(f"  P75 Score:        {np.percentile(self._signal_scores, 75):.2%}")

            # 按质量分组
            high_q = [t for t in trades if t.quality_score >= 0.7]
            mid_q = [t for t in trades if 0.5 <= t.quality_score < 0.7]
            low_q = [t for t in trades if t.quality_score < 0.5]

            for label, group in [("High(≥0.7)", high_q), ("Mid(0.5~0.7)", mid_q), ("Low(<0.5)", low_q)]:
                if group:
                    avg_ret = np.mean([t.return_pct for t in group])
                    wr = sum(1 for t in group if t.return_pct > 0) / len(group)
                    print(f"  {label:<14s}: {len(group):>4d} trades  "
                          f"avg={avg_ret:>+7.2%}  wr={wr:.1%}")

        # ── Regime Impact ──
        regime_trades = defaultdict(list)
        for t in trades:
            regime_trades[t.regime].append(t.return_pct)
        if regime_trades:
            print(f"\n  ── Performance by Regime ──")
            for regime, rets in sorted(regime_trades.items()):
                print(f"  {regime:<18s}: {len(rets):>4d} trades  "
                      f"avg={np.mean(rets):>+7.2%}  wr={sum(1 for r in rets if r>0)/len(rets):.1%}")

        # ── Equity Curve ──
        if self.equity_curve:
            eq_df = pd.DataFrame(self.equity_curve)
            eq_df.set_index("date", inplace=True)
            equity = eq_df["equity"]

            final_equity = equity.iloc[-1]
            total_return_pct = (final_equity / self.cfg.initial_cash) - 1
            days = (equity.index[-1] - equity.index[0]).days
            years = days / 365.25
            annual_return = (final_equity / self.cfg.initial_cash) ** (1 / years) - 1 if years > 0 else 0
            peak = equity.expanding().max()
            drawdown = (equity - peak) / peak
            max_dd = drawdown.min()
            daily_ret = equity.pct_change().dropna()
            sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
            calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-9 else 0
            win_days = (daily_ret > 0).mean()

            print(f"\n  ── Performance Metrics ──")
            print(f"  Total Return:     {total_return_pct:>10.2%}")
            print(f"  Annual Return:    {annual_return:>10.2%}")
            print(f"  Sharpe Ratio:     {sharpe:>10.2f}")
            print(f"  Max Drawdown:     {max_dd:>10.2%}")
            print(f"  Calmar Ratio:     {calmar:>10.2f}")
            print(f"  Daily Win Rate:   {win_days:>10.1%}")
            print(f"  Trading Days:     {len(daily_ret):>10d}")

            # Monthly
            monthly = equity.resample("ME").last().pct_change().dropna()
            if len(monthly) > 0:
                print(f"\n  ── Monthly Returns ──")
                print(f"  Positive:         {(monthly > 0).mean():>10.1%}")
                print(f"  Best:             {monthly.max():>10.2%}")
                print(f"  Worst:            {monthly.min():>10.2%}")
                print(f"  Avg:              {monthly.mean():>10.2%}")

            # Yearly
            yearly = equity.resample("YE").last().pct_change().dropna()
            if len(yearly) > 0:
                print(f"\n  ── Yearly Returns ──")
                for dt, ret in yearly.items():
                    print(f"  {dt.year}:           {ret:>10.2%}")

            # ── Top/Bottom ──
            print(f"\n  ── Top 5 Best Trades ──")
            for t in sorted(trades, key=lambda x: x.return_pct, reverse=True)[:5]:
                print(f"  {t.symbol}  {t.buy_date.date()}→{t.sell_date.date()}  "
                      f"{t.hold_days}d  buy=¥{t.buy_price:.2f}  ret={t.return_pct:+.2%}  "
                      f"Q={t.quality_score:.0%}  [{t.exit_type}]")

            print(f"\n  ── Top 5 Worst Trades ──")
            for t in sorted(trades, key=lambda x: x.return_pct)[:5]:
                print(f"  {t.symbol}  {t.buy_date.date()}→{t.sell_date.date()}  "
                      f"{t.hold_days}d  buy=¥{t.buy_price:.2f}  ret={t.return_pct:+.2%}  "
                      f"Q={t.quality_score:.0%}  [{t.exit_type}]")

            # Save
            eq_df.to_csv(r"d:\quant_framework\equity_curve_v2.csv", encoding="utf-8-sig")
            print(f"\n  Equity curve saved to: d:\\quant_framework\\equity_curve_v2.csv")

        # Save trade log
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
            "hold_days": t.hold_days,
            "quality_score": t.quality_score,
            "regime": t.regime,
            "signal": t.signal_name,
        } for t in trades]
        trade_df = pd.DataFrame(trade_records)
        trade_df.to_csv(r"d:\quant_framework\trade_log_v2.csv", encoding="utf-8-sig", index=False)
        print(f"  Trade log saved to: d:\\quant_framework\\trade_log_v2.csv")

        print(f"\n[4/5] ✓ V2 Backtest complete!")
        print("=" * 70)

        # ── V2 Summary Card ──
        print(f"\n[5/5] ── V2 Upgrade Summary ──")
        print(f"  Quality filter retained: {self._signal_passed}/{self._signal_count} signals "
              f"({self._signal_passed/max(1,self._signal_count):.0%})")
        if self.cfg.enable_smart_hold:
            avg_hold = np.mean(hold_days_list) if hold_days_list else 1
            print(f"  Smart hold avg duration: {avg_hold:.1f} days (max={self.cfg.max_hold_days})")
            extended = sum(1 for d in hold_days_list if d > 1)
            print(f"  Extended beyond T+1: {extended}/{len(hold_days_list)} trades "
                  f"({extended/max(1,len(hold_days_list)):.0%})")
        if self.cfg.enable_regime_filter:
            regime_shares = defaultdict(int)
            for h in self._regime_history:
                regime_shares[h["regime"]] += 1
            total_regime_days = sum(regime_shares.values())
            print(f"  Regime distribution: " +
                  ", ".join(f"{r}={c/total_regime_days:.0%}" for r, c in sorted(regime_shares.items())))


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="A-Share T+1 Scalp V2 Backtest (Upgraded Strategy)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_t1_backtest_v2.py
  python run_t1_backtest_v2.py --signal tdx_resonance
  python run_t1_backtest_v2.py --min-quality 0.75
  python run_t1_backtest_v2.py --no-smart-hold --no-quality-filter
  python run_t1_backtest_v2.py --compare-v1
        """,
    )
    parser.add_argument("--signal", default="tdx2_final",
                        choices=list(SIGNAL_FUNCTIONS.keys()))
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--base-position-pct", type=float, default=0.30)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--stop-loss", type=float, default=-0.05)
    parser.add_argument("--min-quality", type=float, default=0.55,
                        help="Minimum quality score (0~1)")
    parser.add_argument("--cooldown", type=int, default=5,
                        help="Cooldown days for same stock")
    parser.add_argument("--max-hold-days", type=int, default=5)
    parser.add_argument("--no-smart-hold", action="store_true",
                        help="Disable smart holding (V1 mode: T+1 only)")
    parser.add_argument("--no-quality-filter", action="store_true",
                        help="Disable quality scoring filter")
    parser.add_argument("--no-regime-filter", action="store_true",
                        help="Disable market regime filter")
    parser.add_argument("--no-volume-filter", action="store_true",
                        help="Disable volume confirmation")
    parser.add_argument("--no-gap-filter", action="store_true",
                        help="Disable gap filter")
    parser.add_argument("--data-root", default="")

    args = parser.parse_args()

    # Data root
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

    cfg = BacktestV2Config(
        signal_name=args.signal,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        base_position_pct=args.base_position_pct,
        max_stop_loss_pct=args.stop_loss,
        min_quality_score=0 if args.no_quality_filter else args.min_quality,
        cooldown_days=args.cooldown,
        max_hold_days=args.max_hold_days,
        enable_smart_hold=not args.no_smart_hold,
        enable_quality_scoring=not args.no_quality_filter,
        enable_regime_filter=not args.no_regime_filter,
        enable_volume_filter=not args.no_volume_filter,
        enable_gap_filter=not args.no_gap_filter,
    )

    print("=" * 70)
    print("  A-Share T+1 Scalp V2 — Multi-Dimension Upgraded Backtest")
    print("=" * 70)
    print(f"  Signal:  {args.signal} ({SIGNAL_FUNCTIONS[args.signal][0]})")
    print(f"  Data:    {data_root}")
    print(f"  Period:  {args.start} → {args.end}")
    print(f"  Capital: ¥{args.initial_cash:,.0f}")

    engine = T1V2BacktestEngine(cfg, data_root)
    n_stocks = engine.load_data()

    if n_stocks < 10:
        print(f"\n  ⚠ Only {n_stocks} valid stocks! Check data directory.")
        sys.exit(1)

    engine.run()
    engine.generate_report()


if __name__ == "__main__":
    main()
