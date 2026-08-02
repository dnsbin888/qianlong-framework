"""市场情绪指标 v1.0 (P2-R)
从现有行情数据实时计算, 零外部依赖:
  1. 涨跌比: 上涨家数/下跌家数
  2. 涨停跌停比: 涨停数/跌停数
  3. 市场宽度: >MA20的股票占比
  4. 北向资金: 从westock读取 (如有)
  5. 综合情绪: 加权合成 0-100

用法: from sentiment import get_market_sentiment
      score = get_market_sentiment(sd)
"""
import numpy as np, os, json


def get_limit_pct(code):
    c = code.replace('sh','').replace('sz','').replace('bj','')
    if c.startswith(('30','688')): return 0.20
    if c.startswith(('8','4')): return 0.30
    return 0.10


def get_market_sentiment(sd: dict) -> dict:
    """从股票数据计算市场情绪

    Args:
        sd: {symbol: DataFrame} 全市场行情

    Returns:
        {score, advance_ratio, limit_ratio, breadth, label}
    """
    up_count = 0
    down_count = 0
    limit_up_count = 0
    limit_down_count = 0
    above_ma20 = 0
    total = 0

    for sym, df in sd.items():
        try:
            c = df['close'].values
            if len(c) < 21:
                continue
            total += 1
            chg = (c[-1] - c[-2]) / max(c[-2], 0.01)
            if chg > 0: up_count += 1
            elif chg < 0: down_count += 1

            limit_pct = get_limit_pct(sym)
            prev_close = c[-2]
            if prev_close > 0:
                if c[-1] >= round(prev_close * (1 + limit_pct), 2) * 0.995:
                    limit_up_count += 1
                elif c[-1] <= round(prev_close * (1 - limit_pct), 2) * 1.005:
                    limit_down_count += 1

            ma20 = np.mean(c[-20:])
            if c[-1] > ma20:
                above_ma20 += 1
        except Exception:
            continue

    if total < 100:
        return {"score": 50, "label": "数据不足", "total": total}

    advance_ratio = up_count / max(up_count + down_count, 1)
    breadth = above_ma20 / max(total, 1)
    limit_ratio = limit_up_count / max(limit_down_count, 1)

    # 综合评分 (0-100)  涨跌比40% + 宽度30% + 封板比20% + 涨跌停比10%
    raw = advance_ratio * 0.4 + breadth * 0.3 + min(limit_up_count/max(total,1)*500, 0.2)
    if limit_down_count > 0 and limit_up_count > 0:
        raw += max(-0.1, min(0.1, (limit_ratio - 1) * 0.1))
    score = max(0, min(100, raw * 100))

    # 5级情绪 (对标私募: 极乐/乐观/中性/悲观/恐慌)
    if score >= 75:
        label = "🔥 极乐"
    elif score >= 60:
        label = "🐂 乐观"
    elif score >= 40:
        label = "📊 中性"
    elif score >= 20:
        label = "🐻 悲观"
    else:
        label = "🔴 恐慌"

    # 行业动量排名 (Top3热门+底部3冷门)
    sector_chgs = {}
    try:
        from collections import defaultdict
        # 行业映射: 从 stock_industry_map.json 读取
        _ind_map = {}
        _ind_path = r"D:\quant_web\data\stock_industry_map.json"
        if os.path.exists(_ind_path):
            try:
                _raw = json.load(open(_ind_path, encoding='utf-8'))
                if isinstance(_raw, dict):
                    _inner = _raw.get("symbol_to_industry", _raw)
                    if isinstance(_inner, dict):
                        _ind_map = _inner
            except: pass
        if not _ind_map:
            # 兜底: 从 stock_names_full.csv (code,name,industry)
            csv_path = r"D:\quant_web\stock_names_full.csv"
            if os.path.exists(csv_path):
                with open(csv_path, encoding='utf-8') as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if len(parts) >= 3:
                            _ind_map[parts[0]] = parts[2]
        sector_data = defaultdict(list)
        for sym, df in sd.items():
            try:
                code = sym.replace('sh','').replace('sz','').replace('bj','')
                ind = _ind_map.get(code, '') or _ind_map.get(sym, '')
                if not ind or len(df) < 6: continue
                # 当日涨跌 (近2日, 非5日)
                c = df['close'].values
                chg = (c[-1] - c[-2]) / max(c[-2], 0.01) if len(c) >= 2 else 0
                sector_data[ind].append(chg)
            except: pass
        for ind, chgs in sector_data.items():
            if len(chgs) >= 5:
                sector_chgs[ind] = round(np.mean(chgs) * 100, 1)
    except Exception as _se: pass
    hot = sorted(sector_chgs.items(), key=lambda x: -x[1])[:3]
    cold = sorted(sector_chgs.items(), key=lambda x: x[1])[:3]

    return {
        "score": round(score, 1),
        "label": label,
        "total": total,
        "advance_ratio": round(advance_ratio * 100, 1),
        "breadth": round(breadth * 100, 1),
        "limit_up": limit_up_count,
        "limit_down": limit_down_count,
        "limit_ratio": round(limit_up_count / max(limit_down_count, 1), 2),
        "hot_sectors": [{"name": n, "chg": c} for n, c in hot],
        "cold_sectors": [{"name": n, "chg": c} for n, c in cold],
    }


def get_sentiment_short(sd: dict) -> str:
    """简短情绪标签"""
    s = get_market_sentiment(sd)
    return f"{s['label']} (涨跌比{s['advance_ratio']}% 宽度{s['breadth']}% 涨停{s['limit_up']}只)"


if __name__ == "__main__":
    import sys, json
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
    r = get_market_sentiment(sd)
    print(json.dumps(r, ensure_ascii=False, indent=2))
