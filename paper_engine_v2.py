"""模拟交易引擎 V5 — SimulatedBroker 全权委托 + RuleEngine 驱动 + ML信号原生接入

设计原则:
  1. SimulatedBroker 管资金/持仓 — 不手写dict
  2. RuleEngine 管出场规则 — 不手写止损/止盈
  3. signal_table.json 管信号源 — 不依赖 FACTOR_CACHE
  4. trade_config_master.json 管参数 — 不硬编码
  5. 前端 API 完全兼容 — paper_account.json 格式不变
  6. 没有 CSV 脏数据 — 启动时清洗
  7. 没有双循环 — 一条循环

上游:  signal_table.json → decision_adapter → auto_trade_check
下游:  paper_account.json ↔ 前端API + SSE + EventBus
参数:  trade_config_master.json (via config_loader)
开关:  auto_enabled (本地) + master_switch.can_buy (总闸)
"""

import json, os, sys, time, threading
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_framework\src")

from quant_framework.execution.brokers.simulated import SimulatedBroker
from quant_framework.execution.rules import (
    RuleEngine, AutoStopLossRule, AutoTrailingStopRule,
    CircuitBreakerRule, MaxDailyTradesRule, DailyLossLimitRule,
)
from quant_framework.execution.rules.engine import RuleAction

# ═══════════════════════════════ 文件路径 ═══════════════════════════════
STATE_FILE      = r"D:\quant_framework\paper_account.json"
MASTER_CONFIG   = r"D:\quant_framework\trade_config_master.json"
SIGNAL_TABLE    = r"D:\quant_web\data\signal_table.json"
PLAN_PATH       = r"D:\quant_web\data\auto_trade_plan.json"
STOCK_NAMES_CSV = r"D:\quant_web\stock_names_full.csv"
QUOTE_CACHE     = r"D:\quant_framework\quote_cache.json"


# ═══════════════════════════════ 工具函数 ═══════════════════════════════

def _load_master():
    """读 trade_config_master.json"""
    try:
        with open(MASTER_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _load_names():
    """加载股票名称映射"""
    names = {}
    if os.path.exists(STOCK_NAMES_CSV):
        with open(STOCK_NAMES_CSV, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip().split(",")
                if len(p) >= 2:
                    names[p[0]] = p[1]
    return names

def _resolve_name(sym, names_cache):
    clean = sym.replace("sh","").replace("sz","").replace("bj","")
    return names_cache.get(sym) or names_cache.get(clean) or sym

def _get_market_price(sym):
    """实时行情: QMT优先 → 新浪 → None"""
    code = sym.replace("sh","").replace("sz","").replace("bj","")
    # QMT缓存
    try:
        if os.path.exists(QUOTE_CACHE) and time.time() - os.path.getmtime(QUOTE_CACHE) < 5:
            with open(QUOTE_CACHE, "r") as f:
                qd = json.load(f).get("data", {})
            for qcode, tick in qd.items():
                c = qcode.replace(".SH","").replace(".SZ","").replace(".BJ","").lower()
                if c == code.lower():
                    return float(tick.get("price", 0)) or None
    except Exception:
        pass
    # 新浪兜底
    try:
        from realtime_quotes import _quote_cache
        if _quote_cache and _quote_cache.get("data"):
            q = _quote_cache["data"].get(code, {})
            p = float(q.get("close", q.get("price", 0)) or 0)
            if p > 0:
                return p
    except Exception:
        pass
    return None

def _can_trade_time():
    """A股交易时间检查"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    if t < datetime.strptime("09:25", "%H:%M").time():
        return False
    if datetime.strptime("11:30", "%H:%M").time() <= t <= datetime.strptime("13:00", "%H:%M").time():
        return False
    if t >= datetime.strptime("15:05", "%H:%M").time():
        return False
    return True


# ═══════════════════════════════ 核心引擎 ═══════════════════════════════

class PaperAccountV2:
    """模拟交易账户 V5 — SimulatedBroker 全权委托"""

    def __init__(self):
        # ── 核心: SimulatedBroker ──
        self._broker = SimulatedBroker(initial_cash=1_000_000.0)
        self._broker.connect()

        # ── A股元数据 (name, buy_date, strategy_id) ──
        self._meta: dict[str, dict] = {}       # {sym: {name, buy_date, strategy_id}}

        # ── 交易记录 (有效数据, 无脏记录) ──
        self._trades: list[dict] = []

        # ── 名称缓存 ──
        self._names = _load_names()

        # ── 运行时状态 ──
        self.auto_enabled = False
        self.start_time = datetime.now()
        self._daily_date = None
        self._daily_trade_count = 0
        self._daily_buy_count = 0
        self._daily_loss_total = 0.0
        self._day_start_equity = None

        # ── 规则引擎 (出场规则) ──
        self._rule_engine = RuleEngine(broker=self._broker)
        self._setup_rules()

        # ── 兼容旧接口 ──
        self._toggle_lock = threading.Lock()
        self._trades_archive = self._trades  # 别名

        # ── 加载持久化状态 ──
        self._load()

    # ═══════════ 资金/持仓属性 (通过 broker) ═══════════

    @property
    def cash(self) -> float:
        return self._broker._cash

    @cash.setter
    def cash(self, value: float):
        self._broker._cash = value

    @property
    def positions(self) -> dict:
        """返回兼容旧接口的持仓 dict"""
        result = {}
        for sym, pos in self._broker._positions.items():
            meta = self._meta.get(sym, {})
            result[sym] = {
                "qty": pos.volume,
                "avg_cost": round(pos.avg_cost, 2),
                "last_price": pos.current_price or pos.avg_cost,
                "name": meta.get("name", _resolve_name(sym, self._names)),
                "buy_date": meta.get("buy_date", ""),
                "strategy_id": meta.get("strategy_id", ""),
                "_verified": True,
            }
        return result

    @property
    def trade_log(self) -> list:
        return self._trades

    @property
    def total_equity(self) -> float:
        return self.get_total_equity()

    def _get_market_price(self, sym):
        return _get_market_price(sym)

    def set_config(self, key, value):
        """兼容旧接口: 不存储，只打日志"""
        pass

    def restart(self):
        """兼容旧接口: 保存+重载"""
        self._save()

    def get_pnl(self) -> float:
        return self.get_total_equity() - 1_000_000.0

    # ═══════════ 规则设置 ═══════════

    def _setup_rules(self):
        m = _load_master()
        tp = m.get("take_profit", {})
        sl = m.get("stop_loss", {})

        # 止损规则
        hard_sl = sl.get("hard", -0.055)
        soft_sl = sl.get("soft", -0.03)
        self._rule_engine.add_rule(AutoStopLossRule(threshold=hard_sl, sell_ratio=1.0))
        self._rule_engine.add_rule(AutoStopLossRule(threshold=soft_sl, sell_ratio=0.5))

        # 移动止盈规则 T1/T2 (T3=涨停炸板，暂不启用)
        for tier, key in [(1, "tp1"), (2, "tp2")]:
            t = tp.get(key, {})
            if t:
                self._rule_engine.add_rule(AutoTrailingStopRule(
                    tier=tier,
                    profit_pct=t.get("profit_pct", 0.05 if tier == 1 else 0.07),
                    trail_pct=t.get("trail_pct", 0.01 if tier == 1 else 0.02),
                    sell_ratio=t.get("sell_ratio", 0.33),
                    stop_loss=t.get("stop_loss", -0.03 if tier == 1 else -0.05),
                ))

    # ═══════════ 持久化 ═══════════

    def _clean_state_file(self):
        """加载时清洗脏数据"""
        if not os.path.exists(STATE_FILE):
            return {}, [], True
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return {}, [], True

        # 洗持仓
        positions = d.get("positions", {})
        clean_pos = {}
        for sym, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            if pos.get("qty", 0) <= 0:
                continue
            if pos.get("avg_cost", 0) <= 0:
                continue
            clean_pos[sym] = {
                "qty": int(pos["qty"]),
                "avg_cost": float(pos.get("avg_cost", 0)),
                "last_price": float(pos.get("last_price", pos.get("avg_cost", 0))),
                "name": str(pos.get("name", "")),
                "buy_date": str(pos.get("buy_date", "")),
                "strategy_id": str(pos.get("strategy_id", "")),
            }

        # 洗交易记录
        raw = d.get("trade_log", [])
        clean_trades = []
        dropped = 0
        for t in raw:
            if not isinstance(t, dict):
                dropped += 1; continue
            # 过滤明细脏记录
            if t.get("qty", 0) <= 0 and t.get("side") == "sell":
                dropped += 1; continue
            if t.get("price", 0) <= 0:
                dropped += 1; continue
            if not t.get("symbol"):
                dropped += 1; continue
            # 补全缺失的名称
            if not t.get("name"):
                t["name"] = _resolve_name(t["symbol"], self._names)
            clean_trades.append(t)

        auto = d.get("auto_enabled", True)

        if dropped:
            print(f"[PaperV2] 启动时清洗 {dropped} 条脏交易记录")

        return clean_pos, clean_trades, auto

    def _load(self):
        clean_pos, clean_trades, auto = self._clean_state_file()
        dropped = 0

        for sym, pos in clean_pos.items():
            qty = pos["qty"]
            avg = pos["avg_cost"]
            self._broker._positions[sym] = self._broker._create_position(
                sym, qty, avg, pos.get("last_price", avg))
            self._meta[sym] = {
                "name": pos.get("name", ""),
                "buy_date": pos.get("buy_date", ""),
                "strategy_id": pos.get("strategy_id", ""),
            }
            self._broker._cash -= qty * avg  # 现金减去持仓成本

        self._trades = clean_trades
        # 补全旧记录缺失的名称
        for _t in self._trades:
            if not _t.get("name"):
                _t["name"] = _resolve_name(_t.get("symbol",""), self._names)
        self.auto_enabled = auto

        print(f"[PaperV2] 加载完成: cash=¥{self.cash:,.0f} "
              f"positions={len(self.positions)} trades={len(self._trades)} "
              f"auto={self.auto_enabled}")
        # 清洗后立即写回, 覆盖旧脏文件
        self._save()
        print(f"[PaperV2] 已写回清洗后的数据到 paper_account.json")

    def _save(self):
        """持久化到 paper_account.json"""
        try:
            data = {
                "cash": round(self.cash, 2),
                "positions": self.positions,
                "trade_log": self._trades,
                "auto_enabled": self.auto_enabled,
                "daily_date": self._daily_date or datetime.now().strftime("%Y%m%d"),
                "daily_trade_count": self._daily_trade_count,
                "daily_buy_count": self._daily_buy_count,
                "daily_loss_total": self._daily_loss_total,
                "day_start_equity": self._day_start_equity,
            }
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            print(f"[PaperV2] 保存状态失败: {e}")

    # ═══════════ 下单 ═══════════

    def _can_buy(self, symbol: str, price: float, qty: int, signal_id: str = "") -> tuple[bool, str]:
        """买入前检查: 总闸 + 风控 + 涨跌停 + 资金 + 信号去重"""
        # 总闸
        try:
            from master_switch import can_buy
            if not can_buy("sim"):
                return False, "风控总闸关闭"
        except Exception:
            pass

        # 风控
        try:
            from risk_guard import PreTradeChecker
            cfg = {"max_order_value": 1_000_000, "max_daily_trades": 5,
                   "signal_min_strength": 3, "max_positions_abs": 5,
                   "max_single_position_pct": 20}
            ck = PreTradeChecker(config=cfg, positions=self.positions,
                                 cash=self.cash, total_equity=self.get_total_equity())
            action, reason, adj_qty = ck.check_buy(symbol, price, qty,
                                                    signal_id=signal_id,
                                                    daily_trades=self._daily_trade_count)
            if action == "REJECT":
                return False, reason
            if action == "REDUCE" and adj_qty < 100:
                return False, f"缩减后不足100股: {reason}"
        except Exception as e:
            return False, f"风控异常: {e}"

        # 资金
        if price * qty > self.cash * 0.5:
            return False, f"资金不足: 需¥{price*qty:,.0f} 可用¥{self.cash:,.0f}"

        return True, ""

    def place_order(self, symbol: str, side: str, price: float, qty: int,
                    trade_type: str = "auto", reason: str = "",
                    signal_source: str = "auto", signal_id: str = "") -> dict:
        """下单 — 模拟成交"""
        if side not in ("buy", "sell"):
            return {"success": False, "error": "side须是buy/sell"}

        price = round(price, 2)
        qty = max(100, qty // 100 * 100)

        # 买入检查
        if side == "buy":
            ok, msg = self._can_buy(symbol, price, qty, signal_id)
            if not ok:
                return {"success": False, "error": msg}

        # 执行
        cost = round(price * qty, 2)
        commission = max(cost * 0.00025, 5.0)  # 万2.5佣金, 最低5元
        stamp = cost * 0.0005 if side == "sell" else 0  # 千0.5印花税(仅卖出)

        if side == "buy":
            self._broker._cash -= (cost + commission)
            # 更新或创建 broker position
            if symbol in self._broker._positions:
                old = self._broker._positions[symbol]
                total_qty = old.volume + qty
                total_cost = old.avg_cost * old.volume + cost
                new_avg = total_cost / total_qty if total_qty > 0 else price
                self._broker._positions[symbol] = self._broker._create_position(
                    symbol, total_qty, new_avg, price)
            else:
                self._broker._positions[symbol] = self._broker._create_position(
                    symbol, qty, price, price)
            # 元数据
            self._meta[symbol] = {
                "name": _resolve_name(symbol, self._names),
                "buy_date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_id": "auto" if trade_type == "auto" else "",
            }
            self._daily_buy_count += 1
        else:
            # 卖出
            if symbol not in self._broker._positions:
                return {"success": False, "error": "无此持仓"}
            pos = self._broker._positions[symbol]
            sell_qty = min(qty, pos.volume)
            sell_amount = price * sell_qty
            sell_commission = max(sell_amount * 0.00025, 5.0) + sell_amount * 0.0005
            pnl = (price - pos.avg_cost) * sell_qty - sell_commission

            self._broker._cash += (sell_amount - sell_commission)
            remaining = pos.volume - sell_qty
            if remaining < 100:
                del self._broker._positions[symbol]
                self._meta.pop(symbol, None)
            else:
                self._broker._positions[symbol] = self._broker._create_position(
                    symbol, remaining, pos.avg_cost, price)

            if pnl < 0:
                self._daily_loss_total += pnl

        self._daily_trade_count += 1

        # 交易记录
        rec = {
            "symbol": symbol,
            "name": self._meta.get(symbol, {}).get("name", _resolve_name(symbol, self._names)),
            "side": side,
            "price": price,
            "qty": qty,
            "cost": cost,
            "amount": cost,
            "fee": round(commission + stamp, 2),
            "pnl": round(pnl, 2) if side == "sell" else None,
            "cost_price": pos.avg_cost if side == "sell" and 'pos' in dir() else price,
            "signal_source": signal_source,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": trade_type,
            "reason": reason or (f"{'买入' if side=='buy' else '卖出'}"),
        }
        self._trades.append(rec)

        self._save()

        # EventBus事件
        try:
            from quant_framework.engine.event_bus import EventBus
            import app as _ap
            eb = getattr(_ap, '_event_bus', None)
            if eb:
                eb.publish("order", rec)
        except Exception:
            pass

        return {"success": True, "symbol": symbol, "side": side,
                "qty": qty, "price": price, "pnl": rec.get("pnl")}

    # ═══════════ 自动交易 ═══════════

    def auto_trade_check(self, signals: list | None = None) -> list[dict]:
        """自动交易检查 — 信号 → decision_adapter → 下单"""
        if not self.auto_enabled:
            return []
        if not _can_trade_time():
            return []

        # 日切重置
        today = datetime.now().strftime("%Y%m%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_trade_count = 0
            self._daily_buy_count = 0
            self._daily_loss_total = 0.0
            self._day_start_equity = self.get_total_equity()

        # 加载信号 (如果调用方没传)
        if signals is None:
            signals = self._load_ml_signals()

        actions = []

        # ── 出场检查: RuleEngine 扫描持仓 ──
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            mp = _get_market_price(sym)
            if mp and mp > 0:
                pos["last_price"] = mp

            rule_pos = {"symbol": sym, "avg_cost": pos["avg_cost"],
                        "qty": pos["qty"], "last_price": pos["last_price"]}
            md = {"price": pos["last_price"], "prev_close": pos["avg_cost"]}

            for rule in self._rule_engine._position_rules:
                try:
                    action = rule.check(rule_pos, md, {})
                    if action and action.action == "sell":
                        sell_qty = min(action.qty, pos["qty"])
                        sell_qty = max(100, sell_qty // 100 * 100)
                        r = self.place_order(sym, "sell", pos["last_price"], sell_qty,
                                            trade_type="auto", reason=action.reason)
                        if r.get("success"):
                            actions.append(r)
                        break  # 每tick每个仓位只执行最高优先级规则
                except Exception:
                    continue

        # ── 进场: 处理信号 ──
        if signals and self._daily_buy_count < 5:
            try:
                from decision_adapter import process_signals as _da
                status = {
                    "total_equity": self.get_total_equity(),
                    "positions": [{"symbol": s, "market_value":
                        p.get("last_price", p["avg_cost"]) * p["qty"]}
                        for s, p in self.positions.items()]
                }
                # decision_adapter 需要 stock_data → 用 parquet
                _sd = {}
                try:
                    from data_loader import load_stock_data_cache
                    _sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
                except Exception:
                    pass

                orders = _da(signals[:15], _sd, status)
                for order in orders[:3]:  # 每轮最多3笔
                    sym = order["symbol"]
                    if sym in self.positions:
                        continue
                    if self._daily_buy_count >= 5:
                        break
                    prc = order.get("close", order.get("raw_close", 0))
                    if prc <= 0:
                        continue
                    qty = order.get("shares", 100)
                    reason = f"信号{order.get('buy_signal','?')}级({order.get('position_pct','?')}%仓) [ML]"
                    r = self.place_order(sym, "buy", prc, qty,
                                        trade_type="auto", reason=reason,
                                        signal_source="auto")
                    if r.get("success"):
                        actions.append(r)
            except Exception as e:
                print(f"[PaperV2] decision_adapter失败: {e}", flush=True)

        if actions:
            self._save()
        return actions

    def _load_ml_signals(self) -> list[dict]:
        """从 signal_table.json 加载ML信号"""
        if not os.path.exists(SIGNAL_TABLE):
            return []
        try:
            with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
                table = json.load(f)
            result = []
            for s in table:
                if not s.get("auto_enabled"):
                    continue
                sc = s.get("combined_score", 0) or 0
                result.append({
                    "symbol": s.get("symbol", ""),
                    "name": s.get("name", ""),
                    "buy_signal": 5 if sc >= 90 else 4 if sc >= 80 else 3 if sc >= 70 else 2,
                    "close": s.get("close", 0),
                    "change_pct": s.get("change_pct", 0) or 0,
                    "vol_ratio": s.get("vol_ratio", 1) or 1,
                    "industry": s.get("industry", ""),
                    "power_score": sc,
                    "strategy": "ml_daily",
                    "score": sc,
                })
            if result:
                print(f"[PaperV2] ML信号: {len(result)}条")
            return result
        except Exception as e:
            print(f"[PaperV2] 信号加载失败: {e}", flush=True)
            return []

    # ═══════════ 状态查询 (前端API兼容) ═══════════

    def get_total_equity(self, quotes=None) -> float:
        mv = self.get_market_value(quotes)
        return self.cash + mv

    def get_market_value(self, quotes=None) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            code = sym.replace("sh", "").replace("sz", "")
            price = pos.get("last_price", pos["avg_cost"])
            if quotes and code in quotes:
                price = float(quotes[code].get("close", price) or price)
            total += price * pos["qty"]
        return round(total, 2)

    def get_status(self, quotes=None) -> dict:
        """返回前端 /api/paper-trade/v2 的完整数据结构"""
        try:
            return self._get_status_impl(quotes)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"code": 500, "error": str(e),
                    "total_equity": 0, "total_pnl": 0, "cash": 0,
                    "positions": [], "position_count": 0}

    def _get_status_impl(self, quotes=None) -> dict:
        import numpy as np

        # 持仓列表
        positions = []
        for sym, pos in self.positions.items():
            code = sym.replace("sh", "").replace("sz", "")
            price = _get_market_price(sym) or pos.get("last_price") or pos["avg_cost"]
            pnl = (price - pos["avg_cost"]) * pos["qty"]
            pnl_pct = (price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] > 0 else 0

            c = code.replace("sh","").replace("sz","")
            bd = ("科创板" if c.startswith(("688","689")) else "创业板" if c.startswith(("300","301"))
                  else "沪主板" if c.startswith(("600","601","603","605"))
                  else "深主板" if c.startswith(("000","001","002","003"))
                  else "北交所" if c.startswith(("8","4")) else "其他")

            positions.append({
                "symbol": sym,
                "name": pos.get("name") or _resolve_name(sym, self._names),
                "board": bd,
                "qty": pos["qty"], "quantity": pos["qty"],
                "avg_cost": round(pos["avg_cost"], 2), "cost_price": round(pos["avg_cost"], 2),
                "last_price": round(price, 2), "current_price": round(price, 2),
                "market_value": round(price * pos["qty"], 2),
                "pnl": round(pnl, 2), "unrealized_pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2), "unrealized_pnl_pct": round(pnl_pct, 2),
                "buy_date": pos.get("buy_date", ""),
            })

        # 胜率 & 夏普
        sells = [t for t in self._trades if t.get("side") == "sell" and t.get("pnl") is not None]
        buys = [t for t in self._trades if t.get("side") == "buy"]
        wins = [s for s in sells if s.get("pnl", 0) > 0]
        wr = round(len(wins) / len(sells) * 100, 1) if sells else None

        returns = []
        buy_q = {b["symbol"]: b for b in buys}
        for s in sells:
            sym = s.get("symbol", "")
            b = buy_q.get(sym, {})
            cost = s.get("cost", b.get("cost", s.get("price", 0) * s.get("qty", 100)))
            if cost > 0:
                returns.append(s.get("pnl", 0) / cost)

        sharpe = 0.0
        if returns and len(returns) >= 2:
            arr = np.array(returns)
            std = max(np.std(arr), 1e-8)
            sharpe = round(float(np.mean(arr) / std) * np.sqrt(min(252, len(arr))), 2)

        # 总盈亏 & 回撤
        initial = 1_000_000.0
        current_eq = self.get_total_equity(quotes)
        total_pnl = round(current_eq - initial, 2)

        eq = initial
        peak = initial
        for t in self._trades:
            if t.get("side") == "sell":
                eq += t.get("pnl", 0)
            if eq > peak:
                peak = eq
        max_dd = round((eq - peak) / peak * 100, 2) if peak > 0 else 0.0
        max_dd = max(max_dd, -100.0)
        calmar = round(abs((total_pnl / initial * 100) / max(abs(max_dd), 0.01)), 2)

        return {
            "code": 200,
            "cash": round(self.cash, 2),
            "market_value": round(self.get_market_value(quotes), 2),
            "total_equity": round(current_eq, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return": round(total_pnl / initial * 100, 2),
            "win_rate": round(wr, 1) if wr is not None else 0,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "calmar": calmar,
            "positions": positions,
            "trade_log": self._trades[-30:],
            "auto_enabled": self.auto_enabled,
            "position_count": len(positions),
        }


# ═══════════════════════════════ 全局实例 ═══════════════════════════════

paper = PaperAccountV2()


# ═══════════════════════════════ app.py 集成 ═══════════════════════════════

def init_stock_data():
    """app.py 数据预热完成后调用，注入 stock_data"""
    try:
        from data_loader import load_stock_data_cache
        sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
        print(f"[PaperV2] stock_data 已注入: {len(sd)}只")
        return sd
    except Exception as e:
        print(f"[PaperV2] stock_data加载失败: {e}")
        return {}
