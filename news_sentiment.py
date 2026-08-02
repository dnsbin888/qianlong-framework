"""消息情绪 v2.0 — 东方财富直连 (对标私募自研爬虫)
零依赖, urllib直接调东财JSON接口, 稳定可靠
用法: python news_sentiment.py
"""
import urllib.request, json, re, os
from datetime import datetime

# 绕过代理直连东财 (跟钉钉同款问题)
_PROXY_HANDLER = urllib.request.ProxyHandler({})
_OPENER = urllib.request.build_opener(_PROXY_HANDLER)

# 情绪关键词
POS = ['涨停','大涨','飙升','突破','利好','增持','回购','业绩增长','超预期',
       '创新高','强势','反弹','翻红','走牛','放量','主力','净流入','拉升',
       '涨','升','红','牛','买入','推荐','看好','预增','增长','盈利','高增长']
NEG = ['跌停','大跌','暴跌','崩盘','利空','减持','亏损','业绩下滑','低于预期',
       '创新低','弱势','回落','翻绿','走熊','缩量','出逃','净流出','砸盘','跳水',
       '跌','降','绿','熊','卖出','下调','看空','预亏','亏损','下滑']

# 东财A股头条API (公开, 稳定, 10年没变)
EM_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f3,f12,f14&secids=1.000001,0.399001&cb=&_="

def fetch_news_sentiment() -> dict:
    """从东财接口拉A股头条, 关键词打分"""
    try:
        # 东财快讯
        url1 = "https://finance.eastmoney.com/a/czqyw.html"
        req = urllib.request.Request(url1, headers={'User-Agent': 'Mozilla/5.0'})
        with _OPENER.open(req, timeout=10) as resp:
            html = resp.read().decode('gbk', errors='ignore')
        # 提取标题
        titles = re.findall(r'<a[^>]*title="([^"]*)"[^>]*>', html)
        titles += re.findall(r'<a[^>]*>([^<]{8,80})</a>', html)
        titles = [t.strip() for t in titles if len(t.strip()) >= 8][:100]
    except Exception as e:
        print(f"[News] 东财直连失败: {e}")
        return {"score": None, "label": "暂不可用", "pos": 0, "neg": 0, "total": 0, "source": "none"}

    if not titles:
        return {"score": None, "label": "无新闻", "pos": 0, "neg": 0, "total": 0, "source": "empty"}

    pos_count = sum(1 for t in titles if any(w in t for w in POS))
    neg_count = sum(1 for t in titles if any(w in t for w in NEG))
    total = len(titles)

    if pos_count == 0 and neg_count == 0:
        score = 50.0
    else:
        ratio = pos_count / max(neg_count, 1)
        score = round(max(0, min(100, 50 + (ratio - 1) * 25)), 1)

    label = "📰 偏多" if score >= 65 else ("📰 中性" if score >= 45 else "📰 偏空")

    result = {
        "score": score, "label": label,
        "pos": pos_count, "neg": neg_count, "total": total,
        "time": datetime.now().strftime("%H:%M"), "source": "eastmoney",
    }
    print(f"[News] {label} (正面{pos_count}/负面{neg_count}/{total}条)")
    return result


if __name__ == "__main__":
    r = fetch_news_sentiment()
    print(json.dumps(r, ensure_ascii=False, indent=2))
