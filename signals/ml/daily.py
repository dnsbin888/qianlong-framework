"""ML日线评分 v3.0 — 训推同源 (与 generate_signal_table 共享管线)
对标: hl-quant 固定评估器模式 — 生产/验证走同一份代码

输出: [{symbol, score, action, close, stop_loss, ...}] 统一信号格式
"""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")


def _load_params():
    """从 trade_config_master.json 读取止损/止盈参数 (参数治理铁律)"""
    try:
        m = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
        sl = m.get("stop_loss", {})
        tp = m.get("take_profit", {})
        return {
            "hard_stop_pct": sl.get("hard", 0.055),
            "soft_stop_pct": sl.get("hard", 0.055),
            "take_profit_pct": tp.get("tp1", {}).get("profit_pct", 0.05),
        }
    except Exception:
        return {"hard_stop_pct": 0.055, "soft_stop_pct": 0.03, "take_profit_pct": 0.08}


def _recent_momentum_ok(sym, sd):
    """回踩确认: 昨天跌+今天涨+站上MA20 (趋势还在, 回踩完成)"""
    df = sd.get(sym)
    if df is None or len(df) < 22:
        return False
    c = df['close'].values
    if len(c) < 22:
        return False
    yest_ret = (c[-2] - c[-3]) / max(c[-3], 0.01)  # 昨天
    today_ret = (c[-1] - c[-2]) / max(c[-2], 0.01)  # 今天
    ma5 = float(c[-5:].mean()) if len(c) >= 5 else c[-1]
    ma20 = float(c[-20:].mean()) if len(c) >= 20 else c[-1]
    # 回踩确认: 昨天跌 + 今天涨 + (站上MA5或MA20)
    on_trend = c[-1] > ma5 or c[-1] > ma20
    return yest_ret < 0 and today_ret > 0 and on_trend


def generate(sd, factor_cache=None):
    """三模型 (LGBM+XGB+Ridge) 高斯化秩平均 → 统一信号列表

    管线 (与 generate_signal_table.main() 共享):
    LGBM stock → XGBoost factor → Ridge linear
    → rank_gaussianize() → ml_combine_scores()  ← 共享函数,训推同源
    → 统一信号格式
    """
    # 前沿: 不用Gaussianize+中性化(广发金工2025: 损害树模型)

    params = _load_params()

    # ── 择时空仓: 熊市/强熊市 ML不做多 (游资铁律: 退潮期空仓) ──
    try:
        from market_regime import detect_regime
        regime = detect_regime(sd).get("regime", "unknown")
        if regime in ("bear", "strong_bear"):
            return []
    except Exception:
        pass  # regime不可用时继续

    all_signals = {}

    # 第1步: LGBM Stock
    _stock_model = r"D:\quant_framework\lgbm_model_stock.pkl"
    if os.path.exists(_stock_model):
        _skip = ('sh000','sh11','sh12','sh13','sh14','sh15','sh2','sh5','sz399','sz11','sz12','sz13','sz15','sz16','sz18','sz5','bj')
        _sd = {k:v for k,v in sd.items() if not k.startswith(_skip)}
        try:
            from lgbm_strategy import generate_lgbm_signals
            for s in generate_lgbm_signals(_sd, top_k=30, min_score=30, model_path=_stock_model):
                all_signals.setdefault(s['symbol'], {})['lgbm'] = {'score': s['score'], 'lv': s['buy_signal']}
        except Exception as e:
            print(f"  LGBM: {e}")

    # 第2步: XGBoost
    _skip_x = ('sh000','sz399','sz98','bj','sh88','sz88','sh51','sh58','sh56','sh15','sh50','sz15','sz16','sh11','sh12','sh13','sh14','sz11','sz12','sz13','sz18','sh2','sh5','sz5')
    _sd_x = {k:v for k,v in sd.items() if not k.startswith(_skip_x)}
    try:
        from xgb_factor_weight import generate_xgb_signals
        for s in generate_xgb_signals(_sd_x, top_k=30, min_score=20):
            all_signals.setdefault(s['symbol'], {})['xgb'] = {'score': s['score'], 'lv': s['buy_signal']}
    except Exception as e:
        print(f"  XGB: {e}")

    # 第3步: Ridge
    try:
        from ridge_model import predict_scores as ridge_predict
        _r = ridge_predict(sd)
        for sym, sc in sorted(_r.items(), key=lambda x: -x[1])[:30]:
            all_signals.setdefault(sym, {})['ridge'] = {'score': sc, 'lv': 5 if sc>=90 else 4 if sc>=80 else 3 if sc>=70 else 2 if sc>=60 else 1}
    except Exception as e:
        print(f"  Ridge: {e}")

    if not all_signals:
        return []

    # 第4步: 原始分秩平均 (行业标准: 不Gaussianize不中性化, 保方向)
    neutralized = {}
    for sym in all_signals:
        scores = []
        for key in ['lgbm', 'xgb', 'ridge']:
            m = all_signals[sym].get(key)
            if m and m.get('score'):
                scores.append(m['score'])
        if scores:
            neutralized[sym] = round(sum(scores) / len(scores), 1)

    # 第5步: 输出统一信号格式
    results = []
    for sym, sc in neutralized.items():
        df = sd.get(sym)
        if df is None or len(df) < 2:
            continue
        close = float(df['close'].values[-1])
        results.append({
            "symbol": sym,
            "score": round(float(sc), 1),
            "action": "buy",
            "close": round(close, 2),
            "stop_loss": round(close * (1 - params["hard_stop_pct"]), 2),
            "soft_stop_loss": round(close * (1 - params["soft_stop_pct"]), 2),
            "take_profit": round(close * (1 + params["take_profit_pct"]), 2),
            "reason": f"ML三模型秩平均 score={sc:.1f}",
            "hold_days": 7,
            "strategy_id": "ml_daily",
            "strategy_type": "trend",
        })

    # ── 方向过滤: 只保留近期动量>0的票 (5日涨>0, 趋势配合) ──
    results = [r for r in results if _recent_momentum_ok(r["symbol"], sd)]

    results.sort(key=lambda x: -x['score'])
    top_n = max(5, len(results) // 20)
    return results[:top_n]
