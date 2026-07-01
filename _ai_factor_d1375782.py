def factor_vol_price_trend(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    n = len(c)
    if n < 20:
        return None
    ma5 = np.mean(c[-5:])
    ma20 = np.mean(c[-20:])
    price_trend = (ma5 - ma20) / max(ma20, 0.01)
    vol_ma5 = np.mean(v[-5:])
    vol_ma20 = np.mean(v[-20:])
    vol_ratio = vol_ma5 / max(vol_ma20, 0.01)
    corr_short = np.corrcoef(c[-5:], v[-5:])[0, 1]
    if np.isnan(corr_short):
        corr_short = 0
    raw = price_trend * 40 + (vol_ratio - 1) * 30 + corr_short * 20 + 50
    return min(100, max(0, raw))