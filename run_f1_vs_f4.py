"""F1(双共振) vs F4(龙头一进二) — 同台PK"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()
def _exist(c, n): return c.rolling(n, min_periods=1).max().fillna(0).astype(bool)
def _every(c, n): return c.rolling(n, min_periods=1).min().fillna(0).astype(bool)

def signal_f1(df):
    """F1: 擒龙决 AND 涨停先锋 (双共振·打板+分歧低吸)"""
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

def signal_f4(df):
    """F4: 龙头一进二 — 首板后次日追二板

    条件:
      昨日: 首板(>9.82%) + 非昨日涨停 + 有实体换手 + 年线上方
           + 筹码集中(获利盘>=65%) + 换手<10% + 流通市值<150亿 + 股价<30
      今日: 涨幅>6.82% + 开盘未涨停 + 60日首次
    """
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]

    # 首板条件 (昨日)
    yesterday_zt = (_ref(c, 1) / _ref(c, 2) > 1.0982)  # 昨涨停>9.82%
    day_before_not_zt = (_ref(c, 2) / _ref(c, 3) < 1.09)  # 前日未涨停
    has_body = (_ref(o, 1) < _ref(h, 1))  # 有上影=非一字
    body_ratio = (_ref(c, 1) - _ref(o, 1)) / (_ref(h, 1) - _ref(l, 1) + 0.001)  # 实体占比>50%
    has_real_body = body_ratio > 0.5

    # 年线
    ma250 = c.rolling(250).mean()
    above_ma = (_ref(c, 1) > _ref(ma250, 1) * 0.85)

    # 获利盘 (简化: COST分布)
    cost85 = c.rolling(60).quantile(0.85)
    profit_ok = (_ref(cost85, 1) < _ref(c, 1))

    # 换手率<10% (简化: 成交量/20日均量<2.5倍 = 约10%换手)
    avg_vol = v.rolling(20).mean()
    turnover_ok = (_ref(v, 1) / _ref(avg_vol, 1) < 2.5)

    # 价格<30
    price_ok = _ref(c, 1) < 30

    # 昨日首板综合
    first_board = (yesterday_zt & day_before_not_zt & has_body & has_real_body &
                   above_ma & profit_ok & turnover_ok & price_ok)

    # 今日二板信号: 涨幅>6.82% + 开盘未涨停 + 60日首次
    today_surge = c / _ref(c, 1) > 1.0682
    not_gap_limit = o / _ref(c, 1) < 1.096
    open_chg = o / _ref(c, 1) - 1
    open_ok = open_chg < 0.08  # 开盘幅度<8%

    xg = _ref(first_board, 1) & today_surge & not_gap_limit & open_ok
    # 60日去重
    return (_count(xg, 60) == 1).astype(int).values & xg.astype(int).values

# ═══════════════════ 加载 ═══════════════════
print("=" * 65)
print("  F1(双共振) vs F4(龙头一进二) — 同台PK")
print("=" * 65)
print(f"  F1: 擒龙决 AND 涨停先锋 (打板+分歧低吸)")
print(f"  F4: 龙头一进二 (首板后追二板)")
print(f"  统一出场: +7%回落1.5%卖 | -3%止损 | 5天时限 | 封板不卖")
print()

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  股票池: {len(data)} 只\n")

# ═══════════════════ 逐公式回测 ═══════════════════
for fname, ffunc in [("F1:双共振(打板+分歧)", signal_f1), ("F4:龙头一进二", signal_f4)]:
    trades = []
    for sym, sd in data.items():
        df = pd.DataFrame({"open": sd["open"][-500:], "high": sd["high"][-500:],
                           "low": sd["low"][-500:], "close": sd["close"][-500:],
                           "volume": sd["volume"][-500:]})
        if len(df) < 300: continue
        try: sig_arr = ffunc(df)
        except Exception: continue

        pos = None
        for i in range(250, len(df)):
            p = df["close"].iloc[i]; o = df["open"].iloc[i]
            h = df["high"].iloc[i]; prev_c = df["close"].iloc[i-1] if i>=1 else p
            limit_up_p = round(prev_c*1.10, 2) if prev_c > 0 else 999999
            if p <= 3.0: continue

            if pos is None:
                if sig_arr[i] and p < limit_up_p - 0.01 and o < h:
                    pos = dict(ep=p, ei=i, peak=p, remain=100, half=False, limit_held=False)
            else:
                if h > pos["peak"]: pos["peak"] = h
                days = i - pos["ei"]
                pnl = (p - pos["ep"]) / pos["ep"]
                peak_pnl = (pos["peak"] - pos["ep"]) / pos["ep"]

                if p >= limit_up_p - 0.01:
                    pos["limit_held"] = True; continue

                if not pos["half"] and days <= 2 and pnl <= -0.03:
                    net = (pnl - 0.0013) * 0.5
                    trades.append(dict(pnl=net, days=days))
                    pos["half"] = True; pos["remain"] = 50; pos["peak"] = p
                    continue

                pnl_now = (p - pos["ep"]) / pos["ep"]
                peak_now = (pos["peak"] - pos["ep"]) / pos["ep"]
                exit_ = False

                if pos.get("limit_held") and peak_now >= 0.03 and (p - pos["peak"])/pos["ep"] <= -0.015:
                    exit_ = True
                elif peak_now >= 0.07 and (p - pos["peak"])/pos["ep"] <= -0.015:
                    exit_ = True
                elif pnl_now >= 0.05 and (pos["peak"] - p)/pos["ep"] >= 0.015:
                    exit_ = True
                elif pnl_now <= -0.03:
                    exit_ = True
                elif days >= 5 and pnl_now < 0.01:
                    exit_ = True

                if exit_:
                    sell_pct = pos["remain"] / 100.0
                    net = (pnl_now - 0.0013) * sell_pct
                    trades.append(dict(pnl=net, days=days))
                    pos = None

    if not trades: print(f"  {fname:<30} 0 trades"); continue
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    avg_d=np.mean([t["days"] for t in trades])

    print(f"  {fname:<30} {len(trades):>5}trades  WR:{wr:>5.1%}  AW:{aw:>6.2%}  AL:{al:>6.2%}  PF:{pf:>5.2f}  Days:{avg_d:>4.1f}")

    # Quick P&L buckets
    pnls = [t["pnl"] for t in trades]
    for lo, hi, label in [(-99, -0.05, "巨亏<-5%"), (-0.05, 0, "小亏"), (0, 0.05, "小赚"), (0.05, 0.10, "大赚"), (0.10, 99, "暴赚>10%")]:
        n = sum(1 for p in pnls if lo <= p < hi)
        print(f"    {label:<12}: {n:>5} ({n/len(trades)*100:>4.0f}%)")

print("\n  Done!")
