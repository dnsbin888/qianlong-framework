"""行业中性化 (P8-1: 对标幻方/九坤)
方法: 行业内截面标准化 → 消除行业偏差
步骤:
  1. 读 factor_registry + stock_names (含行业映射)
  2. 按行业分组，每组内 z-score 标准化因子值
  3. 输出中性化后的因子值
"""
import json, os, numpy as np
from collections import defaultdict

NAMES_PATH = r"D:\quant_web\stock_names_full.csv"
REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"

def load_industry_map() -> dict[str, str]:
    """加载 stock→行业 映射"""
    # 优先从 JSON (已有 _INDUSTRY_MAP)
    from sys import path as _sp
    _sp.insert(0, r"D:\quant_web")
    try:
        from app import _INDUSTRY_MAP
        if _INDUSTRY_MAP:
            return _INDUSTRY_MAP
    except: pass
    # 兜底: 从 CSV
    if os.path.exists(NAMES_PATH):
        import csv
        m = {}
        with open(NAMES_PATH, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                code = row.get('code','')
                ind = row.get('industry','') or row.get('sector','')
                if code and ind: m[code] = ind
        return m
    return {}

def sector_neutralize(symbols: list[str], raw_scores: list[float]) -> list[float]:
    """行业内 z-score 标准化。

    Args:
        symbols: 股票代码列表
        raw_scores: 原始因子值 (与 symbols 同序)

    Returns:
        行业中性化后的因子值
    """
    ind_map = load_industry_map()
    if not ind_map:
        return raw_scores  # 无行业数据 → 不做中性化

    # 按行业分组
    groups = defaultdict(list)
    for sym, score in zip(symbols, raw_scores):
        code = sym.replace('sh','').replace('sz','')
        ind = ind_map.get(code, '') or ind_map.get(sym, '') or '未分类'
        groups[ind].append(score)

    # 每组 z-score
    neutral = []
    for sym, score in zip(symbols, raw_scores):
        code = sym.replace('sh','').replace('sz','')
        ind = ind_map.get(code, '') or ind_map.get(sym, '') or '未分类'
        vals = np.array(groups[ind])
        if len(vals) >= 3 and np.std(vals) > 1e-8:
            neutral.append(float((score - np.mean(vals)) / np.std(vals)))
        else:
            neutral.append(score)  # 样本太少 → 保持原值

    return neutral

def rank_neutralize(symbols: list[str], raw_scores: list[float]) -> list[float]:
    """行业内百分位排名 (更稳健, 不受极端值影响)"""
    ind_map = load_industry_map()
    if not ind_map:
        return raw_scores

    groups = defaultdict(list)
    for sym, score in zip(symbols, raw_scores):
        code = sym.replace('sh','').replace('sz','')
        ind = ind_map.get(code, '') or ind_map.get(sym, '') or '未分类'
        groups[ind].append(score)

    neutral = []
    for sym, score in zip(symbols, raw_scores):
        code = sym.replace('sh','').replace('sz','')
        ind = ind_map.get(code, '') or ind_map.get(sym, '') or '未分类'
        vals = sorted(groups[ind])
        if len(vals) >= 3:
            pct = (len(vals) - 1 - vals.index(score)) / max(len(vals) - 1, 1) if score in vals else 0.5
            neutral.append(pct * 100)
        else:
            neutral.append(score)

    return neutral

# --- 市值中性化 ---

def _get_market_cap(sym: str, sd: dict = None) -> float:
    """获取股票近似的总市值 (亿元)
    优先用 total_shares*close, 其次 outstanding*close, 都没有则用 日均成交额*50 代理
    (成交额≈市值×换手率2%, 所以市值≈成交额×50)
    """
    try:
        if sd and sym in sd:
            df = sd[sym]
            close = float(df['close'].values[-1])
            if 'total_shares' in df.columns:
                shares = float(df['total_shares'].values[-1])
                return close * shares / 1e8
            elif 'outstanding' in df.columns:
                shares = float(df['outstanding'].values[-1]) * 1.5
                return close * shares / 1e8
            else:
                # 代理: 日均成交额 × 50 ≈ 总市值
                vol = df['volume'].values[-20:]
                avg_amt = float(np.nanmean(vol * close)) if len(vol) > 0 else 0
                if avg_amt > 0:
                    return avg_amt * 50 / 1e8  # 转为亿元
    except Exception:
        pass
    return 0


def market_cap_neutralize(symbols: list, raw_scores: list,
                          sd: dict = None) -> list:
    """市值中性化: 对 ML 得分做 ln(市值) 回归, 取残差

    原理: ML得分 = α + β × ln(市值) + ε
          中性化得分 = ε (残差)
          大票不再系统性排前面, 小票好票也有机会
    """
    if len(symbols) < 10:
        return raw_scores

    caps = []
    for sym in symbols:
        mc = _get_market_cap(sym, sd)
        caps.append(mc if mc > 0 else np.nan)

    caps = np.array(caps)
    scores = np.array(raw_scores, dtype=float)

    valid = ~np.isnan(caps) & (caps > 0)
    if valid.sum() < 10:
        return raw_scores

    log_cap = np.log(caps[valid])
    y = scores[valid]

    X = np.vstack([np.ones(len(log_cap)), log_cap]).T
    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        predicted = beta[0] + beta[1] * log_cap
        residuals = y - predicted
        result = scores.copy()
        result[valid] = residuals + np.mean(y)
        return result.tolist()
    except Exception:
        return raw_scores


if __name__ == "__main__":
    m = load_industry_map()
    print(f"行业映射: {len(m)} 只")
    sample = list(m.items())[:10]
    for code, ind in sample:
        print(f"  {code}: {ind}")
    print("\n✅ exposure.py 就绪 (行业中性化 + 市值中性化)")
