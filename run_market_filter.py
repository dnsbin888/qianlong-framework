"""大盘过滤 — 只在指数多头时做多，对比效果"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_f1(df):
    c, v = df["close"], df["volume"]
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    dev = (c - _ema(c, 20)).pow(2).rolling(20).mean().pow(0.5)
    upper = _ref(_ema(c, 20) + 2*dev, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    qlj = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = qlj & (_count(qlj, 7) == 1)
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    ztxf = (c > profit100) & (_count(c > profit100, 7) == 1)
    return (qlj & ztxf).astype(int).values

print("=" * 65)
print("  大盘过滤测试 — F1双共振")
print("=" * 65)

# Load market index
print("  加载指数数据...")
from quant_framework.data.providers.ths_day import THSDayDataProvider
provider = THSDayDataProvider()
provider.connect()
index_data = {}
for sym in ["999999", "1A0001"]:
    d = provider._read_day_file(sym)
    if d and len(d) > 500:
        index_data = d
        print(f"  Index: {sym} ({len(d)} records)")
        break

# Build index DataFrame
idx_dates = sorted(index_data.keys())
idx_df = pd.DataFrame({
    "close": [index_data[d][3] for d in idx_dates],
    "high": [index_data[d][1] for d in idx_dates],
    "low": [index_data[d][2] for d in idx_dates],
}, index=idx_dates)

# Index status: 1=多头(可做), 0=观望
idx_df["ma20"] = idx_df["close"].rolling(20).mean()
idx_df["ma60"] = idx_df["close"].rolling(60).mean()
idx_df["trend_up"] = (idx_df["close"] > idx_df["ma20"]).astype(int)  # C>MA20
idx_df["strong_up"] = ((idx_df["close"] > idx_df["ma20"]) & (idx_df["ma20"] > idx_df["ma60"])).astype(int)
idx_df["not_bear"] = (idx_df["close"] > idx_df["ma60"]).astype(int)  # C>MA60
idx_df["daily_up"] = (idx_df["close"] > idx_df["close"].shift(1)).astype(int)  # 当日收阳

# ═══════════════════ 测试四种过滤 ═══════════════════
FILTERS = [
    ("无过滤(基准)",          None),
    ("C>MA20(短多)",          "trend_up"),
    ("C>MA60(非熊)",          "not_bear"),
    ("C>MA20>MA60(强多)",     "strong_up"),
    ("C>MA20+当日收阳",       "trend_up"),  # special: AND daily_up
]

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}

for fname, idx_col in FILTERS:
    trades = []
    for sym, sd in data.items():
        df = pd.DataFrame({"open": sd["open"][-500:], "high": sd["high"][-500:],
                           "low": sd["low"][-500:], "close": sd["close"][-500:],
                           "volume": sd["volume"][-500:]})
        if len(df) < 300: continue
        try: sig_arr = signal_f1(df)
        except Exception: continue

        dates_arr = sd["dates"][-500:] if len(sd["dates"]) >= 500 else sd["dates"]

        pos = None
        for i in range(250, len(df)):
            if i >= len(dates_arr): continue
            date_int = dates_arr[i]
            p = df["close"].iloc[i]; o = df["open"].iloc[i]
            h = df["high"].iloc[i]; prev_c = df["close"].iloc[i-1] if i>=1 else p
            limit_up_p = round(prev_c*1.10, 2) if prev_c > 0 else 999999
            if p <= 3.0: continue

            # ── 大盘过滤 ──
            if idx_col is not None:
                # Find index status for this date
                idx_dates_list = list(idx_df.index)
                if date_int not in idx_dates_list:
                    closest = [d for d in idx_dates_list if d <= date_int]
                    if not closest: continue
                    idx_i = idx_dates_list.index(closest[-1])
                else:
                    idx_i = idx_dates_list.index(date_int)
                if idx_i >= len(idx_df): continue
                if fname == "C>MA20+当日收阳":
                    if not (idx_df["trend_up"].iloc[idx_i] and idx_df["daily_up"].iloc[idx_i]):
                        if pos is None: continue  # skip entry, but still check exits
                else:
                    if not idx_df[idx_col].iloc[idx_i]:
                        if pos is None: continue

            if pos is None:
                if sig_arr[i] and p < limit_up_p - 0.01 and o < h:
                    pos = dict(ep=p, ei=i, peak=p, remain=100, half=False, limit_held=False)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["ei"]
                pnl = (p - pos["ep"]) / pos["ep"]
                peak_pnl = (pos["peak"] - pos["ep"]) / pos["ep"]

                if p >= limit_up_p - 0.01: pos["limit_held"] = True; continue
                if not pos["half"] and days <= 2 and pnl <= -0.03:
                    net = (pnl - 0.0013) * 0.5
                    trades.append(dict(pnl=net, days=days))
                    pos["half"] = True; pos["remain"] = 50; pos["peak"] = p; continue

                pnl_now = (p - pos["ep"]) / pos["ep"]
                peak_now = (pos["peak"] - pos["ep"]) / pos["ep"]
                exit_ = False
                if pos.get("limit_held") and peak_now>=0.03 and (p-pos["peak"])/pos["ep"]<=-0.015: exit_=True
                elif peak_now>=0.07 and (p-pos["peak"])/pos["ep"]<=-0.015: exit_=True
                elif pnl_now>=0.05 and (pos["peak"]-p)/pos["ep"]>=0.015: exit_=True
                elif pnl_now<=-0.03: exit_=True
                elif days>=5 and pnl_now<0.01: exit_=True

                if exit_:
                    net = (pnl_now - 0.0013) * (pos["remain"]/100.0)
                    trades.append(dict(pnl=net, days=days)); pos = None

    if not trades:
        print(f"  {fname:<25} 0 trades")
        continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    avg_d=np.mean([t["days"] for t in trades])
    win_n = len(w); loss_n = len(l_)
    print(f"  {fname:<25} {len(trades):>5}t  WR:{wr:>5.1%}  AW:{aw:>5.2%}  AL:{al:>5.2%}  PF:{pf:>5.2f}  Days:{avg_d:>4.1f}  W{win_n}/L{loss_n}")

print("\n  Done!")
