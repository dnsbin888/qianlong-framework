"""直接用TDX日线跑完整公式 — 擒龙决+涨停先锋 + 牛线XG"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd
from collections import defaultdict
import random, time as _time
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N_STOCKS = 5000

# ═══════════════════ 加载TDX日线 ═══════════════════
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

print("Loading TDX data...")
stocks = {}
for market in ["sh", "sz"]:
    lday_dir = os.path.join(ROOT, market, "lday")
    if not os.path.isdir(lday_dir): continue
    for fname in os.listdir(lday_dir):
        if not fname.endswith(".day"): continue
        s = load_stock(market, fname)
        if s and len(s["dates"]) >= 300:
            stocks[s["code"]] = s

codes = list(stocks.keys())
if len(codes) > N_STOCKS:
    random.seed(42)
    codes = random.sample(codes, N_STOCKS)
stocks = {k: stocks[k] for k in codes}
print(f"{len(stocks)} stocks loaded\n")

# ═══════════════════ 公式1: 擒龙决+涨停先锋 ═══════════════════
def compute_f1(df):
    c, v = df["close"].values, df["volume"].values
    n = len(c)
    # 压力线
    hhv30 = pd.Series(c).rolling(30).max().shift(1)
    pressure = hhv30.rolling(2).mean().values
    # 布林上轨
    ema20 = pd.Series(c).ewm(span=20, adjust=False).mean()
    dev = ((pd.Series(c) - ema20)**2).rolling(20).mean()**0.5
    upper = (ema20 + 2*dev).shift(1).values
    # 量比
    ma_vol5 = pd.Series(v).rolling(5).mean().shift(1)
    vol_ratio = v / ma_vol5.replace(0, np.nan)
    # 擒龙决
    qlj_raw = np.zeros(n, dtype=bool)
    for i in range(60, n):
        if c[i] > pressure[i] and c[i] > upper[i] and vol_ratio[i] > 1.8:
            qlj_raw[i] = True
    qlj = np.zeros(n, dtype=int)
    for i in range(60, n):
        if qlj_raw[i] and qlj_raw[i-7:i].sum() <= 1:
            qlj[i] = 1
    # 涨停先锋
    cost99 = pd.Series(c).rolling(60).quantile(0.99)
    profit100 = cost99.ewm(span=5, adjust=False).mean()
    ztxf_raw = np.zeros(n, dtype=bool)
    for i in range(60, n):
        if c[i] > profit100.iloc[i]:
            ztxf_raw[i] = True
    ztxf = np.zeros(n, dtype=int)
    for i in range(60, n):
        if ztxf_raw[i] and ztxf_raw[i-7:i].sum() <= 1:
            ztxf[i] = 1
    return (qlj & ztxf).astype(int)

# ═══════════════════ 公式2: XG(牛线突破) ═══════════════════
def compute_f2(df):
    c, o, h, l, v = (df["close"].values, df["open"].values,
                     df["high"].values, df["low"].values, df["volume"].values)
    n = len(c)
    # 牛线
    t1 = (2.15*c + l + h) / 4
    t2 = (3.48*c + h + l) / 4
    ema23 = pd.Series(c).ewm(span=23, adjust=False).mean().values
    w = np.abs(t2 - ema23) / np.maximum(ema23, 1e-9)
    w = np.clip(w, 0, 1)
    dma = np.zeros(n)
    dma[0] = t1[0]
    for i in range(1, n): dma[i] = w[i]*t1[i] + (1-w[i])*dma[i-1]
    bull = pd.Series(dma).ewm(span=200, adjust=False).mean().values * 1.118
    # MACD
    dif = pd.Series(c).ewm(span=12, adjust=False).mean() - pd.Series(c).ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    # XG
    xg = np.zeros(n, dtype=int)
    for i in range(250, n):
        if (c[i-1] <= bull[i-1] and c[i] > bull[i]  # CROSS牛线
            and dif.iloc[i] > dea.iloc[i]            # MACD多头
            and c[i]/c[i-1] > 1.09):                  # 涨幅>9%
            xg[i] = 1
    return xg

# ═══════════════════ 回测 ═══════════════════
print("Computing signals...")
t0 = _time.time()
all_f1, all_f2 = [], []

for si, code in enumerate(codes):
    if si % 1000 == 0: print(f"  {si}/{len(codes)}...")
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"],
                       "close": s["c"], "volume": s["v"]})
    try:
        f1 = compute_f1(df)
        f2 = compute_f2(df)
        dates = s["dates"]
        for i in range(250, len(df)):
            if f1[i]: all_f1.append({"code": code, "date": dates[i], "price": s["c"][i]})
            if f2[i]: all_f2.append({"code": code, "date": dates[i], "price": s["c"][i]})
    except Exception:
        continue

print(f"Done in {_time.time()-t0:.0f}s\n")

# ═══════════════════ 统计 ═══════════════════
def analyze(signals, name):
    if not signals:
        print(f"  {name}: 0 signals!")
        return
    df = pd.DataFrame(signals)
    df["year_month"] = df["date"].astype(str).str[:6]
    daily = df.groupby("date").size()
    monthly = df.groupby("year_month").size()
    print(f"  {name}:")
    print(f"    总信号: {len(df):,}")
    print(f"    日均: {daily.mean():.1f} (中位数{daily.median():.0f})")
    print(f"    月均: {monthly.mean():.1f}")
    print(f"    有信号的股票数: {df['code'].nunique()}")
    print(f"    日信号分布: 0只={(daily==0).sum()}天, 1-3只={(daily[(daily>=1)&(daily<=3)]).sum():.0f}天均, 4-8只={((daily>=4)&(daily<=8)).sum()}天, 8+={((daily>8)).sum()}天")
    print()

analyze(all_f1, "F1:擒龙决+涨停先锋")
analyze(all_f2, "F2:牛线突破XG")

# 交集
f1_dates = set((s["code"], s["date"]) for s in all_f1)
f2_dates = set((s["code"], s["date"]) for s in all_f2)
both = f1_dates & f2_dates
print(f"  双共振(F1 AND F2): {len(both)} 信号")
print("  Done!")
