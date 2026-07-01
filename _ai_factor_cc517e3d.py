def factor_vol_price_trend(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    n = len(c)
    if n < 20:
        return None
    ret_5 = (c[-1] - c[-5]) / (c[-5] + 1e-8)
    vol_ratio = v[-1] / (np.mean(v[-20:]) + 1e-8)
    amp = (h[-1] - l[-1]) / (o[-1] + 1e-8)
    close_pos = (c[-1] - l[-1]) / (h[-1] - l[-1] + 1e-8)
    raw = (ret_5 * 200 + np.log(vol_ratio + 1e-8) * 50 + amp * 100 + close_pos * 80)
    score = np.clip((raw + 50), 0, 100)
    return float(score)