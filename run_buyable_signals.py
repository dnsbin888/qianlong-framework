"""过滤可买信号: 出信号时价格<涨停价, 且非一字板"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd
import random, time as _time
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 5000

def load_stock(market, fname):
    code = fname.replace(market, "").replace(".day", "")
    if len(code) != 6 or not code.isdigit(): return None
    path = os.path.join(ROOT, market, "lday", fname)
    if not os.path.exists(path): return None
    with open(path, "rb") as f: raw = f.read()
    dates, o, h, l, c, v = [], [], [], [], [], []
    for i in range(len(raw)//32):
        vals = struct.unpack_from("<I I I I I f I I", raw, i*32)
        d, o_, h_, l_, c_, amt, vol = vals[0], vals[1]/100., vals[2]/100., vals[3]/100., vals[4]/100., vals[5], vals[6]
        if 20100101 <= d <= 20270101 and o_ > 0:
            dates.append(d); o.append(o_); h.append(h_); l.append(l_); c.append(c_); v.append(vol)
    return {"code": code, "dates": dates, "o": o, "h": h, "l": l, "c": c, "v": v}

print("Loading...")
stocks = {}
for market in ["sh", "sz"]:
    lday_dir = os.path.join(ROOT, market, "lday")
    if not os.path.isdir(lday_dir): continue
    for fname in os.listdir(lday_dir):
        if not fname.endswith(".day"): continue
        s = load_stock(market, fname)
        if s and len(s["dates"]) >= 300: stocks[s["code"]] = s

codes = list(stocks.keys())
if len(codes) > N: random.seed(42); codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}
print(f"{len(stocks)} stocks\n")

def compute_f1(df):
    c, v = df["close"].values, df["volume"].values
    hhv30 = pd.Series(c).rolling(30).max().shift(1)
    pressure = hhv30.rolling(2).mean().values
    ema20 = pd.Series(c).ewm(span=20, adjust=False).mean()
    dev = ((pd.Series(c) - ema20)**2).rolling(20).mean()**0.5
    upper = (ema20 + 2*dev).shift(1).values
    ma_vol5 = pd.Series(v).rolling(5).mean().shift(1)
    vol_ratio = v / ma_vol5.replace(0, np.nan)
    qlj_raw = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if qlj_raw[i] and qlj_raw[i-7:i].sum() <= 1: qlj[i] = 1
    cost99 = pd.Series(c).rolling(60).quantile(0.99)
    profit100 = cost99.ewm(span=5, adjust=False).mean()
    ztxf_raw = c > profit100.values
    ztxf = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if ztxf_raw[i] and ztxf_raw[i-7:i].sum() <= 1: ztxf[i] = 1
    return (qlj & ztxf).astype(int)

def compute_f2(df):
    c, h, l = df["close"].values, df["high"].values, df["low"].values
    t1 = (2.15*c + l + h) / 4; t2 = (3.48*c + h + l) / 4
    ema23 = pd.Series(c).ewm(span=23, adjust=False).mean().values
    w = np.abs(t2 - ema23) / np.maximum(ema23, 1e-9)
    w = np.clip(w, 0, 1)
    dma = np.zeros(len(c)); dma[0] = t1[0]
    for i in range(1, len(c)): dma[i] = w[i]*t1[i] + (1-w[i])*dma[i-1]
    bull = pd.Series(dma).ewm(span=200, adjust=False).mean().values * 1.118
    dif = pd.Series(c).ewm(span=12,adjust=False).mean() - pd.Series(c).ewm(span=26,adjust=False).mean()
    dea = dif.ewm(span=9,adjust=False).mean()
    xg = np.zeros(len(c), dtype=int)
    for i in range(250, len(c)):
        if (c[i-1] <= bull[i-1] and c[i] > bull[i] and dif.iloc[i] > dea.iloc[i] and c[i]/c[i-1] > 1.09):
            xg[i] = 1
    return xg

# ═══════════════════ 统计: 可买 vs 买不到 ═══════════════════
f1_total = f2_total = f1_buyable = f2_buyable = both_buyable = 0
daily_buyable = {}

for si, code in enumerate(codes):
    if si % 1000 == 0: print(f"  {si}/{len(codes)}...")
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"],
                       "close": s["c"], "volume": s["v"]})
    try:
        f1 = compute_f1(df); f2 = compute_f2(df)
    except: continue

    for i in range(250, len(df)):
        p = s["c"][i]; o = s["o"][i]; h = s["h"][i]
        prev_c = s["c"][i-1]
        limit_up = round(prev_c * 1.10, 2) if prev_c > 0 else 999

        # 是否能买到: 收盘价<涨停价 且 非一字板 且 收盘价不是最高价
        buyable = (p < limit_up - 0.01) and (o < h) and (p < h)

        if f1[i]: f1_total += 1
        if f2[i]: f2_total += 1
        if f1[i] and f2[i]:
            date = s["dates"][i]
            if buyable:
                both_buyable += 1
                daily_buyable[date] = daily_buyable.get(date, 0) + 1

print(f"\n{'='*55}")
print(f"  信号可买性分析 ({len(stocks)}只)")
print(f"{'='*55}")
print(f"  F1总信号:         {f1_total:>8,}")
print(f"  F2总信号:         {f2_total:>8,}")
print(f"  F1 AND F2(全部):  {f1_total+f2_total:>8}")
print(f"  F1 AND F2(可买):  {both_buyable:>8}  ← 没涨停,能买到!")
print()

if daily_buyable:
    dates = sorted(daily_buyable.keys())
    counts = [daily_buyable[d] for d in dates]
    print(f"  日均可买信号: {np.mean(counts):.1f} (中位{np.median(counts):.0f})")
    print(f"  日分布:")
    for rng, label in [((1,3),"1-3只"), ((4,8),"4-8只"), ((9,20),"9-20只"), ((21,99),"20+只")]:
        n = sum(1 for c in counts if rng[0] <= c <= rng[1])
        print(f"    {label}: {n}天 ({n/len(dates)*100:.0f}%)")
    print(f"    最多一天: {max(counts)}只")
    print(f"\n  你实际每天能交易的1-8只: {sum(1 for c in counts if 1<=c<=8)}天 ({sum(1 for c in counts if 1<=c<=8)/len(dates)*100:.0f}%)")

print("  Done!")
