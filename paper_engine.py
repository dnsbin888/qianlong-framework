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

import json, os, sys, threading
from datetime import datetime

sys.path.insert(0, r"d:\quant_framework\src")

from quant_framework.execution.brokers.simulated import SimulatedBroker
from quant_framework.execution.rules import (
    RuleEngine, AutoStopLossRule, AutoTrailingStopRule,
    CircuitBreakerRule, MaxDailyTradesRule, DailyLossLimitRule,
    SignalQualityFilter, PositionSizingRule,
)

STATE_FILE = r"d:\quant_framework\paper_account.json"
TRADE_LOG_CSV = r"d:\quant_framework\trade_log.csv"


def _load_trade_config(paper_config=None):
    """加载交易规则配置 — 与实盘共享规则，E06: 支持模拟盘独立覆盖。"""
    try:
        from live_trader import CONFIG
        cfg = dict(CONFIG)  # 浅拷贝，避免修改原CONFIG
        if paper_config:
            cfg.update(paper_config)  # 模拟盘独立配置覆盖
        return cfg
    except Exception as _e:
        print(f"[Paper] 加载交易配置失败: {_e}")
    return {
        "tp1_profit_pct": 0.05, "tp1_trail_pct": -0.01, "tp1_sell_ratio": 0.33, "tp1_stop_loss": -0.03,
        "tp2_profit_pct": 0.07, "tp2_trail_pct": -0.02, "tp2_sell_ratio": 0.33, "tp2_stop_loss": -0.05,
        "tp3_profit_pct": 0.12, "tp3_trail_pct": -0.03, "tp3_sell_ratio": 0.33, "tp3_stop_loss": -0.05,
        "max_daily_trades": 5, "max_daily_loss": -5.0, "max_positions": 5, "min_cash_reserve": 100000,
        "signal_min_strength": 5,
        "limit_up_drop_sell": -0.03, "max_hold_days": 3,
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
        self._positions_compat = {}  # E30: 实例变量
        self._trades_archive: list = []  # 交易日志
        # E60: 风控数据源（由 app.py 注入）
        self.factor_cache = []
        self.stock_data = {}
        self._name_cache = {}  # FIX: 初始化时就创建，避免下单时报错
        # E06: 模拟盘独立配置（不与实盘CONFIG共享）
        self._paper_config = {}
        # C14: 直接在__init__中初始化所有运行时计数器
        self._daily_date = None
        self._daily_trade_count = 0
        self._daily_buy_count = 0
        self._daily_loss_total = 0.0
        self._day_start_equity = None
        self.start_time = datetime.now()
        # E33-1: 规则引擎提前初始化，避免 auto_trade_check() 抛 AttributeError
        # R1-1: 注入 SimulatedBroker，使 RuleEngine 可直接执行订单
        self._rule_engine = RuleEngine(broker=self._broker)
        self._setup_rules()

    def set_risk_data(self, factor_cache=None, stock_data=None):
        """E60: 更新风控所需的数据源"""
        if factor_cache is not None:
            self.factor_cache = factor_cache
        if stock_data is not None:
            self.stock_data = stock_data
        self._name_cache = {}

        # ── 规则引擎 ──
        self._rule_engine = RuleEngine(broker=self._broker)
        self._setup_rules()

        # ── 运行时追踪（首次初始化，后续调用不重置） ──
        if not hasattr(self, '_daily_trade_count') or self._daily_trade_count is None:
            self._daily_date = None
            self._daily_trade_count = 0
            self._daily_buy_count = 0
            self._daily_loss_total = 0.0
            self.start_time = datetime.now()  # E49: 记录启动时间用于现金收益

        # 从文件恢复状态
        self._load()

    # ═══════════════════ 规则配置 ═══════════════════

    def set_config(self, key, value):
        """E06: 设置模拟盘独立配置（如 signal_min_strength）"""
        self._paper_config[key] = value
        print(f"[Paper] 配置更新: {key}={value}")
        self.restart()  # 重新加载规则使新配置生效

    def get_config(self, key, default=None):
        """获取配置，优先独立配置→共享CONFIG"""
        if key in self._paper_config:
            return self._paper_config[key]
        try:
            from live_trader import CONFIG
            return CONFIG.get(key, default)
        except:
            return default

    def restart(self):
        """重启模拟交易引擎 — 重新加载交易规则配置。

        进化参数应用后调用，使新止损/止盈立即生效。
        不影响持仓和资金状态。
        """
        try:
            self._rule_engine = RuleEngine(broker=self._broker)
            self._setup_rules()
            print(f"[Paper] 规则已重新加载: {len(self._rule_engine._rules)}条")
        except Exception as _re:
            print(f"[Paper] restart失败: {_re}")

    def _setup_rules(self):
        """初始化 7 条自动交易规则。"""
        cfg = _load_trade_config(self._paper_config)  # E06

        tp1_profit = cfg.get("tp1_profit_pct", 0.05)
        tp1_trail = abs(cfg.get("tp1_trail_pct", -0.01))
        tp1_sell = cfg.get("tp1_sell_ratio", 0.33)
        tp1_stop = cfg.get("tp1_stop_loss", -0.03)

        tp2_profit = cfg.get("tp2_profit_pct", 0.07)
        tp2_trail = abs(cfg.get("tp2_trail_pct", -0.02))
        tp2_sell = cfg.get("tp2_sell_ratio", 0.33)
        tp2_stop = cfg.get("tp2_stop_loss", -0.05)

        tp3_profit = cfg.get("tp3_profit_pct", 0.12)
        tp3_trail = abs(cfg.get("tp3_trail_pct", -0.03))
        tp3_sell = cfg.get("tp3_sell_ratio", 0.33)
        tp3_stop = cfg.get("tp3_stop_loss", -0.05)

        # 1. 基本止损: -3%清仓 (E02: 半仓导致反复触发, 改全卖)
        self._rule_engine.add_rule(AutoStopLossRule(threshold=-0.03, sell_ratio=1.0))

        # 2-4. 三级移动止盈
        self._rule_engine.add_rule(AutoTrailingStopRule(
            tier=2, profit_pct=tp2_profit, trail_pct=tp2_trail,
            sell_ratio=tp2_sell, stop_loss=tp2_stop,
        ))
        self._rule_engine.add_rule(AutoTrailingStopRule(
            tier=1, profit_pct=tp1_profit, trail_pct=tp1_trail,
            sell_ratio=tp1_sell, stop_loss=tp1_stop,
        ))
        self._rule_engine.add_rule(AutoTrailingStopRule(
            tier=3, profit_pct=tp3_profit, trail_pct=tp3_trail,
            sell_ratio=tp3_sell, stop_loss=tp3_stop,
        ))

        # 4. 熔断
        self._rule_engine.add_rule(CircuitBreakerRule(
            max_daily_loss_pct=cfg.get("max_daily_loss", -0.05),
            initial_capital=1_000_000,
        ))

        # 5. 下单频率限制
        self._rule_engine.add_rule(MaxDailyTradesRule(
            max_trades=cfg.get("max_daily_trades", 4),
        ))

        # 6. 日内亏损限制
        self._rule_engine.add_rule(DailyLossLimitRule(
            max_loss_pct=cfg.get("max_daily_loss", -0.05),
            initial_capital=1_000_000,
        ))

        # 7. 信号质量过滤 + 仓位计算 (在 auto_trade_check 中显式调用)
        self._signal_filter = SignalQualityFilter(
            min_strength=cfg.get("signal_min_strength", 3),
        )
        self._position_sizer = PositionSizingRule()

    # ═══════════════════ 状态持久化 ═══════════════════
    _save_lock = threading.Lock()

    def _repair_missing_buys(self):
        """C18: 检测并回补缺失的买入记录（从卖出记录回推）。"""
        sells = [t for t in self._trades_archive if t.get("side") == "sell"]
        repaired = 0
        for s in sells:
            sym = s.get("symbol", "")
            qty = s.get("qty", 0)
            cost_price = s.get("cost_price", 0)
            # 检查是否有对应买入
            has_buy = any(t.get("side") == "buy" and t.get("symbol") == sym
                         for t in self._trades_archive)
            if not has_buy and cost_price > 0 and qty > 0:
                self._trades_archive.insert(0, {
                    "symbol": sym, "name": s.get("name", sym),
                    "side": "buy", "price": cost_price, "qty": qty,
                    "cost": round(cost_price * qty, 2),
                    "time": "09:30:00", "type": "system", "repair": True,
                })
                repaired += 1
                print(f"[Paper] ✅ 回补缺失买入: {sym} x{qty} @{cost_price}")
        if repaired > 0:
            print(f"[Paper] 共回补{repaired}笔缺失买入记录")

    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    d = json.load(f)
                file_cash = d.get("cash", 1_000_000)
                self._broker._cash = float(file_cash)
                self._trades_archive = d.get("trade_log", [])
                self.auto_enabled = d.get("auto_enabled", False)
                # C18: 回补缺失的买入记录
                self._repair_missing_buys()
                # E259根治: 从 trade_log.csv 恢复缺失记录
                self._sync_from_csv()
                # D04: 恢复确认日志
                print(f"[Paper] _load: cash={self._broker._cash} (from file={file_cash}), "
                      f"positions={len(d.get('positions',{}))}, trades={len(self._trades_archive)}, auto={self.auto_enabled}")
                for sym, pos in d.get("positions", {}).items():
                    # C09: 校验持仓来源 — 必须有对应买入记录
                    buy_recs = [t for t in self._trades_archive if t.get("side")=="buy" and t.get("symbol")==sym]
                    if not buy_recs:
                        print(f"[Paper] ⚠️ 忽略无源持仓 {sym}: 无对应买入记录")
                        continue
                    if not pos.get("last_price"): pos["last_price"] = pos.get("avg_cost", 1.0)
                    if not pos.get("avg_cost"): pos["avg_cost"] = pos.get("last_price", 1.0)
                    if not pos.get("qty"): pos["qty"] = 100
                    pos["_verified"] = True  # C09: 有源持仓标记
                    self.positions[sym] = pos
                # 恢复日内计数器（崩溃后不丢失）
                self._daily_date = d.get("daily_date")
                self._daily_trade_count = d.get("daily_trade_count", 0)
                self._daily_buy_count = d.get("daily_buy_count", 0)
                self._daily_loss_total = d.get("daily_loss_total", 0.0)
                self._day_start_equity = d.get("day_start_equity")
            except Exception as _e:
                print(f"[Paper] 状态文件加载失败: {_e}")

    def _save(self):
        try:
            # E17: 磁盘空间检查
            import shutil
            _dir = os.path.dirname(STATE_FILE)
            if os.path.exists(_dir):
                _disk = shutil.disk_usage(_dir)
                if _disk.free < 100 * 1024 * 1024:  # <100MB
                    print(f"[Paper] 磁盘空间不足({_disk.free//1024//1024}MB)，跳过保存")
                    return
            with self._save_lock:  # 线程安全
                # E17: 保存前备份
                if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 100:
                    shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
                data = {
                    "cash": self.cash,
                    "positions": self.positions,
                    "trade_log": self._trades_archive[-200:],
                    "auto_enabled": self.auto_enabled,
                    "daily_date": self._daily_date,
                    "daily_trade_count": self._daily_trade_count,
                    "daily_buy_count": getattr(self, '_daily_buy_count', 0),
                    "daily_loss_total": self._daily_loss_total,
                    "day_start_equity": getattr(self, '_day_start_equity', None),
                    "last_trade_ts": datetime.now().strftime("%H:%M:%S"),
                }
                tmp = STATE_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f)
                # E17: 写入后校验
                with open(tmp, "r") as f:
                    verify = json.load(f)
                if verify.get("cash") != data.get("cash"):
                    print(f"[Paper] 校验失败(cash mismatch)，放弃保存")
                    return
                os.replace(tmp, STATE_FILE)  # 原子写入: tmp→正式
        except Exception as _e:
            print(f"[Paper] 保存状态文件失败: {_e}")

    def _append_trade_csv(self, trade: dict) -> None:
        """E259根治: 卖出成交时同步追加 trade_log.csv，统一数据源。"""
        try:
            import csv as _csv, os as _os
            _exists = _os.path.exists(TRADE_LOG_CSV)
            _cols = ["symbol","buy_date","sell_date","buy_price","sell_price","volume","return_pct","net_profit","exit_type","signal"]
            with open(TRADE_LOG_CSV, "a", newline="", encoding="utf-8-sig") as _f:
                _w = _csv.DictWriter(_f, fieldnames=_cols)
                if not _exists:
                    _w.writeheader()
                _w.writerow({
                    "symbol": trade.get("symbol", ""),
                    "buy_date": trade.get("buy_date", ""),
                    "sell_date": trade.get("sell_date", datetime.now().strftime("%Y-%m-%d")),
                    "buy_price": round(trade.get("cost_price", 0), 2),
                    "sell_price": round(trade.get("price", 0), 2),
                    "volume": trade.get("qty", 0),
                    "return_pct": round(trade.get("pnl", 0) / max(trade.get("cost", 1), 1) * 100, 2),
                    "net_profit": round(trade.get("pnl", 0), 2),
                    "exit_type": trade.get("type", "signal"),
                    "signal": trade.get("signal", ""),
                })
        except Exception as _e:
            print(f"[Paper] CSV追加失败: {_e}")

    def _sync_from_csv(self) -> None:
        """E259根治: 从 trade_log.csv 恢复 trades_archive 中缺失的记录。"""
        try:
            import csv as _csv, os as _os
            if not _os.path.exists(TRADE_LOG_CSV):
                return
            _existing = {(t.get("symbol",""), t.get("sell_date",""), t.get("qty",0))
                         for t in self._trades_archive if t.get("side") == "sell"}
            with open(TRADE_LOG_CSV, "r", encoding="utf-8-sig") as _f:
                for _row in _csv.DictReader(_f):
                    _key = (_row.get("symbol",""), _row.get("sell_date",""), int(float(_row.get("volume", 0) or 0)))
                    if _key in _existing:
                        continue
                    _qty = int(float(_row.get("volume", 0) or 0))
                    _price = float(_row.get("sell_price", 0) or 0)
                    _cost_price = float(_row.get("buy_price", 0) or 0)
                    _pnl = (_price - _cost_price) * _qty
                    self._trades_archive.append({
                        "symbol": _row.get("symbol", ""),
                        "side": "sell",
                        "price": _price,
                        "qty": _qty,
                        "revenue": _price * _qty,
                        "cost": _cost_price * _qty,
                        "pnl": round(_pnl, 2),
                        "cost_price": _cost_price,
                        "time": "00:00:00",
                        "sell_date": _row.get("sell_date", ""),
                        "buy_date": _row.get("buy_date", ""),
                        "type": _row.get("exit_type", "csv_sync"),
                        "_csv_synced": True,
                    })
                    _existing.add(_key)
            _csv_sells = sum(1 for t in self._trades_archive if t.get("_csv_synced"))
            if _csv_sells > 0:
                print(f"[Paper] 从CSV恢复 {_csv_sells} 笔卖出记录")
        except Exception as _e:
            print(f"[Paper] CSV同步失败: {_e}")

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
            except Exception as _e:
                print(f"[Paper] 股票名称文件加载失败: {_e}")
        return self._name_cache.get(code, code)

    # ═══════════════════ 价格查询 ═══════════════════

    def _resolve_price(self, symbol, fallback=None, fast=False):
        """统一价格获取: 实时行情→westock→价格缓存→因子缓存→fallback→成本价。
        fast=True: 跳过westock，仅用实时行情+缓存（用于页面展示，毫秒级响应）。"""
        code = symbol.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        try:
            from realtime_quotes import _quote_cache
            if _quote_cache and _quote_cache.get("data") and code in _quote_cache["data"]:
                p = float(_quote_cache["data"][code].get("close", 0) or 0)
                if p > 0:
                    return p  # fix: 只有有效价格才返回，否则继续fallback
        except Exception as _e:
            print(f"[Paper] 实时行情查询失败: {_e}")
        # E56: westock实时行情优先于旧缓存（页面加载fast=True跳过，避免500ms限速阻塞）
        if not fast:
            try:
                from westock_factors import get_quote
                q = get_quote(symbol)
                if q:
                    for price_key in ['current_price', 'close', 'price']:
                        try:
                            p = float(q.get(price_key, 0) or 0)
                            if 0 < p < 10000:
                                return p
                        except (ValueError, TypeError):
                            continue
            except Exception as _e:
                print(f"[Paper] westock价格查询失败: {_e}")
        # 价格缓存（回退：westock失败/限速时使用）
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
        except Exception as _e:
            print(f"[Paper] 价格缓存读取失败: {_e}")

        if fallback and fallback > 0:
            return fallback
        # 所有价格源失效，用上次有效价格兜底（避免PnL在成本价和市价间跳动）
        if symbol in self.positions:
            pos = self.positions[symbol]
            last_price = pos.get("last_price", 0)
            avg = pos.get("avg_cost", 0)
            price = last_price if last_price > 0 and last_price != avg else avg
            if price > 0:
                _now = datetime.now()
                if not hasattr(self, '_price_fail_log'):
                    self._price_fail_log = {}
                _last = self._price_fail_log.get(symbol, datetime(2000, 1, 1))
                if (_now - _last).total_seconds() >= 120:
                    print(f"[Paper] 价格获取失败 symbol={symbol}, 用上次有效价{price:.2f}兜底")
                    self._price_fail_log[symbol] = _now
                return price
        if not hasattr(self, '_price_fail_log2'):
            self._price_fail_log2 = {}
        _last2 = self._price_fail_log2.get(symbol, datetime(2000, 1, 1))
        _now2 = datetime.now()
        if (_now2 - _last2).total_seconds() >= 300:
            print(f"[Paper] 价格获取失败 symbol={symbol}, 所有回退均无效，返回0")
            self._price_fail_log2[symbol] = _now2
        return 0.0

    def _get_market_price(self, symbol):
        # E45-B: 纯数字代码自动补 sh/sz 前缀
        clean = symbol.replace('sh', '').replace('sz', '').replace('bj', '')
        if clean.isdigit() and len(clean) == 6:
            for prefix in ['sh', 'sz']:
                prefixed = prefix + clean
                try:
                    price = self._resolve_price(prefixed)
                    if price and price != 10.0:
                        return price
                except Exception as _e:
                    print(f"[Paper] 市价查询异常: {_e}")
        return self._resolve_price(symbol)

    def get_market_value(self, quotes=None):
        total = 0.0
        for sym, pos in self.positions.items():
            code = sym.replace("sh", "").replace("sz", "")
            price = pos.get("last_price", pos["avg_cost"])
            if quotes and code in quotes:
                price = quotes[code].get("close", price)
            else:
                try:
                    cached = self._resolve_price(sym)
                    if cached and cached > 0:
                        price = cached
                except Exception as _e:
                    print(f"[Paper] 价格查询失败: {_e}")
            if price is None or price <= 0: price = pos.get("avg_cost", 1.0)
            total += price * pos["qty"]
        return total

    def get_total_equity(self, quotes=None):
        return self.cash + self.get_market_value(quotes)

    def get_pnl(self, quotes=None):
        cost = sum(p["avg_cost"] * p["qty"] for p in self.positions.values())
        return self.get_market_value(quotes) - cost

    # ═══════════════════ 下单 ═══════════════════

    _trade_lock = threading.Lock()  # 防多线程穿仓

    def place_order(self, symbol, side, price=None, qty=100, trade_type="manual"):
        """下单 — P0-模拟-02: 全面强制 T+1 约束。"""
        try:
            with self._trade_lock:  # 线程安全: 检查+扣除是原子操作
                return self._place_order_locked(symbol, side, price, qty, trade_type)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Paper] place_order EXCEPTION: {e}")
            return {"success": False, "error": f"下单异常: {e}"}

    def _place_order_locked(self, symbol, side, price=None, qty=100, trade_type="manual"):
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
            # E60: 涨跌停/资金/仓位事前风控
            from risk_guard import PreTradeChecker
            _chk = PreTradeChecker(config=_load_trade_config(), positions=self.positions,
                                   cash=self.cash, total_equity=self.get_total_equity(),
                                   factor_cache=self.factor_cache, stock_data=self.stock_data)
            _ind = self._resolve_name(symbol)
            try:
                from stock_names import get_industry as _gi
                _ind = _gi(symbol.replace('sh','').replace('sz','').replace('bj','')) or ''
            except: pass
            # FIX: 在风控检查前解析价格，避免 None*int 报错
            if price is None or price <= 0:
                price = self._resolve_price(symbol)
            if price is None or price <= 0:
                return {"success": False, "error": f"无法获取{symbol}实时价格"}
            _ok, _reason = _chk.check_buy(symbol, price, qty, industry=_ind)
            if not _ok:
                return {"success": False, "error": _reason}
            # E42+E49: 过滤非A股品种(逆回购/ETF/可转债等)
            if not sym.startswith(('sh', 'sz', 'bj')):
                print(f"[PaperTrade] 跳过非A股品种: {sym}")
                return {"success": False, "error": f"非A股品种: {sym}"}
            # 强化过滤: 纯数字部分以逆回购/ETF/可转债前缀开头
            clean_code = sym.replace('sh','').replace('sz','').replace('bj','')
            _bad_prefixes = ('204', '131', '51', '159', '16', '18', '58')
            if clean_code.startswith(_bad_prefixes):
                print(f"[PaperTrade] 跳过非A股品种: {sym} (code={clean_code})")
                return {"success": False, "error": f"非A股品种: {sym}"}
            if price is None or price <= 0:
                price = self._resolve_price(sym)
            if price is None or price <= 0:
                return {"success": False, "error": f"无法获取{sym}实时价格，请手动输入"}
            cost = price * qty
            if cost > self.cash:
                return {"success": False, "error": f"资金不足: 需{cost:.0f} 余{self.cash:.0f}"}

            self.cash -= cost
            # 买入佣金: 0.03% 最低5元
            commission = max(5.0, cost * 0.0003)
            self.cash -= commission
            if sym in self.positions:
                old = self.positions[sym]
                tq = old["qty"] + qty
                old["avg_cost"] = (old["avg_cost"] * old["qty"] + price * qty) / tq
                old["qty"] = tq
                old["last_price"] = price
                old["_verified"] = True
                old["buy_date"] = max(old.get("buy_date", ""), today)
            else:
                self.positions[sym] = {
                    "qty": qty, "avg_cost": price, "last_price": price,
                    "name": name, "buy_date": today, "_verified": True,
                }
            # 滑点统计: 信号价 vs 实际成交价
            sig_price = price  # signal价格
            actual_price = self._resolve_price(sym, fast=True) or price
            slippage = round((actual_price - sig_price) / max(sig_price, 0.01) * 100, 2) if sig_price > 0 else 0
            self._trades_archive.append({
                "symbol": sym, "name": name, "side": "buy", "price": round(price, 2),
                "qty": qty, "cost": round(cost, 2), "time": datetime.now().strftime("%H:%M:%S"),
                "type": trade_type, "slippage_pct": slippage,
            })
            self._save()
            return {"success": True, "action": "buy", "symbol": sym, "price": round(price, 2),
                    "qty": qty, "type": trade_type}

        elif side == "sell":
            # E60: 跌停板卖出检查
            from risk_guard import PreTradeChecker
            _chk = PreTradeChecker(config=_load_trade_config(), positions=self.positions,
                                   cash=self.cash, total_equity=self.get_total_equity(),
                                   factor_cache=self.factor_cache, stock_data=self.stock_data)
            _ok, _reason = _chk.check_sell(symbol, qty)
            if not _ok:
                return {"success": False, "error": _reason}

            if sym not in self.positions or self.positions[sym]["qty"] < qty:
                return {"success": False, "error": "持仓不足"}

            # ── P0-模拟-02: T+1 强约束 (所有交易类型) ──
            pos = self.positions[sym]
            # C09: 无源持仓拦截
            if not pos.get("_verified", False):
                return {"success": False, "error": "无源持仓不可卖出("+sym+")，请先买入或手动重置"}
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
            # 卖出成本: 佣金0.03% + 印花税0.1%
            sell_commission = max(5.0, revenue * 0.0003)
            stamp_tax = revenue * 0.001
            self.cash -= (sell_commission + stamp_tax)

            pos = self.positions[sym]
            pos["qty"] -= qty
            n = pos.get("name", name)
            if pos["qty"] <= 0:
                del self.positions[sym]

            # 记录盈亏用于日内亏损跟踪
            cost_price = pos["avg_cost"]
            pnl = (price - cost_price) * qty
            self._daily_loss_total += pnl

            _sell_trade = {
                "symbol": sym, "name": n, "side": "sell", "price": round(price, 2),
                "qty": qty, "revenue": round(revenue, 2),
                "cost": round(cost_price * qty, 2), "pnl": round(pnl, 2),
                "cost_price": round(cost_price, 2),
                "buy_date": buy_date, "sell_date": today,
                "time": datetime.now().strftime("%H:%M:%S"), "type": trade_type,
            }
            self._trades_archive.append(_sell_trade)
            self._save()
            # E259根治: 同步写入 trade_log.csv
            self._append_trade_csv(_sell_trade)
            return {"success": True, "action": "sell", "symbol": sym, "price": round(price, 2),
                    "qty": qty, "type": trade_type}

        return {"success": False, "error": "unknown side"}

    # ═══════════════════ 状态查询 ═══════════════════

    def get_status(self, quotes=None):
        """获取账户状态 — 接口不变。"""
        try:
            return self._get_status_impl(quotes)
        except Exception as _e:
            import traceback
            traceback.print_exc()
            return {"code": 500, "error": str(_e), "trace": traceback.format_exc()}

    def _get_status_impl(self, quotes=None):
        import numpy as np

        positions = []
        for sym, pos in self.positions.items():
            code = sym.replace("sh", "").replace("sz", "")
            price = pos.get("last_price") or pos.get("avg_cost") or 1.0
            if quotes and code in quotes:
                price = quotes[code].get("close", price)
            else:
                # 未命中实时行情：从缓存快速获取（跳过westock，毫秒级）
                try:
                    cached = self._resolve_price(sym, fast=True)
                    if cached and cached > 0:
                        price = cached
                except Exception as _e:
                    print(f"[Paper] 价格查询失败: {_e}")
            pnl = (price - pos["avg_cost"]) * pos["qty"]
            pnl_pct = (price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] > 0 else 0
            # D15: 板块标记
            _c = code.replace('sh','').replace('sz','')
            _bd = '科创板' if _c.startswith(('688','689')) else '创业板' if _c.startswith(('300','301')) else \
                  '沪主板' if _c.startswith(('600','601','603','605')) else '深主板' if _c.startswith(('000','001','002','003')) else \
                  '北交所' if _c.startswith(('8','4')) else '其他'
            positions.append({
                "symbol": sym, "name": pos.get("name", code),
                "board": _bd,  # D15
                "qty": pos["qty"], "quantity": pos["qty"],  # E235: 兼容前端 quantity 字段
                "avg_cost": round(pos["avg_cost"], 2), "cost_price": round(pos["avg_cost"], 2),
                "last_price": round(price, 2), "current_price": round(price, 2),
                "market_value": round(price * pos["qty"], 2),
                "pnl": round(pnl, 2), "unrealized_pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2), "unrealized_pnl_pct": round(pnl_pct, 2),
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
            rev = s.get("revenue", 0)
            qty = s.get("qty", 100)
            # C08: 优先使用卖出记录自带的cost/pnl字段，不依赖买入匹配
            cost = s.get("cost", 0)
            if cost <= 0:
                # 无自带cost → 尝试匹配买入记录
                sym = s.get("symbol", "")
                if sym in buy_queue and buy_queue[sym]:
                    matched = buy_queue[sym].pop(0)
                    buy_price = matched.get("price", 0)
                    buy_qty = matched.get("qty", qty)
                    cost = buy_price * min(qty, buy_qty)
                    if buy_qty > qty:
                        matched["qty"] = buy_qty - qty
                        buy_queue[sym].insert(0, matched)
            if cost <= 0:
                # P1-3修复: 成本无法确定时标记为估算，不虚增利润
                cost = rev  # 保守：假设保本（不再虚增10%利润）
                s["cost_estimated"] = True
            else:
                s["cost_estimated"] = False
            pnl = rev - cost
            if pnl > 0:
                wins.append(s)
            if cost > 0:
                trade_returns.append(pnl / cost)

        wr = round(len(wins) / len(sells) * 100, 1) if sells else None  # None=无卖出,前端显示N/A
        # 真实总盈亏 = 总资产 - 初始本金（包含已实现+未实现）
        initial = 1_000_000.0
        current_eq = self.get_total_equity(quotes)
        total_pnl = round(current_eq - initial, 2)

        if trade_returns and len(trade_returns) >= 2:
            arr = np.array(trade_returns)
            std = max(np.std(arr), 1e-8)  # C08: min_std保护，防止return相同时std极小→夏普爆炸
            sharpe = round(float(np.mean(arr) / std) * np.sqrt(min(252, len(arr))), 2)
        else:
            sharpe = 0.0

        # 最大回撤: 基于交易流水的净PnL累计（买入=现金转资产，不产生回撤）
        # P1-2修复: 加载历史峰值，重启后不丢失
        dd_state_file = r"D:\quant_framework\data\max_drawdown_state.json"
        _hist_peak = float(initial)
        _hist_max_dd = 0.0
        try:
            import json as _jdd, os as _odd
            if _odd.path.exists(dd_state_file):
                with open(dd_state_file, "r") as _fdd:
                    _saved = _jdd.load(_fdd)
                _hist_peak = max(float(_saved.get("peak", initial)), initial)
                _hist_max_dd = float(_saved.get("max_drawdown", 0))
        except Exception:
            pass

        eq = initial
        peak = max(initial, _hist_peak)  # P1-2: 继承历史峰值
        for t in self._trades_archive:
            if t.get("side") == "sell":
                eq += t.get("pnl", t.get("revenue", 0) - t.get("cost", 0))
            if eq > peak:
                peak = eq
        # 历史最大回撤 vs 当前未实现亏损，取较大者
        dd_from_peak = round((eq - peak) / peak * 100, 2) if peak > 0 else 0.0
        if dd_from_peak < 0:
            max_dd = min(dd_from_peak, _hist_max_dd)  # P1-2: 保留历史最深回撤
        elif current_eq < initial:
            max_dd = min(round((current_eq - initial) / initial * 100, 2), _hist_max_dd)
        else:
            max_dd = _hist_max_dd  # P1-2: 无新回撤时保留历史值

        # P1-2: 持久化当前峰值和最大回撤
        try:
            import json as _jdd2, os as _odd2
            _odd2.makedirs(_odd2.dirname(dd_state_file), exist_ok=True)
            with open(dd_state_file, "w") as _fdd2:
                _jdd2.dump({"peak": peak, "max_drawdown": max_dd,
                            "current_equity": current_eq, "initial": initial,
                            "updated_at": datetime.now().isoformat()}, _fdd2)
        except Exception:
            pass
        # 钳制非法值
        if max_dd < -100:
            max_dd = -100.0
        calmar = round(abs((total_pnl / initial * 100) / max(abs(max_dd), 0.01)), 2)

        # E49-C: 现金年化收益（年化1.5%）
        if not hasattr(self, 'start_time'): self.start_time = datetime.now()
        days_held = max(1, (datetime.now() - self.start_time).days)
        cash_interest = round(self.cash * 0.015 / 365 * days_held, 2)

        # E55: 含现金利息的总盈亏
        total_pnl_with_interest = round(total_pnl + cash_interest, 2)

        # D11: 计算模拟盘日亏损（已实现+未实现）
        try:
            day_cost = sum(pos["avg_cost"] * pos["qty"] for pos in self.positions.values())
            day_market = sum(pos.get("last_price", pos["avg_cost"]) * pos["qty"] for pos in self.positions.values())
            unrealized_pct = (day_market - day_cost) / max(day_cost, 1) * 100 if day_cost > 0 else 0
            realized_pct = getattr(self, '_daily_loss_total', 0) / 1_000_000 * 100
            daily_loss = round(min(realized_pct, unrealized_pct) if self.positions else realized_pct, 2)
        except Exception:
            daily_loss = 0

        return {
            "cash": round(self.cash, 2),
            "cash_interest": cash_interest,
            "market_value": round(self.get_market_value(quotes), 2),
            "total_equity": round(self.get_total_equity(quotes), 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_with_interest": total_pnl_with_interest,  # E55
            "total_return": round(total_pnl / 1_000_000 * 100, 2),
            "win_rate": round(wr, 1) if wr is not None else 0, "sharpe": sharpe,
            "max_drawdown": max_dd, "calmar": calmar,
            "positions": positions,
            "trade_log": self._trades_archive[-30:],
            "auto_enabled": self.auto_enabled,
            "position_count": len(positions),
            "trade_count": len(self._trades_archive),
            "risk": {"daily_loss": daily_loss},  # D11: 模拟盘日亏损
        }

    # ═══════════════════ 自动交易 ═══════════════════

    def _reset_daily_if_new_day(self):
        today = datetime.now().strftime("%Y%m%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_trade_count = 0
            self._daily_buy_count = 0
            self._daily_loss_total = 0.0
            self._day_start_equity = self.get_total_equity()  # 新交易日重置基准

    def auto_trade_check(self, signals):
        """自动交易规则 — 通过 RuleEngine 执行。

        P0-模拟-01: 7条规则全交由 RuleEngine 处理。
        与旧 PaperAccount.auto_trade_check() 的结果接口完全一致。
        """
        if not self.auto_enabled:
            return []
        # E33-1: 防御性初始化（__init__已初始化，此处兜底）
        if not hasattr(self, '_rule_engine') or self._rule_engine is None:
            self._rule_engine = RuleEngine()
            self._setup_rules()
        self._reset_daily_if_new_day()
        cfg = _load_trade_config()
        actions = []

        # 日初权益基准(用于计算含未实现亏损的日内总亏损)
        try:
            if not hasattr(self, '_day_start_equity') or self._day_start_equity is None:
                self._day_start_equity = self.get_total_equity()
            current_eq = self.get_total_equity()
            unrealized_loss = current_eq - self._day_start_equity
        except Exception:
            current_eq = self.cash
            unrealized_loss = 0
        # 更新 _daily_loss_total 为已实现+当前未实现中的较大亏损
        total_daily_loss = min(self._daily_loss_total, unrealized_loss)

        # ── 构建上下文 ──
        context = {
            "daily_trade_count": getattr(self, '_daily_buy_count', 0),  # 买入上限只计买入，卖出不受限
            "daily_loss_total": total_daily_loss,  # 含未实现亏损
            "cash": self.cash,
            "config": cfg,
        }

        # ── 1. 检查持仓规则: 止损/止盈 ──
        # E58+E90+fix: 刷新持仓实时价格 + 防止回退到买入价
        if self.positions:
            print(f"[Paper] 卖出检查: {len(self.positions)}只持仓 ")
        for sym, pos in list(self.positions.items()):
            old_price = pos.get("last_price", pos["avg_cost"])
            fresh_price = self._resolve_price(sym, fallback=None)  # 不给fallback，得null才知道失败
            if fresh_price and fresh_price > 0 and fresh_price != 10.0:  # 10.0=默认值=无效
                pos["last_price"] = fresh_price
            elif not fresh_price or fresh_price <= 0:
                fresh_price = old_price  # 保持上次有效价格，不回退
            # E90: 诊断 — 价格更新后计算账面盈亏
            pnl_pct = (fresh_price / pos["avg_cost"] - 1) * 100 if fresh_price and pos["avg_cost"] > 0 else 0
            if pnl_pct < -3:
                print(f"[Paper] 持仓诊断 {sym}: avg={pos['avg_cost']:.2f} "
                      f"old_price={old_price:.2f} new_price={fresh_price:.2f} "
                      f"pnl={pnl_pct:.1f}% buy_date={pos.get('buy_date','?')}")
            elif pnl_pct > 0:
                print(f"[Paper] {sym} 浮盈{pnl_pct:.1f}% (avg={pos['avg_cost']:.2f} price={fresh_price:.2f})")

        # E203: 持仓天数到期检查
        max_days = cfg.get("max_hold_days", 0)
        if max_days > 0:
            for sym, pos in list(self.positions.items()):
                buy_date_str = pos.get("buy_date", "")
                if not buy_date_str:
                    continue
                try:
                    buy_date = datetime.strptime(buy_date_str, "%Y-%m-%d")
                    held = (datetime.now() - buy_date).days
                except Exception:
                    continue
                if held < max_days:
                    continue
                # E203b: 涨停跳过
                try:
                    from risk_guard import PreTradeChecker
                    _chk = PreTradeChecker(config=cfg, positions=self.positions,
                                           cash=self.cash, total_equity=self.get_total_equity())
                    if _chk._is_limit_up(sym):
                        print(f"[Paper] 持仓{sym}已{held}天到期，涨停中跳过")
                        continue
                except: pass
                price = pos.get("last_price", pos["avg_cost"])
                qty = pos["qty"]
                r = self.place_order(sym, "sell", price, qty, trade_type="auto")
                if r.get("success"):
                    r["reason"] = f"持仓到期({held}天>={max_days}天)"
                    actions.append(r)
                    self._daily_trade_count += 1
                    print(f"[Paper] 持仓到期卖出 {sym} x{qty} @{price:.2f} (已持{held}天)")

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

                # 用实时价执行，不依赖规则检查时的缓存价
                exec_price = self._resolve_price(ra.symbol or sym, fallback=ra.price)
                if exec_price and exec_price > 0 and exec_price != 10.0:
                    exec_price = exec_price
                else:
                    exec_price = ra.price
                r = self.place_order(ra.symbol or sym, "sell", exec_price, sell_qty, trade_type="auto")
                if r.get("success"):
                    r["reason"] = ra.reason
                    actions.append(r)
                    self._daily_trade_count += 1
                    # 钉钉通知
                    try:
                        from dingtalk_alerts import trade_signal
                        trade_signal(sym, "", "sell", str(ra.reason), ra.price, 0, "", source="模拟")
                    except: pass

                # 检查是否附带止损 (TrailingStopRule 的 meta 处理)
                if ra.meta and ra.meta.get("stop_loss_check") and ra.meta.get("remaining_qty", 0) > 0:
                    remaining = self.positions.get(sym, {}).get("qty", 0)
                    if remaining > 0 and pos["avg_cost"] > 0:
                        pnl = (ra.price / pos["avg_cost"] - 1)
                        if pnl <= ra.meta.get("stop_loss_threshold", -0.05):
                            r2 = self.place_order(sym, "sell", ra.price, remaining, trade_type="auto")
                            if r2.get("success"):
                                r2["reason"] = f"止盈T止损({pnl*100:.1f}%)"
                                actions.append(r2)
                                self._daily_trade_count += 1

        # ── 1.5 E117: 账户级日亏两层控制（在全局规则前，先于个股止损后） ──
        account_loss_pct = total_daily_loss / 1_000_000  # 小数(0.03=3%)
        half_cfg = cfg.get("daily_loss_sell_half", -0.03)  # 配置也是小数(-0.05=-5%)
        clear_cfg = cfg.get("daily_loss_clear_all", -0.05)
        if account_loss_pct <= half_cfg and total_eq > 0:
            sell_ratio = 1.0 if account_loss_pct <= clear_cfg else 0.5
            label = "清仓" if sell_ratio >= 1 else "卖半"
            print(f"[Paper] 账户日亏{account_loss_pct*100:.1f}%触发{label}")
            sorted_pos = sorted(self.positions.items(),
                                key=lambda x: x[1].get("last_price", 0) * x[1].get("qty", 0),
                                reverse=True)
            for sym, pos in sorted_pos:
                total_qty = pos.get("qty", 0)
                sell_qty = max(100, int(total_qty * sell_ratio) // 100 * 100)
                if sell_qty >= 100:
                    price = pos.get("last_price", pos.get("avg_cost", 0))
                    r = self.place_order(sym, "sell", price, sell_qty, trade_type="auto")
                    if r.get("success"):
                        r["reason"] = f"账户日亏{label}({account_loss_pct*100:.1f}%)"
                        actions.append(r)
                        self._daily_trade_count += 1
                        print(f"[Paper] 账户日亏{label} {sym} x{sell_qty} @{price:.2f}")

        # ── 2. 检查全局规则: 熔断/频率限制 ──
        can_buy, reject_reason = self._rule_engine.can_buy(context)

        # ── 2.5 E89+E92: 双层集中度风控 ──
        max_soft = cfg.get("max_single_position_pct", 30) / 100  # 🟡建议线30%
        max_hard = cfg.get("max_single_position_hard", 50) / 100  # 🔴硬上限50%
        for threshold, label in [(max_hard, "硬上限"), (max_soft, "建议线")]:
            total_eq = self.get_total_equity()  # fix: 每轮重算，卖后权益变化
            if total_eq <= 0:
                break
            for sym, pos in list(self.positions.items()):
                price = pos.get("last_price") or pos.get("avg_cost") or 1.0
                mkt_val = price * pos["qty"]
                if mkt_val / total_eq > threshold:
                    target_val = total_eq * threshold
                    excess_val = mkt_val - target_val
                    sell_qty = max(100, int(excess_val / (price + 0.01)) // 100 * 100)
                    if sell_qty >= 100 and sell_qty <= pos["qty"]:
                        r = self.place_order(sym, "sell", price, sell_qty, trade_type="auto")
                        if r.get("success"):
                            r["reason"] = f"集中度{label}({mkt_val/total_eq*100:.0f}%>{threshold*100:.0f}%)减{sell_qty}股"
                            actions.append(r)
                            self._daily_trade_count += 1
                            print(f"[Paper] 集中度减仓 {sym}: {mkt_val/total_eq*100:.0f}%→{threshold*100:.0f}% ({label})")

        # ── 2.6 日亏损清算: can_buy被拦截时从最大头寸开始卖
        if not can_buy and reject_reason and "亏损" in str(reject_reason):
            print(f"[Paper] 日亏损触发清算: {reject_reason}")
            sorted_pos = sorted(self.positions.items(),
                                key=lambda x: x[1].get("last_price", 0) * x[1].get("qty", 0),
                                reverse=True)
            for sym, pos in sorted_pos:
                qty = pos.get("qty", 0)
                price = pos.get("last_price", pos.get("avg_cost", 0))
                if qty >= 100 and price > 0:
                    r = self.place_order(sym, "sell", price, qty, trade_type="auto")
                    if r.get("success"):
                        r["reason"] = "日亏清算(最大头寸优先)"
                        actions.append(r)
                        self._daily_trade_count += 1
                        print(f"[Paper] 日亏清算卖出 {sym} x{qty} @{price:.2f}")

        # ── 3. 信号买入 ──
        max_positions = cfg.get("max_positions", 3)
        # E263: 多策略信号聚合
        if can_buy and not signals:
            # FIX: 多策略信号源 + HTTP兜底
            try:
                from strategy_manager import mgr
                m = mgr.get_default()
                if self.factor_cache and self.stock_data:
                    mgr_signals = m.generate_signals(self.factor_cache, self.stock_data, max_total=15)
                    if mgr_signals:
                        signals = mgr_signals
                        print(f"[Paper] 多策略信号: {len(mgr_signals)}只")
            except Exception: pass
            if not signals:
                try:
                    import urllib.request, json as _j2
                    _r2 = urllib.request.urlopen('http://127.0.0.1:5002/api/signal-center', timeout=10)
                    signals = _j2.loads(_r2.read().decode()).get('signals', [])
                    if signals: print(f"[Paper] HTTP信号兜底: {len(signals)}条")
                except Exception: pass

        if can_buy and signals:
            signal_min = _load_trade_config().get("signal_min_strength", self._signal_filter.min_strength)
            max_daily = cfg.get("max_daily_trades", 4)
            min_cash = cfg.get("min_cash_reserve", 50000)  # E38: 最低现金保留

            buy_this_cycle = 0
            max_cycle = cfg.get("max_buy_per_cycle", 3)  # A02: 配置可控
            for sig in (signals or [])[:15]:
                if getattr(self, '_daily_buy_count', 0) >= max_daily or buy_this_cycle >= max_cycle:
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
                # E303: 当日同股票不重复买入（信号幂等）
                _today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
                if any(t.get("symbol") == sym and t.get("side") == "buy" and str(t.get("date",""))[:10] == _today
                       for t in (getattr(self, '_trades_archive', None) or [])):
                    continue
                # A01: 用实时行情价覆盖信号缓存价（提高成交率）
                real_price = sig.get("close", 10)
                try:
                    rt = self._resolve_price(sym, fast=True)
                    if rt and rt > 0: real_price = rt
                except: pass
                # E266: 黑名单检查
                try:
                    from blacklist import is_blocked
                    if is_blocked(sym):
                        continue
                except: pass
                # E38: 检查持仓数上限
                if max_positions > 0 and len(self.positions) >= max_positions:
                    break  # 已达上限，停止买入
                # E38: 检查最低现金保留
                if min_cash > 0 and self.cash < min_cash:
                    break  # 现金不足，停止买入

                # 仓位计算（A01: 用实时价）
                qty = self._position_sizer.calculate_shares(bs, self.cash, real_price)
                if qty >= 100:
                    r = self.place_order(sym, "buy", real_price, qty, trade_type="auto")
                    buy_this_cycle += 1
                    # E32: 买入失败记日志
                    if not r.get("success"):
                        print(f"[Paper] 自动买入失败 {sym}: {r.get('error','未知')}")
                    if r.get("success"):
                        pct = self._position_sizer.sizing_map.get(bs, 0.15)
                        strat = sig.get("strategy", sig.get("name", ""))
                        r["reason"] = f"信号{bs}级({int(pct*100)}%仓)"
                        if strat: r["reason"] += f" [{strat}]"
                        r["strategy"] = strat  # 绩效追踪
                        actions.append(r)
                        self._daily_trade_count += 1
                        self._daily_buy_count = getattr(self, '_daily_buy_count', 0) + 1
                        # 钉钉通知
                        try:
                            from dingtalk_alerts import trade_signal
                            trade_signal(sym, "", "buy", str(r["reason"]), sig.get("close", 0), 0, "", source="模拟")
                        except: pass

        # E45-A: 超额持仓主动减仓（每次最多卖2只，优先亏损最大的）
        if max_positions > 0 and len(self.positions) > max_positions:
            excess = len(self.positions) - max_positions
            sorted_pos = sorted(
                self.positions.items(),
                key=lambda x: (x[1].get("last_price", x[1].get("avg_cost", 0))
                               / max(x[1].get("avg_cost", 1), 0.01) - 1)
            )
            sold = 0
            for sym, pos in sorted_pos:
                if sold >= min(excess, 2):
                    break
                qty = pos.get("qty", 0)
                if qty > 0:
                    price = pos.get("last_price", pos.get("avg_cost", 0))
                    r = self.place_order(sym, "sell", price, qty, trade_type="auto")
                    if r.get("success"):
                        r["reason"] = f"超额减仓(持仓{len(self.positions)}>{max_positions})"
                        actions.append(r)
                        self._daily_trade_count += 1
                        sold += 1

        # D13: 更新线程心跳（看门狗监控用）
        self._last_heartbeat = datetime.now()
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


# ═══════════════════════════════════════════════════════
#  PaperAutoLoop — 模拟盘自动交易循环 (蓝图 v3.0 Phase 1)
# ═══════════════════════════════════════════════════════

class PaperAutoLoop:
    """模拟盘自动交易循环 — RuleEngine 统一驱动。

    启动时自动禁用 app.py 旧版内联循环，避免双循环冲突。
    补齐审计日志、SSE推送、A/B测试、实时价格刷新等旧循环功能。
    """

    CHECK_INTERVAL = 10  # 扫描间隔(秒)

    def __init__(self):
        self.running = False
        self._thread = None
        self._last_scan = None
        self._scan_count = 0
        self._errors = 0

    # ── 生命周期 ──

    def start(self):
        """启动循环，同时禁用 app.py 旧版内联循环避免双循环冲突。"""
        if self.running:
            return
        # P0: 禁用旧循环 (app.py:308 _paper_auto_loop)
        try:
            import app as _app
            if hasattr(_app, '_paper_auto_running'):
                _app._paper_auto_running[0] = False
                print("[PaperLoop] 已禁用旧版内联循环")
        except Exception:
            pass
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PaperAutoLoop")
        self._thread.start()
        print("[PaperLoop] 自动交易循环已启动 (10s 间隔, RuleEngine 驱动)")

    def stop(self):
        """停止循环，恢复旧版循环作为降级保底。"""
        self.running = False
        # 恢复旧循环 (降级保底: 宪法第二章第2条)
        try:
            import app as _app
            if hasattr(_app, '_paper_auto_running'):
                _app._paper_auto_running[0] = True
                print("[PaperLoop] 已恢复旧版内联循环(降级)")
        except Exception:
            pass
        print("[PaperLoop] 自动交易循环已停止")

    def is_running(self) -> bool:
        return self.running and (self._thread and self._thread.is_alive())

    # ── 交易时间检查 ──

    @staticmethod
    def _can_trade() -> bool:
        """复用 realtime_quotes.is_trading_time()，与旧循环一致。"""
        try:
            from realtime_quotes import is_trading_time
            return is_trading_time()
        except ImportError:
            now = datetime.now()
            t = now.time()
            if now.weekday() >= 5:
                return False
            if t < datetime.strptime("09:25", "%H:%M").time():
                return False
            if datetime.strptime("11:30", "%H:%M").time() <= t <= datetime.strptime("13:00", "%H:%M").time():
                return False
            if t >= datetime.strptime("15:05", "%H:%M").time():
                return False
            return True

    # ── 信号获取 + 实时价格刷新 ──

    @staticmethod
    def _get_signals() -> list[dict]:
        """获取信号 — FACTOR_CACHE优先，HTTP兜底，实时价格刷新 (对齐旧循环)。"""
        signals = []
        try:
            import app as _app
            cache = getattr(_app, '_FACTOR_CACHE', None)
            if cache and getattr(_app, '_CACHE_READY', False):
                for s in cache[:200]:
                    sym = getattr(s, 'symbol', '')
                    if not sym:
                        continue
                    rt_close = getattr(s, 'close', 0) or 0
                    rt_chg = getattr(s, 'change_pct', 0) or 0
                    # 实时价格刷新 (对齐旧循环)
                    try:
                        from realtime_quotes import _quote_cache
                        code = sym.replace('sh', '').replace('sz', '')
                        if _quote_cache and _quote_cache.get('data') and code in _quote_cache['data']:
                            q = _quote_cache['data'][code]
                            rt_close = float(q.get('close', rt_close) or rt_close)
                            rt_chg = float(q.get('change_pct', rt_chg) or rt_chg)
                    except Exception:
                        pass
                    signals.append({
                        'symbol': sym,
                        'name': getattr(s, 'name', '') or '',
                        'buy_signal': getattr(s, 'buy_signal', 0) or 0,
                        'close': rt_close,
                        'change_pct': rt_chg,
                        'vol_ratio': getattr(s, 'vol_ratio', 1) or 1,
                        'industry': getattr(s, 'industry', '') or '',
                        'power_score': getattr(s, 'power_score', 0) or 0,
                    })
        except Exception as e:
            print(f"[PaperLoop] 信号读取失败(FACTOR_CACHE): {e}")

        if not signals:
            try:
                import urllib.request, json as _j
                r = urllib.request.urlopen('http://127.0.0.1:5002/api/signal-center', timeout=10)
                raw = _j.loads(r.read().decode()).get('signals', [])
                for _s in raw[:50]:
                    signals.append({
                        'symbol': _s.get('symbol', ''),
                        'buy_signal': _s.get('buy_signal', 0) or 0,
                        'close': _s.get('close', 0) or 0,
                        'vol_ratio': 1,
                        'change_pct': _s.get('change_pct', 0) or 0,
                    })
                if signals:
                    print(f"[PaperLoop] HTTP信号兜底: {len(signals)}条")
            except Exception as e:
                print(f"[PaperLoop] HTTP兜底也失败: {e}")

        # FactorRegistry 驱动: 对所有 active 因子生成信号
        try:
            from factor_registry import get_active_factors
            active_factors = {f["name"] for f in get_active_factors()}
            from quant_framework.execution.rules.engine import RuleEngine
            re_engine = getattr(paper, '_rule_engine', None)
            if re_engine is None:
                re_engine = RuleEngine(broker=paper._broker)
            watchlist = list(paper.positions.keys())[:10]
            if not watchlist:
                for s in (signals[:30] if signals else []):
                    sym = s.get("symbol", "")
                    if sym and sym not in watchlist:
                        watchlist.append(sym)
            if not watchlist:
                try:
                    from stock_pool_manager import get_stock_pool
                    pool = get_stock_pool("core_plus_extended")
                    watchlist = pool[:50]
                except Exception: pass
            for sym in watchlist[:30]:
                try:
                    bs = re_engine.check_buy_signal(sym, {}, market_state="unknown")
                    # 只接受 Registry active 因子的信号
                    if bs and bs.get("strategy") in active_factors:
                        signals.append({
                            "symbol": sym, "name": bs.get("strategy", ""),
                            "buy_signal": int(bs.get("score", 0) / 20),
                            "close": bs.get("entry_price", 10),
                            "change_pct": 0, "vol_ratio": 1, "industry": "",
                            "power_score": bs.get("score", 0),
                            "_v15_strategy": bs.get("strategy"),
                            "_v15_score": bs.get("score"),
                        })
                except Exception: pass
        except Exception as e:
            print(f"[PaperLoop] Registry信号异常: {e}")

        return signals

    # ── 审计日志 + SSE推送 (补齐旧循环功能) ──

    @staticmethod
    def _write_audit_log(actions: list):
        """写入审计日志，对齐旧循环 audit_trade.jsonl。"""
        try:
            import json as _aj
            from config import AUDIT_LOG_JSONL
            now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_line = _aj.dumps({
                'ts': now_ts,
                'actions': [
                    {'symbol': a.get('symbol', ''), 'action': a.get('action', ''),
                     'reason': a.get('reason', '')}
                    for a in actions
                ]
            }, ensure_ascii=False)
            with open(AUDIT_LOG_JSONL, 'a', encoding='utf-8') as _af:
                _af.write(log_line + '\n')
            # 限制最多5000行
            if os.path.exists(AUDIT_LOG_JSONL) and os.path.getsize(AUDIT_LOG_JSONL) > 1_000_000:
                with open(AUDIT_LOG_JSONL, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                if len(lines) > 5000:
                    open(AUDIT_LOG_JSONL, 'w', encoding='utf-8').writelines(lines[-2000:])
        except Exception:
            pass

    @staticmethod
    def _push_sse_events(actions: list):
        """推送风控事件到SSE，对齐旧循环 store.set('risk_event')。"""
        try:
            now_ts = datetime.now().strftime('%H:%M:%S')
            from state_persist import store
            for a in actions:
                store.set('risk_event', {
                    'type': 'auto_trade',
                    'time': now_ts,
                    'symbol': a.get('symbol', ''),
                    'action': a.get('action', ''),
                    'reason': a.get('reason', ''),
                })
        except Exception:
            pass

    # ── 主循环 ──

    def _loop(self):
        """后台循环: 每10秒扫描信号 → RuleEngine → 下单。"""
        import time as _time
        _time.sleep(5)  # 首次延迟等系统就绪

        while self.running:
            try:
                self._scan_count += 1
                self._last_scan = datetime.now()

                if not self._can_trade():
                    _time.sleep(self.CHECK_INTERVAL)
                    continue

                if not paper.auto_enabled:
                    _time.sleep(self.CHECK_INTERVAL)
                    continue

                # 1. 获取信号 (含实时价格刷新)
                signals = self._get_signals()

                # 2. 刷新持仓市价 (对齐旧循环)
                for sym, pos in list(paper.positions.items()):
                    mp = paper._get_market_price(sym)
                    if mp and mp > 0:
                        pos['last_price'] = mp

                if signals and self._scan_count % 3 == 0:
                    print(f"[PaperLoop] 第{self._scan_count}次扫描, "
                          f"信号{len(signals)}条, 持仓{len(paper.positions)}只, "
                          f"资金¥{paper.cash:,.0f}")

                # 3. RuleEngine 驱动 (信号取50条，对齐旧循环)
                actions = paper.auto_trade_check(signals[:50] if signals else None)

                # P3-2: 日终自动报告 (15:05)
                now = datetime.now()
                if now.hour == 15 and now.minute >= 5 and not getattr(self, '_report_generated_today', False):
                    try:
                        from daily_report import generate_daily_report
                        generate_daily_report()
                        self._report_generated_today = True
                        print("[PaperLoop] 日终报告已生成")
                    except Exception as e:
                        print(f"[PaperLoop] 日终报告失败: {e}")
                if now.hour < 15:
                    self._report_generated_today = False  # 重置标记

                # G2: 策略自动熔断 (每30循环≈5分钟)
                if self._scan_count % 30 == 0:
                    try:
                        from factor_health import check_strategy_circuit_breaker
                        cb_actions = check_strategy_circuit_breaker()
                        if cb_actions:
                            print(f"[PaperLoop] 熔断触发: {len(cb_actions)}个策略")
                    except Exception: pass

                # 4. A/B测试信号处理 (对齐旧循环)
                if signals:
                    try:
                        from ab_test import runner as _ab_runner
                        if _ab_runner.running:
                            _ab_runner.process_signals(signals)
                    except Exception:
                        pass

                # 5. 审计日志 + SSE推送 (补齐旧循环功能)
                if actions:
                    buy_count = sum(1 for a in actions if a.get("action") == "buy")
                    sell_count = sum(1 for a in actions if a.get("action") == "sell")
                    print(f"[PaperLoop] 执行{len(actions)}笔 (买{buy_count}/卖{sell_count})")
                    self._write_audit_log(actions)
                    self._push_sse_events(actions)

                self._errors = 0

            except Exception as e:
                self._errors += 1
                print(f"[PaperLoop] 循环异常(#{self._errors}): {e}")
                if self._errors > 5:
                    print("[PaperLoop] 连续错误>5次，停止循环 → 恢复旧循环")
                    self.running = False
                    try:
                        import app as _app
                        if hasattr(_app, '_paper_auto_running'):
                            _app._paper_auto_running[0] = True
                    except Exception:
                        pass
                    break

            _time.sleep(self.CHECK_INTERVAL)

        print("[PaperLoop] 循环退出")


# ── 全局实例 ──
_paper_loop = PaperAutoLoop()


def start_auto_loop():
    """启动模拟盘自动交易循环 (app.py 启动时调用)。"""
    if not _paper_loop.is_running():
        _paper_loop.start()
        return True
    return False


def stop_auto_loop():
    """停止模拟盘自动交易循环。"""
    _paper_loop.stop()
    return True


def get_loop_status() -> dict:
    """获取自动循环运行状态。"""
    return {
        "running": _paper_loop.is_running(),
        "auto_enabled": paper.auto_enabled,
        "scan_count": _paper_loop._scan_count,
        "last_scan": _paper_loop._last_scan.strftime("%H:%M:%S") if _paper_loop._last_scan else None,
        "errors": _paper_loop._errors,
        "positions": len(paper.positions),
        "cash": paper.cash,
    }
