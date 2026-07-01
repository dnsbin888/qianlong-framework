def factor_vol_price_trend(df):
    import numpy as np
    close = df['close'].values
    volume = df['volume'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)
    if n < 20:
        return None
    
    # 量价趋势一致性评分
    # 1. 价格方向: 用最近5日收益率
    ret5 = (close[-1] - close[-6]) / max(close[-6], 0.01)
    
    # 2. 成交量方向: 最近5日均量 vs 前10日均量
    vol_ma5 = np.mean(volume[-5:])
    vol_ma10 = np.mean(volume[-15:-5])
    vol_ratio = vol_ma5 / max(vol_ma10, 0.01)
    
    # 3. 日内强度: (close - low) / (high - low) 最近3日均值
    daily_strength = (close[-3:] - low[-3:]) / np.maximum(high[-3:] - low[-3:], 0.001)
    avg_strength = np.mean(daily_strength)
    
    # 量价配合得分: 上涨放量+日内强势 -> 高分; 下跌放量+日内弱势 -> 低分
    score = (ret5 * 0.4 + (vol_ratio - 1.0) * 0.3 + (avg_strength - 0.5) * 0.3) * 100 + 50
    return min(100, max(0, score))