"""验证 P0-因子-01 修复 — 因子按年度切片计算的正确性。

验证内容:
  1. factors_by_year 结构是否正确（每年份有独立的因子数组）
  2. 同一日期在不同年份切片中的因子值（因果操作下应相等）
  3. 边界处因子值（不同年份截断点前后的行为）
"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np
import warnings; warnings.filterwarnings("ignore")

from quant_framework.factors.tdx_signals import factor_trend_bottom, factor_add_position
from quant_framework.factors.tdx_signals2 import factor_bull_position
import pandas as pd

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"

print("=" * 70)
print("  P0-因子-01 验证: 因子分年切片 — 未来函数防护")
print("=" * 70)

# ── 1. 加载缓存 ──
with open(CACHE, "rb") as f:
    cache = pickle.load(f)

# 选一只数据足够长的股票
test_sym = None
for sym, sd in cache.items():
    fby = sd.get("factors_by_year", {})
    if len(fby) >= 4:  # 至少有4个年份的切片
        test_sym = sym
        break

if test_sym is None:
    print("\n  ERROR: No stock with >=4 year slices found. Run build_cache.py first.")
    sys.exit(1)

sd = cache[test_sym]
dates = sd["dates"]
fby = sd["factors_by_year"]
years = sorted(fby.keys())

print(f"\n  Test stock: {test_sym}")
print(f"  Total dates: {len(dates)}")
date_range = f"{str(dates[0])[:4]}-{str(dates[0])[4:6]}-{str(dates[0])[6:8]}"
date_range += f" → {str(dates[-1])[:4]}-{str(dates[-1])[4:6]}-{str(dates[-1])[6:8]}"
print(f"  Date range: {date_range}")
print(f"  Year slices: {years}")
print(f"  Available years: {len(years)}")

# ── 2. 验证结构 ──
print(f"\n  ── Structure Check ──")
all_ok = True
for year in years:
    yf = fby[year]
    for fn in ["trend_bottom", "add_position", "bull_position"]:
        arr = yf.get(fn)
        if arr is None:
            print(f"    Year {year}: {fn} = MISSING!")
            all_ok = False
        elif len(arr) != len(dates):
            print(f"    Year {year}: {fn} length={len(arr)} != dates={len(dates)}")
            all_ok = False
        else:
            valid = np.sum(~np.isnan(arr))
            print(f"    Year {year}: {fn} valid={valid}/{len(arr)} (cutoff at {valid} / {len(arr)})")

if all_ok:
    print(f"\n  ✓ Structure OK: All {len(years)} years × 3 factors present, aligned with dates")

# ── 3. 核心验证: 同一日期在不同年份切片中的因子值 ──
print(f"\n  ── Cross-Year Comparison ──")
print(f"  Compare factor values at the same date across different year slices.")

# 找一个较早的日期，在所有年份切片中都应该有值
# 取数据范围的中间日期
mid_idx = len(dates) // 3
test_date = dates[mid_idx]
test_date_str = f"{str(test_date)[:4]}-{str(test_date)[4:6]}-{str(test_date)[6:8]}"
print(f"\n  Test date: {test_date_str} (index {mid_idx})")
print(f"  {'Year Slice':<12} {'trend_bottom':>14} {'add_position':>14} {'bull_position':>14} {'Diff from prev':>14}")
print(f"  {'-'*12} {'-'*14} {'-'*14} {'-'*14} {'-'*14}")

prev_vals = None
all_same = True
for year in years:
    yf = fby[year]
    tb = yf["trend_bottom"][mid_idx]
    ap = yf["add_position"][mid_idx]
    bp = yf["bull_position"][mid_idx]

    diff_str = ""
    if prev_vals is not None:
        # 检查是否与前一年相同
        if (not np.isnan(tb) and not np.isnan(prev_vals[0]) and abs(tb - prev_vals[0]) > 1e-9):
            all_same = False
            diff_str = f"  TB differs!"
        elif (not np.isnan(ap) and not np.isnan(prev_vals[1]) and abs(ap - prev_vals[1]) > 1e-9):
            all_same = False
            diff_str = f"  AP differs!"
        elif (not np.isnan(bp) and not np.isnan(prev_vals[2]) and abs(bp - prev_vals[2]) > 1e-9):
            all_same = False
            diff_str = f"  BP differs!"
        else:
            diff_str = "same ✓"

    print(f"  {year:<12} {tb:>14.6f} {ap:>14.6f} {bp:>14.6f} {diff_str:>14}")
    prev_vals = (tb, ap, bp)

if all_same:
    print(f"\n  ✓ 因果因子一致性: 所有年份切片同一日期的因子值完全相同")
    print(f"    原因: factor_trend_bottom / add_position / bull_position 全部使用")
    print(f"    因果操作 (ewm/rolling/shift/sma/dma)，不依赖未来数据。")
    print(f"    年份切片机制确保即使未来添加非因果因子，也能安全回测。")

# ── 4. 验证截断边界 ──
print(f"\n  ── Cutoff Boundary Check ──")
for year in years:
    yf = fby[year]
    # 找到该年份因子数组的最后一个有效值位置
    valid_mask = ~np.isnan(yf["trend_bottom"])
    if not np.any(valid_mask):
        print(f"    Year {year}: No valid values!")
        continue
    last_valid = np.where(valid_mask)[0][-1]
    first_nan = last_valid + 1
    last_date = dates[last_valid]
    ld_str = f"{str(last_date)[:4]}-{str(last_date)[4:6]}-{str(last_date)[6:8]}"

    nan_info = ""
    if first_nan < len(dates):
        first_nan_date = dates[first_nan]
        fnd_str = f"{str(first_nan_date)[:4]}-{str(first_nan_date)[4:6]}-{str(first_nan_date)[6:8]}"
        nan_info = f" | First NaN: {fnd_str} (idx {first_nan})"

    print(f"    Year {year}: Last valid={ld_str} (idx {last_valid}){nan_info}")

# ── 5. 本地精确验证: 手动截断 vs factors_by_year ──
print(f"\n  ── Local Precision Check ──")
print(f"  Manually compute factors on truncated data, compare with factors_by_year cache.")

raw_data = {}
for d in dates:
    # reconstruct from cached arrays
    pass  # we already have the raw data in sd

# Find a year to verify
verify_year = years[len(years) // 2]  # middle year
print(f"  Verify year: {verify_year}")

# Get the truncated data (dates up to end of verify_year-1)
cutoff_year = verify_year - 1
cutoff_idx = 0
for i, d in enumerate(dates):
    if int(str(d)[:4]) <= cutoff_year:
        cutoff_idx = i + 1
    else:
        break

print(f"  Data cutoff: year <= {cutoff_year}, {cutoff_idx} days")

if cutoff_idx >= 500:
    # Manually compute
    trunc_df = pd.DataFrame({
        "open": sd["open"][:cutoff_idx],
        "high": sd["high"][:cutoff_idx],
        "low": sd["low"][:cutoff_idx],
        "close": sd["close"][:cutoff_idx],
        "volume": sd["volume"][:cutoff_idx],
        "amount": [0] * cutoff_idx,
    })

    # Compute factors manually
    manual_tb = factor_trend_bottom(trunc_df).values
    manual_ap = factor_add_position(trunc_df).values
    manual_bp = factor_bull_position(trunc_df).values

    # Compare with cached
    cached_tb = fby[verify_year]["trend_bottom"][:cutoff_idx]
    cached_ap = fby[verify_year]["add_position"][:cutoff_idx]
    cached_bp = fby[verify_year]["bull_position"][:cutoff_idx]

    # Align lengths (manual might be slightly different if trunc_df had issues)
    min_len = min(len(manual_tb), len(cached_tb))

    tb_match = np.allclose(manual_tb[:min_len], cached_tb[:min_len], equal_nan=True)
    ap_match = np.allclose(manual_ap[:min_len], cached_ap[:min_len], equal_nan=True)
    bp_match = np.allclose(manual_bp[:min_len], cached_bp[:min_len], equal_nan=True)

    if tb_match and ap_match and bp_match:
        print(f"  ✓ Manual vs Cache: All 3 factors match exactly")
    else:
        if not tb_match:
            diff = np.max(np.abs(manual_tb[:min_len] - cached_tb[:min_len]))
            print(f"  ✗ trend_bottom mismatch, max diff={diff:.6e}")
        if not ap_match:
            diff = np.max(np.abs(manual_ap[:min_len] - cached_ap[:min_len]))
            print(f"  ✗ add_position mismatch, max diff={diff:.6e}")
        if not bp_match:
            diff = np.max(np.abs(manual_bp[:min_len] - cached_bp[:min_len]))
            print(f"  ✗ bull_position mismatch, max diff={diff:.6e}")
else:
    print(f"  ⚠ Not enough data for manual verification (need 500, got {cutoff_idx})")

# ── 6. get_factor_for_date 测试 ──
print(f"\n  ── get_factor_for_date() Test ──")
from quant_framework.factors.factor_utils import get_factor_for_date, has_year_sliced_factors, available_years

has = has_year_sliced_factors(sd)
yrs = available_years(sd)
print(f"  has_year_sliced_factors: {has}")
print(f"  available_years: {yrs}")

# Test getting factor values for a date in each year
test_years = yrs
for year in test_years:
    # Find a date in this year
    year_dates = [d for d in dates if int(str(d)[:4]) == year]
    if not year_dates:
        year_dates = [d for d in dates if int(str(d)[:4]) <= year and int(str(d)[:4]) >= year - 1]
    if year_dates:
        td = year_dates[0]
        tb = get_factor_for_date(sd, td, "trend_bottom")
        ap = get_factor_for_date(sd, td, "add_position")
        bp = get_factor_for_date(sd, td, "bull_position")
        td_str = f"{str(td)[:4]}-{str(td)[4:6]}-{str(td)[6:8]}"
        print(f"  {td_str} (year {year}): tb={tb:.4f}, add={ap:.0f}, bp={bp:.4f}")

print(f"\n{'=' * 70}")
print(f"  Verification complete.")
print(f"{'=' * 70}")
