"""已废弃，请使用 run_factor_backtest_unified.py:

    python run_factor_backtest_unified.py --mode single --factor trend_bottom --stocks 1500

原说明: 因子IC回测 — 用同花顺真实数据，对43个因子跑时间序列IC分析。
由于THS数据较稀疏（非统一的日线对齐），采用:
  时间序列IC = 对每只股票，计算因子值与未来收益的 Spearman 秩相关
  然后汇总所有股票的IC统计
"""

import sys
sys.path.insert(0, r"d:\quant_framework\src")

import time
import numpy as np
import pandas as pd
from scipy import stats

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.definitions import FACTOR_LIBRARY, FACTOR_MAP
from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS

# ======================================================================
# 1. 数据加载
# ======================================================================
print("=" * 60)
print("  Factor IC Backtest — Time-Series Method")
print("=" * 60)

print("\n[1/3] Loading TDX data...")
provider = THSDayDataProvider()  # Auto-detect TDX vipdoc
provider.connect()
all_symbols = provider.scan_symbols()

MIN_DAYS = 80
valid_symbols = []
import random
random.seed(42)
for sym in all_symbols:
    data = provider._read_day_file(sym)
    if data and len(data) >= MIN_DAYS:
        valid_symbols.append(sym)

# Sample for speed — use all if <2000, otherwise random 1500
if len(valid_symbols) > 1500:
    valid_symbols = random.sample(valid_symbols, 1500)
    print(f"  Sampling 1500 stocks for speed")

print(f"  Valid symbols (>={MIN_DAYS} days): {len(valid_symbols)}")

# ======================================================================
# 2. Select factors to test
# ======================================================================
ALL_FACTORS: dict[str, dict] = {}
for f in FACTOR_LIBRARY:
    ALL_FACTORS[f.name] = {"name": f.name, "label": f.label, "compute": f.compute,
                           "direction": f.direction, "category": f.category.value}
for name, info in TDX_SIGNAL_FACTORS.items():
    ALL_FACTORS[name] = info
for name, info in TDX2_SIGNAL_FACTORS.items():
    ALL_FACTORS[name] = info

# Core builtin factors + all TDX factors
TEST_FACTORS = [
    # Builtin (15 core)
    "ret_5d", "ret_20d", "ret_1d", "max_ret_20d", "min_ret_20d",
    "vol_20d", "skewness_20d",
    "turnover_5d", "volume_ratio_5d",
    "rsi_14", "ma_dev_20", "ma_cross_5_20", "bias_20", "up_days_ratio_20", "price_position_20",
    "amplitude_5d", "volume_price_corr_20",
    # All TDX signals
    "tdx_qlj", "tdx_ztxf", "tdx_qbd", "tdx_qz", "tdx_hmqd",
    "tdx_dmi", "tdx_trend_bottom", "tdx_money_flow", "tdx_high_control",
    "tdx_add_position", "tdx_caishen", "tdx_bbiboll", "tdx_red_line",
    "tdx2_xg", "tdx2_bb", "tdx2_t", "tdx2_b1", "tdx2_final", "tdx2_bull_line",
]

print(f"  Testing {len(TEST_FACTORS)} factors on {len(valid_symbols)} stocks")

# ======================================================================
# 3. Time-series IC per stock
# ======================================================================
print(f"\n[2/3] Computing time-series IC per stock...")
t0 = time.time()

# IC results: {factor_name: [IC_1d_list, IC_5d_list, IC_20d_list]}
ic_collector: dict[str, dict] = {f: {"ic_1d": [], "ic_5d": [], "ic_20d": [], "n_stocks": 0, "n_pairs": 0} for f in TEST_FACTORS}

for si, sym in enumerate(valid_symbols):
    if si % 50 == 0:
        elapsed = time.time() - t0
        rate = (si + 1) / elapsed if elapsed > 0 else 0
        eta = (len(valid_symbols) - si) / rate if rate > 0 else 0
        print(f"  {si}/{len(valid_symbols)} ({si/len(valid_symbols)*100:.0f}%) rate={rate:.1f}/s ETA={eta:.0f}s")

    # Load stock data
    data = provider._read_day_file(sym)
    if not data or len(data) < MIN_DAYS:
        continue

    dates = sorted(data.keys())
    records = []
    for d in dates:
        o, h, l, c, amt, vol = data[d]
        if o <= 0 or c <= 0:
            continue
        records.append({"open": o, "high": h, "low": l, "close": c, "amount": amt, "volume": vol})

    if len(records) < MIN_DAYS:
        continue

    df = pd.DataFrame(records)

    # Compute all factors as time series
    factor_ts: dict[str, pd.Series] = {}
    for fname in TEST_FACTORS:
        fdef = ALL_FACTORS.get(fname)
        if fdef is None:
            continue
        try:
            result = fdef["compute"](df)
            if isinstance(result, pd.Series):
                factor_ts[fname] = result
            elif isinstance(result, pd.DataFrame):
                factor_ts[fname] = result.iloc[:, -1] if not result.empty else pd.Series(dtype=float)
        except Exception:
            continue

    # Compute forward returns
    close = df["close"]
    ret_1d = close.pct_change(1).shift(-1)   # Next period return
    ret_5d = close.pct_change(5).shift(-5)
    ret_20d = close.pct_change(20).shift(-20)

    # For each factor, compute Spearman correlation with forward returns
    for fname, fseries in factor_ts.items():
        fseries = fseries.dropna()
        if len(fseries) < 20:
            continue

        # Align with returns
        for ret_name, ret_series in [("ic_1d", ret_1d), ("ic_5d", ret_5d), ("ic_20d", ret_20d)]:
            aligned = pd.concat([fseries, ret_series], axis=1).dropna()
            if len(aligned) < 15:
                continue

            try:
                with np.errstate(invalid='ignore'):
                    ic, pvalue = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
                if not np.isnan(ic):
                    ic_collector[fname][ret_name].append(ic)
                    ic_collector[fname]["n_pairs"] += len(aligned)
            except Exception:
                continue

        ic_collector[fname]["n_stocks"] += 1

print(f"  Done in {time.time() - t0:.1f}s")

# ======================================================================
# 4. Summarize results
# ======================================================================
print(f"\n[3/3] Summarizing IC results...")

summary_rows = []
for fname, collected in ic_collector.items():
    for period in ["ic_1d", "ic_5d", "ic_20d"]:
        ic_list = collected[period]
        if len(ic_list) < 5:
            continue

        ic_arr = np.array(ic_list)
        ic_mean = np.mean(ic_arr)
        ic_std = np.std(ic_arr)
        icir = ic_mean / ic_std if ic_std > 0 else 0
        ic_pos = (ic_arr > 0).mean()

        fdef = ALL_FACTORS.get(fname, {})
        direction = fdef.get("direction", 1)
        # If factor is supposed to be negative, flip sign for ICIR comparison
        effective_icir = abs(icir)

        summary_rows.append({
            "factor": fname,
            "label": fdef.get("label", fname),
            "category": fdef.get("category", "?"),
            "period": period,
            "n_stocks": collected["n_stocks"],
            "n_obs": collected["n_pairs"],
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "icir": icir,
            "abs_icir": effective_icir,
            "ic_pos_pct": ic_pos,
            "direction": direction,
        })

summary = pd.DataFrame(summary_rows)
if summary.empty:
    print("  ERROR: No valid IC results!")
    sys.exit(1)

# ======================================================================
# 5. Output results
# ======================================================================
print(f"\n{'=' * 85}")
print(f"  FACTOR IC BACKTEST RESULTS — Time-Series Method")
print(f"  {len(valid_symbols)} stocks, {summary['n_obs'].sum()} total observations")
print(f"{'=' * 85}")

# Best by ret_5d ICIR
for period in ["ic_1d", "ic_5d", "ic_20d"]:
    period_df = summary[summary["period"] == period].sort_values("abs_icir", ascending=False)
    if period_df.empty:
        continue

    period_label = {"ic_1d": "1-day", "ic_5d": "5-day", "ic_20d": "20-day"}[period]
    print(f"\n--- Top 10 Factors by |ICIR| — {period_label} Forward Return ---")
    print(f"{'Rank':<5} {'Factor':<24} {'Label':<22} {'nStock':>7} {'IC_mean':>8} {'ICIR':>7} {'IC>0%':>7}")
    print(f"{'-'*5} {'-'*24} {'-'*22} {'-'*7} {'-'*8} {'-'*7} {'-'*7}")

    for rank, (_, row) in enumerate(period_df.head(10).iterrows(), 1):
        print(f"{rank:<5} {row['factor']:<24} {str(row['label'])[:21]:<22} {row['n_stocks']:>7} {row['ic_mean']:>8.4f} {row['icir']:>7.2f} {row['ic_pos_pct']:>7.1%}")

# Highlight TDX factors
print(f"\n{'=' * 85}")
print(f"  TDX Signal Factors (Your Custom Formulas)")
print(f"{'=' * 85}")
print(f"{'Factor':<24} {'Label':<22} {'IC_5d_mean':>10} {'ICIR_5d':>8} {'IC>0%':>7} {'Stocks':>7}")
print(f"{'-'*24} {'-'*22} {'-'*10} {'-'*8} {'-'*7} {'-'*7}")

tdx_summary = summary[summary["factor"].str.startswith("tdx") & (summary["period"] == "ic_5d")].sort_values("abs_icir", ascending=False)
for _, row in tdx_summary.iterrows():
    print(f"{row['factor']:<24} {str(row['label'])[:21]:<22} {row['ic_mean']:>10.4f} {row['icir']:>8.2f} {row['ic_pos_pct']:>7.1%} {row['n_stocks']:>7}")

# Save
output_path = r"d:\quant_framework\factor_ic_results.csv"
summary.to_csv(output_path, index=False, encoding="utf-8-sig")
print(f"\nFull results saved to: {output_path}")
print("Backtest complete!")

