"""全参数网格搜索 — 找出最优策略组合"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd, itertools, time as _time
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 1500  # 用1500只快速扫描

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_qlj_ztxf(df):
    """F1: 擒龙决 AND 涨停先锋 (双共振)"""
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

def signal_tb_add(df):
    """F5: 趋势线底部 + 加仓信号"""
    c, h, l = df["close"], df["high"], df["low"]
    k = (c - l.rolling(55,min_periods=1).min()) / (h.rolling(55,min_periods=1).max() - l.rolling(55,min_periods=1).min() + 1e-9) * 100
    v11 = 3 * k.ewm(alpha=1/5, adjust=False).mean() - 2 * k.ewm(alpha=1/5, adjust=False).mean().ewm(alpha=1/3, adjust=False).mean()
    tl = v11.ewm(span=3, adjust=False).mean()
    tb = np.zeros(len(tl)); mask = tl.values <= 13; tb[mask] = 1.0 - tl.values[mask]/13.0
    mtm = c.diff(); dx_num = mtm.ewm(span=6,adjust=False).mean().ewm(span=6,adjust=False).mean()*100
    dx_den = mtm.abs().ewm(span=6,adjust=False).mean().ewm(span=6,adjust=False).mean()
    dx = dx_num/dx_den.replace(0,np.nan); dx_ma = dx.rolling(2).mean()
    add = ((dx.shift(1)<=dx_ma.shift(1))&(dx>dx_ma)&(dx.rolling(2).min()==dx.rolling(7).min())&(dx.rolling(2).apply(lambda x:(x<0).sum())>0)).astype(float).values
    return ((tb > 0.5) & (add > 0.5)).astype(int).values

# ═══════════════════ 参数网格 ═══════════════════
PARAM_GRID = list(itertools.product(
    # 信号
    [("F1:双共振(qlj+ztxf)", signal_qlj_ztxf),
     ("F5:趋势底+加仓(tb+add)", signal_tb_add)],
    # 硬止损
    [-0.03, -0.05],
    # 时间止损(天)
    [5, 8, 12],
    # 跟踪止盈启动点
    [0.05, 0.07, 0.10],
    # 低开减半
    [True, False],
    # 大盘过滤(C>MA20)
    [True, False],
))

print("=" * 65)
print(f"  全参数网格搜索 — {len(PARAM_GRID)} 种组合")
print("=" * 65)

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  {len(data)} 只股票\n")

# 加载指数
from quant_framework.data.providers.ths_day import THSDayDataProvider
provider = THSDayDataProvider(); provider.connect()
idx_data = provider._read_day_file("999999")
idx_dates = sorted(idx_data.keys())
idx_closes = [idx_data[d][3] for d in idx_dates]
idx_ma20 = pd.Series(idx_closes).rolling(20).mean().values
idx_map = {d: (idx_closes[i] > idx_ma20[i]) for i, d in enumerate(idx_dates) if i >= 20}

results = []
for gi, ((sig_name, sig_func), hard_s, time_d, trail_p, half_exit, market_f) in enumerate(PARAM_GRID):
    trades = []; t0 = _time.time()

    for sym, sd in data.items():
        df = pd.DataFrame({"open": sd["open"][-500:], "high": sd["high"][-500:],
                           "low": sd["low"][-500:], "close": sd["close"][-500:],
                           "volume": sd["volume"][-500:]})
        if len(df) < 300: continue
        try: sig_arr = sig_func(df)
        except Exception: continue

        dates_arr = sd["dates"][-500:] if len(sd["dates"]) >= 500 else sd["dates"]
        pos = None

        for i in range(250, len(df)):
            if i >= len(dates_arr): continue
            date_int = dates_arr[i]
            p = df["close"].iloc[i]; o = df["open"].iloc[i]; h = df["high"].iloc[i]
            prev_c = df["close"].iloc[i-1] if i>=1 else p; limit_up = round(prev_c*1.10,2) if prev_c>0 else 999
            if p <= 3: continue

            # 大盘过滤
            if market_f and pos is None:
                if date_int in idx_map and not idx_map[date_int]:
                    continue

            if pos is None:
                if sig_arr[i] and p < limit_up - 0.01 and o < h:
                    pos = dict(ep=p, peak=p, remain=100, half=False, limit_held=False, ei=i)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["ei"]; pnl = (p-pos["ep"])/pos["ep"]
                peak_pnl = (pos["peak"]-pos["ep"])/pos["ep"]

                if p >= limit_up - 0.01: pos["limit_held"] = True; continue

                if half_exit and not pos["half"] and days <= 2 and pnl <= -0.03:
                    net = (pnl - 0.0013) * 0.5
                    trades.append(dict(pnl=net, days=days))
                    pos["half"] = True; pos["remain"] = 50; pos["peak"] = p; continue

                pnl_now = (p-pos["ep"])/pos["ep"]; peak_now = (pos["peak"]-pos["ep"])/pos["ep"]
                exit_ = False
                if pos.get("limit_held") and peak_now>=0.03 and (p-pos["peak"])/pos["ep"]<=-0.015: exit_=True
                elif peak_now>=trail_p and (p-pos["peak"])/pos["ep"]<=-0.015: exit_=True
                elif pnl_now>=0.05 and (pos["peak"]-p)/pos["ep"]>=0.015: exit_=True
                elif pnl_now<=hard_s: exit_=True
                elif days>=time_d and pnl_now<0.01: exit_=True

                if exit_:
                    net = (pnl_now - 0.0013) * (pos["remain"]/100.0)
                    trades.append(dict(pnl=net, days=days)); pos = None

    if not trades: results.append((sig_name, hard_s, time_d, trail_p, half_exit, market_f, 0,0,0,0,0,0)); continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    avg_d=np.mean([t["days"] for t in trades])
    results.append((sig_name, hard_s, time_d, trail_p, half_exit, market_f, len(trades), wr, aw, al, pf, avg_d))

    if gi % 20 == 0:
        print(f"  {gi}/{len(PARAM_GRID)}...")

# ═══════════════════ 排序输出 ═══════════════════
results.sort(key=lambda x: (-x[10], -x[7]))  # Sort by PF desc, then WR desc

print(f"\n{'='*85}")
print(f"  TOP 20 策略组合 (按Profit Factor排序)")
print(f"{'='*85}")
print(f"  {'Rank':<4} {'信号':<22} {'止损':>5} {'时限':>4} {'止盈':>5} {'减半':>4} {'大盘':>4} {'交易':>6} {'胜率':>6} {'均盈':>6} {'均亏':>6} {'PF':>6} {'持仓':>5}")
print(f"  {'-'*4} {'-'*22} {'-'*5} {'-'*4} {'-'*5} {'-'*4} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5}")

for rank, r in enumerate(results[:20]):
    name, hs, td, tp, he, mf, nt, wr, aw, al, pf, ad = r
    if nt == 0: continue
    print(f"  {rank+1:<4} {name:<22} {hs:>4.0%} {td:>4}d {tp:>4.0%} {'是' if he else '否':>4} {'是' if mf else '否':>4} {nt:>6} {wr:>5.1%} {aw:>5.2%} {al:>5.2%} {pf:>6.2f} {ad:>5.1f}")

# ═══════════════════ 最佳组合详情 ═══════════════════
best = results[0]
print(f"\n{'='*85}")
print(f"  🏆 最优策略: {best[0]}")
print(f"{'='*85}")
print(f"  硬止损: {best[1]:.0%} | 时间止损: {best[2]}天 | 跟踪止盈: +{best[3]:.0%}回落1.5%卖")
print(f"  低开减半: {'是' if best[4] else '否'} | 大盘过滤: {'是' if best[5] else '否'}")
print(f"  交易数: {best[6]} | 胜率: {best[7]:.1%} | PF: {best[10]:.2f} | 持仓: {best[11]:.1f}天")
print(f"  均盈: {best[8]:.2%} | 均亏: {best[9]:.2%} | 盈亏比: {abs(best[8]/best[9]):.2f}:1")

# ═══════════════════ 按信号分组对比 ═══════════════════
print(f"\n{'='*85}")
print(f"  信号类型对比 (各取最优参数)")
print(f"{'='*85}")
for sig_name in ["F1:双共振(qlj+ztxf)", "F5:趋势底+加仓(tb+add)"]:
    group = [r for r in results if r[0] == sig_name and r[6] > 0]
    if group:
        best_g = max(group, key=lambda x: x[10])
        print(f"  {sig_name}: PF={best_g[10]:.2f} WR={best_g[7]:.1%} 交易={best_g[6]} 持仓={best_g[11]:.1f}天")

print("\n  Done!")
