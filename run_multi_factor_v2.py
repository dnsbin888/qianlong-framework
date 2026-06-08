"""已废弃，请使用 run_factor_backtest_unified.py:

    python run_factor_backtest_unified.py --mode multi --factors trend_bottom,add_position,bull_position,ret_20d --stocks 500

原说明: 多因子选股回测 V2 — 高效版。优化: 对每只股票只计算一次全时间轴因子值，缓存后用于回测。
"""

import sys
sys.path.insert(0, r"d:\quant_framework\src")

import time, random, os, pickle
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.tdx_signals import factor_trend_bottom, factor_add_position
from quant_framework.factors.tdx_signals2 import factor_bull_position
from quant_framework.factors.definitions import FACTOR_MAP

# ======================================================================
# Config
# ======================================================================
TOP_K = 30
MIN_DAYS = 500  # Need decent history for factor computation
N_STOCKS = 99999  # All stocks (cap at available)

FACTOR_SPEC = [
    ("trend_bottom", factor_trend_bottom, +1, 0.40),   # Long: 底部抄底
    ("add_position", factor_add_position, +1, 0.25),    # Long: DX底背离
    ("bull_position", factor_bull_position, -1, 0.25),  # Short: 牛线上方␤空
    ("ret_20d", FACTOR_MAP["ret_20d"].compute, -1, 0.10), # Short: 反转
]

# ======================================================================
print("=" * 65)
print("  Multi-Factor Portfolio Backtest")
print(f"  Stocks: {N_STOCKS} | Top K: {TOP_K} | Monthly Rebalance")
print("=" * 65)

# 1. Load data
print("\n[1/3] Loading & filtering stocks...")
provider = THSDayDataProvider()
provider.connect()
all_syms = provider.scan_symbols()

random.seed(42)
valid = []
for s in all_syms:
    data = provider._read_day_file(s)
    if data and len(data) >= MIN_DAYS:
        valid.append(s)
if len(valid) > N_STOCKS:
    valid = random.sample(valid, N_STOCKS)
print(f"  Using {len(valid)} stocks with >= {MIN_DAYS} days")

# 2. Pre-compute factor time series for each stock
print(f"\n[2/3] Computing factor time series...")
CACHE_FILE = r"d:\quant_framework\factor_cache.pkl"

if os.path.exists(CACHE_FILE):
    print("  Loading from cache...")
    with open(CACHE_FILE, "rb") as f:
        factor_cache = pickle.load(f)
else:
    factor_cache = {}
    t0 = time.time()
    for si, sym in enumerate(valid):
        if si % 100 == 0:
            elapsed = time.time() - t0
            rate = (si + 1) / elapsed if elapsed > 0 else 0
            eta = (len(valid) - si) / rate if rate > 0 else 0
            print(f"  {si}/{len(valid)} rate={rate:.1f}/s ETA={eta:.0f}s")

        data = provider._read_day_file(sym)
        dates = sorted(data.keys())
        records = [{"open": data[d][0], "high": data[d][1], "low": data[d][2],
                     "close": data[d][3], "volume": data[d][5], "amount": data[d][4]} for d in dates]
        df = pd.DataFrame(records)
        if len(df) < 100:
            continue

        # Compute all factors
        factor_vals = {}
        for fname, func, direction, weight in FACTOR_SPEC:
            try:
                result = func(df)
                if isinstance(result, pd.Series):
                    factor_vals[fname] = result.values
                elif isinstance(result, pd.DataFrame):
                    factor_vals[fname] = result.iloc[:, -1].values if result.shape[1] > 0 else None
            except Exception:
                factor_vals[fname] = None

        factor_cache[sym] = {
            "dates": dates,
            "close": [data[d][3] for d in dates],
            "factors": factor_vals,
        }

    with open(CACHE_FILE, "wb") as f:
        pickle.dump(factor_cache, f)
    print(f"  Cached {len(factor_cache)} stocks in {time.time()-t0:.0f}s")

# 3. Portfolio backtest
print(f"\n[3/3] Running portfolio backtest...")

# Find all rebalance dates (month-ends)
all_dates_set = set()
for sym, cache in factor_cache.items():
    for d in cache["dates"]:
        if 20200101 <= d <= 20260601 and len(str(d)) == 8:
            all_dates_set.add(d)
all_dates = sorted(all_dates_set)

# Get last trading day of each month
date_df = pd.DataFrame({"date": all_dates})
date_df["dt"] = pd.to_datetime(date_df["date"].astype(str), format="%Y%m%d")
date_df["ym"] = date_df["dt"].dt.to_period("M")
rebalance_dates = date_df.groupby("ym")["date"].max().tolist()
print(f"  {len(rebalance_dates)} monthly rebalance periods")

# Run backtest
cash = 1_000_000
holdings = {}
equity_curve = []
bench_equity = 1_000_000
bench_curve = []

for ri, rdate in enumerate(rebalance_dates):
    # ---- Compute composite scores for all stocks ----
    scores = {}
    market_prices = []
    for sym, cache in factor_cache.items():
        dates = cache["dates"]
        if rdate not in dates:
            # Find closest previous date
            prev = [d for d in dates if d <= rdate]
            if not prev:
                continue
            idx = len(prev) - 1
        else:
            idx = dates.index(rdate)

        if idx < 100:  # Need enough history for factor computation
            continue

        price = cache["close"][idx]
        if price <= 0:
            continue
        market_prices.append(price)

        # Compute composite score
        score = 0.0
        valid_n = 0
        for fname, func, direction, weight in FACTOR_SPEC:
            fvals = cache["factors"].get(fname)
            if fvals is None or idx >= len(fvals):
                continue
            raw = fvals[idx]
            if raw is None or np.isnan(raw) or np.isinf(raw):
                continue
            raw = np.clip(float(raw), -5, 5)
            score += raw * direction * weight
            valid_n += 1

        if valid_n >= 2:
            scores[sym] = (score, price)

    if len(scores) < TOP_K:
        continue

    # ---- Sell all ----
    for sym, shares in list(holdings.items()):
        if sym in scores:
            cash += shares * scores[sym][1] * 0.9997
        del holdings[sym]

    # ---- Buy Top K ----
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)[:TOP_K]
    alloc = cash * 0.95 / TOP_K
    for sym, (score, price) in ranked:
        shares = int(alloc / price / 100) * 100
        if shares >= 100:
            cost = shares * price * 1.0003
            if cost <= cash:
                cash -= cost
                holdings[sym] = shares

    # ---- Mark-to-market ----
    mkt_val = sum(shares * scores[sym][1] for sym, shares in holdings.items() if sym in scores)
    total = cash + mkt_val
    equity_curve.append({"date": rdate, "equity": total, "n_holdings": len(holdings)})

    # Benchmark: equal-weight all stocks
    if ri > 0 and market_prices:
        avg_ret = np.mean([(scores[s][1] / prev_prices.get(s, scores[s][1]) - 1)
                           for s in scores if s in prev_prices]) if prev_prices else 0
        bench_equity *= (1 + avg_ret)
    bench_curve.append({"date": rdate, "equity": bench_equity})

    prev_prices = {sym: scores[sym][1] for sym in scores}

# ======================================================================
# Results
# ======================================================================
eq = pd.DataFrame(equity_curve).set_index("date")
bn = pd.DataFrame(bench_curve).set_index("date")

if len(eq) < 3:
    print("  ERROR: Not enough periods for analysis")
    sys.exit(1)

total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
bench_ret = bn["equity"].iloc[-1] / bn["equity"].iloc[0] - 1
months = len(eq)
ann_ret = (1 + total_ret) ** (12 / months) - 1
bench_ann = (1 + bench_ret) ** (12 / months) - 1

monthly = eq["equity"].pct_change().dropna()
bench_m = bn["equity"].pct_change().dropna()

sharpe = monthly.mean() / monthly.std() * np.sqrt(12) if monthly.std() > 0 else 0
peak = eq["equity"].expanding().max()
max_dd = ((eq["equity"] - peak) / peak).min()
win_rate = (monthly > 0).mean()

excess = monthly - bench_m
alpha = excess.mean() * 12
ir = alpha / (excess.std() * np.sqrt(12)) if excess.std() > 0 else 0

print(f"\n{'=' * 65}")
print(f"  PORTFOLIO PERFORMANCE")
print(f"{'=' * 65}")
print(f"  {'Metric':<30} {'Portfolio':>15} {'Benchmark':>15}")
print(f"  {'-'*30} {'-'*15} {'-'*15}")
print(f"  {'Total Return':<30} {total_ret:>15.2%} {bench_ret:>15.2%}")
print(f"  {'Annual Return':<30} {ann_ret:>15.2%} {bench_ann:>15.2%}")
print(f"  {'Sharpe Ratio':<30} {sharpe:>15.2f} {'-':>15}")
print(f"  {'Max Drawdown':<30} {max_dd:>15.2%} {'-':>15}")
print(f"  {'Monthly Win Rate':<30} {win_rate:>15.1%} {'-':>15}")
print(f"  {'Alpha (annual)':<30} {alpha:>15.2%} {'-':>15}")
print(f"  {'Information Ratio':<30} {ir:>15.2f} {'-':>15}")
print(f"  {'Total Periods':<30} {months:>15} {'-':>15}")
print(f"  {'Final Equity':<30} {eq['equity'].iloc[-1]:>15,.0f} {bn['equity'].iloc[-1]:>15,.0f}")

# Top holdings
if holdings:
    print(f"\n  Current Holdings (Top 10):")
    for sym, shares in sorted(holdings.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {sym}: {shares} shares")

# Year-by-year
print(f"\n{'=' * 65}")
print(f"  YEAR-BY-YEAR PERFORMANCE")
print(f"{'=' * 65}")
print(f"  {'Year':<8} {'Portfolio':>12} {'Benchmark':>12} {'Excess':>12} {'Win Rate':>10}")
print(f"  {'-'*8} {'-'*12} {'-'*12} {'-'*12} {'-'*10}")
eq["year"] = eq.index.astype(str).str[:4].astype(int)
bn["year"] = bn.index.astype(str).str[:4].astype(int)
for year in sorted(eq["year"].unique()):
    ey = eq[eq["year"] == year]["equity"]
    by = bn[bn["year"] == year]["equity"]
    if len(ey) < 2:
        continue
    port_ret = ey.iloc[-1] / ey.iloc[0] - 1
    bench_ret = by.iloc[-1] / by.iloc[0] - 1
    excess_r = port_ret - bench_ret
    monthly_r = ey.pct_change().dropna()
    wr = (monthly_r > 0).mean()
    print(f"  {year:<8} {port_ret:>11.1%} {bench_ret:>11.1%} {excess_r:>+11.1%} {wr:>10.0%}")

eq.to_csv(r"d:\quant_framework\equity_curve.csv", encoding="utf-8-sig")
print(f"\n  Saved: d:\\quant_framework\\equity_curve.csv")
print("  Done!")
