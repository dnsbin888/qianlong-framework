"""LLM情绪因子 v1.0 — DeepSeek API 分析东财头条
成本: ~0.1元/天 (1次调用/天)
输出: sentiment_score -100~+100 → 注册到factor_registry
"""
import json, os

def _get_api_key():
    """优先读配置, 其次读环境变量"""
    try:
        cfg = json.load(open(r"D:\quant_framework\live_trader_config.json", encoding="utf-8"))
        key = cfg.get("deepseek_api_key", "")
        if key: return key
    except: pass
    return os.environ.get("DEEPSEEK_API_KEY", "")


def get_llm_sentiment(news_titles=None):
    """LLM情绪评分: 东财头条 → DeepSeek → -100~+100

    Args:
        news_titles: 新闻标题列表, None=自动拉东财头条
    Returns:
        {"score": float, "label": str, "reason": str}
    """
    api_key = _get_api_key()
    if not api_key:
        return {"score": 0, "label": "LLM未配置API Key", "reason": ""}

    # 拉东财快讯标题
    if news_titles is None:
        try:
            import urllib.request, re
            req = urllib.request.Request("https://finance.eastmoney.com/a/czqyw.html",
                headers={'User-Agent': 'Mozilla/5.0'})
            handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(handler)
            with opener.open(req, timeout=10) as resp:
                html = resp.read().decode('gbk', errors='ignore')
            titles = re.findall(r'<a[^>]*title="([^"]{8,100})"[^>]*>', html)
            titles += re.findall(r'<a[^>]*>([^<]{8,80})</a>', html)
            news_titles = [t.strip() for t in titles if len(t.strip()) >= 8][:30]
        except Exception:
            news_titles = []

    if not news_titles:
        return {"score": 0, "label": "无新闻", "reason": ""}

    # 调用 DeepSeek
    try:
        import requests
        prompt = f"""分析以下A股新闻标题的市场情绪, 返回JSON格式:
{{"score": <整数 -100到100, -100=极度恐慌, 0=中性, 100=极度乐观>,
 "label": "<乐观/中性/悲观/恐慌>",
 "reason": "<一句话原因>"}}

新闻标题:
{chr(10).join(news_titles[:20])}"""

        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 100,
            },
            timeout=15,
            proxies={"http": None, "https": None},
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # 解析 JSON
        result = json.loads(content.strip().replace("```json","").replace("```",""))
        return {
            "score": max(-100, min(100, int(result.get("score", 0)))),
            "label": result.get("label", "中性"),
            "reason": result.get("reason", ""),
        }
    except Exception as e:
        return {"score": 0, "label": f"LLM异常: {str(e)[:30]}", "reason": ""}


# ── 因子注册接口 ──
def factor_llm_sentiment(df=None) -> float | None:
    """LLM情绪因子 (注册到factor_registry)
    返回 -100~+100, 正=乐观, 负=悲观
    """
    result = get_llm_sentiment()
    return float(result["score"]) if result else None


def pre_market_brief(market_data=None):
    """盘前市场解读 — LLM生成一句话简报 (推钉钉)
    Args:
        market_data: {regime, sentiment, position_scale}
    Returns:
        str: 简报文字
    """
    api_key = _get_api_key()
    if not api_key:
        return "LLM未配置API Key"

    if market_data is None:
        try:
            import sys as _sp, json as _j
            _sp.path.insert(0, r"D:\quant_web"); _sp.path.insert(0, r"D:\quant_framework")
            from market_regime import detect_regime
            from data_loader import load_stock_data_cache
            sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
            r = detect_regime(sd) if sd else {}
            from sentiment import get_market_sentiment
            s = get_market_sentiment(sd) if sd else {}
            market_data = {
                "regime": r.get("regime", "?"),
                "label": {"strong_bull":"强牛","bull":"牛市","sideways":"震荡","bear":"熊市","strong_bear":"强熊"}.get(r.get("regime",""),"?"),
                "position_scale": r.get("position_scale", 0.5),
                "sentiment": s,
            }
        except Exception:
            return "无法获取市场数据"

    regime = market_data.get("regime", "?")
    label = market_data.get("label", "?")
    ps = market_data.get("position_scale", 0)
    s = market_data.get("sentiment", {})
    mood = s.get("label", "?")
    score = s.get("score", 0)
    limit_up = s.get("limit_up", 0)
    limit_down = s.get("limit_down", 0)
    hot = [x["name"] for x in s.get("hot_sectors", [])[:3]]
    cold = [x["name"] for x in s.get("cold_sectors", [])[:3]]

    prompt = f"""你是A股量化交易助手。根据以下数据，用一段话(~60字)做盘前解读，包含操作建议。

市场状态: {label} ({regime})
仓位系数: {ps*100:.0f}%
情绪: {mood} (分数{score})
涨停{limit_up}家 跌停{limit_down}家
热门板块: {','.join(hot) if hot else '无'}
冷门板块: {','.join(cold) if cold else '无'}

直接输出一句话解读，不加前缀:"""

    try:
        import requests as _req
        resp = _req.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 150},
            timeout=15,
            proxies={"http": None, "https": None},
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"LLM异常: {e}"
