"""全因子 IC 重验证 — 对比 registry 记录 vs 实测值
对标: WorldQuant WebSim 因子验证
"""
import json, numpy as np
from scipy.stats import spearmanr
from full_market_ic import load_data

# 加载
print("加载 stock_data...")
data = load_data()
print(f"  {len(data)} 只股票\n")

# 因子列表
with open('d:/quant_framework/factor_registry.json') as f:
    reg = json.load(f)

# 只验证有 compute 函数且非 retired 的因子（但包含退役的作参考）
factors = [f for f in reg['factors'] if f.get('compute') and f['status'] in ('active','pending')]

print(f"{'因子':<20s} {'registry IC':>10s} {'实测 IC':>10s} {'匹配':>6s} {'p值':>8s} {'N':>6s}")
print("-" * 65)

for factor in factors:
    name = factor['name']
    compute_path = factor.get('compute','')
    if not compute_path: continue

    # 动态加载因子函数
    try:
        parts = compute_path.rsplit('.',1)
        mod = __import__(parts[0], fromlist=[parts[1]])
        fn = getattr(mod, parts[1])
    except Exception as e:
        print(f"{name:<20s} {'(import fail)':>10s} {'':>10s}")
        continue

    # 计算 IC
    ics = []
    sample = list(data.keys())[:500]
    for sym in sample:
        df = data.get(sym)
        if df is None or len(df) < 30: continue
        try:
            fv = fn(df)
            if fv is None: continue
            if hasattr(fv, 'iloc'): fv = float(fv.iloc[-1])
            ret = df['close'].iloc[-1] / df['close'].iloc[-6] - 1 if len(df) >= 6 else 0
            ics.append((fv, ret))
        except: pass

    if len(ics) < 30:
        print(f"{name:<20s} {'':>10s} {'N<30':>10s}")
        continue

    fv_arr = np.array([x[0] for x in ics])
    ret_arr = np.array([x[1] for x in ics])
    ic, p = spearmanr(fv_arr, ret_arr)

    reg_ic = factor.get('ic_5d', 0) or 0
    match = '✅' if (ic * reg_ic > 0 or abs(reg_ic) < 0.001) else '❌符号反'
    if abs(ic) < 0.01 and abs(reg_ic) < 0.01: match = '✅均≈0'

    print(f"{name:<20s} {reg_ic:>10.4f} {ic:>10.4f} {match:>6s} {p:>8.4f} {len(ics):>6d}")

print(f"\n✅=一致  ❌符号反=registry IC方向错了")
print(f"建议: ❌的因子需要更新 registry IC 值")
