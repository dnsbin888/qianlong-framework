"""潜龙策略信号库 v2.0 (2026-07-13)
架构: 每个策略独立目录, 含 daily.py(日线) + realtime.py(QMT实时) + weights.py(共享参数)
用法:
  from signals.reversal.daily import score_oversold       # Flask日线
  from signals.reversal.realtime import confirm_oversold   # QMT实时
  from signals.daban.daily import score_board_quality       # Flask日线
  from signals.daban.realtime import confirm_board          # QMT实时
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 策略注册: {strategy_id: {daily_fn, realtime_fn}}
REGISTRY = {}

def register(strategy_id, daily_fn=None, realtime_fn=None):
    REGISTRY[strategy_id] = {"daily": daily_fn, "realtime": realtime_fn}
    return strategy_id

# 自动注册已有策略
register("ml_daily", daily_fn="signals.ml.daily.generate")
register("oversold_bounce", daily_fn="signals.reversal.daily.score_oversold",
         realtime_fn="signals.reversal.realtime.confirm_oversold")
register("weak_to_strong", daily_fn="signals.reversal.daily.score_weak_to_strong",
         realtime_fn="signals.reversal.realtime.confirm_weak_to_strong")
register("daban", daily_fn="signals.daban.daily.score_board_quality",
         realtime_fn="signals.daban.realtime.confirm_board")
