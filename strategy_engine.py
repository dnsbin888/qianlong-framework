"""strategy_engine.py — 统一信号生成服务 (Plan I)
行业对标: QuantConnect Lean Alpha模块
单一信号源, 策略构建器驱动, 纸引擎/实盘共用

用法:
  from strategy_engine import generate_for_paper  # 纸引擎
  from strategy_engine import generate_for_live   # 实盘
"""
import json, os, sys
import numpy as np


def _load_active_strategy(target: str = "sim") -> dict | None:
    """读活跃策略: sim=模拟盘, real=实盘。优先取 _evo_applied 的"""
    sp = r"D:\quant_framework\user_customizations\user_strategies.json"
    if not os.path.exists(sp):
        return None
    data = json.load(open(sp, "r", encoding="utf-8"))
    candidates = [s for s in data.get("strategies", [])
                  if s.get("status") == target and s.get("type") == "builder" and s.get("factors")]
    if not candidates:
        return None
    evo = [s for s in candidates if s.get("_evo_applied")]
    return (evo or candidates)[0]


def _load_all_active_strategies(target: str = "sim") -> list[dict]:
    """读所有活跃策略。支持多策略并行。"""
    sp = r"D:\quant_framework\user_customizations\user_strategies.json"
    if not os.path.exists(sp):
        return []
    data = json.load(open(sp, "r", encoding="utf-8"))
    return [s for s in data.get("strategies", [])
            if s.get("status") in (target, "sim_running") and s.get("type") == "builder" and s.get("factors")]


def _map_score_to_signal(score: float) -> int:
    """策略得分(0-100) → 信号等级(0-5)"""
    if score >= 90: return 5
    if score >= 80: return 4
    if score >= 70: return 3
    if score >= 60: return 2
    if score >= 40: return 1
    return 0


def generate(strategy: dict, stock_data: dict, top_k: int = 20) -> list[dict]:
    """批量生成信号 — 对全市场评估单个策略

    Args:
        strategy: 策略定义 (含 factors, trigger, stop_loss, take_profit, hold_days)
        stock_data: {symbol: DataFrame} 全集
        top_k: 返回前K个信号

    Returns:
        [{symbol, buy_signal, close, score, name, stop_loss, take_profit, strategy}]
    """
    from strategy_builder import evaluate_strategy
    from factor_registry import get_all_compute_fns
    compute_fns = get_all_compute_fns()

    results = []
    syms = list(stock_data.keys())
    for sym in syms[:500]:  # 只扫前500只，传预加载数据
        try:
            sig = evaluate_strategy(strategy, sym, compute_fns, stock_data=stock_data)
            if sig and sig.get("signal") == "buy":
                df = stock_data.get(sym)
                close = float(df["close"].iloc[-1]) if df is not None and len(df) > 0 else 0
                results.append({
                    "symbol": sym,
                    "buy_signal": _map_score_to_signal(sig["score"]),
                    "close": close,
                    "score": sig["score"],
                    "name": "",
                    "stop_loss": sig.get("stop_loss", close * 0.97),
                    "take_profit": sig.get("take_profit", [close * 1.05]),
                    "strategy": sig.get("strategy", strategy.get("name", "")),
                })
        except Exception:
            continue

    # 按score降序, 取top_k
    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


def generate_for_paper(stock_data: dict, top_k: int = 15) -> list[dict]:
    """纸引擎用: 取 ALL status=sim 的策略，每个出 top_k 信号，去重合并。"""
    all_strategies = _load_all_active_strategies("sim")
    if not all_strategies:
        return _fallback_signals(stock_data, top_k)
    all_signals = []
    seen = set()
    for strategy in all_strategies:
        try:
            per_k = max(5, top_k // len(all_strategies))
            signals = generate(strategy, stock_data, per_k)
            for s in signals:
                key = s["symbol"]
                if key not in seen:
                    seen.add(key)
                    all_signals.append(s)
            if signals:
                print(f"[StrategyEngine] {strategy['name']}: {len(signals)}信号")
        except Exception as e:
            print(f"[StrategyEngine] {strategy['name']} 失败: {e}")
    if all_signals:
        print(f"[StrategyEngine] 合并: {len(all_signals)}信号 (来自{len(all_strategies)}策略)")
        return sorted(all_signals, key=lambda x: -x["score"])[:top_k]
    return _fallback_signals(stock_data, top_k)


def generate_lgbm_for_paper(stock_data: dict, top_k: int = 15) -> list[dict]:
    """纸引擎用: LightGBM 模型信号 (ML 驱动)。

    当 LGBM 模型已训练时优先使用；模型未就绪时自动降级到老信号源。
    """
    try:
        from lgbm_strategy import is_model_ready, generate_lgbm_signals
        if is_model_ready():
            signals = generate_lgbm_signals(stock_data, top_k=top_k)
            if signals:
                print(f"[StrategyEngine] LGBM信号: {len(signals)}只")
                return signals
            print("[StrategyEngine] LGBM模型就绪但无信号，降级老源...")
        else:
            print("[StrategyEngine] LGBM模型未训练，降级老源...")
    except Exception as e:
        print(f"[StrategyEngine] LGBM信号失败: {e}，降级老源...")

    # 降级: 老因子源
    return generate_for_paper(stock_data, top_k)


def generate_xgb_for_paper(stock_data: dict, top_k: int = 15) -> list[dict]:
    """纸引擎用: XGBoost 模型信号 (ML 驱动, 8因子OHLCV代理特征)。

    当 XGBoost 模型已训练时使用；模型未就绪时自动降级到 LGBM → 老源。
    """
    try:
        from xgb_factor_weight import is_ready, generate_xgb_signals
        if is_ready():
            signals = generate_xgb_signals(stock_data, top_k=top_k)
            if signals:
                print(f"[StrategyEngine] XGBoost信号: {len(signals)}只")
                return signals
            print("[StrategyEngine] XGBoost模型就绪但无信号，降级LGBM...")
        else:
            print("[StrategyEngine] XGBoost模型未训练，降级LGBM...")
    except Exception as e:
        print(f"[StrategyEngine] XGBoost信号失败: {e}，降级LGBM...")

    # 降级: LGBM → 老因子源
    return generate_lgbm_for_paper(stock_data, top_k)


def generate_ml_v2(stock_data: dict, top_k: int = 15, paper_status: dict = None) -> list[dict]:
    """V2 增强信号链: 三模型投票 → B2-B6 决策适配

    三模型 (LGBM+XGBoost+CatBoost) 投票 → HRP仓位 → ATR滑点 → 行业限制 → 市场自适应
    """
    # Step 1: 三模型投票
    try:
        from triple_vote import generate_consensus_signals
        signals = generate_consensus_signals(stock_data, top_k=top_k)
        if not signals:
            return generate_ml_for_paper(stock_data, top_k)
    except Exception as e:
        print(f"[StrategyEngine] 三模型投票失败: {e}, 降级")
        return generate_ml_for_paper(stock_data, top_k)

    # Step 2: B2-B6 决策适配
    try:
        from decision_adapter import process_signals
        ps = paper_status or {"total_equity": 1_000_000, "positions": []}
        orders = process_signals(signals, stock_data, ps)
        if orders:
            print(f"[StrategyEngine] ML-V2: {len(orders)}订单 (三模型+B2-B6)")
            return orders
    except Exception as e:
        print(f"[StrategyEngine] 决策适配失败: {e}")

    return signals  # 降级: 返回未适配的信号


def generate_ml_for_paper(stock_data: dict, top_k: int = 15) -> list[dict]:
    """纸引擎用: 双 ML 模型信号链 (LGBM → XGBoost → 老因子源)。

    优先 LGBM (16因子非线性回归)，其次 XGBoost (8因子分类)，
    都不可用时降级到传统加权求和。
    """
    # 第一优先: LGBM
    try:
        from lgbm_strategy import is_model_ready as lgbm_ready, generate_lgbm_signals
        if lgbm_ready():
            signals = generate_lgbm_signals(stock_data, top_k=top_k)
            if signals:
                print(f"[StrategyEngine] ML-LGBM: {len(signals)}信号")
                return signals
    except Exception as e:
        print(f"[StrategyEngine] LGBM跳过: {e}")

    # 第二优先: XGBoost
    try:
        from xgb_factor_weight import is_ready as xgb_ready, generate_xgb_signals
        if xgb_ready():
            signals = generate_xgb_signals(stock_data, top_k=top_k)
            if signals:
                print(f"[StrategyEngine] ML-XGBoost: {len(signals)}信号")
                return signals
    except Exception as e:
        print(f"[StrategyEngine] XGBoost跳过: {e}")

    # 兜底: 老因子源
    print("[StrategyEngine] ML模型均未就绪，使用老因子源")
    return generate_for_paper(stock_data, top_k)


def generate_for_live(stock_data: dict, top_k: int = 15) -> list[dict]:
    """实盘用: 取 status=real 的策略生成信号。无信号降级"""
    strategy = _load_active_strategy("real")
    if not strategy:
        return _fallback_signals(stock_data, top_k)
    try:
        signals = generate(strategy, stock_data, top_k)
        if signals:
            return signals
        print(f"[StrategyEngine] 实盘策略 '{strategy['name']}' 无信号，降级...")
        strategy["trigger"]["min_score"] = max(40, strategy.get("trigger", {}).get("min_score", 60) - 20)
        signals = generate(strategy, stock_data, top_k)
        return signals if signals else _fallback_signals(stock_data, top_k)
    except Exception as e:
        print(f"[StrategyEngine] 实盘信号失败: {e}, 回退...")
        return _fallback_signals(stock_data, top_k)


def _fallback_signals(stock_data: dict, top_k: int = 15) -> list[dict]:
    """老信号源兜底: 从 _FACTOR_CACHE 直读 (保留兼容)"""
    try:
        import __main__ as _m
        cache = getattr(_m, '_FACTOR_CACHE', None)
        if not cache:
            # 尝试从 app 拿
            import app as _app
            cache = getattr(_app, '_FACTOR_CACHE', None)
        if cache and getattr(_m, '_CACHE_READY', False) or getattr(__import__('app'), '_CACHE_READY', False):
            signals = []
            for s in cache[:50]:
                sym = getattr(s, 'symbol', '')
                if not sym: continue
                signals.append({
                    "symbol": sym,
                    "name": getattr(s, 'name', '') or '',
                    "buy_signal": getattr(s, 'buy_signal', 0) or 0,
                    "close": getattr(s, 'close', 0) or 0,
                    "score": getattr(s, 'power_score', 0) or 0,
                })
            if signals:
                print(f"[StrategyEngine] Fallback: FACTOR_CACHE {len(signals)} signals")
                return sorted(signals, key=lambda x: -x["buy_signal"])[:top_k]
    except Exception:
        pass
    return []


if __name__ == "__main__":
    print("StrategyEngine 独立测试")
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_from_cache
    sd = load_stock_data_from_cache()
    if not sd:
        import pickle, gzip
        for p in [r"D:\quant_web\stock_data.pkl.gz", r"D:\quant_web\stock_data.pkl"]:
            if os.path.exists(p):
                sd = pickle.load(gzip.open(p, "rb")) if p.endswith(".gz") else pickle.load(open(p, "rb"))
                break
    if sd:
        sigs = generate_for_paper(sd, 10)
        print(f"Generated {len(sigs)} signals:")
        for s in sigs[:5]:
            print(f"  {s['symbol']} buy_signal={s['buy_signal']} score={s['score']:.1f} close={s['close']:.2f}")
    else:
        print("No stock data available")
