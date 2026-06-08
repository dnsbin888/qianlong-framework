"""
══════════════════════════════════════════════════════
  策略: 龙头一进二 — 首板后追二板
══════════════════════════════════════════════════════
  选股: 昨日首板（>9.82%+有实体+年线上+筹码集中）
        + 今日涨幅>6.82% + 开盘未涨停 + 60日首次
  参数网格: 止损 × 时限 × 止盈 × 持仓天数
══════════════════════════════════════════════════════
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd, itertools
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_yijiner(df):
    """龙头一进二: 昨日首板 + 今日追二板 (放宽版)"""
    c, o, h, v = df["close"], df["open"], df["high"], df["volume"]

    # 昨日首板条件 (放宽)
    y_zt = (_ref(c, 1)/_ref(c, 2) > 1.096)                       # 昨涨>9.6%(放宽)
    db_no_zt = (_ref(c, 2)/_ref(c, 3) < 1.096)                    # 前日未涨停
    has_body = (_ref(o, 1) < _ref(h, 1))                           # 非一字板

    # 年线 (放宽)
    ma250 = c.rolling(250).mean()
    above_ma = _ref(c, 1) > _ref(ma250, 1)*0.75                   # 放宽到75%

    # 换手 (直接用成交量判断,放宽)
    avg_v = v.rolling(20).mean()
    turnover_ok = _ref(v, 1)/_ref(avg_v, 1) < 4.0                 # 放宽到4倍

    # 价格 (放宽)
    price_ok = _ref(c, 1) < 50

    # 昨日首板
    first_board = (y_zt & db_no_zt & has_body & above_ma & turnover_ok & price_ok)

    # 今日二板: 涨>5% + 开盘未涨停
    today_surge = c/_ref(c, 1) > 1.05                              # 放宽到5%
    not_gap = o/_ref(c, 1) < 1.096
    open_ok = (o/_ref(c, 1)-1) < 0.09                              # 放宽到9%

    xg = _ref(first_board, 1) & today_surge & not_gap & open_ok
    return (xg & (_count(xg, 60)==1)).astype(int).values

# ═══════════════════ 参数网格 ═══════════════════
GRID = list(itertools.product(
    [-0.03, -0.05, -0.08],
    [3, 5, 8],
    [0.05, 0.07, 0.10],
    [True, False],
))

print("=" * 65)
print("  策略: 龙头一进二 — 参数网格搜索")
print(f"  {len(GRID)} 种组合")
print("=" * 65)

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  {len(data)} 只股票\n")

results = []
for gi, (hard_s, time_d, trail_p, half_exit) in enumerate(GRID):
    trades = []
    for sym, sd in data.items():
        try:
            df = pd.DataFrame({"open": sd["open"][-500:], "high": sd["high"][-500:],
                               "low": sd["low"][-500:], "close": sd["close"][-500:],
                               "volume": sd["volume"][-500:]})
            if len(df) < 350: continue
            sig_arr = signal_yijiner(df)
        except Exception:
            continue

        pos = None
        for i in range(300, len(df)):
            p = df["close"].iloc[i]; o = df["open"].iloc[i]; h = df["high"].iloc[i]
            pc = df["close"].iloc[i-1] if i>=1 else p; lu = round(pc*1.10,2) if pc>0 else 999
            if p <= 3: continue

            if pos is None:
                if sig_arr[i] and p < lu - 0.01 and o < h:
                    pos = dict(ep=p, peak=p, remain=100, half=False, lh=False, ei=i)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["ei"]; pnl = (p-pos["ep"])/pos["ep"]; pp = (pos["peak"]-pos["ep"])/pos["ep"]

                if p >= lu - 0.01: pos["lh"] = True; continue
                if half_exit and not pos["half"] and days<=1 and pnl<=-0.03:
                    trades.append(dict(pnl=(pnl-0.0013)*0.5, days=days))
                    pos["half"] = True; pos["remain"] = 50; pos["peak"] = p; continue

                if ((pos.get("lh") and pp>=0.03) or pp>=trail_p) and (p-pos["peak"])/pos["ep"]<=-0.015:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos=None
                elif pnl <= hard_s:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos=None
                elif days >= time_d and pnl < 0.01:
                    trades.append(dict(pnl=(pnl-0.0013)*(pos["remain"]/100), days=days)); pos=None

    if not trades: results.append((hard_s, time_d, trail_p, half_exit, 0,0,0,0,0,0)); continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    results.append((hard_s, time_d, trail_p, half_exit, len(trades), wr, aw, al, pf, np.mean([t["days"] for t in trades])))
    if len(trades)>5:
        print(f"  [{gi+1}/{len(GRID)}] stop={hard_s:.0%} t={time_d}d trail=+{trail_p:.0%} half={half_exit} T={len(trades)} PF={pf:.2f} WR={wr:.1%}")

results.sort(key=lambda x: (-x[8], -x[5]))

print(f"\n{'='*75}")
print(f"══════════════════════════════════════════════════════")
print(f"  回测最优方案: 龙头一进二")
print(f"══════════════════════════════════════════════════════")
print(f"  {'#':<3} {'止损':>5} {'时限':>4} {'止盈':>5} {'减半':>4} {'交易':>6} {'胜率':>6} {'均盈':>6} {'均亏':>6} {'PF':>6} {'持仓':>5}")
print(f"  {'-'*3} {'-'*5} {'-'*4} {'-'*5} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5}")

for rank, r in enumerate(results[:15]):
    if r[4] == 0: continue
    print(f"  {rank+1:<3} {r[0]:>4.0%} {r[1]:>4}d {r[2]:>4.0%} {'是' if r[3] else '否':>4} {r[4]:>6} {r[5]:>5.1%} {r[6]:>5.2%} {r[7]:>5.2%} {r[8]:>6.2f} {r[9]:>5.1f}")

best = [r for r in results if r[4] > 0][0]
print(f"\n══════════════════════════════════════════════════════")
print(f"  实盘交易策略")
print(f"══════════════════════════════════════════════════════")
print(f"  买入: 昨日首板+今日追二板 (开盘涨幅<8% 挂单)")
print(f"  止损: {best[0]:.0%}")
print(f"  止盈: +{best[2]:.0%}回落1.5%")
print(f"  时限: {best[1]}天")
print(f"  仓位: 单只10-15%（追涨风险大，轻仓）")
print(f"  PF: {best[8]:.2f} | 胜率: {best[5]:.1%} | 均盈: {best[6]:.2%} | 均亏: {best[7]:.2%}")
print(f"══════════════════════════════════════════════════════")

# 两策略对比
f1_best = (0.05, 12, 0.10, False, 1034, 0.514, 0.051, -0.039, 1.41, 5.2)
print(f"\n══════════════════════════════════════════════════════")
print(f"  策略对比")
print(f"══════════════════════════════════════════════════════")
print(f"  {'指标':<18} {'F1:双共振(打板+低吸)':<25} {'F4:龙头一进二':<25}")
print(f"  {'-'*18} {'-'*25} {'-'*25}")
print(f"  {'选股逻辑':<18} {'放量突破+资金共识':<25} {'首板确认+追二板':<25}")
for label, fi, f4 in [
    ("PF", f1_best[8], best[8]),
    ("胜率", f1_best[5], best[5]),
    ("交易数", f1_best[4], best[4]),
    ("持仓天数", f1_best[9], best[9]),
]:
    arrow = "←" if fi > f4 else ""
    arrow2 = "←" if f4 > fi else ""
    print(f"  {label:<18} {fi:<25.2f} {f4:<25.2f}")

# Recommendation
best_signal = "F1:双共振" if f1_best[8] > best[8] else "F4:一进二"
print(f"\n  建议: {best_signal} 为主策略, 另一个做补充")
print("  Done!")
