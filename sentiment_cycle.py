"""情绪周期识别 v1.0 — 四阶段判定 (对标游资: 启动→发酵→高潮→退潮)
数据源: sentiment.get_market_sentiment() + market_regime.detect_regime()
用法:
  from sentiment_cycle import classify
  stage = classify(sd)  # → {stage, position_scale, label, signals}
"""
import sys, os
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")


def classify(sd: dict, regime: dict = None) -> dict:
    """判定当前情绪周期阶段

    Args:
        sd: {symbol: DataFrame} 全市场行情
        regime: market_regime.detect_regime() 的结果, None则自动检测

    Returns:
        {stage, position_scale, label, limit_up, limit_down, advance_ratio, breadth}
        stage: "startup" | "ferment" | "climax" | "retreat"
    """
    from sentiment import get_market_sentiment
    sent = get_market_sentiment(sd)

    limit_up = sent.get("limit_up_count", 0)
    limit_down = sent.get("limit_down_count", 0)
    advance_ratio = sent.get("advance_ratio", 0.5)
    breadth = sent.get("breadth", 50)
    score = sent.get("score", 50)

    # 从 market_regime 获取趋势确认
    if regime is None:
        try:
            from market_regime import detect_regime
            regime = detect_regime(sd) or {}
        except Exception:
            regime = {}
    regime_type = regime.get("regime", "sideways")
    regime_conf = regime.get("confidence", 0.5)

    # 四阶段判定
    if limit_up >= 100 and breadth >= 70 and score >= 75:
        stage = "climax"       # 高潮期: 百股涨停+宽度极高
        scale = 0.5
        label = "🔥 高潮期"
    elif limit_up >= 60 and advance_ratio >= 1.3 and breadth >= 50:
        stage = "ferment"      # 发酵期: 涨停递增+涨多跌少
        scale = 1.0
        label = "📈 发酵期"
    elif limit_up < 25 and advance_ratio < 0.7 and regime_type == "bear":
        stage = "retreat"      # 退潮期: 涨停稀少+跌多涨少+熊市
        scale = 0.0
        label = "🌧 退潮期"
    elif limit_up >= 25 and regime_type != "bear" and regime_conf > 0.4:
        stage = "startup"      # 启动期: 涨停回暖+非熊市
        scale = 0.7
        label = "🌱 启动期"
    else:
        # 无法判定 → 保守
        if regime_type == "bear":
            stage, scale, label = "retreat", 0.0, "🌧 退潮期(保守)"
        elif regime_type == "bull":
            stage, scale, label = "ferment", 0.8, "📈 发酵期(推定)"
        else:
            stage, scale, label = "startup", 0.5, "🌱 启动期(推定)"

    return {
        "stage": stage,
        "position_scale": scale,
        "label": label,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "advance_ratio": round(advance_ratio, 2),
        "breadth": round(breadth, 1),
        "sentiment_score": round(score, 1),
        "regime": regime_type,
        # 游资口诀
        "advice": {
            "startup": "轻仓试错首板, 等龙头出现",
            "ferment": "二板定龙, 重仓出击",
            "climax": "锁仓持有, 等爆量滞涨→清仓",
            "retreat": "空仓观望, 退潮期不幻想反弹",
        }.get(stage, ""),
    }


def position_scale_from_cycle(sd: dict) -> float:
    """从情绪周期获取仓位系数, 替代简单牛/熊三态"""
    result = classify(sd)
    return result["position_scale"]


if __name__ == "__main__":
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
    r = classify(sd)
    print(f"阶段: {r['label']}")
    print(f"仓位系数: {r['position_scale']}")
    print(f"涨停: {r['limit_up']} 跌停: {r['limit_down']}")
    print(f"涨跌比: {r['advance_ratio']} 宽度: {r['breadth']}%")
    print(f"情绪分: {r['sentiment_score']} 市态: {r['regime']}")
    print(f"建议: {r['advice']}")
