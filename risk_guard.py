"""
事前风控检查器 + 持仓相关性分析 (E371重构 v2)
报单前验证: 资金/仓位/涨跌停/T+1/信号ID去重/乌龙指/集中度/相关性
返回值: (action, reason, adjusted_qty) — APPROVE/REJECT/REDUCE/QUEUE
"""
import numpy as np
from datetime import datetime
from collections import defaultdict


class PreTradeChecker:
    """报单前风控检查 — E371 v2: 四元返回值 + 信号ID去重(行业标准)"""

    _executed_signals: dict = {}  # E371 v2: {date: {signal_id, ...}} 每日自动清

    _audit_file = r"D:\quant_web\data\audit_log.jsonl"  # E372: 审计日志

    def __init__(self, config=None, positions=None, cash=0, total_equity=0,
                 factor_cache=None, stock_data=None):
        self.config = config or {}
        self.positions = positions or {}
        self.cash = cash
        self.total_equity = total_equity
        self.factor_cache = factor_cache or []
        self.stock_data = stock_data or {}

    def check_buy(self, symbol, price, qty, industry='', signal_level=3,
                  live_mode=False, daily_trades=0, signal_id=''):
        """买入前检查 (v2.1: 1000万阈值)"""
        if price is None or price <= 0:
            return ("REJECT", f"价格无效: {price}", 0)
        cost = price * qty

        # 1. 信号ID去重 (E371 v2: 对齐QuantConnect idempotency key)
        if signal_id:
            _today = datetime.now().strftime("%Y%m%d")
            if _today not in PreTradeChecker._executed_signals:
                PreTradeChecker._executed_signals = {_today: set()}  # 新的一天, 清空
            if signal_id in PreTradeChecker._executed_signals.get(_today, set()):
                return ("REJECT", f"信号{signal_id}今日已执行", 0)

        # 2. 乌龙指防护
        max_order_value = self.config.get("max_order_value", 1_000_000)
        if cost > max_order_value:
            return ("REJECT", f"单笔{cost:,.0f}超上限{max_order_value:,.0f}", 0)

        # 3. 日交易笔数
        max_daily = self.config.get("max_daily_trades", 5)
        if daily_trades >= max_daily:
            return ("REJECT", f"日交易{daily_trades}笔已达上限{max_daily}", 0)

        # 4. 涨跌停 (实盘允许排队)
        if self._is_limit_up(symbol):
            if live_mode:
                return ("QUEUE", "涨停排队挂单", qty)
            return ("REJECT", "涨停板不追买", 0)

        # 5. 信号等级
        min_sig = self.config.get("signal_min_strength", 3)
        if signal_level < min_sig:
            return ("REJECT", f"信号等级{signal_level}<最低{min_sig}", 0)

        # 6. 仓位总数
        if len(self.positions) >= self.config.get("max_positions_abs", 10):
            return ("REJECT", f"持仓数已达上限{self.config.get('max_positions_abs',10)}只", 0)

        # 7. 资金检查
        if cost > self.cash:
            return ("REJECT", f"资金不足: 需¥{cost:,.0f} 可用¥{self.cash:,.0f}", 0)

        # 8. 单票集中度 — REDUCE
        max_single = self.config.get("max_single_position_pct", 20) / 100
        after_value = cost + sum(
            p.get('last_price', p.get('avg_cost', 0)) * p.get('qty', 0)
            for s, p in self.positions.items() if s == symbol
        )
        if self.total_equity > 0 and after_value / self.total_equity > max_single:
            reduced = max(100, int(qty * 0.5))
            return ("REDUCE", f"集中度偏高,缩至{reduced}股", reduced)

        # 9. 行业集中度 — REDUCE
        max_sector = self.config.get("max_sector_pct", 25) / 100  # E372: 25%
        sector_value = sum(
            p.get('last_price', p.get('avg_cost', 0)) * p.get('qty', 0)
            for s, p in self.positions.items() if p.get('industry', '') == industry
        ) + cost
        if industry and self.total_equity > 0 and sector_value / self.total_equity > max_sector:
            reduced = max(100, int(qty * 0.5))
            return ("REDUCE", f"行业{industry}集中度偏高,缩至{reduced}股", reduced)

        # 10. 持仓相关性限制 (P2 — 2026-07-10)
        # 同一行业已有≥2只持仓时拒绝再开, 防板块集中爆雷
        same_sector_count = sum(
            1 for s, p in self.positions.items()
            if p.get('industry', '') == industry and industry
        )
        max_same_sector = self.config.get("max_same_sector_positions", 2)
        if industry and same_sector_count >= max_same_sector:
            return ("REJECT",
                f"行业[{industry}]已有{same_sector_count}只持仓(上限{max_same_sector}), 过度集中",
                0)

        # E372: 风险预算检查
        _risk_budget_pct = self.config.get("daily_risk_budget_pct", 0.01)
        _daily_pnl = sum(
            (p.get('last_price',0) - p.get('avg_cost',0)) * p.get('qty',0)
            for p in (self.positions or {}).values()
        )
        if self.total_equity > 0 and _daily_pnl / self.total_equity < -_risk_budget_pct:
            return ("REJECT", f"日风险预算已用完(PnL={_daily_pnl/self.total_equity*100:.1f}%)", 0)

        # 流动性门槛: 从配置读取, 默认2000万 (对齐游资标准)
        _min_amount = self.config.get("min_daily_amount", 20_000_000)
        _avg_amt = self._get_avg_daily_amount(symbol)
        if _avg_amt > 0 and _avg_amt < _min_amount:
            return ("REJECT", f"日均成交额{_avg_amt/1e4:.0f}万<{_min_amount/1e4:.0f}万,流动性差", 0)

        # 记录信号 (通过后标记已执行)
        if signal_id:
            PreTradeChecker._executed_signals.setdefault(_today, set()).add(signal_id)

        # E372: 审计日志
        self._audit(symbol, "APPROVE", f"qty={qty} price={price}")

        return ("APPROVE", "OK", qty)

    def check_sell(self, symbol, qty, live_mode=False):
        """卖出前检查 (E371: T+1 + 跌停)
        Returns: (action, reason, adjusted_qty)
        """
        if symbol not in self.positions:
            return ("REJECT", f"未持有{symbol}", 0)
        pos = self.positions[symbol]
        if qty > pos.get('qty', 0):
            return ("REJECT", f"卖出{qty}股>持仓{pos.get('qty',0)}股", 0)

        # T+1检查
        buy_date = pos.get("buy_date", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if buy_date == today:
            return ("REJECT", f"T+1锁定: {symbol}今日买入不可卖出", 0)

        # 跌停 (实盘允许排队)
        if self._is_limit_down(symbol):
            if live_mode:
                return ("QUEUE", "跌停排队挂单", qty)
            return ("REJECT", "跌停板无法卖出", 0)

        return ("APPROVE", "OK", qty)

    # ═══ E371: 统一走 market_limits ═══

    def _is_limit_up(self, symbol):
        try:
            from quant_framework.core.market_limits import is_limit_up
        except ImportError:
            return False
        prev_close = self._get_prev_close(symbol)
        if prev_close <= 0: return False
        cur = self._get_current_price(symbol)
        if cur <= 0: return False
        return is_limit_up(symbol, cur, prev_close)

    def _is_limit_down(self, symbol):
        try:
            from quant_framework.core.market_limits import is_limit_down
        except ImportError:
            return False
        prev_close = self._get_prev_close(symbol)
        if prev_close <= 0: return False
        cur = self._get_current_price(symbol)
        if cur <= 0: return False
        return is_limit_down(symbol, cur, prev_close)

    def _get_prev_close(self, symbol):
        for fc in self.factor_cache:
            if getattr(fc, 'symbol', '') == symbol:
                return getattr(fc, 'pre_close', 0) or 0
        df = self.stock_data.get(symbol)
        if df is not None and len(df) >= 2:
            return float(df['close'].iloc[-2])
        return 0

    def _get_current_price(self, symbol):
        for fc in self.factor_cache:
            if getattr(fc, 'symbol', '') == symbol:
                close = getattr(fc, 'close', 0) or 0
                if close > 0: return close
        return 0

    def _get_avg_daily_amount(self, symbol):
        """E372: 估算日均成交额"""
        try:
            df = self.stock_data.get(symbol) if self.stock_data else None
            if df is not None and len(df) >= 5:
                vols = df['volume'].values[-5:]
                closes = df['close'].values[-5:]
                return float(sum(v * c for v, c in zip(vols, closes)) / len(vols))
        except: pass
        return 0

    def _audit(self, symbol, action, detail):
        """E372: 审计日志"""
        try:
            import json as _j, os as _os, datetime as _dt
            _line = _j.dumps({"ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              "symbol": symbol, "action": action, "detail": detail}, ensure_ascii=False)
            _os.makedirs(_os.path.dirname(PreTradeChecker._audit_file), exist_ok=True)
            with open(PreTradeChecker._audit_file, "a", encoding="utf-8") as _f:
                _f.write(_line + "\n")
        except: pass


class CorrelationAnalyzer:
    """持仓相关性分析 — 检测集中风险"""

    def __init__(self, stock_data=None, factor_cache=None):
        self.stock_data = stock_data or {}
        self.factor_cache = factor_cache or []

    def analyze(self, positions, lookback=60):
        syms = list(positions.keys())
        if len(syms) < 2:
            return {"risk_level": "低", "warning": "", "details": {}}
        closes = {}
        for sym in syms:
            df = self.stock_data.get(sym)
            if df is not None and len(df) >= lookback:
                closes[sym] = df['close'].values[-lookback:]
        if len(closes) < 2:
            return {"risk_level": "低", "warning": "", "details": {}}
        returns = {}
        for sym, c in closes.items():
            if len(c) > 1:
                returns[sym] = np.diff(c) / c[:-1]
        if len(returns) < 2:
            return {"risk_level": "低", "warning": "", "details": {}}
        corr_matrix = np.corrcoef(list(returns.values()))
        max_corr = 0
        max_pair = ("", "")
        n = len(returns)
        keys = list(returns.keys())
        for i in range(n):
            for j in range(i+1, n):
                if abs(corr_matrix[i][j]) > max_corr:
                    max_corr = abs(corr_matrix[i][j])
                    max_pair = (keys[i], keys[j])
        if max_corr > 0.85:
            level = "高"
            warning = f"{max_pair[0]}与{max_pair[1]}高度相关({max_corr:.2f})"
        elif max_corr > 0.7:
            level = "中"
            warning = f"{max_pair[0]}与{max_pair[1]}中度相关({max_corr:.2f})"
        else:
            level = "低"
            warning = ""
        return {"risk_level": level, "warning": warning, "details": {"max_correlation": round(max_corr, 3), "pair": max_pair}}


class RiskEventBus:
    """风控事件总线 — SSE推送"""
    def __init__(self, store=None):
        self.store = store
        self._events = []

    def emit(self, event_type, data):
        e = {"type": event_type, "data": data, "time": datetime.now().strftime("%H:%M:%S")}
        self._events.append(e)
        if len(self._events) > 100: self._events = self._events[-100:]
        return e

    def get_recent(self, n=20):
        return self._events[-n:]


class RiskCycleScheduler:
    """风控周期检查 — 盘前/盘后 (E372)"""
    def __init__(self, paper_engine=None, store=None):
        self.paper = paper_engine
        self.store = store

    def pre_market_check(self):
        """盘前检查: 隔夜风险"""
        if not self.paper: return None
        try:
            eq = self.paper.get_total_equity()
            cash = self.paper.cash
            pos_count = len(self.paper.positions)
            pnl = eq - 1_000_000
            # 隔夜持仓>80% → 告警
            exposure = (eq - cash) / max(eq, 1) * 100
            warnings = []
            if exposure > 80: warnings.append(f"隔夜敞口{exposure:.0f}%偏高")
            if pnl < -50000: warnings.append(f"累计亏损¥{abs(pnl):,.0f}")
            return {"equity": eq, "cash": cash, "positions": pos_count, "exposure_pct": round(exposure,1),
                    "pnl": pnl, "warnings": warnings}
        except: return None

    def post_market_report(self):
        """日终风控报告 (E372)"""
        if not self.paper: return None
        try:
            eq = self.paper.get_total_equity()
            pos = self.paper.positions
            trades = getattr(self.paper, '_trades_archive', []) or []
            today = datetime.now().strftime("%Y-%m-%d")
            today_trades = [t for t in trades if str(t.get("date","")) == today]
            buys = [t for t in today_trades if t.get("side")=="buy"]
            sells = [t for t in today_trades if t.get("side")=="sell"]
            win_sells = [t for t in sells if t.get("pnl",0) > 0]

            # 行业敞口
            sectors = {}
            for sym, p in pos.items():
                ind = p.get("industry","其他") or "其他"
                mkt = p.get("last_price",0) * p.get("qty",0)
                sectors[ind] = sectors.get(ind,0) + mkt

            report = {
                "date": today, "total_equity": round(eq,2),
                "daily_pnl": round(sum(t.get("pnl",0) for t in today_trades), 2),
                "trades_today": len(today_trades),
                "win_rate": round(len(win_sells)/max(len(sells),1)*100, 1),
                "position_count": len(pos),
                "sector_exposure": {k: round(v/eq*100,1) for k,v in sorted(sectors.items(), key=lambda x:-x[1])[:5]},
                "max_single": max([p.get("last_price",0)*p.get("qty",0) for p in pos.values()], default=0),
                "warnings": [],
            }
            # 告警
            if report["daily_pnl"] < -(eq * 0.03): report["warnings"].append("日亏损>3%")
            for ind, pct in report["sector_exposure"].items():
                if pct > 25: report["warnings"].append(f"{ind}行业超25%")
            if report["max_single"] / max(eq,1) > 0.20: report["warnings"].append("单票集中度>20%")

            # 写文件供前端展示
            try:
                import json as _j, os as _os
                _rp = r"D:\quant_web\data\daily_risk_report.json"
                _os.makedirs(_os.path.dirname(_rp), exist_ok=True)
                _j.dump(report, open(_rp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            except: pass
            return report
        except: return None


class StressTester:
    """压力测试 — 历史极端行情"""
    SCENARIOS = [
        ("2015股灾", -0.30), ("2020疫情", -0.08), ("2024小微盘", -0.20), ("千股跌停", -0.35),
    ]

    def run(self, positions, total_equity, quotes=None):
        results = {}
        for name, drop in self.SCENARIOS:
            loss = 0
            for sym, pos in positions.items():
                mkt = pos.get('last_price', pos.get('avg_cost', 0)) * pos.get('qty', 0)
                loss += mkt * drop
            results[name] = {"drop_pct": f"{drop*100:.0f}%", "estimated_loss": round(loss, 0),
                           "loss_pct": round(loss / max(total_equity, 1) * 100, 1)}
        return results
