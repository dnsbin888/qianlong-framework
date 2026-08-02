"""三模型投票 v1.0 — LGBM + XGBoost + CatBoost
2/3 同意才进信号池, 降低假阳性
"""
import sys, os
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")


def generate_consensus_signals(stock_data, top_k=15, min_models=1):
    """三模型投票: 2/3同意才出信号

    Returns:
        [{symbol, score, models, buy_signal, ...}]
    """
    signals = {}
    model_votes = {}

    # LGBM
    try:
        from lgbm_strategy import generate_lgbm_signals, is_model_ready
        if is_model_ready():
            for s in generate_lgbm_signals(stock_data, top_k=top_k, min_score=30):
                sym = s["symbol"]
                signals.setdefault(sym, {"symbol": sym, "close": s["close"], "scores": [], "models": []})
                signals[sym]["scores"].append(s["score"])
                signals[sym]["models"].append("LGBM")
                model_votes[f"{sym}_LGBM"] = s
    except Exception as e:
        print(f"[Vote] LGBM: {e}")

    # XGBoost
    try:
        from xgb_factor_weight import generate_xgb_signals, is_ready
        if is_ready():
            for s in generate_xgb_signals(stock_data, top_k=top_k, min_score=30):
                sym = s["symbol"]
                signals.setdefault(sym, {"symbol": sym, "close": s["close"], "scores": [], "models": []})
                signals[sym]["scores"].append(s["score"])
                signals[sym]["models"].append("XGBoost")
                model_votes[f"{sym}_XGB"] = s
    except Exception as e:
        print(f"[Vote] XGBoost: {e}")

    # CatBoost — DEPRECATED 2026-07-12 (标签退化+stacking泄露)
    # 阶段4 以 Meta裁判 回归，当前不可用于选股评分
    # 模型文件 catboost_model.cbm 已删除，此块自动跳过
    try:
        from train_catboost import generate_catboost_signals
        import os as _os
        if _os.path.exists(r"D:\quant_framework\catboost_model.cbm"):
            for s in generate_catboost_signals(stock_data, top_k=top_k, min_score=30):
                sym = s["symbol"]
                signals.setdefault(sym, {"symbol": sym, "close": s["close"], "scores": [], "models": []})
                signals[sym]["scores"].append(s["score"])
                signals[sym]["models"].append("CatBoost")
                model_votes[f"{sym}_CB"] = s
    except Exception as e:
        print(f"[Vote] CatBoost: {e}")

    # 投票: min_models 个模型同意才通过
    consensus = []
    for sym, info in signals.items():
        n_models = len(info["models"])
        if n_models >= min_models:
            avg_score = sum(info["scores"]) / len(info["scores"])
            # 信号等级: 根据平均分
            lv = 5 if avg_score >= 90 else 4 if avg_score >= 80 else 3 if avg_score >= 70 else 2 if avg_score >= 60 else 1
            info["buy_signal"] = lv
            info["score"] = round(avg_score, 1)
            info["n_models"] = n_models
            info["stop_loss"] = round(info["close"] * 0.97, 2)
            info["take_profit"] = [round(info["close"] * 1.05, 2), round(info["close"] * 1.10, 2)]
            info["strategy"] = f"TripleVote-{n_models}"
            consensus.append(info)

    consensus.sort(key=lambda x: -x["score"])
    result = consensus[:top_k]

    if result:
        top_n = sum(1 for s in result if len(s["models"]) >= 3)
        print(f"[TripleVote] {len(result)}共识信号 (3模型={top_n}, 2模型={len(result)-top_n})")

    return result


if __name__ == "__main__":
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    if sd:
        sigs = generate_consensus_signals(sd, top_k=15)
        for s in sigs[:10]:
            print(f"  {s['symbol']} 评分{s['score']:.0f} Lv{s['buy_signal']} {s['n_models']}模型 {'|'.join(s['models'])}")
        print(f"\n✅ 三模型投票就绪 ({len(sigs)}信号)\n")
