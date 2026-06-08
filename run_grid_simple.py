"""简化网格搜索 — F1双共振 × 12组参数"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd, itertools, time as _time, traceback
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"; N_SAMPLE = 1500

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_f1(df):
    c, v = df["close"], df["volume"]
    p = _ref(_hhv(c, 30), 1).rolling(2).mean()
    d = (c - _ema(c, 20)).pow(2).rolling(20).mean().pow(0.5)
    u = _ref(_ema(c, 20) + 2*d, 1)
    vr = v / _ref(v.rolling(5).mean(), 1).replace(0, np.nan)
    qlj = ((c > p) & (c > u) & (vr > 1.8)).astype(int)
    qlj = qlj & (qlj.rolling(7).sum() == 1)
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    p100 = cost99.ewm(span=5, adjust=False).mean()
    ztxf = ((c > p100).astype(int).rolling(7).sum() == 1).astype(int)
    return ((qlj > 0) & (ztxf > 0)).astype(int).values

GRID = list(itertools.product(
    [-0.03, -0.05, -0.08],           # hard stop
    [5, 8, 12],                       # time stop
    [0.05, 0.07, 0.10],               # trail peak
    [True, False],                     # half exit
))

print("=" * 65); print(f"  Grid: F1 x {len(GRID)} combos"); print("=" * 65)

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}

# Index
from quant_framework.data.providers.ths_day import THSDayDataProvider
provider = THSDayDataProvider(); provider.connect()
idx_raw = provider._read_day_file("999999")
idx_closes = np.array([idx_raw[d][3] for d in sorted(idx_raw.keys())])
idx_ma20 = pd.Series(idx_closes).rolling(20).mean().values
idx_map = {d: idx_closes[i] > idx_ma20[i] for i, d in enumerate(sorted(idx_raw.keys())) if i >= 20}

results = []
for gi, (hard_s, time_d, trail_p, half_exit) in enumerate(GRID):
    trades = []
    for sym, sd in data.items():
        try:
            df = pd.DataFrame({"open": sd["open"][-500:], "high": sd["high"][-500:],
                               "low": sd["low"][-500:], "close": sd["close"][-500:],
                               "volume": sd["volume"][-500:]})
            if len(df) < 300: continue
            sig_arr = signal_f1(df)
            dates_arr = sd["dates"][-500:] if len(sd["dates"]) >= 500 else sd["dates"]
        except Exception:
            continue

        pos = None
        for i in range(250, len(df)):
            if i >= len(dates_arr): continue
            p = df["close"].iloc[i]; o = df["open"].iloc[i]; h = df["high"].iloc[i]
            pc = df["close"].iloc[i-1] if i>=1 else p; lu = round(pc*1.10,2) if pc>0 else 999
            if p <= 3: continue

            if pos is None:
                if sig_arr[i] and p < lu - 0.01 and o < h:
                    pos = dict(ep=p, peak=p, remain=100, half=False, lh=False, ei=i)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["ei"]; pnl = (p-pos["ep"])/pos["ep"]
                pp = (pos["peak"]-pos["ep"])/pos["ep"]

                if p >= lu - 0.01: pos["lh"] = True; continue
                if half_exit and not pos["half"] and days <= 2 and pnl <= -0.03:
                    trades.append(dict(pnl=(pnl-0.0013)*0.5, days=days))
                    pos["half"] = True; pos["remain"] = 50; pos["peak"] = p; continue

                if ((pos.get("lh") and pp>=0.03) or pp>=trail_p) and (p-pos["peak"])/pos["ep"]<=-0.015:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos = None
                elif pnl <= hard_s:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos = None
                elif days >= time_d and pnl < 0.01:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos = None

    if not trades: results.append((hard_s, time_d, trail_p, half_exit, 0, 0, 0, 0, 0, 0)); continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    results.append((hard_s, time_d, trail_p, half_exit, len(trades), wr, aw, al, pf, np.mean([t["days"] for t in trades])))
    print(f"  [{gi+1}/{len(GRID)}] stop={hard_s:.0%} time={time_d}d trail=+{trail_p:.0%} half={half_exit}  T={len(trades)} PF={pf:.2f}")

results.sort(key=lambda x: (-x[8], -x[5]))

print(f"\n{'='*75}")
print(f"  RANKING (F1双共振)")
print(f"{'='*75}")
print(f"  {'#':<3} {'止损':>5} {'时限':>4} {'止盈':>5} {'减半':>4} {'交易':>6} {'胜率':>6} {'均盈':>6} {'均亏':>6} {'PF':>6} {'持仓':>5}")
print(f"  {'-'*3} {'-'*5} {'-'*4} {'-'*5} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5}")
for rank, r in enumerate(results):
    if r[4] == 0: continue
    print(f"  {rank+1:<3} {r[0]:>4.0%} {r[1]:>4}d {r[2]:>4.0%} {'是' if r[3] else '否':>4} {r[4]:>6} {r[5]:>5.1%} {r[6]:>5.2%} {r[7]:>5.2%} {r[8]:>6.2f} {r[9]:>5.1f}")

best = [r for r in results if r[4] > 0][0]
print(f"\n  🏆 最优: 止损{best[0]:.0%} 时限{best[1]}天 止盈+{best[2]:.0%}回落 减半={'是' if best[3] else '否'}")
print(f"     PF={best[8]:.2f} 胜率={best[5]:.1%} 均盈={best[6]:.2%} 均亏={best[7]:.2%} 持仓={best[9]:.1f}天")
print("  Done!")
