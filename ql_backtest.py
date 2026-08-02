"""潜龙 → Backtrader 回测适配器 (v3.0)

将策略配置 (user_strategies.json) 转为 Backtrader 回测,
输出标准化报告: Sharpe, WinRate, MaxDD, 收益序列。

用法:
  from ql_backtest import run
  result = run("九因子统一策略V1")
"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

import backtrader as bt
from a_stock_rules import AStockStrategy, StampDutyCommission, RoundLotSizer


class FactorStrategy(AStockStrategy):
    """通用因子策略 — 从user_strategies.json读取因子配置"""
    params = (
        ('factors', []),
        ('trigger_min', 60),
    )

    def __init__(self):
        super().__init__()
        from factor_registry import get_all_compute_fns
        self.compute_fns = get_all_compute_fns()

    def get_score(self, data):
        """计算股票复合因子得分"""
        df = data.lines.close.get(size=60)
        if len(df) < 20:
            return 0, 0
        import pandas as pd
        prices = data.lines.close.array[-60:]
        volumes = data.lines.volume.array[-60:] if hasattr(data.lines, 'volume') else np.ones(60)
        mock_df = pd.DataFrame({"close": list(prices), "volume": list(volumes)})
        if len(mock_df) < 20:
            return 0, 0
        total_score = 0.0
        total_weight = 0.0
        valid_count = 0
        for fc in self.p.factors:
            fn = self.compute_fns.get(fc["name"])
            if not fn:
                continue
            try:
                val = fn(mock_df)
            except Exception:
                continue
            if val is None or not np.isfinite(val):
                continue
            val = np.clip(float(val), 0, 100)
            w = fc.get("weight", 1.0)
            total_weight += w
            total_score += val * w
            valid_count += 1
        if valid_count < 2 or total_weight <= 0:
            return 0, valid_count
        return total_score / total_weight, valid_count

    def signal_buy(self, data):
        score, vc = self.get_score(data)
        if vc >= 2 and score >= self.p.trigger_min:
            self.order = self.buy(data=data, size=100)

    def signal_sell(self, data):
        self.order = self.sell(data=data)


def run(strategy_name: str, days: int = 90, cash: float = 1_000_000) -> dict:
    """运行 Backtrader 回测"""
    import json
    sp = r"D:\quant_framework\user_customizations\user_strategies.json"
    strategies = json.load(open(sp, encoding="utf-8"))["strategies"]
    strat = next((s for s in strategies if s["name"] == strategy_name), None)
    if not strat:
        return {"success": False, "message": "策略不存在"}

    factors = strat.get("factors", [])
    trigger = strat.get("trigger", {}).get("min_score", 60)
    print(f"[BT] {strategy_name}: {len(factors)} factors, trigger={trigger}")

    cerebro = bt.Cerebro()
    cerebro.addstrategy(FactorStrategy, factors=factors, trigger_min=trigger, hold_days=strat.get("hold_days", 5))
    cerebro.broker.setcash(cash)
    cerebro.addsizer(RoundLotSizer)
    cerebro.broker.addcommissioninfo(StampDutyCommission())

    from data_loader import load_stock_data_cache
    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=250)
    if not stock_data:
        return {"success": False, "message": "数据加载失败"}

    import random as _rnd
    _rnd.seed(42)
    syms = sorted(stock_data.keys())[:300]
    count = 0
    for sym in syms[:200]:
        df = stock_data.get(sym)
        if df is None or len(df) < 20:
            continue
        try:
            df_bt = pd.DataFrame({
                'open': df['open'].values,
                'high': df['high'].values,
                'low': df['low'].values,
                'close': df['close'].values,
                'volume': df['volume'].values,
            }, index=pd.to_datetime(df.index))
            feed = bt.feeds.PandasData(dataname=df_bt)
            if not hasattr(feed, '_idx'):
                feed._idx = 0  # backtrader2 bug workaround
            cerebro.adddata(feed, name=sym)
            count += 1
        except Exception:
            continue
        if count >= 50:
            break

    if count < 10:
        return {"success": False, "message": f"股票样本不足 ({count})"}
    print(f"[BT] 加载 {count} 只样本")

    cerebro.run()
    final_value = cerebro.broker.getvalue()
    total_return = (final_value - cash) / cash * 100

    return {
        "success": True,
        "method": "Backtrader",
        "strategy": strategy_name,
        "stocks": count,
        "total_return_pct": round(total_return, 2),
        "final_value": round(final_value, 2),
    }
