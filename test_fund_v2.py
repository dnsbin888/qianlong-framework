"""fund_v2 IC 重验证 — 诊断 IC 值 vs registry 记录"""
import numpy as np
from scipy.stats import spearmanr
from full_market_ic import _factor_fund, load_data

print("加载数据...")
data = load_data()
print(f"数据: {len(data)} 只股票")

sample = list(data.keys())[:500]
ics = []

for sym in sample:
    df = data.get(sym)
    if df is None or len(df) < 30:
        continue
    try:
        fv = _factor_fund(df)
        if fv is None:
            continue
        ret = df['close'].iloc[-1] / df['close'].iloc[-6] - 1 if len(df) >= 6 else 0
        ics.append((fv, ret))
    except:
        pass

if len(ics) >= 30:
    fv_arr = np.array([x[0] for x in ics])
    ret_arr = np.array([x[1] for x in ics])
    ic, p = spearmanr(fv_arr, ret_arr)
    print(f"\nfund_v2 IC(5d) = {ic:.4f}  p = {p:.4f}  N = {len(ics)}")
    print(f"registry 记录: -0.007")
    print(f"实测值:       {ic:.4f}")
    print(f"符号一致?     {'✅' if (ic * (-0.007) > 0) else '❌ 符号相反!'}")
    print(f"|IC| > 0.02?  {'✅ 有效' if abs(ic) > 0.02 else '❌ 不显著'}")
else:
    print(f"样本不足: {len(ics)}")
