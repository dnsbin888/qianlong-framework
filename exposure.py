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

if __name__ == "__main__":
    # 快速测试
    m = load_industry_map()
    print(f"行业映射: {len(m)} 只")
    sample = list(m.items())[:10]
    for code, ind in sample:
        print(f"  {code}: {ind}")
