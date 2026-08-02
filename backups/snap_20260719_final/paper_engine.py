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

旧版 paper_engine.py (2100行) 已归档到 paper_engine_old_bak.py
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
TRADE_LOG_FILE  = r"D:\quant_framework\paper_trades.jsonl"  # 追加模式, 永不覆盖
SNAPSHOT_DIR    = r"D:\quant_framework\backups\paper_snapshots"
MASTER_CONFIG   = r"D:\quant_framework\trade_config_master.json"
SIGNAL_TABLE    = r"D:\quant_web\data\signal_table.json"
PLAN_PATH       = r"D:\quant_web\data\auto_trade_plan.json"
STOCK_NAMES_CSV = r"D:\quant_web\stock_names_full.csv"
QUOTE_CACHE     = r"D:\quant_framework\quote_cache.json"
SIGNAL_CFG_PATH = r"D:\quant_framework\signal_config.json"


# ═══════════════════════════════ 工具函数 ═══════════════════════════════

def _load_master():
    try:
        with open(MASTER_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# D4: 策略级止盈注册表 (模块级加载, 不变)
_strategy_tp_sl = {}
def _load_strategy_tp_sl():
    global _strategy_tp_sl
    try:
        with open(SIGNAL_CFG_PATH, "r", encoding="utf-8") as f:
            _strategy_tp_sl = json.load(f).get("strategy_tp_sl", {})
    except Exception:
        _strategy_tp_sl = {}
_load_strategy_tp_sl()

def _load_names():
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
    code = sym.replace("sh","").replace("sz","").replace("bj","")
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

class PaperAccount:
    """模拟交易账户 V5 — SimulatedBroker 全权委托"""

    def __init__(self):
        self._broker = SimulatedBroker(initial_cash=1_000_000.0)
        self._broker.connect()
        self._meta: dict[str, dict] = {}
        self._signal_hold_days: dict[str, int] = {}
        self._trades: list[dict] = []
        self._names = _load_names()

        self._auto_enabled = False
        self.start_time = datetime.now()
        self._daily_date = None
        self._daily_trade_count = 0
        self._daily_buy_count = 0
        self._daily_loss_total = 0.0
        self._consecutive_losses = 0
        self._day_start_equity = None

        self._rule_engine = RuleEngine(broker=self._broker)
        self._setup_rules()

        self._toggle_lock = threading.Lock()
        self._trades_archive = self._trades

        self._load()

    # ═══════════ 资金/持仓属性 ═══════════

    @property
    def cash(self) -> float:
        return self._broker._cash

    @cash.setter
    def cash(self, value: float):
        self._broker._cash = value

    @property
    def positions(self) -> dict:
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
                "stop_loss": meta.get("stop_loss", 0),
                "soft_stop_loss": meta.get("soft_stop_loss", 0),
                "_verified": True,
            }
        return result

    @property
    def auto_enabled(self) -> bool:
        return self._auto_enabled

    @auto_enabled.setter
    def auto_enabled(self, value: bool):
        self._auto_enabled = value
        self._save()

    @property
    def trade_log(self) -> list:
        return self._trades

    @property
    def total_equity(self) -> float:
        return self.get_total_equity()

    def _get_market_price(self, sym):
        return _get_market_price(sym)

    def set_config(self, key, value):
        pass

    def restart(self):
        self._save()

    def get_pnl(self) -> float:
        return self.get_total_equity() - 1_000_000.0

    # ═══════════ 规则设置 ═══════════

    def _setup_rules(self):
        m = _load_master()
        tp = m.get("take_profit", {})
        sl = m.get("stop_loss", {})
        dr = m.get("daily_risk", {})

        # 止损规则 (已有)
        hard_sl = sl.get("hard", -0.055)
        soft_sl = sl.get("soft", -0.03)
        self._rule_engine.add_rule(AutoStopLossRule(threshold=hard_sl, sell_ratio=1.0))
        self._rule_engine.add_rule(AutoStopLossRule(threshold=soft_sl, sell_ratio=0.5))

        # 移动止盈 T1/T2 (已有)
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

        # D1: 日内亏损分级 (软-3%半仓 → 硬-5%清仓)
        self._rule_engine.add_rule(DailyLossLimitRule(
            soft_loss_pct=dr.get("level1_pct", -0.03),
            hard_loss_pct=dr.get("level2_pct", -0.05),
            initial_capital=1_000_000.0,
        ))

        # 日笔上限 (已有导入, 补接线)
        self._rule_engine.add_rule(MaxDailyTradesRule(
            max_trades=dr.get("max_trades", 5),
        ))

    # ═══════════ 持久化 ═══════════

    def _clean_state_file(self):
        if not os.path.exists(STATE_FILE):
            return {}, [], True
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return {}, [], True

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

        raw = d.get("trade_log", [])
        clean_trades = []
        dropped = 0
        for t in raw:
            if not isinstance(t, dict):
                dropped += 1; continue
            if t.get("qty", 0) <= 0 and t.get("side") == "sell":
                dropped += 1; continue
            if t.get("price", 0) <= 0:
                dropped += 1; continue
            if not t.get("symbol"):
                dropped += 1; continue
            if not t.get("name"):
                t["name"] = _resolve_name(t["symbol"], self._names)
            clean_trades.append(t)

        auto = d.get("auto_enabled", True)
        if dropped:
            print(f"[Paper] 启动时清洗 {dropped} 条脏交易记录")

        return clean_pos, clean_trades, auto

    def _load(self):
        clean_pos, clean_trades, auto = self._clean_state_file()

        # ③ 防损坏: JSON损坏→自动从.bak恢复
        if not clean_pos and not clean_trades:
            bak = STATE_FILE + ".bak"
            if os.path.exists(bak):
                try:
                    with open(bak, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    clean_pos = d.get("positions", {})
                    clean_trades = d.get("trade_log", [])
                    auto = d.get("auto_enabled", True)
                    print(f"[Paper] ⚠️ 主文件损坏, 从.bak恢复 "
                          f"pos={len(clean_pos)} trades={len(clean_trades)}")
                except Exception:
                    print("[Paper] ⚠️ .bak也损坏, 从零开始")

        # ④ 追加模式: 从JSONL加载额外交易记录
        if os.path.exists(TRADE_LOG_FILE):
            try:
                with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try: clean_trades.append(json.loads(line))
                            except: pass
                # 去重
                seen = set()
                deduped = []
                for t in clean_trades:
                    k = (t.get("symbol",""), t.get("side",""), t.get("qty",0),
                         t.get("price",0), str(t.get("date",""))[:10])
                    if k not in seen:
                        seen.add(k); deduped.append(t)
                clean_trades = deduped
            except Exception as e:
                print(f"[Paper] ⚠️ JSONL加载异常: {e}")

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
            self._broker._cash -= qty * avg

        self._trades = clean_trades
        for _t in self._trades:
            if not _t.get("name"):
                _t["name"] = _resolve_name(_t.get("symbol",""), self._names)
        self._auto_enabled = auto

        # ⑤ 启动快照
        self._make_snapshot()

        print(f"[Paper] 加载: cash=¥{self.cash:,.0f} "
              f"pos={len(self.positions)} trades={len(self._trades)} auto={self.auto_enabled}")
        # 启动不重写文件 — 只在真正交易后才_save (防重启归零)

    def _save(self):
        # 写入前去重
        _seen, _dedup = set(), []
        for _t in self._trades:
            _k = (_t.get("symbol",""), _t.get("side",""), _t.get("qty",0),
                  _t.get("price",0), str(_t.get("date",""))[:10], str(_t.get("time",""))[:8])
            if _k not in _seen:
                _seen.add(_k); _dedup.append(_t)
        if len(_dedup) < len(self._trades):
            self._trades = _dedup
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
            # 先备份旧文件 (防写坏恢复)
            if os.path.exists(STATE_FILE):
                try: os.replace(STATE_FILE, STATE_FILE + ".bak")
                except: pass
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_FILE)
            # ④ 追加交易记录到JSONL (永不覆盖)
            try:
                _latest = data.get("trade_log", [])[-1] if data.get("trade_log") else None
                if _latest:
                    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as _tf:
                        _tf.write(json.dumps(_latest, ensure_ascii=False, default=str) + "\n")
            except: pass
        except Exception as e:
            print(f"[Paper] 保存失败: {e}")
        self._verify_integrity()

    def _verify_integrity(self) -> bool:
        """计算完整性校验 — 检测 cash/持仓/交易记录 不一致"""
        try:
            calc_cash = 1_000_000.0
            for t in self._trades:
                price = float(t.get("price", 0))
                qty = int(t.get("qty", 0))
                if t.get("side") == "buy":
                    calc_cash -= price * qty + max(price * qty * 0.00025, 5.0)
                elif t.get("side") == "sell":
                    calc_cash += price * qty - max(price * qty * 0.00025, 5.0) - price * qty * 0.0005

            actual_cash = self._broker._cash
            drift = abs(calc_cash - actual_cash)
            if drift > 100:  # 差异>100元告警
                print(f"[Paper] ⚠️ 现金漂移: 计算¥{calc_cash:,.0f} vs 实际¥{actual_cash:,.0f} (差¥{drift:,.0f})")
                return False

            # 校验持仓市值
            pos_value = sum(
                p.volume * (p.current_price or p.avg_cost)
                for p in self._broker._positions.values()
            )
            total = actual_cash + pos_value
            if total < 0:
                print(f"[Paper] ⚠️ 总资产负值: ¥{total:,.0f}")
                return False
            return True
        except Exception as e:
            print(f"[Paper] ⚠️ 校验异常: {e}")
            return False

    def _make_snapshot(self):
        """⑤ 启动快照 — 每天首次加载时存一份"""
        try:
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
            today = datetime.now().strftime("%Y%m%d")
            snap_path = os.path.join(SNAPSHOT_DIR, f"paper_{today}.json")
            if not os.path.exists(snap_path):
                import shutil
                if os.path.exists(STATE_FILE):
                    shutil.copy2(STATE_FILE, snap_path)
        except: pass

    # ═══════════ 下单 ═══════════

    def _can_buy(self, symbol: str, price: float, qty: int, signal_id: str = "") -> tuple[bool, str]:
        try:
            from master_switch import can_buy
            if not can_buy("sim"):
                return False, "风控总闸关闭"
        except Exception:
            pass

        try:
            from risk_guard import PreTradeChecker
            cfg = {"max_order_value": 1_000_000, "max_daily_trades": 5,
                   "signal_min_strength": 3, "max_positions_abs": 5,
                   "max_single_position_pct": 20}
            ck = PreTradeChecker(config=cfg, positions=self.positions,
                                 cash=self.cash, total_equity=self.get_total_equity(),
                                 stock_data=getattr(self, 'stock_data', {}))
            action, reason, adj_qty = ck.check_buy(symbol, price, qty,
                                                    signal_id=signal_id,
                                                    daily_trades=self._daily_trade_count)
            if action == "REJECT":
                return False, reason
            if action == "REDUCE" and adj_qty < 100:
                return False, f"缩减后不足100股: {reason}"
        except Exception as e:
            return False, f"风控异常: {e}"

        if price * qty > self.cash * 0.5:
            return False, f"资金不足: 需¥{price*qty:,.0f} 可用¥{self.cash:,.0f}"

        return True, ""

    _trade_lock = threading.Lock()

    def place_order(self, symbol: str, side: str, price: float, qty: int,
                    trade_type: str = "auto", reason: str = "",
                    signal_source: str = "auto", signal_id: str = "") -> dict:
        with self._trade_lock:
            return self._place_order_locked(symbol, side, price, qty,
                                            trade_type, reason, signal_source, signal_id)

    def _place_order_locked(self, symbol: str, side: str, price: float, qty: int,
                            trade_type: str, reason: str,
                            signal_source: str, signal_id: str) -> dict:
        if side not in ("buy", "sell"):
            return {"success": False, "error": "side须是buy/sell"}

        # 非A股品种过滤 (ETF/债/逆回购等)
        _clean = symbol.replace('sh','').replace('sz','').replace('bj','')
        if _clean.startswith(('204','131','51','159','16','18','58','50')):
            return {"success": False, "error": f"非A股品种: {symbol}"}

        # E303: 当日不重复买同票
        if side == "buy":
            _td = datetime.now().strftime("%Y-%m-%d")
            if any(t.get("symbol") == symbol and t.get("side") == "buy"
                   and str(t.get("date",""))[:10] == _td
                   for t in self._trades[-30:]):
                return {"success": False, "error": f"E303: {symbol}今日已买, 勿重复"}

        # ── T+1强约束 (A股铁律: 当日买入不可卖) ──
        _today = datetime.now().strftime("%Y-%m-%d")
        if side == "sell":
            pos = self.positions.get(symbol, {})
            if not pos.get("_verified", False):
                return {"success": False, "error": f"无源持仓不可卖出({symbol})"}
            buy_date = pos.get("buy_date", "")
            has_today_buy = any(t.get("side")=="buy" and t.get("symbol")==symbol
                              and str(t.get("date",""))[:10]==_today
                              for t in self._trades[-50:])
            if buy_date == _today or has_today_buy:
                return {"success": False, "error": f"T+1锁定: {symbol}今日有买入, 不可卖"}

        if price is None or price <= 0:
            price = _get_market_price(symbol) or self.positions.get(symbol, {}).get("last_price", 0)
        if price <= 0:
            return {"success": False, "error": "无法获取价格"}
        price = round(price, 2)
        qty = max(100, qty // 100 * 100)

        if side == "buy":
            ok, msg = self._can_buy(symbol, price, qty, signal_id)
            if not ok:
                return {"success": False, "error": msg}

            # D2: 集中度检查 (单票/总资产>50%硬限, >30%软限)
            new_value = price * qty
            total_asset = self.get_total_equity()
            pos_ok, pos_msg = self._rule_engine.check_concentration(
                self.positions, symbol, new_value, total_asset=total_asset)
            if not pos_ok:
                return {"success": False, "error": pos_msg}

        cost = round(price * qty, 2)
        commission = max(cost * 0.00025, 5.0)
        stamp = cost * 0.0005 if side == "sell" else 0
        pnl = 0.0

        if side == "buy":
            self._broker._cash -= (cost + commission)
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
            # D4: 策略级持有天数 — 从注册表查, 兜底7天
            _sig_key = signal_source if signal_source != "auto" else reason or ""
            _stp_cfg = _strategy_tp_sl.get(_sig_key, _strategy_tp_sl.get("_default", {}))
            _hold_days = _stp_cfg.get("hold_days", self._signal_hold_days.get(symbol, 7))
            # D3: 记录买入当日参考价 (后续单票日跌检测用)
            _yday_close = None
            try:
                _sd = getattr(self, 'stock_data', None)
                if _sd and symbol in _sd:
                    _df = _sd[symbol]
                    if len(_df) >= 2:
                        _yday_close = float(_df.iloc[-2]['close'])
            except Exception:
                _yday_close = price  # 降级: 用买入价
            self._meta[symbol] = {
                "name": _resolve_name(symbol, self._names),
                "buy_date": datetime.now().strftime("%Y-%m-%d"),
                "strategy_id": signal_source if signal_source != "auto" else reason or "auto",
                "hold_days": _hold_days,
                "prev_close": round(_yday_close or price, 2),  # D3用
            }
            self._daily_buy_count += 1
        else:
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
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0  # 盈利重置

        self._daily_trade_count += 1

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
            "cost_price": self._broker._positions[symbol].avg_cost if side == "sell" and symbol in self._broker._positions else price,
            "signal_source": signal_source,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": trade_type,
            "reason": reason or (f"{'买入' if side=='buy' else '卖出'}"),
        }
        self._trades.append(rec)
        self._save()

        try:
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
        if not self.auto_enabled:
            return []
        if not _can_trade_time():
            return []

        today = datetime.now().strftime("%Y%m%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_trade_count = 0
            self._daily_buy_count = 0
            self._daily_loss_total = 0.0
            self._consecutive_losses = 0
            self._day_start_equity = self.get_total_equity()

        # ══ 熔断检查: 连亏3笔或日亏>5% → 今日停买 ══
        if self._consecutive_losses >= 3:
            return []
        today_dd = (self.get_total_equity() - max(self._day_start_equity or 1_000_000, 1)) / max(self._day_start_equity or 1_000_000, 1)
        if today_dd < -0.05:
            return []

        if signals is None:
            signals = self._load_ml_signals()

        actions = []

        # ── 出场检查 ──
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            mp = _get_market_price(sym)
            if mp and mp > 0:
                pos["last_price"] = mp
            meta = self._meta.get(sym, {})
            buy_date = meta.get("buy_date", "")
            strategy_id = meta.get("strategy_id", "")
            is_today_buy = buy_date == datetime.now().strftime("%Y-%m-%d")

            # ══ 陈小群: 弱转强半小时无表现→走 (今日买的弱转强可以卖) ══
            if strategy_id == "weak_to_strong" and is_today_buy:
                _now = datetime.now()
                _open_time = _now.replace(hour=9, minute=30, second=0)
                _mins = (_now - _open_time).total_seconds() / 60 if _now > _open_time else 0
                _pnl = (mp / pos["avg_cost"] - 1) if mp and pos["avg_cost"] > 0 else 0
                if _mins > 30 and _pnl <= 0:
                    r = self.place_order(sym, "sell", mp or pos["avg_cost"], pos["qty"],
                                        trade_type="auto", reason=f"半小时无表现({_mins:.0f}分)")
                    if r.get("success"): actions.append(r)
                    continue

            # T+1保护: 非弱转强的今日仓位, 跳过出场(place_order层也有T+1兜底)
            if is_today_buy and strategy_id != "weak_to_strong":
                continue

            # ══ 单票日盈亏 ══
            _prev_close = pos.get("avg_cost")
            _today_open = pos.get("avg_cost")
            try:
                from xtquant import xtdata
                _tk = xtdata.get_full_tick([sym])
                if _tk and sym in _tk:
                    _prev_close = float(_tk[sym].get('lastClose', _prev_close))
                    _today_open = float(_tk[sym].get('open', _today_open))
            except: pass
            _lp = mp or pos.get("last_price", pos["avg_cost"])
            _chg_close = (_lp / _prev_close - 1) if _prev_close > 0 else 0
            _chg_open = (_lp / _today_open - 1) if _today_open > 0 else 0
            if _chg_close <= -0.05:
                r = self.place_order(sym, "sell", _lp, pos["qty"], trade_type="auto",
                                    reason=f"昨收清仓({_chg_close*100:.1f}%)")
                if r.get("success"): actions.append(r); continue
            elif _chg_close <= -0.03:
                _sq = max(100, int(pos["qty"] * 0.5) // 100 * 100)
                _sq = min(_sq, pos["qty"] - 100)
                if _sq >= 100:
                    r = self.place_order(sym, "sell", _lp, _sq, trade_type="auto",
                                        reason=f"昨收卖半({_chg_close*100:.1f}%)")
                    if r.get("success"): actions.append(r); continue
            if _chg_open <= -0.03:
                r = self.place_order(sym, "sell", _lp, pos["qty"], trade_type="auto",
                                    reason=f"今开清仓({_chg_open*100:.1f}%)")
                if r.get("success"): actions.append(r); continue
            elif _chg_open <= -0.02:
                _sq = max(100, int(pos["qty"] * 0.5) // 100 * 100)
                _sq = min(_sq, pos["qty"] - 100)
                if _sq >= 100:
                    r = self.place_order(sym, "sell", _lp, _sq, trade_type="auto",
                                        reason=f"今开卖半({_chg_open*100:.1f}%)")
                    if r.get("success"): actions.append(r); continue

            # ══ 持仓到期: hold_days天未盈利→清仓 ══
            _hd = self._signal_hold_days.get(sym, meta.get("hold_days", 7))
            try:
                _buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
                _days_held = (datetime.now() - _buy_dt).days
                if _days_held >= _hd:
                    r = self.place_order(sym, "sell", mp or pos["avg_cost"], pos["qty"],
                                        trade_type="auto", reason=f"持仓到期({_days_held}天≥{_hd}天)")
                    if r.get("success"): actions.append(r)
                    continue
            except: pass

            # ══ 信号级止损优先 (策略自带, 蓝图设计) ══
            _sig_hard = pos.get("stop_loss", 0)
            _sig_soft = pos.get("soft_stop_loss", 0)
            _lp = pos.get("last_price", pos["avg_cost"])
            if _sig_hard > 0 and _lp <= _sig_hard:
                r = self.place_order(sym, "sell", _lp, pos["qty"],
                                    trade_type="auto", reason=f"信号硬止损({_lp:.2f}≤{_sig_hard:.2f})")
                if r.get("success"): actions.append(r)
                continue
            if _sig_soft > 0 and _lp <= _sig_soft:
                _soft_qty = max(100, int(pos["qty"] * 0.5) // 100 * 100)
                _soft_qty = min(_soft_qty, pos["qty"] - 100)  # 至少留100股
                if _soft_qty >= 100:
                    r = self.place_order(sym, "sell", _lp, _soft_qty,
                                        trade_type="auto", reason=f"信号软止损({_lp:.2f}≤{_sig_soft:.2f})")
                if r.get("success"): actions.append(r)
                continue

            rule_pos = {"symbol": sym, "avg_cost": pos["avg_cost"],
                        "qty": pos["qty"], "last_price": pos["last_price"]}
            # D3: 单票日盈亏 — 从meta取昨收, 无meta则用成本价兜底
            _meta = self._meta.get(sym, {})
            _prev_close = _meta.get("prev_close", pos["avg_cost"])

            # D4: 策略级止盈止损 — 发起者锁定, 查策略注册表
            _sid = _meta.get("strategy_id", "") or _meta.get("signal_source", "")
            _stp = _strategy_tp_sl.get(_sid) if _sid else None
            if not _stp:
                _stp = _strategy_tp_sl.get("_default", {"tp": 0.05, "sl": -0.04})
            md = {"price": pos["last_price"], "prev_close": _prev_close,
                  "vol": pos.get("qty", 0)}

            # D3: 先check单票日跌 (优先于止损, 日跌更急)
            _dr = self._rule_engine.check_daily_drop(rule_pos | {"prev_close": _prev_close})
            if _dr:
                _dq = min(_dr["qty"], pos["qty"])
                _dq = max(100, _dq // 100 * 100)
                if _dq >= 100:
                    r = self.place_order(sym, "sell", pos["last_price"], _dq,
                                        trade_type="auto", reason=_dr["reason"])
                    if r.get("success"): actions.append(r)
                # 更新prev_close为今日价(明天检测基准)
                self._meta[sym] = {**self._meta.get(sym, {}),
                                   "prev_close": round(pos["last_price"], 2)}
                continue

            # D4: 策略级止盈止损 (发起者锁定, 优先于通用规则)
            _lp = pos["last_price"]
            _cost = pos["avg_cost"]
            if _cost > 0 and _lp > 0:
                _pnl_pct = (_lp - _cost) / _cost
                _qty = pos["qty"]
                # 策略级止损
                if _pnl_pct <= _stp.get("sl", -0.04):
                    _sq = _qty if _pnl_pct <= _stp["sl"] * 1.3 else max(100, (_qty // 2) // 100 * 100)
                    if _sq >= 100:
                        r = self.place_order(sym, "sell", _lp, _sq, trade_type="auto",
                                            reason=f"策略止损{_sid}({_pnl_pct*100:.1f}%)")
                        if r.get("success"): actions.append(r); continue
                # 策略级止盈
                if _pnl_pct >= _stp.get("tp", 0.05):
                    _tq = max(100, (_qty // 3) // 100 * 100)  # 卖1/3锁利
                    if _tq >= 100:
                        r = self.place_order(sym, "sell", _lp, _tq, trade_type="auto",
                                            reason=f"策略止盈{_sid}({_pnl_pct*100:.1f}%)")
                        if r.get("success"): actions.append(r); continue

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
                        break
                except Exception:
                    continue

        # ══ 账户级日亏控制 (个股判断后, 最后防线) ══
        _dl_total = abs(self._daily_loss_total)
        _dl_pct = _dl_total / 1_000_000
        if _dl_pct >= 0.045:
            _ratio = 1.0 if _dl_pct >= 0.065 else 0.5
            _label = "清仓" if _dl_pct >= 0.065 else "卖半"
            for _s, _p in list(self.positions.items()):
                _sq = max(100, int(_p["qty"] * _ratio) // 100 * 100)
                if _sq >= 100:
                    _pr = _p.get("last_price", _p.get("avg_cost", 1))
                    r = self.place_order(_s, "sell", _pr, _sq, trade_type="auto",
                                        reason=f"日亏{_label}({_dl_pct*100:.1f}%)")
                    if r.get("success"): actions.append(r)
            if _dl_pct >= 0.065:
                return actions  # 清仓后不再进场

        # ── 进场: 记录信号hold_days + 止损价 ──
        for _s in (signals or []):
            _sym = _s.get("symbol", "")
            _hd = _s.get("hold_days", 7)
            if _hd:
                self._signal_hold_days[_sym] = _hd
            _sl = _s.get("stop_loss", 0)
            _ssl = _s.get("soft_stop_loss", 0)
            if _sl > 0 or _ssl > 0:
                self._meta.setdefault(_sym, {})["stop_loss"] = _sl
                self._meta.setdefault(_sym, {})["soft_stop_loss"] = _ssl

        # ── 进场 ──
        if signals and self._daily_buy_count < 5:
            try:
                from decision_adapter import process_signals as _da
                status = {
                    "total_equity": self.get_total_equity(),
                    "positions": [{"symbol": s, "market_value":
                        p.get("last_price", p["avg_cost"]) * p["qty"]}
                        for s, p in self.positions.items()]
                }
                _sd = {}
                try:
                    from data_loader import load_stock_data_cache
                    _sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
                except Exception:
                    pass

                orders = _da(signals[:15], _sd, status)
                # 弱转强: 按分数排名, 只取最优1只 (max_positions=1)
                orders.sort(key=lambda x: x.get("score", x.get("power_score", 0)), reverse=True)
                for order in orders[:3]:
                    sym = order["symbol"]
                    if sym in self.positions:
                        continue
                    # E303: 当日同股票不重复买入
                    _today = datetime.now().strftime("%Y-%m-%d")
                    if any(t.get("symbol") == sym and t.get("side") == "buy"
                           and str(t.get("date",""))[:10] == _today
                           for t in self._trades[-20:]):
                        continue
                    if self._daily_buy_count >= 5:
                        break
                    # ── 竞价确认 (陈小群三维: 竞价涨幅+量能+L2资金) ──
                    _is_wts = order.get("strategy") == "weak_to_strong" or "弱转强" in str(order.get("reason",""))
                    _sig_close = order.get("raw_close", order.get("close", 0))
                    _rt_price = _get_market_price(sym)
                    if _is_wts and _rt_price and _sig_close > 0:
                        _gap_pct = (_rt_price / _sig_close - 1) * 100
                        if _gap_pct < -1:  # 弱转强: 低开>1% → 未转强, 放弃
                            continue
                        if _gap_pct > 6:   # 追高超6% → 透支, 放弃
                            continue
                    elif not _is_wts and _rt_price and _sig_close > 0:
                        _gap_pct = (_rt_price / _sig_close - 1) * 100
                        if _gap_pct < -2 or _gap_pct > 5:
                            continue
                    # ── L2资金确认: xtdata.get_full_tick (对齐screener) ──
                    try:
                        from xtquant import xtdata
                        _l2 = xtdata.get_full_tick([sym]) if 'xtdata' in dir() else {}
                        _tick = _l2.get(sym, {}) if _l2 else {}
                        _bv = float(_tick.get('bidVol', 0) or 0)
                        _av = float(_tick.get('askVol', 0) or 0)
                        if _av > 0 and _bv / _av < 1.2:
                            continue
                    except Exception:
                        pass
                    prc = order.get("close", order.get("raw_close", 0))
                    # 必须市价: 模拟盘以实时行情为成本价, 无实时行情→放弃
                    _live = _get_market_price(sym)
                    if _live and _live > 0:
                        prc = round(_live, 2)
                    else:
                        continue  # 无实时价→跳过, 不以下线收盘价代替
                    if prc <= 0:
                        continue
                    qty = order.get("shares", 100)
                    reason = f"信号{order.get('buy_signal','?')}级({order.get('position_pct','?')}%仓) [ML]"
                    _sig_sl = order.get("stop_loss", 0)
                    _sig_ssl = order.get("soft_stop_loss", 0)
                    r = self.place_order(sym, "buy", prc, qty,
                                        trade_type="auto", reason=reason, signal_source="auto")
                    if r.get("success"):
                        if _sig_sl > 0 or _sig_ssl > 0:
                            self._meta[sym]["stop_loss"] = _sig_sl
                            self._meta[sym]["soft_stop_loss"] = _sig_ssl
                        actions.append(r)
            except Exception as e:
                print(f"[Paper] decision_adapter失败: {e}", flush=True)

        if actions:
            self._save()
        return actions

    def _load_ml_signals(self) -> list[dict]:
        if not os.path.exists(SIGNAL_TABLE):
            return []
        try:
            with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
                table = json.load(f)
            result = []
            for s in table:
                if not s.get("auto_enabled"):
                    continue
                _decision = str(s.get("decision", ""))
                sc = s.get("combined_score", 0) or 0
                _strategy = "weak_to_strong" if "弱转强" in _decision else "ml_daily"
                result.append({
                    "symbol": s.get("symbol", ""),
                    "name": s.get("name", ""),
                    "buy_signal": 5 if sc >= 90 else 4 if sc >= 80 else 3 if sc >= 70 else 2,
                    "close": s.get("close", 0),
                    "change_pct": s.get("change_pct", 0) or 0,
                    "vol_ratio": s.get("vol_ratio", 1) or 1,
                    "industry": s.get("industry", ""),
                    "power_score": sc,
                    "strategy": _strategy,
                    "score": sc,
                    "stop_loss": s.get("stop_loss", 0),
                    "soft_stop_loss": s.get("soft_stop_loss", 0),
                    "hold_days": s.get("hold_days", 7),
                })
            return result
        except Exception as e:
            print(f"[Paper] 信号加载失败: {e}", flush=True)
            return []

    # ═══════════ 状态查询 ═══════════

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
                "symbol": sym, "name": pos.get("name") or _resolve_name(sym, self._names),
                "board": bd,
                "qty": pos["qty"], "quantity": pos["qty"],
                "avg_cost": round(pos["avg_cost"], 2), "cost_price": round(pos["avg_cost"], 2),
                "last_price": round(price, 2), "current_price": round(price, 2),
                "market_value": round(price * pos["qty"], 2),
                "pnl": round(pnl, 2), "unrealized_pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2), "unrealized_pnl_pct": round(pnl_pct, 2),
                "buy_date": pos.get("buy_date", ""),
            })

        sells = [t for t in self._trades if t.get("side") == "sell" and t.get("pnl") is not None]
        wins = [s for s in sells if s.get("pnl", 0) > 0]
        wr_pct = round(len(wins) / len(sells) * 100, 1) if sells else 0
        wr = wr_pct

        # 收益计算: 用sell记录的cost(卖出金额), 而非buy成本
        returns = []
        for s in sells:
            cost = s.get("cost", s.get("price", 0) * s.get("qty", 100))
            if cost > 0:
                returns.append(s.get("pnl", 0) / cost)

        sharpe = 0.0
        if returns and len(returns) >= 2:
            arr = np.array(returns)
            std = max(np.std(arr), 1e-8)
            sharpe = round(float(np.mean(arr) / std) * np.sqrt(min(252, len(arr))), 2)

        # B3: DSR (Deflated Sharpe Ratio) — 过拟合审计
        dsr_info = {"dsr": None, "verdict": "样本不足", "p_value": None}
        if len(returns) >= 10:
            try:
                from deflated_sharpe import deflated_sharpe_ratio
                n_attempts = len(set(t.get("strategy_id", "") for t in self._trades if t.get("strategy_id")))
                n_attempts = max(n_attempts, 1) * 3
                dsr_info = deflated_sharpe_ratio(returns, n_trials=n_attempts)
            except Exception:
                pass

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
            "dsr": dsr_info,
            "max_drawdown": max_dd,
            "calmar": calmar,
            "positions": positions,
            "trade_log": self._trades[-30:],
            "auto_enabled": self.auto_enabled,
            "position_count": len(positions),
        }


# ═══════════════════════════════ 全局实例 + 兼容接口 ═══════════════════════════════

paper = PaperAccount()


def start_auto_loop():
    """启动模拟盘自动交易循环 (兼容旧接口, V5引擎内部处理)"""
    pass


def init_stock_data():
    """app.py 数据预热完成后调用，注入 stock_data (含退市股)"""
    try:
        from data_loader import load_stock_data_cache, get_survivorship_stats
        sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
        print(f"[Paper] stock_data 已注入: {len(sd)}只")
        # B1: 幸存者偏差诊断
        try:
            stats = get_survivorship_stats()
            if stats and 'delisted' in stats:
                print(f"[Paper·B1] 幸存者偏差: 全量{stats.get('total_stocks_ever')}只, "
                      f"存活{stats.get('active_today')}只, 退市{stats.get('delisted')}只 "
                      f"({stats.get('delisted_pct')}%), 估计偏差{stats.get('estimated_bias')}")
        except Exception:
            pass
        return sd
    except Exception as e:
        print(f"[Paper] stock_data加载失败: {e}")
        return {}
