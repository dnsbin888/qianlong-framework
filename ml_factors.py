"""ML因子 — LGBM/XGBoost评分作为因子注入选股器
用法: factor_registry.json 注册:

  {"name":"ml_lgbm","compute":"ml_factors.factor_ml_lgbm"}
  {"name":"ml_xgb","compute":"ml_factors.factor_ml_xgb"}

选股器调用前需设置 _ctx_symbol (由 generate_signal_table 或 auto脚本处理)
"""
import os, json

_cache = None
_cache_path = os.path.join(os.path.dirname(__file__), "..", "quant_web", "data", "ml_score_cache.json")


def _load():
    global _cache
    if _cache is None and os.path.exists(_cache_path):
        try:
            _cache = json.load(open(_cache_path, encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache or {}


def factor_ml_lgbm(df) -> float | None:
    """LGBM综合评分因子 (来自ML模型)"""
    # 因子接口限制:只能拿到df,拿不到symbol
    # 通过全局上下文获取当前计算的股票代码
    import sys
    ctx = getattr(sys.modules.get('__main__', None), '_ml_ctx_symbol', None)
    if not ctx:
        return None
    cache = _load()
    entry = cache.get(ctx, {})
    return entry.get("lgbm")


def factor_ml_xgb(df) -> float | None:
    """XGBoost综合评分因子 (来自ML模型)"""
    import sys
    ctx = getattr(sys.modules.get('__main__', None), '_ml_ctx_symbol', None)
    if not ctx:
        return None
    cache = _load()
    entry = cache.get(ctx, {})
    return entry.get("xgb")


def update_cache_from_table():
    """从 signal_table.json 刷新缓存"""
    table_path = os.path.join(os.path.dirname(__file__), "..", "quant_web", "data", "signal_table.json")
    if not os.path.exists(table_path):
        return 0
    data = json.load(open(table_path, encoding="utf-8"))
    cache = {}
    for r in data:
        sym = r["symbol"]
        entry = {}
        if r.get("lgbm_score"): entry["lgbm"] = round(r["lgbm_score"], 1)
        if r.get("xgb_score"): entry["xgb"] = round(r["xgb_score"], 1)
        if entry: cache[sym] = entry
    os.makedirs(os.path.dirname(_cache_path), exist_ok=True)
    json.dump(cache, open(_cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    global _cache
    _cache = cache
    return len(cache)
