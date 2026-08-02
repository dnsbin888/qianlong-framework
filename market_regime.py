"""B6: 市场状态识别 v2.0 — 多因子评分 (⭐⭐⭐)
方法: 趋势(40%) + 量价(20%) + 宽度(20%) + 波动(20%)
对标: 中大型私募多因子市场状态模型
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")


def detect_regime(stock_data, lookback=60):
    """多因子市场状态检测

    四因子打分:
      1. 趋势 (40%): 多周期MA排列 + MA斜率
      2. 量价 (20%): 成交量趋势配合
      3. 宽度 (20%): 全市场 >MA20 的股票占比
      4. 波动 (20%): 近期波动率水平

    Returns: {regime, confidence, position_scale, ...}
    """
    # 获取基准指数 (优先沪深300, 其次上证指数, 都没有则用全市场聚合)
    bm = stock_data.get("sh000300")
    if bm is None:
        bm = stock_data.get("sh000001")

    if bm is None or len(bm) < 20:
        # 兜底: 用全市场个股聚合构造虚拟指数
        closes_all = []
        volumes_all = []
        for sym, df in stock_data.items():
            try:
                if len(df) >= 20:
                    closes_all.append(df['close'].values[-lookback:])
                    if 'volume' in df.columns:
                        volumes_all.append(df['volume'].values[-lookback:])
            except Exception:
                continue
        if len(closes_all) < 50:
            return _unknown()
        # 等权聚合
        min_len = min(len(c) for c in closes_all)
        close = np.mean([c[-min_len:] for c in closes_all], axis=0)
        volume = np.mean([v[-min_len:] for v in volumes_all], axis=0) if volumes_all else np.ones(min_len)
        lookback = min(lookback, len(close))
        if len(close) < 20:
            return _unknown()
    else:
        lookback = min(lookback, len(bm))
        close = bm["close"].values[-lookback:]
        volume = bm["volume"].values[-lookback:] if "volume" in bm.columns else np.ones(lookback)
        if len(close) < 20:
            return _unknown()

    # ═══ 1. 趋势得分 (40%) — 多周期MA排列 ═══
    current = close[-1]
    ma5 = np.mean(close[-5:])
    ma10 = np.mean(close[-10:]) if len(close) >= 10 else ma5
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:]) if len(close) >= 60 else ma20

    # MA多头排列: ma5>ma10>ma20>ma60
    alignment = 0
    if ma5 > ma10: alignment += 1
    if ma10 > ma20: alignment += 1
    if ma20 > ma60: alignment += 1
    trend_score = alignment / 3.0  # 0-1

    # 价格在MA60上方
    above_ma60 = current > ma60

    # MA60斜率
    if len(close) >= 80:
        ma60_old = np.mean(close[-80:-20])
        slope = (ma60 - ma60_old) / max(ma60_old, 0.01)
    else:
        slope = 0

    # 综合趋势
    trend = 0.3 * trend_score + 0.3 * (1.0 if above_ma60 else 0.0) + 0.2 * min(1.0, max(0, slope * 50 + 0.5)) + 0.2

    # ═══ 2. 量价得分 (20%) — 量增价涨=健康 ═══
    vol_score = 0.5
    if len(volume) >= 20:
        vol_ma5 = np.mean(volume[-5:])
        vol_ma20 = np.mean(volume[-20:])
        vol_ratio = vol_ma5 / max(vol_ma20, 1)
        price_chg_5 = (close[-1] - close[-6]) / max(close[-6], 0.01) if len(close) >= 6 else 0
        # 量增价涨=强势, 量缩价跌=弱势
        if vol_ratio > 1.2 and price_chg_5 > 0:
            vol_score = 0.8
        elif vol_ratio < 0.8 and price_chg_5 < 0:
            vol_score = 0.2
        elif vol_ratio > 1.0:
            vol_score = 0.6
        else:
            vol_score = 0.4

    # ═══ 3. 宽度得分 (20%) — 全市场 >MA20 占比 ═══
    breadth_score = 0.5
    breadth_count = 0
    breadth_total = 0
    for sym in list(stock_data.keys())[:300]:
        df = stock_data.get(sym)
        if df is None or len(df) < 20:
            continue
        c = df["close"].values
        if len(c) < 20:
            continue
        breadth_total += 1
        if c[-1] > np.mean(c[-20:]):
            breadth_count += 1
    if breadth_total >= 20:
        pct = breadth_count / breadth_total
        # >60%股票在MA20上方=强势, <30%=弱势
        breadth_score = min(1.0, max(0.1, pct + 0.1))

    # ═══ 4. 波动得分 (20%) — 低波=安全, 高波=风险 ═══
    n = min(30, len(close) - 1)
    rets = np.diff(close[-n-1:]) / close[-n-1:-1]
    vol = np.std(rets) if len(rets) > 1 else 0.02
    # 波动率越低分越高 (低波环境更安全)
    if vol < 0.01:
        vol_risk = 0.9
    elif vol < 0.02:
        vol_risk = 0.7
    elif vol < 0.03:
        vol_risk = 0.4
    else:
        vol_risk = 0.2

    # ═══ 加权综合 ═══
    score = (0.40 * trend + 0.20 * vol_score + 0.20 * breadth_score + 0.20 * vol_risk)
    score = round(max(0.0, min(1.0, score)), 2)

    # ═══ 判定 (5级, 对齐游资) ═══
    # 主升80%/慢牛65%/震荡50%/退潮20%/冰点空仓
    if score >= 0.80:
        regime = "strong_bull"; pos_scale = 0.80; max_pos = 5
    elif score >= 0.65:
        regime = "bull";        pos_scale = 0.65; max_pos = 5
    elif score >= 0.40:
        regime = "sideways";    pos_scale = 0.50; max_pos = 3
    elif score >= 0.25:
        regime = "bear";        pos_scale = 0.20; max_pos = 2
    else:
        regime = "strong_bear"; pos_scale = 0.0; max_pos = 0

    return {
        "regime": regime,
        "confidence": score,
        "position_scale": pos_scale,
        "suggested_max_positions": max_pos,
        "trend_score": round(trend, 3),
        "volume_score": round(vol_score, 3),
        "breadth_score": round(breadth_score, 3),
        "vol_risk_score": round(vol_risk, 3),
        "volatility": round(float(vol), 4),
        "price_above_ma60": bool(above_ma60),
        "market_breadth_pct": round(breadth_count / max(breadth_total, 1), 3) if breadth_total >= 20 else None,
    }


def _unknown():
    return {"regime": "unknown", "confidence": 0.5, "position_scale": 0.5,
            "suggested_max_positions": 2, "volatility": 0.02, "price_above_ma60": False}


def get_strategy_params(regime_info):
    """返回当前市场状态下的策略参数 (从 trade_config_master 读取)"""
    import json as _j, os as _os
    regime = regime_info.get("regime", "unknown")
    _defaults = {
        "strong_bull": {"max_positions":5, "position_scale":0.80, "stop_loss":-0.06, "take_profit":[0.10,0.12,0.15], "hold_days":10},
        "bull":        {"max_positions":5, "position_scale":0.65, "stop_loss":-0.05, "take_profit":[0.07,0.10,0.15], "hold_days":7},
        "sideways":    {"max_positions":3, "position_scale":0.50, "stop_loss":-0.04, "take_profit":[0.05,0.07,0.10], "hold_days":7},
        "bear":        {"max_positions":2, "position_scale":0.20, "stop_loss":-0.03, "take_profit":[0.03,0.05],      "hold_days":5},
        "strong_bear": {"max_positions":0, "position_scale":0.0,  "stop_loss":0,     "take_profit":[],                "hold_days":0},
        "unknown":     {"max_positions":2, "position_scale":0.30, "stop_loss":-0.03, "take_profit":[0.03,0.05],      "hold_days":3},
    }
    try:
        _mp = r"D:\quant_framework\trade_config_master.json"
        if _os.path.exists(_mp):
            _mc = _j.load(open(_mp, encoding="utf-8"))
            _mr = _mc.get("market_regime", {})
            return _mr.get(regime, _defaults.get(regime, _defaults["bear"]))
    except Exception:
        pass
    return _defaults.get(regime, _defaults["bear"])


if __name__ == "__main__":
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=120)
    if sd:
        info = detect_regime(sd)
        params = get_strategy_params(info)
        print(f"市场状态: {info['regime'].upper()} (置信度={info['confidence']:.0%})")
        print(f"  趋势={info.get('trend_score',0):.0%} 量价={info.get('volume_score',0):.0%} "
              f"宽度={info.get('breadth_score',0):.0%} 波动={info.get('vol_risk_score',0):.0%}")
        print(f"  仓位系数={info['position_scale']:.0%} 最大持仓={params['max_positions']}只")
    print("\n✅ market_regime v2.0 就绪")
