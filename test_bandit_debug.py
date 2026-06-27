"""Debug: 波段擒妖因子各步骤触发量"""
import sys; sys.path.insert(0, 'D:\\quant_framework\\src')
import numpy as np
import pandas as pd
from quant_framework.factors.tdx_signals import (
    factor_bandit_sniper, factor_resonance, factor_yaogu_resonance_bandit
)
from quant_framework.factors.tdx_signals import _ema, _dma, _hhv, _ref, _barslast

# 读一只典型股票数据
from quant_framework.data.providers.ths_day import THSDayDataProvider
provider = THSDayDataProvider()
provider.connect()

# 先取几只看看
syms = provider.scan_symbols()
valid = [s for s in syms if len(provider._read_day_file(s)) >= 500]
print(f"Valid stocks: {len(valid)}")

# 找一只波动大的
for sym in ['000519', '002068', '300560', '301356', '301548']:
    if sym in valid:
        break
else:
    sym = valid[0]

print(f"Testing: {sym}")
data = provider._read_day_file(sym)
dates = sorted(data.keys())
records = []
for d in dates:
    o, h, l, c, amt, vol = data[d]
    if o > 0 and c > 0:
        records.append({"close": c, "high": h, "low": l, "open": o, "volume": vol})
df = pd.DataFrame(records)
print(f"Rows: {len(df)}, 2022-2026")

c = df["close"]
h = df["high"]
l = df["low"]

# Step 1: 牛线
typ = (2.15 * c + l + h) / 4.0
ema23 = _ema(c, 23)
cmp_price = (3.48 * c + h + l) / 4.0
weight = (cmp_price - ema23).abs() / ema23.replace(0, np.nan)
niu = _ema(_dma(typ, weight), 200) * 1.118

print(f"\n牛线: valid={niu.notna().sum()}")

# Step 2: XG conditions
dif = _ema(c, 12) - _ema(c, 26)
dea = _ema(dif, 9)

cross_niu = (_ref(c, 1) <= _ref(niu, 1)) & (c > niu)
xg_macd = dif > dea
xg_ret = c / _ref(c, 1) > 1.09

print(f"cross_niu:  {cross_niu.sum()}")
print(f"MACD多头:   {xg_macd.sum()}")
print(f"涨幅>9%:    {xg_ret.sum()}")

xg = cross_niu & xg_macd & xg_ret
print(f"XG sub-total: {xg.sum()}")

# Step 3: ATR / AA / BB / T
tr = pd.concat([h - l, (h - _ref(c, 1)).abs(), (l - _ref(c, 1)).abs()], axis=1).max(axis=1)
atr = _ema(tr, 14)
aa = _hhv(h, 20) - 2.0 * atr

ref_hhv55 = _ref(_hhv(h, 55), 1)
bb = (_ref(c, 1) <= _ref(ref_hhv55, 1)) & (c > ref_hhv55)

ma13 = c.rolling(13).mean()
min_ma13_aa = pd.concat([ma13, aa], axis=1).min(axis=1)
t = (_ref(min_ma13_aa, 1) <= _ref(c, 1)) & (min_ma13_aa > c)

print(f"\nBB (55日新高):  {bb.sum()}")
print(f"T  (支撑交叉):  {t.sum()}")

# Step 4: B1
bbb = _barslast(bb)
r_barslast = _barslast(t)
bbb0 = bbb.fillna(999).astype(int) == 0
ref_cond = _ref(r_barslast, 1).fillna(999) < _ref(bbb, 1).fillna(999)
b1 = bbb0 & ref_cond
print(f"B1:             {b1.sum()}")

# Step 5: Final
final = xg & b1
print(f"\nFINAL (XG&B1):  {final.sum()}")

# Also check resonance + bandit combo
resonance = factor_resonance(df)
bandit = factor_bandit_sniper(df)
combo = factor_yaogu_resonance_bandit(df)
print(f"\n共振(擒龙+涨停): {resonance.fillna(0).astype(bool).sum()}")
print(f"波段擒妖:        {bandit.fillna(0).astype(bool).sum()}")
print(f"组合:            {combo.sum()}")

# If final is 0, find which XG+B1 sub-condition fails most
if final.sum() == 0:
    print(f"\n--- 根因分析 ---")
    # Check if any day satisfies both XG and B1 separately
    xg_b1_overlap = xg.astype(int) + b1.astype(int)
    print(f"XG only: {(xg_b1_overlap == 1).sum()}")
    print(f"B1 only: {(xg_b1_overlap == 1).sum()}")
    print(f"Both:    {(xg_b1_overlap == 2).sum()}")
    
    # Show latest 5 rows
    print(f"\nLast 5 rows of 2026:")
    tail = df.tail(40)
    print(tail[['close']].tail(10))
    
    # Show days where XG is true
    xg_true = xg[xg].index
    print(f"\nXG true dates (last 5): {list(xg_true[-5:])}")
    
    # Show days where B1 is true
    b1_true = b1[b1].index
    print(f"B1 true dates (last 5): {list(b1_true[-5:])}")

    # Check CROSS conditions more carefully
    # The issue might be that CROSS uses REF and the initial values are NaN
    print(f"\n--- Cross check ---")
    valid_cross = cross_niu & (dif > dea) & (c / _ref(c, 1) > 1.09)
    print(f"Valid XG (any): {valid_cross.sum()}")
