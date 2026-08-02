"""测试 QMT 实时策略完整流程 — 模拟竞价/突破/尾盘触发"""
import sys
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

from qmt_strategy import load_pool, check_triggers, get_ml_score, preload_ma20_and_vol
from data_loader import load_stock_data_cache

print("=" * 60)
print("  QMT 实时策略流程测试")
print("=" * 60)

# 1. 加载候选池
print("\n[1/5] 加载候选池...")
load_pool()

# 2. 加载日线数据 (预填充MA20和均量)
print("\n[2/5] 加载日线...")
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
preload_ma20_and_vol(sd)
print(f"  MA20缓存: {len(sys.modules['qmt_strategy']._ma20_cache)} 只")

# 3. 取一个候选股模拟测试
print("\n[3/5] 模拟行情触发...")
import random
symbols = list(sd.keys())[:100]
test_sym = symbols[random.randint(0, 99)]
df = sd.get(test_sym)
if df is not None and len(df) >= 2:
    c = df['close'].values
    v = df['volume'].values
    prev = {'close': float(c[-2]), 'volume': float(v[-2]), 'open': float(c[-2])}
    curr = {'close': float(c[-1]), 'volume': float(v[-1]), 'open': float(c[-1]) * 1.03}  # 模拟高开3%

    triggers = check_triggers(test_sym, curr, prev)
    print(f"  测试股票: {test_sym}")
    print(f"  前收盘: ¥{prev['close']:.2f}  当前价: ¥{curr['close']:.2f}")
    print(f"  开: ¥{curr['open']:.2f} (高开{((curr['open']/max(prev['close'],0.01)-1)*100):.1f}%)")
    print(f"  触发条件: {triggers if triggers else '无触发'}")

# 4. 查ML评分
print("\n[4/5] 查ML评分...")
ml = get_ml_score(test_sym)
print(f"  LGBM={ml.get('lgbm',0):.0f}  XGB={ml.get('xgb',0):.0f}")

# 5. 如果要下单
print("\n[5/5] 决策适配...")
if triggers and (ml.get('lgbm',0)>=60 or ml.get('xgb',0)>=60):
    from decision_adapter import process_signals
    score = (ml.get('lgbm',0)+ml.get('xgb',0))/2
    orders = process_signals(
        [{"symbol": test_sym, "buy_signal": 4, "score": score, "close": curr['close']}],
        sd, {"total_equity": 1_000_000, "positions": []}
    )
    if orders:
        o = orders[0]
        print(f"  🎯 通过! {o['symbol']} {o['position_pct']}% {o['shares']}股 @¥{o['close']}")
        print(f"  止损: ¥{o['stop_loss']}  止盈: ¥{o['take_profit'][0]}")
    else:
        print(f"  ❌ 决策适配拒绝")
else:
    print(f"  ❌ 条件不满足 (需触发+ML≥60)")

print(f"\n{'='*60}")
print(f"  ✅ 流程测试完成 — 等待明天09:25实盘")
print(f"{'='*60}\n")
