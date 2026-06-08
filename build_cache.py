"""重新生成因子缓存 — 包含OHLCV + 趋势线底部/加仓信号/牛线位置

P0-因子-01: 修复未来函数 — 因子按年度切片，每年只使用前N-1年的数据计算。
     因子值存储在 factors_by_year[year] 中，回测时按当前日期取对应年份的因子值，
     确保不会看到未来数据。
"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, time, random, numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.tdx_signals import factor_trend_bottom, factor_add_position
from quant_framework.factors.tdx_signals2 import factor_bull_position

N_SAMPLE = 2000
MIN_DAYS = 500
OUT = r"d:\quant_framework\cache_ohlcv.pkl"

# 待计算的因子列表 (名称, 计算函数)
FACTOR_FUNCS = [
    ("trend_bottom", factor_trend_bottom),
    ("add_position", factor_add_position),
    ("bull_position", factor_bull_position),
]


def _compute_factors_on_data(df: pd.DataFrame) -> dict:
    """在一份 DataFrame 上计算全部因子，返回 {name: np.array}。
    所有因子只使用因果操作 (ewm/rolling/shift/sma/dma)，天然无未来函数。
    """
    factors = {}
    for fname, func in FACTOR_FUNCS:
        try:
            r = func(df)
            factors[fname] = r.values if isinstance(r, pd.Series) else np.array([])
        except Exception:
            factors[fname] = np.array([])
    return factors


def _build_factors_by_year(dates: list, df: pd.DataFrame) -> dict:
    """按年度切片计算因子，消除未来函数风险。

    对每个年份 Y，截取 <= Y-1 年底的数据独立计算因子，
    然后将结果数组填充到与完整 dates 对齐的长度。
    超出截断日期的位置填 NaN。

    Args:
        dates: 排序后的完整日期列表 (int, YYYYMMDD)
        df: 完整的 OHLCV DataFrame (与 dates 等长)

    Returns:
        {year: {factor_name: np.array}}  每个 array 长度 = len(dates)
    """
    factors_by_year = {}

    if len(dates) < MIN_DAYS:
        return factors_by_year

    # 提取所有出现的年份
    all_years = sorted(set(int(str(d)[:4]) for d in dates))

    for year in all_years:
        cutoff_year = year - 1
        # 找出截断点: <= cutoff_year 年底的日期数量
        cutoff_count = 0
        for d in dates:
            if int(str(d)[:4]) <= cutoff_year:
                cutoff_count += 1
            else:
                break

        if cutoff_count < MIN_DAYS:
            continue  # 历史数据不足，跳过该年份

        # 截取数据
        trunc_df = df.iloc[:cutoff_count]

        # 计算因子
        factors = _compute_factors_on_data(trunc_df)

        # 填充到与完整 dates 对齐: 截断日期之后填 NaN
        padded = {}
        for fname, arr in factors.items():
            if len(arr) == 0:
                padded[fname] = np.full(len(dates), np.nan)
            else:
                full = np.full(len(dates), np.nan)
                full[:len(arr)] = arr
                padded[fname] = full

        factors_by_year[year] = padded

    return factors_by_year


# ======================================================================
# 主流程
# ======================================================================

print("Building OHLCV factor cache (future-function-safe, year-sliced)...")
provider = THSDayDataProvider()
provider.connect()
all_syms = provider.scan_symbols()

random.seed(42)
valid = [s for s in all_syms if len(provider._read_day_file(s)) >= MIN_DAYS]
if len(valid) > N_SAMPLE:
    valid = random.sample(valid, N_SAMPLE)

print(f"Processing {len(valid)} stocks...")
cache = {}
t0 = time.time()

for si, sym in enumerate(valid):
    if si % 500 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (si + 1) * (len(valid) - si) if si > 0 else 0
        print(f"  {si}/{len(valid)}  elapsed={elapsed:.0f}s  eta={eta:.0f}s")

    raw = provider._read_day_file(sym)
    dates = sorted(raw.keys())
    o_arr = [raw[d][0] for d in dates]
    h_arr = [raw[d][1] for d in dates]
    l_arr = [raw[d][2] for d in dates]
    c_arr = [raw[d][3] for d in dates]
    v_arr = [raw[d][5] for d in dates]

    df = pd.DataFrame({"open": o_arr, "high": h_arr, "low": l_arr,
                       "close": c_arr, "volume": v_arr, "amount": [0] * len(dates)})

    # ── P0-因子-01: 按年度切片计算因子 ──
    factors_by_year = _build_factors_by_year(dates, df)

    # 兼容旧格式: factors 指向最近一年的因子计算结果
    if factors_by_year:
        latest_year = max(factors_by_year.keys())
        legacy_factors = {}
        for fname in factors_by_year[latest_year]:
            legacy_factors[fname] = factors_by_year[latest_year][fname]
    else:
        # 数据不足，用全量兜底
        legacy_factors = _compute_factors_on_data(df)

    cache[sym] = {
        "dates": dates,
        "open": o_arr, "high": h_arr, "low": l_arr,
        "close": c_arr, "volume": v_arr,
        "factors_by_year": factors_by_year,   # 新增: 分年因子
        "factors": legacy_factors,             # 兼容旧代码
    }

with open(OUT, "wb") as f:
    pickle.dump(cache, f)

# ── 统计输出 ──
n_with_years = sum(1 for v in cache.values() if v["factors_by_year"])
year_counts = [len(v["factors_by_year"]) for v in cache.values() if v["factors_by_year"]]
print(f"Done: {len(cache)} stocks in {time.time() - t0:.0f}s → {OUT}")
if year_counts:
    print(f"  Year slices per stock: min={min(year_counts)}, max={max(year_counts)}, "
          f"avg={np.mean(year_counts):.1f}")
    # 显示年范围
    all_years = set()
    for v in cache.values():
        all_years.update(v["factors_by_year"].keys())
    print(f"  Coverage years: {sorted(all_years)}")
