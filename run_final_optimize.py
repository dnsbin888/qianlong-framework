"""最终优化 — 参数网格 + DMI趋势过滤 + 量能确认"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")
import time as _time

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _sma(s, n, m):
    r = s.copy(); a = m/n
    for i in range(1,len(r)):
        if not np.isnan(r.iloc[i]): r.iloc[i] = a*s.iloc[i] + (1-a)*r.iloc[i-1]
    return r
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_f1(df):
    """擒龙决 AND 涨停先锋"""
    c, v = df["close"], df["volume"]
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    ema20 = _ema(c, 20)
    dev = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper = _ref(ema20 + 2*dev, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    qlj = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = qlj & (_count(qlj, 7) == 1)
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    ztxf = (c > profit100) & (_count(c > profit100, 7) == 1)
    return (qlj & ztxf).astype(int).values

def signal_f2(df):
    """突破起飞 (牛线+MACD+涨停)"""
    c, h, l = df["close"], df["high"], df["low"]
    t1 = (2.15*c + l + h) / 4; t2 = (3.48*c + h + l) / 4
    w = np.abs(t2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan); w = w.clip(0, 1)
    dma = t1.copy()
    for i in range(1, len(dma)): dma.iloc[i] = w.iloc[i]*t1.iloc[i] + (1-w.iloc[i])*dma.iloc[i-1]
    bull = _ema(dma, 200) * 1.118
    dif = _ema(c, 12) - _ema(c, 26); dea = _ema(dif, 9)
    cross_bull = (_ref(c, 1) <= _ref(bull, 1)) & (c > bull)
    return (cross_bull & (dif > dea) & (c / _ref(c, 1) > 1.09)).astype(int).values

def get_dmi_bullish(df):
    """DMI趋势: PDI > MDI 且 ADX >= 阈值 → 牛市做多"""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-_ref(c,1)).abs(), (l-_ref(c,1)).abs()], axis=1).max(axis=1)
    tr1 = _sma(tr, 14, 1)
    hd = h - _ref(h, 1); ld = _ref(l, 1) - l
    dmp = _sma(pd.Series(np.where((hd>0)&(hd>ld), hd, 0), index=df.index), 14, 1)
    dmm = _sma(pd.Series(np.where((ld>0)&(ld>hd), ld, 0), index=df.index), 14, 1)
    pdi = dmp*100/tr1.replace(0, np.nan); mdi = dmm*100/tr1.replace(0, np.nan)
    adx = _sma((mdi-pdi).abs()/(mdi+pdi).replace(0,np.nan)*100, 14, 1)
    return (pdi > mdi).values, adx.values

print("=" * 65)
print("  最终参数优化 — 网格搜索")
print("=" * 65)

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  {len(data)} stocks loaded\n")

# ═══════════════════ 参数网格 ═══════════════════
GRID = [
    # (name, signal_func, dmi_min_adx, hard_stop, time_stop, trail_peak)
    ("基准(F1)",          signal_f1, 0,   -0.03, 5, 0.07),
    ("+DMI>20过滤",      signal_f1, 20,  -0.03, 5, 0.07),
    ("+DMI>25过滤",      signal_f1, 25,  -0.03, 5, 0.07),
    ("+DMI>20+宽止损",   signal_f1, 20,  -0.05, 8, 0.07),
    ("+DMI>25+宽止损",   signal_f1, 25,  -0.05, 8, 0.08),
    ("+DMI>25+极宽",     signal_f1, 25,  -0.05, 10, 0.08),
    ("F1+F2共振+DMI>20", signal_f1, 20,  -0.03, 5, 0.07),  # will combine below
    ("仅DMI>25无F1",     signal_f1, 25,  -0.03, 5, 0.07),  # same as +DMI>25
]

# For F1+F2 combo, we need special handling. Replace last 2 with useful variants
GRID = [
    ("基准(F1)",               signal_f1, 0,  -0.03, 5, 0.07),
    ("+DMI>20",               signal_f1, 20, -0.03, 5, 0.07),
    ("+DMI>25",               signal_f1, 25, -0.03, 5, 0.07),
    ("+DMI>20+宽止损",        signal_f1, 20, -0.05, 8, 0.07),
    ("+DMI>25+宽止损",        signal_f1, 25, -0.05, 8, 0.08),
    ("+DMI>30+极宽+高门槛",   signal_f1, 30, -0.05, 10, 0.08),
    ("F1+F2双公式+DMI>20",    signal_f1, 20, -0.03, 5, 0.07),  # temp, will AND with F2 below
    ("F1+F2双公式+DMI>25",    signal_f1, 25, -0.03, 5, 0.07),
]

results = []

for name, sig_func, dmi_min, hard_s, time_s, trail_p in GRID:
    trades = []; t0 = _time.time()
    is_f1f2 = "F1+F2" in name

    for sym, sd in data.items():
        df = pd.DataFrame({
            "open": sd["open"][-500:], "high": sd["high"][-500:],
            "low": sd["low"][-500:], "close": sd["close"][-500:],
            "volume": sd["volume"][-500:],
        })
        if len(df) < 300: continue

        try:
            sig_arr = sig_func(df)
            if is_f1f2:
                sig_f2 = signal_f2(df)
                sig_arr = ((sig_arr > 0) & (sig_f2 > 0)).astype(int)
            dmi_bull, adx_arr = get_dmi_bullish(df)
        except Exception:
            continue

        pos = None
        for i in range(250, len(df)):
            p = df["close"].iloc[i]; o = df["open"].iloc[i]
            h = df["high"].iloc[i]; prev_c = df["close"].iloc[i-1] if i>=1 else p
            limit_up_p = round(prev_c*1.10, 2) if prev_c > 0 else 999999
            if p <= 3.0: continue

            # DMI filter
            dmi_ok = (dmi_min == 0) or (adx_arr[i] >= dmi_min and dmi_bull[i])

            if pos is None:
                if sig_arr[i] and dmi_ok and p < limit_up_p - 0.01 and o < h:
                    pos = dict(entry_p=p, entry_i=i, peak=p, shares=100, remain=100,
                              half_sold=False, limit_held=False)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["entry_i"]
                pnl = (p - pos["entry_p"]) / pos["entry_p"]
                peak_pnl = (pos["peak"] - pos["entry_p"]) / pos["entry_p"]

                # 封板持有
                if p >= limit_up_p - 0.01:
                    pos["limit_held"] = True; continue

                # 低开-3%减半
                if not pos["half_sold"] and days <= 2 and pnl <= -0.03:
                    net = (pnl - 0.0003 - 0.001) * 0.5
                    trades.append(dict(pnl=net, days=days))
                    pos["half_sold"] = True; pos["remain"] = 50; pos["peak"] = p
                    continue

                pnl_now = (p - pos["entry_p"]) / pos["entry_p"]
                peak_now = (pos["peak"] - pos["entry_p"]) / pos["entry_p"]
                exit_reason = False

                if pos.get("limit_held") and peak_now >= 0.03 and (p - pos["peak"])/pos["entry_p"] <= -0.015:
                    exit_reason = True
                elif peak_now >= trail_p and (p - pos["peak"])/pos["entry_p"] <= -0.015:
                    exit_reason = True
                elif pnl_now >= 0.05 and (pos["peak"] - p)/pos["entry_p"] >= 0.015:
                    exit_reason = True
                elif pnl_now <= hard_s:
                    exit_reason = True
                elif days >= time_s and pnl_now < 0.01:
                    exit_reason = True

                if exit_reason:
                    sell_pct = pos["remain"] / 100.0
                    net = (pnl_now - 0.0003 - 0.001) * sell_pct
                    trades.append(dict(pnl=net, days=days))
                    pos = None

    if not trades: results.append((name, 0, 0, 0, 0, 0, 0)); continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    avg_d=np.mean([t["days"] for t in trades])
    results.append((name, len(trades), wr, aw, al, pf, avg_d, _time.time()-t0))

# ═══════════════════ 输出 ═══════════════════
print(f"  {'Config':<28} {'Trades':>6} {'WR':>6} {'AvgW':>6} {'AvgL':>6} {'PF':>6} {'Days':>5} {'Time':>5}")
print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5} {'-'*5}")
for r in sorted(results, key=lambda x: -x[5]):
    name, nt, wr, aw, al, pf, avg_d, et = r
    print(f"  {name:<28} {nt:>6} {wr:>5.1%} {aw:>5.2%} {al:>5.2%} {pf:>6.2f} {avg_d:>5.1f} {et:>4.0f}s")

best = sorted(results, key=lambda x: -x[5])[0]
print(f"\n  最优: {best[0]} PF={best[5]:.2f} WR={best[2]:.1%} Trades={best[1]}")
print("  Done!")
