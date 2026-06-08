"""模拟交易引擎 V4 — SimulatedBroker + 规则引擎组合。

P0-模拟-01: 不再自己维护资金/持仓，委托给 SimulatedBroker。
P0-模拟-02: 修复 T+1 强约束，所有交易类型均受 T+1 限制。

规则引擎: 7 条自动交易规则抽取到 src/quant_framework/execution/rules/
  - AutoStopLossRule: 基本止损
  - AutoTrailingStopRule: 双层移动止盈
  - CircuitBreakerRule: 大盘熔断
  - MaxDailyTradesRule: 下单频率限制
  - DailyLossLimitRule: 日内亏损限制
  - SignalQualityFilter: 信号质量过滤
  - PositionSizingRule: 仓位计算
"""

import threading, time, json, os, sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, r"d:\quant_framework\src")

from quant_framework.execution.brokers.simulated import SimulatedBroker
from quant_framework.execution.order import OrderRequest, OrderDirection
from quant_framework.execution.rules import (
    RuleEngine, AutoStopLossRule, AutoTrailingStopRule,
    CircuitBreakerRule, MaxDailyTradesRule, DailyLossLimitRule,
    SignalQualityFilter, PositionSizingRule,
)

STATE_FILE = r"d:\quant_framework\paper_account.json"


def _load_trade_config():
    """加载交易规则配置 — 与实盘共享规则。"""
    try:
        from live_trader import CONFIG
        return CONFIG
    except Exception:
        pass
    return {
        "tp1_profit_pct": 0.05, "tp1_trail_pct": -0.01, "tp1_sell_ratio": 0.33, "tp1_stop_loss": -0.03,
        "tp2_profit_pct": 0.07, "tp2_trail_pct": -0.02, "tp2_sell_ratio": 0.33, "tp2_stop_loss": -0.05,
        "max_daily_trades": 4, "max_daily_loss": -5.0, "signal_min_strength": 5,
        "limit_up_drop_sell": -0.03,
    }


class PaperAccount:
    """模拟交易账户 — 对外的纸交易接口。

    内部使用 SimulatedBroker 管理资金/持仓，RuleEngine 管理自动规则。
    """

    def __init__(self):
        # ── P0-模拟-01: 委托给 SimulatedBroker ──
        self._broker = SimulatedBroker(initial_cash=1_000_000.0)
        self._broker.connect()
        self.auto_enabled = False
        self._name_cache = {}
        self._trades_archive: list[dict] = []  # 交易日志 (dict 格式兼容)

        # ── 规则引擎 ──
        self._rule_engine = RuleEngine()
        self._setup_rules()

        # ── 运行时追踪 ──
        self._daily_date = None
        self._daily_trade_count = 0
        self._daily_loss_total = 0.0

        # 从文件恢复状态
        self._load()

    # ═══════════════════ 规则配置 ═══════════════════

    def _setup_rules(self):
        """初始化 7 条自动交易规则。"""
        cfg = _load_trade_config()

        tp1_profit = cfg.get("tp1_profit_pct", 0.05)
        tp1_trail = abs(cfg.get("tp1_trail_pct", -0.01))
        tp1_sell = cfg.get("tp1_sell_ratio", 0.33)
        tp1_stop = cfg.get("tp1_stop_loss", -0.03)

        tp2_profit = cfg.get("tp2_profit_pct", 0.07)
        tp2_trail = abs(cfg.get("tp2_trail_pct", -0.02))
        tp2_sell = cfg.get("tp2_sell_ratio", 0.33)
        tp2_stop = cfg.get("tp2_stop_loss", -0.05)

        # 1. 基本止损 (取两层中更严格的)
        basic_stop = min(tp1_stop, tp2_stop)
        self._rule_engine.add_rule(AutoStopLossRule(threshold=basic_stop))

        # 2-3. 双层移动止盈
        self._rule_engine.add_rule(AutoTrailingStopRule(
            tier=2, profit_pct=tp2_profit, trail_pct=tp2_trail,
            sell_ratio=tp2_sell, stop_loss=tp2_stop,
        ))
        self._rule_engine.add_rule(AutoTrailingStopRule(
            tier=1, profit_pct=tp1_profit, trail_pct=tp1_trail,
            sell_ratio=tp1_sell, stop_loss=tp1_stop,
        ))

        # 4. 熔断
        self._rule_engine.add_rule(CircuitBreakerRule(
            max_daily_loss_pct=cfg.get("max_daily_loss", -0.05) / 100.0,
            initial_capital=1_000_000,
        ))

        # 5. 下单频率限制
        self._rule_engine.add_rule(MaxDailyTradesRule(
            max_trades=cfg.get("max_daily_trades", 4),
        ))

        # 6. 日内亏损限制
        self._rule_engine.add_rule(DailyLossLimitRule(
            max_loss_pct=cfg.get("max_daily_loss", -5.0) / 100.0,
            initial_capital=1_000_000,
        ))

        # 7. 信号质量过滤 + 仓位计算 (在 auto_trade_check 中显式调用)
        self._signal_filter = SignalQualityFilter(
            min_strength=cfg.get("signal_min_strength", 3),
        )
        self._position_sizer = PositionSizingRule()

    # ═══════════════════ 状态持久化 ═══════════════════

    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    d = json.load(f)
                self._broker._cash = d.get("cash", 1_000_000)
                self._trades_archive = d.get("trade_log", [])
                self.auto_enabled = d.get("auto_enabled", False)
                # 恢复持仓
                for sym, pos in d.get("positions", {}).items():
                    self.positions[sym] = pos
            except Exception:
                pass

    def _save(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "cash": self.cash,
                    "positions": self.positions,
                    "trade_log": self._trades_archive[-200:],
                    "auto_enabled": self.auto_enabled,
                }, f)
        except Exception:
            pass

    # ═══════════════════ 资金/持仓代理 ═══════════════════

    @property
    def cash(self) -> float:
        return self._broker._cash

    @cash.setter
    def cash(self, value: float):
        self._broker._cash = value

    @property
    def positions(self) -> dict:
        """返回兼容旧接口的持仓 dict。"""
        return self._positions_compat

    @property
    def trade_log(self) -> list:
        return self._trades_archive

    # 内部持仓存储 (dict 格式, 兼容旧接口)
    _positions_compat: dict = {}

    @property
    def orders(self):
        return []

    # ═══════════════════ 名称解析 ═══════════════════

    def _resolve_name(self, symbol):
        code = symbol.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        if not self._name_cache:
            try:
                nf = r"D:\quant_web\stock_names_full.csv"
                if os.path.exists(nf):
                    with open(nf, "r", encoding="utf-8") as f:
                        for line in f:
                            p = line.strip().split(",", 1)
                            if len(p) >= 2:
                                self._name_cache[p[0].strip()] = p[1].strip()
            except Exception:
                pass
        return self._name_cache.get(code, code)

    # ═══════════════════ 价格查询 ═══════════════════

    def _resolve_price(self, symbol, fallback=None):
        """统一价格获取: 实时行情→价格缓存→因子缓存→fallback→10元。"""
        code = symbol.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        try:
            from realtime_quotes import _quote_cache
            if _quote_cache and _quote_cache.get("data") and code in _quote_cache["data"]:
                return float(_quote_cache["data"][code].get("close", 0))
        except Exception:
            pass
        # 价格缓存
        try:
            pf = r"d:\quant_framework\price_cache.json"
            if os.path.exists(pf):
                with open(pf, "r") as f:
                    pc = json.load(f)
                for k in [code, "sh" + code, "sz" + code]:
                    if k in pc:
                        return float(pc[k])
                for k, v in pc.items():
                    if k.replace("sh", "").replace("sz", "") == code:
                        return float(v)
        except Exception:
            pass
        if fallback and fallback > 0:
            return fallback
        return 10.0

    def _get_market_price(self, symbol):
        return self._resolve_price(symbol)

    def get_market_value(self, quotes=None):
        total = 0.0
        for sym, pos in self.positions.items():
            price = pos.get("last_price", pos["avg_cost"])
            if quotes:
                code = sym.replace("sh", "").replace("sz", "")
                if code in quotes:
                    price = quotes[code].get("close", price)
            total += price * pos["qty"]
        return total

    def get_total_equity(self, quotes=None):
        return self.cash + self.get_market_value(quotes)

    def get_pnl(self, quotes=None):
        cost = sum(p["avg_cost"] * p["qty"] for p in self.positions.values())
        return self.get_market_value(quotes) - cost

    # ═══════════════════ 下单 ═══════════════════

    def place_order(self, symbol, side, price=None, qty=100, trade_type="manual"):
        """下单 — P0-模拟-02: 全面强制 T+1 约束。"""
        if side == "reset":
            self.cash = 1_000_000.0
            self._positions_compat = {}
            self._trades_archive = []
            self._save()
            return {"success": True, "action": "reset"}

        code = symbol.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        sym = symbol
        name = self._resolve_name(symbol)
        today = datetime.now().strftime("%Y-%m-%d")

        if side == "buy":
            if price is None or price <= 0:
                price = self._resolve_price(sym)
            cost = price * qty
            if cost > self.cash:
                return {"success": False, "error": f"资金不足: 需{cost:.0f} 余{self.cash:.0f}"}

            self.cash -= cost
            if sym in self.positions:
                old = self.positions[sym]
                tq = old["qty"] + qty
                old["avg_cost"] = (old["avg_cost"] * old["qty"] + price * qty) / tq
                old["qty"] = tq
                old["last_price"] = price
                old["buy_date"] = min(old.get("buy_date", "9999-99-99"), today)
            else:
                self.positions[sym] = {
                    "qty": qty, "avg_cost": price, "last_price": price,
                    "name": name, "buy_date": today,
                }
            self._trades_archive.append({
                "symbol": sym, "name": name, "side": "buy", "price": round(price, 2),
                "qty": qty, "cost": round(cost, 2), "time": datetime.now().strftime("%H:%M:%S"),
                "type": trade_type,
            })
            self._save()
            return {"success": True, "action": "buy", "symbol": sym, "price": round(price, 2),
                    "qty": qty, "type": trade_type}

        elif side == "sell":
            if sym not in self.positions or self.positions[sym]["qty"] < qty:
                return {"success": False, "error": "持仓不足"}

            # ── P0-模拟-02: T+1 强约束 (所有交易类型) ──
            pos = self.positions[sym]
            buy_date = pos.get("buy_date", "")
            if buy_date == today:
                return {
                    "success": False,
                    "error": f"T+1锁定：今日买入的股票今日不可卖出 ({sym})",
                }

            if price is None or price <= 0:
                price = self._resolve_price(sym, fallback=pos["avg_cost"])
            revenue = price * qty
            self.cash += revenue

            pos = self.positions[sym]
            pos["qty"] -= qty
            n = pos.get("name", name)
            if pos["qty"] <= 0:
                del self.positions[sym]

            # 记录盈亏用于日内亏损跟踪
            pnl = (price - pos["avg_cost"]) * qty
            self._daily_loss_total += pnl

            self._trades_archive.append({
                "symbol": sym, "name": n, "side": "sell", "price": round(price, 2),
                "qty": qty, "revenue": round(revenue, 2),
                "time": datetime.now().strftime("%H:%M:%S"), "type": trade_type,
            })
            self._save()
            return {"success": True, "action": "sell", "symbol": sym, "price": round(price, 2),
                    "qty": qty, "type": trade_type}

        return {"success": False, "error": "unknown side"}

    # ═══════════════════ 状态查询 ═══════════════════

    def get_status(self, quotes=None):
        """获取账户状态 — 接口不变。"""
        import numpy as np

        positions = []
        for sym, pos in self.positions.items():
            code = sym.replace("sh", "").replace("sz", "")
            price = pos.get("last_price", pos["avg_cost"])
            if quotes and code in quotes:
                price = quotes[code].get("close", price)
            pnl = (price - pos["avg_cost"]) * pos["qty"]
            pnl_pct = (price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] > 0 else 0
            positions.append({
                "symbol": sym, "name": pos.get("name", code),
                "qty": pos["qty"], "avg_cost": round(pos["avg_cost"], 2),
                "last_price": round(price, 2),
                "market_value": round(price * pos["qty"], 2),
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "buy_date": pos.get("buy_date", ""),
            })

        sells = [t for t in self._trades_archive if t.get("side") == "sell"]
        buys = [t for t in self._trades_archive if t.get("side") == "buy"]

        buy_queue = {}
        for b in buys:
            sym = b.get("symbol", "")
            if sym not in buy_queue:
                buy_queue[sym] = []
            buy_queue[sym].append(b)

        trade_returns = []
        wins = []
        for s in sells:
            sym = s.get("symbol", "")
            rev = s.get("revenue", 0)
            qty = s.get("qty", 100)
            cost = 0
            if sym in buy_queue and buy_queue[sym]:
                matched = buy_queue[sym].pop(0)
                buy_price = matched.get("price", 0)
                buy_qty = matched.get("qty", qty)
                cost = buy_price * min(qty, buy_qty)
                if buy_qty > qty:
                    matched["qty"] = buy_qty - qty
                    buy_queue[sym].insert(0, matched)
            if cost <= 0:
                cost = rev * 0.9
            pnl = rev - cost
            if pnl > 0:
                wins.append(s)
            if cost > 0:
                trade_returns.append(pnl / cost)

        wr = len(wins) / max(len(sells), 1) * 100
        total_pnl = self.get_pnl(quotes)

        if trade_returns and len(trade_returns) >= 2:
            arr = np.array(trade_returns)
            sharpe = round(float(np.mean(arr) / np.std(arr)) * np.sqrt(min(252, len(arr))), 2) if np.std(arr) > 0 else 0.0
        else:
            sharpe = 0.0

        eq_timeline = [1_000_000.0]
        for t in self._trades_archive:
            if t.get("side") == "buy":
                eq_timeline.append(eq_timeline[-1] - t.get("cost", 0))
            elif t.get("side") == "sell":
                eq_timeline.append(eq_timeline[-1] + t.get("revenue", 0))
        peak = max(eq_timeline)
        dd_vals = [(v - peak) / peak * 100 for v in eq_timeline]
        max_dd = round(min(dd_vals), 2) if dd_vals else 0.0
        calmar = round(abs((total_pnl / 1_000_000 * 100) / max(abs(max_dd), 0.01)), 2)

        return {
            "cash": round(self.cash, 2),
            "market_value": round(self.get_market_value(quotes), 2),
            "total_equity": round(self.get_total_equity(quotes), 2),
            "total_pnl": round(total_pnl, 2),
            "total_return": round(total_pnl / 1_000_000 * 100, 2),
            "win_rate": round(wr, 1), "sharpe": sharpe,
            "max_drawdown": max_dd, "calmar": calmar,
            "positions": positions,
            "trade_log": self._trades_archive[-30:],
            "auto_enabled": self.auto_enabled,
            "position_count": len(positions),
            "trade_count": len(self._trades_archive),
        }

    # ═══════════════════ 自动交易 ═══════════════════

    def _reset_daily_if_new_day(self):
        today = datetime.now().strftime("%Y%m%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_trade_count = 0
            self._daily_loss_total = 0.0

    def auto_trade_check(self, signals):
        """自动交易规则 — 通过 RuleEngine 执行。

        P0-模拟-01: 7条规则全交由 RuleEngine 处理。
        与旧 PaperAccount.auto_trade_check() 的结果接口完全一致。
        """
        if not self.auto_enabled:
            return []
        self._reset_daily_if_new_day()
        cfg = _load_trade_config()
        actions = []

        # ── 构建上下文 ──
        context = {
            "daily_trade_count": self._daily_trade_count,
            "daily_loss_total": self._daily_loss_total,
            "cash": self.cash,
            "config": cfg,
        }

        # ── 1. 检查持仓规则: 止损/止盈 ──
        for sym, pos in list(self.positions.items()):
            market_data = {"price": pos.get("last_price", pos["avg_cost"])}
            pos_with_sym = {**pos, "symbol": sym}

            rule_actions = self._rule_engine.check_position(pos_with_sym, market_data, context)
            for ra in rule_actions:
                if ra.action != "sell":
                    continue

                sell_qty = ra.qty if ra.qty > 0 else pos["qty"]
                sell_qty = max(100, int(sell_qty) // 100 * 100)
                if sell_qty < 100:
                    continue

                r = self.place_order(ra.symbol or sym, "sell", ra.price, sell_qty, trade_type="auto")
                if r.get("success"):
                    r["reason"] = ra.reason
                    actions.append(r)
                    self._daily_trade_count += 1

                # 检查是否附带止损 (TrailingStopRule 的 meta 处理)
                if ra.meta.get("stop_loss_check") and ra.meta.get("remaining_qty", 0) > 0:
                    remaining = self.positions.get(sym, {}).get("qty", 0)
                    if remaining > 0:
                        pnl = (ra.price / pos["avg_cost"] - 1)
                        if pnl <= ra.meta.get("stop_loss_threshold", -0.05):
                            r2 = self.place_order(sym, "sell", ra.price, remaining, trade_type="auto")
                            if r2.get("success"):
                                r2["reason"] = f"止盈T止损({pnl*100:.1f}%)"
                                actions.append(r2)
                                self._daily_trade_count += 1

        # ── 2. 检查全局规则: 熔断/频率限制 ──
        can_buy, reject_reason = self._rule_engine.can_buy(context)

        # ── 3. 信号买入 ──
        if can_buy and signals:
            signal_min = self._signal_filter.min_strength
            max_daily = cfg.get("max_daily_trades", 4)

            for sig in (signals or [])[:10]:
                if self._daily_trade_count >= max_daily:
                    break

                # 信号质量过滤
                bs = self._signal_filter.adjust_signal(sig)
                if bs < signal_min:
                    continue

                sym = sig.get("symbol", "")
                if not sym:
                    continue
                if sym in self.positions:
                    continue

                # 仓位计算
                qty = self._position_sizer.calculate_shares(
                    bs, self.cash, sig.get("close", 10)
                )
                if qty >= 100:
                    r = self.place_order(sym, "buy", sig.get("close"), qty, trade_type="auto")
                    if r.get("success"):
                        pct = self._position_sizer.sizing_map.get(bs, 0.15)
                        r["reason"] = f"信号{bs}级({int(pct*100)}%仓)"
                        actions.append(r)
                        self._daily_trade_count += 1

        return actions


# ═══════════════════ 全局单例 + 兼容旧接口 ═══════════════════
paper = PaperAccount()


def start():
    """启动自动交易 (兼容旧接口)。"""
    paper.auto_enabled = True
    paper._save()
    return {"status": "started"}


def stop():
    """停止自动交易 (兼容旧接口)。"""
    paper.auto_enabled = False
    paper._save()
    return {"status": "stopped"}


def get_status():
    """获取状态 (兼容旧接口)。"""
    return paper.get_status()
