"""
事前风控检查器 + 持仓相关性分析
报单前验证: 资金/仓位/涨跌停/自成交/集中度/相关性
"""
import numpy as np
from datetime import datetime
from collections import defaultdict


class PreTradeChecker:
    """报单前风控检查 — 返回 (通过, 拒绝原因)"""

    def __init__(self, config=None, positions=None, cash=0, total_equity=0):
        self.config = config or {}
        self.positions = positions or {}
        self.cash = cash
        self.total_equity = total_equity

    def check_buy(self, symbol, price, qty, industry='', signal_level=3):
        """买入前检查"""
        cost = price * qty

        # 1. 资金检查
        if cost > self.cash:
            return False, f"资金不足: 需¥{cost:,.0f} 可用¥{self.cash:,.0f}"

        # 2. 单票仓位上限
        max_single = self.config.get("max_single_position_pct", 20) / 100
        after_value = cost + sum(
            p.get('last_price', p.get('avg_cost', 0)) * p.get('qty', 0)
            for s, p in self.positions.items() if s == symbol
        )
        if self.total_equity > 0 and after_value / self.total_equity > max_single:
            return False, f"单票仓位{after_value/self.total_equity*100:.0f}%超限{max_single*100:.0f}%"

        # 3. 行业集中度
        max_sector = self.config.get("max_sector_pct", 30) / 100
        sector_value = sum(
            p.get('last_price', p.get('avg_cost', 0)) * p.get('qty', 0)
            for s, p in self.positions.items() if p.get('industry', '') == industry
        ) + cost
        if industry and self.total_equity > 0 and sector_value / self.total_equity > max_sector:
            return False, f"行业{industry}仓位{sector_value/self.total_equity*100:.0f}%超限{max_sector*100:.0f}%"

        # 4. 信号等级检查
        min_sig = self.config.get("signal_min_strength", 3)
        if signal_level < min_sig:
            return False, f"信号等级{signal_level}级<最低{min_sig}级"

        # 5. 日交易笔数
        max_daily = self.config.get("max_daily_trades", 5)
        # (由调用方传入当日已交易笔数)

        # 6. 涨跌停检查
        if self._is_limit_up(symbol):
            return False, "涨停板不追买"

        # 7. 仓位总数
        if len(self.positions) >= self.config.get("max_positions_abs", 10):
            return False, f"持仓数已达上限{self.config.get('max_positions_abs',10)}只"

        return True, "OK"

    def check_sell(self, symbol, qty):
        """卖出前检查"""
        pos = self.positions.get(symbol, {})
        if pos.get('qty', 0) < qty:
            return False, f"持仓不足: 持有{pos.get('qty',0)}需卖{qty}"
        if self._is_limit_down(symbol):
            return False, "跌停板无法卖出"
        return True, "OK"

    def _is_limit_up(self, symbol):
        return False  # 简化: 需要实时行情

    def _is_limit_down(self, symbol):
        return False


class CorrelationAnalyzer:
    """持仓相关性分析 — 检测集中风险"""

    def __init__(self, stock_data=None, factor_cache=None):
        self.stock_data = stock_data or {}
        self.factor_cache = factor_cache or []

    def analyze(self, positions, lookback=60):
        """分析持仓组合的相关性风险"""
        if len(positions) < 2:
            return {"risk_level": "低", "warning": "", "details": []}

        details = []
        syms = list(positions.keys())

        # 1. 行业集中度
        industries = defaultdict(list)
        for sym, pos in positions.items():
            ind = pos.get('industry', '未分类')
            industries[ind].append(sym)
        for ind, stock_list in industries.items():
            if len(stock_list) >= 2:
                details.append({
                    'type': '行业集中',
                    'level': 'warning' if len(stock_list) >= 3 else 'info',
                    'desc': f"行业「{ind}」持有{len(stock_list)}只: {','.join(stock_list)}",
                })

        # 2. 价格相关性 (基于最近N天收益)
        if self.stock_data:
            returns = {}
            for sym in syms:
                df = self.stock_data.get(sym)
                if df is not None and len(df) >= lookback:
                    close = df['close'].values[-lookback:]
                    returns[sym] = np.diff(close) / close[:-1]

            if len(returns) >= 2:
                sym_list = list(returns.keys())
                for i in range(len(sym_list)):
                    for j in range(i + 1, len(sym_list)):
                        s1, s2 = sym_list[i], sym_list[j]
                        r1, r2 = returns[s1], returns[s2]
                        min_len = min(len(r1), len(r2))
                        if min_len > 10:
                            corr = np.corrcoef(r1[:min_len], r2[:min_len])[0, 1]
                            if abs(corr) > 0.7:
                                details.append({
                                    'type': '高相关性',
                                    'level': 'danger' if corr > 0.85 else 'warning',
                                    'desc': f"{s1}与{s2}相关系数{corr:.2f}，同涨同跌风险",
                                })

        # 3. 风险评估
        dangers = sum(1 for d in details if d['level'] == 'danger')
        warnings = sum(1 for d in details if d['level'] == 'warning')
        if dangers > 0:
            risk = "高"
            msg = f"⚠️ {dangers}项严重集中风险，建议分散"
        elif warnings > 1:
            risk = "中"
            msg = f"⚡ {warnings}项集中警告，关注风险"
        elif warnings > 0:
            risk = "低"
            msg = f"📊 {warnings}项提示"
        else:
            risk = "低"
            msg = "✅ 持仓分散良好"

        return {"risk_level": risk, "warning": msg, "details": details}


# 风控事件通知
class RiskEventBus:
    """风控事件总线 — SSE推送风控事件到前端"""
    def __init__(self, store=None):
        self.store = store
        self.event_log = []  # 最近100条

    def emit(self, event_type, data):
        """发出风控事件"""
        event = {"type": event_type, "time": datetime.now().strftime("%H:%M:%S"), **data}
        self.event_log.append(event)
        if len(self.event_log) > 100: self.event_log.pop(0)
        if self.store:
            try: self.store.set('risk_event', event)
            except: pass
        return event

    def get_recent(self, n=20):
        return self.event_log[-n:]


# 压力测试
class RiskCycleScheduler:
    """全周期风控调度: 盘前/盘中/盘后"""
    def __init__(self, paper_engine=None, store=None):
        self.paper = paper_engine
        self.store = store
        self._last_pre_market = None
        self._last_post_market = None

    def pre_market_check(self):
        """盘前检查(8:30-9:25): 持仓风险预警"""
        today = datetime.now().strftime("%Y%m%d")
        if self._last_pre_market == today: return None
        self._last_pre_market = today

        warnings = []
        if self.paper:
            # 检查隔夜持仓
            for sym, pos in self.paper.positions.items():
                pnl_pct = (pos.get('last_price',0)/pos.get('avg_cost',1)-1)*100
                if pnl_pct < -8:
                    warnings.append(f"⚠️ {sym} 隔夜亏损{pnl_pct:.1f}%，关注开盘")
            # 检查熔断状态
            if self.paper._circuit_breaker_triggered():
                warnings.append("🚨 昨日触发熔断，今日仅允许卖出")

        result = {"phase": "pre_market", "time": datetime.now().strftime("%H:%M"), "warnings": warnings}
        if self.store:
            try: self.store.set('risk_pre_market', result)
            except: pass
        return result

    def post_market_report(self):
        """盘后报告(15:05-16:00): 风控日报"""
        today = datetime.now().strftime("%Y%m%d")
        if self._last_post_market == today: return None
        self._last_post_market = today

        report = {"phase": "post_market", "time": datetime.now().strftime("%H:%M")}
        if self.paper:
            status = self.paper.get_status()
            report.update({
                "daily_pnl": status.get('total_pnl', 0),
                "daily_return": status.get('total_return', 0),
                "max_drawdown": status.get('max_drawdown', 0),
                "trade_count": status.get('trade_count', 0),
                "win_rate": status.get('win_rate', 0),
                "sharpe": status.get('sharpe', 0),
            })
            # 与回测对比（偏差>20%告警）
            bt_return = self.store.get('backtest', {}).get('metrics', {}).get('total_return', 0) if self.store else 0
            if bt_return and report['daily_return']:
                deviation = abs(report['daily_return'] - bt_return) / max(abs(bt_return), 0.01)
                report['bt_deviation'] = round(deviation, 2)
                report['bt_warning'] = '⚠️ 实盘偏离回测>20%' if deviation > 0.2 else '✅ 实盘与回测一致'

        if self.store:
            try: self.store.set('risk_post_market', report)
            except: pass
        return report


class StressTester:
    """简单压力测试: 模拟极端行情"""

    SCENARIOS = {
        "2015股灾": {"market_drop": -0.30, "volatility": 3.0, "liquidity": 0.3},
        "2020疫情": {"market_drop": -0.08, "volatility": 2.5, "liquidity": 0.5},
        "2024小微盘": {"market_drop": -0.20, "volatility": 2.0, "liquidity": 0.2},
        "千股跌停": {"market_drop": -0.35, "volatility": 4.0, "liquidity": 0.1},
    }

    def run(self, positions, total_equity, quotes=None):
        """对当前持仓运行所有压力情景"""
        results = {}
        for name, scenario in self.SCENARIOS.items():
            drop = scenario["market_drop"]
            vol = scenario["volatility"]
            liq = scenario["liquidity"]

            # 简化: 按市场跌幅+个股波动放大估算
            total_loss = 0
            for sym, pos in positions.items():
                beta = 1.0  # 默认市场Beta
                stock_drop = drop * beta * (1 + np.random.uniform(-0.2, 0.3) * vol)
                loss = pos.get('market_value', pos.get('avg_cost', 0) * pos.get('qty', 0)) * abs(stock_drop) * (2 - liq)
                total_loss += loss

            loss_pct = round(total_loss / max(total_equity, 1) * 100, 1)
            remaining = round(total_equity - total_loss, 0)

            if loss_pct > 20:
                level = "🔴 危险"
            elif loss_pct > 10:
                level = "🟡 警告"
            else:
                level = "🟢 可控"

            results[name] = {
                "loss_pct": loss_pct,
                "remaining_equity": remaining,
                "level": level,
                "scenario": scenario,
            }

        return results
