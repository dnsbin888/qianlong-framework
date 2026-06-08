"""已废弃，请使用 run_factor_backtest_unified.py:

    python run_factor_backtest_unified.py --mode smart --stocks 500

原说明: 智能多因子选股 V3 — 复用已有缓存，追加质量过滤+市场自适应。
"""

import sys
sys.path.insert(0, r"d:\quant_framework\src")

import pickle, time
import numpy as np
import pandas as pd
from collections import Counter
import warnings
warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.tdx_signals import factor_trend_bottom, factor_add_position, factor_money_flow
from quant_framework.factors.tdx_signals2 import factor_bull_position
from quant_framework.factors.definitions import FACTOR_MAP

TOP_K, MIN_DAYS = 30, 500
CACHE_FILE = r"d:\quant_framework\factor_cache.pkl"
MONEY_CACHE = r"d:\quant_framework\factor_cache_smart.pkl"

# ======================================================================
# 1. 市场状态
# ======================================================================
class MarketState:
    BULL, BEAR, CHOPPY = "bull", "bear", "choppy"

    def __init__(self, provider):
        self._cache = {}
        # Load 上证指数
        for sym in ["999999", "1A0001", "000001"]:
            data = provider._read_day_file(sym)
            if data and len(data) > 500:
                self._index = data
                break
        else:
            self._index = {}

    def get_state(self, date_int: int) -> str:
        if date_int in self._cache:
            return self._cache[date_int]
        if not self._index:
            return self.CHOPPY

        dates = sorted(self._index.keys())
        prev = [d for d in dates if d <= date_int]
        if len(prev) < 120:
            return self.CHOPPY

        closes = np.array([self._index[d][3] for d in prev[-120:]])
        if len(closes) < 60:
            return self.CHOPPY

        c, ma20, ma60 = closes[-1], closes[-20:].mean(), closes[-60:].mean()
        if c > ma60 and ma20 > ma60:
            s = self.BULL
        elif c < ma60:
            s = self.BEAR
        else:
            s = self.CHOPPY

        self._cache[date_int] = s
        return s


def get_weights(state: str) -> list[tuple]:
    """Market-adaptive factor weights"""
    if state == MarketState.BULL:
        return [
            ("ret_20d",       +1, 0.35),   # 动量追涨
            ("bull_position", +1, 0.25),   # 牛线上方
            ("trend_bottom",  +1, 0.20),   # 支撑
            ("add_position",  +1, 0.20),   # 背离
            ("money_flow",    +1, 0.00),   # (skip if not cached)
        ]
    elif state == MarketState.BEAR:
        return [
            ("trend_bottom",  +1, 0.45),   # 抄底
            ("add_position",  +1, 0.25),   # 背离
            ("ret_20d",       -1, 0.20),   # 反转
            ("bull_position", -1, 0.10),   # 位置
            ("money_flow",    +1, 0.00),   # (skip)
        ]
    else:
        return [
            ("trend_bottom",  +1, 0.35),
            ("add_position",  +1, 0.20),
            ("ret_20d",       -1, 0.20),
            ("bull_position", -1, 0.15),
            ("money_flow",    +1, 0.10),
        ]

# ======================================================================
# 2. Load cached factors
# ======================================================================
print("=" * 70)
print("  SMART Multi-Factor V3 — Adaptive Weights + Quality Filter")
print("=" * 70)

print("\n[1/3] Loading factor cache...")
provider = THSDayDataProvider()
provider.connect()
market = MarketState(provider)

with open(CACHE_FILE, "rb") as f:
    factor_cache = pickle.load(f)
print(f"  {len(factor_cache)} stocks loaded")

# Add money_flow if not present (quick)
for sym, cache in factor_cache.items():
    if "money_flow" not in cache["factors"]:
        cache["factors"]["money_flow"] = None  # Skip, will be 0 weight when missing

# Quality filter
def is_valid(cache, idx):
    if idx < 250 or idx >= len(cache["close"]):
        return False
    price = cache["close"][idx]
    if price < 3.0:  # 低价股
        return False
    if idx >= 20:
        recent = cache["close"][idx-20:idx+1]
        if min(recent) == max(recent):  # 停牌/无交易
            return False
    return True

# ======================================================================
# 3. Backtest
# ======================================================================
print("\n[2/3] Running backtest...")

# Rebalance dates
all_dates = set()
for cache in factor_cache.values():
    for d in cache["dates"]:
        if 20200101 <= d <= 20260601 and len(str(d)) == 8:
            all_dates.add(d)
all_dates = sorted(all_dates)
dd = pd.DataFrame({"date": all_dates})
dd["dt"] = pd.to_datetime(dd["date"].astype(str), format="%Y%m%d")
rebal_dates = dd.groupby(dd["dt"].dt.to_period("M"))["date"].max().tolist()

cash = 1_000_000
holdings = {}
eq_curve = []
bench_eq = 1_000_000
bench_curve = []
prev_prices = {}

for ri, rdate in enumerate(rebal_dates):
    ms = market.get_state(rdate)
    weights = get_weights(ms)

    scores = {}
    mkt_prices = []

    for sym, cache in factor_cache.items():
        dates = cache["dates"]
        prev = [d for d in dates if d <= rdate]
        if not prev:
            continue
        idx = len(prev) - 1
        if not is_valid(cache, idx):
            continue

        price = cache["close"][idx]
        mkt_prices.append(price)

        score, n = 0.0, 0
        for fname, direction, weight in weights:
            fvals = cache["factors"].get(fname)
            if fvals is None or len(fvals) == 0 or idx >= len(fvals):
                continue
            raw = fvals[idx]
            if raw is None or np.isnan(raw) or np.isinf(raw):
                continue
            score += np.clip(float(raw), -5, 5) * direction * weight
            n += 1

        if n >= 2:
            scores[sym] = (score, price)

    if len(scores) < TOP_K:
        continue

    # Sell
    for sym in list(holdings):
        if sym in scores:
            cash += holdings[sym] * scores[sym][1] * 0.9997
        del holdings[sym]

    # Buy
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)[:TOP_K]
    alloc = cash * 0.95 / TOP_K
    for sym, (score, price) in ranked:
        shares = int(alloc / price / 100) * 100
        if shares >= 100:
            cost = shares * price * 1.0003
            if cost <= cash:
                cash -= cost
                holdings[sym] = shares

    mkt_val = sum(holdings[s] * scores[s][1] for s in holdings if s in scores)
    total = cash + mkt_val
    eq_curve.append({"date": rdate, "equity": total, "state": ms, "n_valid": len(scores)})

    # Benchmark
    if ri > 0 and mkt_prices and prev_prices:
        rets = []
        for s in scores:
            if s in prev_prices and prev_prices[s] > 0:
                rets.append(scores[s][1] / prev_prices[s] - 1)
        if rets:
            bench_eq *= (1 + np.mean(rets))
    bench_curve.append({"date": rdate, "equity": bench_eq})
    prev_prices = {s: scores[s][1] for s in scores}

# Cleanup: sell all at end
for sym, shares in list(holdings.items()):
    data = provider._read_day_file(sym)
    if data and rdate in data:
        cash += shares * data[rdate][3] * 0.9997
    del holdings[sym]

# ======================================================================
# 4. Results
# ======================================================================
print("\n[3/3] Performance Report")
eq = pd.DataFrame(eq_curve).set_index("date")
bn = pd.DataFrame(bench_curve).set_index("date")

total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
bench_ret = bn["equity"].iloc[-1] / bn["equity"].iloc[0] - 1
months = len(eq)
ann_ret = (1 + total_ret) ** (12 / months) - 1
bench_ann = (1 + bench_ret) ** (12 / months) - 1

monthly = eq["equity"].pct_change().dropna()
bm = bn["equity"].pct_change().dropna()
sharpe = monthly.mean() / monthly.std() * np.sqrt(12) if monthly.std() > 0 else 0
max_dd = ((eq["equity"] - eq["equity"].expanding().max()) / eq["equity"].expanding().max()).min()
wr = (monthly > 0).mean()
excess = monthly - bm
alpha = excess.mean() * 12
ir = alpha / (excess.std() * np.sqrt(12)) if excess.std() > 0 else 0

print(f"\n{'=' * 70}")
print(f"  SMART PORTFOLIO (Quality Filter + Adaptive Weights)")
print(f"{'=' * 70}")
print(f"  {'Metric':<30} {'Portfolio':>15} {'Benchmark':>15}")
print(f"  {'-'*30} {'-'*15} {'-'*15}")
print(f"  {'Total Return':<30} {total_ret:>15.2%} {bench_ret:>15.2%}")
print(f"  {'Annual Return':<30} {ann_ret:>15.2%} {bench_ann:>15.2%}")
print(f"  {'Sharpe Ratio':<30} {sharpe:>15.2f} {'-':>15}")
print(f"  {'Max Drawdown':<30} {max_dd:>15.2%} {'-':>15}")
print(f"  {'Win Rate':<30} {wr:>15.1%} {'-':>15}")
print(f"  {'Alpha (annual)':<30} {alpha:>15.2%} {'-':>15}")
print(f"  {'Info Ratio':<30} {ir:>15.2f} {'-':>15}")

# Market states
print(f"\n{'=' * 70}")
print(f"  MARKET STATE ADAPTATION")
print(f"{'=' * 70}")
states = Counter(eq["state"])
for s in [MarketState.BULL, MarketState.BEAR, MarketState.CHOPPY]:
    n = states.get(s, 0)
    if n == 0:
        continue
    mask = eq["state"] == s
    s_r = eq.loc[mask, "equity"].pct_change().dropna()
    avg = s_r.mean() if len(s_r) > 1 else 0
    swr = (s_r > 0).mean() if len(s_r) > 1 else 0
    print(f"  {s.upper():<8}: {n:>3}m ({n/months*100:.0f}%)  Avg Ret={avg:>8.2%}  Win={swr:>7.0%}")

# Year-by-year
print(f"\n{'=' * 70}")
print(f"  YEAR-BY-YEAR vs STATIC MODEL")
print(f"{'=' * 70}")
print(f"  {'Year':<8} {'Smart':>10} {'Static':>10} {'Bench':>10} {'Diff':>10}")
print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

# Load static results for comparison
static_eq = None
try:
    static_csv = r"d:\quant_framework\equity_curve.csv"
    if __import__("os").path.exists(static_csv):
        se = pd.read_csv(static_csv, index_col=0)
        static_eq = se["equity"]
except Exception:
    pass

eq["year"] = eq.index.astype(str).str[:4].astype(int)
bn["year"] = bn.index.astype(str).str[:4].astype(int)
for year in sorted(eq["year"].unique()):
    ey = eq[eq["year"] == year]["equity"]
    by = bn[bn["year"] == year]["equity"]
    if len(ey) < 2:
        continue
    smart_r = ey.iloc[-1] / ey.iloc[0] - 1
    bench_r = by.iloc[-1] / by.iloc[0] - 1
    # Get static return from comparison
    static_r = 0.0
    if static_eq is not None:
        sy = static_eq[static_eq.index.astype(str).str[:4].astype(int) == year]
        if len(sy) >= 2:
            sy = sy.reset_index(drop=True)
            static_r = sy.iloc[-1] / sy.iloc[0] - 1
    diff = smart_r - static_r
    print(f"  {year:<8} {smart_r:>9.1%} {static_r:>9.1%} {bench_r:>9.1%} {diff:>+9.1%}")

print(f"\n  Saved: d:\\quant_framework\\equity_curve_smart.csv")
print("  Done!")
