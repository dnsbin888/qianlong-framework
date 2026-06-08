"""
因子IC分析 — Information Coefficient / IR / 分层回测
IC = 因子值与前N期收益的相关系数, 衡量因子预测能力
"""
import numpy as np
from collections import defaultdict


def compute_ic_series(factor_values, forward_returns, periods=[1, 5, 20]):
    """
    计算因子IC序列
    factor_values: {date: {symbol: value}}
    forward_returns: {date: {symbol: return}}  forward N-day return
    返回: {period: {date: ic_value}}
    """
    ic_series = {p: {} for p in periods}
    dates = sorted(set(factor_values.keys()) & set(forward_returns.keys()))

    for date in dates:
        fv = factor_values[date]
        fr = forward_returns[date]
        common = set(fv.keys()) & set(fr.keys())
        if len(common) < 30:
            continue

        f_vals = np.array([fv[s] for s in common])
        for p in periods:
            r_vals = np.array([fr[s].get(p, 0) for s in common])
            # Rank IC (Spearman)
            if np.std(f_vals) > 0 and np.std(r_vals) > 0:
                ic = np.corrcoef(f_vals, r_vals)[0, 1]
                if not np.isnan(ic):
                    ic_series[p][date] = round(float(ic), 4)

    return ic_series


def compute_ic_stats(ic_series):
    """计算IC统计: IC均值/ICIR/IC>0比例"""
    stats = {}
    for period, series in ic_series.items():
        vals = list(series.values())
        if not vals:
            continue
        arr = np.array(vals)
        ic_mean = np.mean(arr)
        ic_std = np.std(arr)
        ir = ic_mean / ic_std if ic_std > 0 else 0  # Information Ratio
        ic_positive = sum(1 for v in vals if v > 0) / len(vals)

        stats[period] = {
            'ic_mean': round(float(ic_mean), 4),
            'ic_std': round(float(ic_std), 4),
            'ir': round(float(ir), 4),
            'ic_positive_ratio': round(ic_positive, 4),
            'samples': len(vals),
            'assessment': '强预测' if abs(ic_mean) > 0.05 and ir > 0.5 else (
                '中等预测' if abs(ic_mean) > 0.02 else '弱预测')
        }
    return stats


def layered_backtest(factor_values, forward_returns, n_groups=5):
    """
    分层回测: 按因子值分N组, 计算每组平均收益
    验证因子单调性
    """
    dates = sorted(set(factor_values.keys()) & set(forward_returns.keys()))
    group_returns = defaultdict(list)

    for date in dates:
        fv = factor_values[date]
        fr = forward_returns[date]
        common = list(set(fv.keys()) & set(fr.keys()))
        if len(common) < n_groups * 5:
            continue

        # 按因子值排序分組
        sorted_stocks = sorted(common, key=lambda s: fv.get(s, 0))
        group_size = len(sorted_stocks) // n_groups

        for g in range(n_groups):
            start = g * group_size
            end = start + group_size if g < n_groups - 1 else len(sorted_stocks)
            group_stocks = sorted_stocks[start:end]
            avg_ret = np.mean([fr[s].get(1, 0) for s in group_stocks])  # 1-day forward
            group_returns[g].append(avg_ret)

    # 汇总
    result = {}
    for g in range(n_groups):
        rets = group_returns.get(g, [])
        if rets:
            result[f'Q{g+1}(低)'] if g == 0 else None  # placeholder
            label = f'Q{g+1}' if g > 0 else 'Q1(低)'
            result[label] = {
                'avg_return': round(float(np.mean(rets)) * 100, 2),
                'win_rate': round(sum(1 for r in rets if r > 0) / len(rets), 4),
                'samples': len(rets)
            }

    return result


def analyze_factors_from_cache(factor_cache, stock_data, lookback_days=60):
    """
    从因子缓存+日线数据中提取因子值并计算IC
    """
    from datetime import datetime, timedelta

    # 构建因子值序列: {date: {symbol: power_score}}
    factor_values = defaultdict(dict)
    for s in (factor_cache or []):
        sym = getattr(s, 'symbol', '')
        ps = getattr(s, 'power_score', 0) or 0
        d = getattr(s, 'signal_date', '') or getattr(s, 'entry_time', '')
        if d and len(d) >= 8 and ps > 0:
            date_key = d[:10]
            factor_values[date_key][sym] = ps

    # 构建前向收益: {date: {symbol: {period: return}}}
    forward_returns = defaultdict(lambda: defaultdict(dict))
    for sym, df in (stock_data or {}).items():
        if len(df) < lookback_days + 20:
            continue
        close = df['close'].values
        for i in range(lookback_days, len(close)):
            date_key = str(df.index[i])[:10]
            cur = close[i]
            for p in [1, 5, 20]:
                if i + p < len(close):
                    forward_returns[date_key][sym][p] = (close[i + p] / cur - 1)

    ic_series = compute_ic_series(factor_values, forward_returns)
    stats = compute_ic_stats(ic_series)
    layered = layered_backtest(factor_values, forward_returns)

    return {
        'ic_stats': stats,
        'layered_backtest': layered,
        'interpretation': _interpret(stats, layered)
    }


def _interpret(stats, layered):
    """生成中文解读"""
    ic5 = stats.get(5, {}).get('ic_mean', 0)
    ir5 = stats.get(5, {}).get('ir', 0)

    parts = []
    if abs(ic5) > 0.03:
        parts.append(f"因子IC={ic5:.3f}，具有{'正向' if ic5>0 else '反向'}预测能力")
    else:
        parts.append(f"因子IC={ic5:.3f}，预测能力较弱，建议改进")

    if ir5 > 0.3:
        parts.append(f"ICIR={ir5:.2f}，因子稳定性良好")
    else:
        parts.append(f"ICIR={ir5:.2f}，因子波动较大")

    # 分层检查
    q_returns = [layered[k]['avg_return'] for k in sorted(layered.keys()) if k in layered]
    if len(q_returns) >= 3:
        if q_returns[-1] > q_returns[0]:
            parts.append("分层收益单调递增 ✅ — 因子有效区分股票")
        else:
            parts.append("分层收益不单调 ❌ — 因子区分度不足")

    return '；'.join(parts)
