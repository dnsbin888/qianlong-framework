"""潜龙 → Alphalens 适配器 (v3.0)

将13个因子函数输出转为Alphalens标准格式, 自动生成IC报告。
用法:
  from adapter import run_ic_analysis
  report = run_ic_analysis("defensive_v2", days=120)
"""
import sys, numpy as np, pandas as pd, time as _time
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

_IC_CACHE = {}  # {factor_name: (timestamp, result)}

def factor_to_alphalens(factor_name: str, sample: int = 500, days: int = 120):
    """将单个因子转为Alphalens格式 (factor_data + prices)。

    Returns:
        factor_data: MultiIndex(date, asset) factor值
        prices: 前向收益价格表
        None on failure
    """
    from data_loader import load_stock_data_cache
    from factor_registry import get_all_compute_fns

    parquet_path = r"D:\quant_web\stock_data.parquet"
    stock_data = load_stock_data_cache(parquet_path, keep_days=days)
    if not stock_data:
        return None, None
    # 过滤指数
    stock_data = {k: v for k, v in stock_data.items() if not k.startswith(('sh000','sz399','bj'))}

    compute_fns = get_all_compute_fns()
    fn = compute_fns.get(factor_name)
    if not fn:
        print(f"[Adapter] 因子 {factor_name} 未注册")
        return None, None

    # 取日期最多的股票作为时间轴
    best = max(stock_data.values(), key=lambda df: len(df) if isinstance(df, pd.DataFrame) else 0)
    dates = sorted(set(str(ts)[:10] for ts in best.index))[-days:]
    syms = sorted(stock_data.keys())[:sample * 2]

    factor_rows = []
    price_rows = []

    for date_str in dates:
        pool = []
        for s in syms:
            if s in stock_data:
                try:
                    stock_data[s].index.get_loc(pd.Timestamp(date_str))
                    pool.append(s)
                except KeyError:
                    continue
            if len(pool) >= sample:
                break
        for sym in pool[:sample]:
            df = stock_data[sym]
            try:
                idx = df.index.get_loc(pd.Timestamp(date_str))
            except KeyError:
                continue
            if idx < 20 or idx + 5 >= len(df):
                continue
            past = df.iloc[max(0, idx - 60):idx + 1]
            val = fn(past)
            if val is None or not np.isfinite(val):
                continue
            val = np.clip(float(val), -100, 100)
            fwd_ret = (float(df.iloc[idx + 5]["close"]) - float(df.iloc[idx]["close"])) / max(
                float(df.iloc[idx]["close"]), 0.01)

            factor_rows.append({"date": pd.Timestamp(date_str), "asset": sym, "factor": val})
            price_rows.append({"date": pd.Timestamp(date_str), "asset": sym, "5d_ret": fwd_ret})

    if len(factor_rows) < 30:
        print(f"[Adapter] {factor_name}: 有效样本不足 ({len(factor_rows)})")
        return None, None

    factor_df = pd.DataFrame(factor_rows).set_index(["date", "asset"])
    prices_df = pd.DataFrame(price_rows).pivot(index="date", columns="asset", values="5d_ret")

    return factor_df, prices_df


def run_ic_analysis(factor_name: str, sample: int = 500, days: int = 120):
    """运行 IC分析, 5分钟缓存"""
    cache_key = f"{factor_name}_{sample}_{days}"
    if cache_key in _IC_CACHE:
        ts, result = _IC_CACHE[cache_key]
        if _time.time() - ts < 300:  # 5min cache
            return result
    try:
        from alphalens import performance as perf
        from alphalens import plotting as plot
        from alphalens import utils
    except ImportError:
        print("[Adapter] Alphalens未安装, pip install alphalens-reloaded")
        return None

    factor_data, prices = factor_to_alphalens(factor_name, sample, days)
    if factor_data is None:
        return None

    n = len(factor_data)
    if n < 30:
        return None

    # scipy Spearman 截面IC
    from scipy.stats import spearmanr
    ic_vals = []
    for date in sorted(set(factor_data.index.get_level_values('date'))):
        mask = factor_data.index.get_level_values('date') == date
        if mask.sum() < 10: continue
        fv = factor_data[mask]["factor"].values
        # 取对应日期的前向收益
        dt = pd.Timestamp(date)
        if dt not in prices.index: continue
        rv = prices.loc[dt].dropna().values
        if len(rv) < 10: continue
        # 匹配因子和收益的股票
        common = [a for a in factor_data[mask].index.get_level_values('asset') if a in prices.columns]
        if len(common) < 10: continue
        f_se = factor_data.xs(dt, level='date')["factor"]
        f = f_se.reindex(common).dropna().values
        r = prices.loc[dt, common].dropna().values
        # 取较小长度对齐
        min_len = min(len(f), len(r))
        f, r = f[:min_len], r[:min_len]
        if len(f) < 10 or len(r) < 10: continue
        try:
            ic, _ = spearmanr(f, r)
        except Exception: continue
        ic_vals.append(ic)

    mean_ic = float(np.mean(ic_vals)) if ic_vals else 0
    result = {
        "factor": factor_name,
        "samples": n,
        "IC_5d": round(mean_ic, 4),
        "IC_std": round(float(np.std(ic_vals)), 4) if ic_vals else 0,
        "dates": len(ic_vals),
        "method": "scipy.spearmanr (v3)",
    }
    _IC_CACHE[cache_key] = (_time.time(), result)
    return result
