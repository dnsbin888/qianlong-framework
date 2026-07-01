def factor_vol_price_continuum(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    n = len(c)
    if n < 20:
        return None
    ret = (c[-1] - c[-2]) / max(c[-2], 0.01)
    vol_ratio = v[-1] / max(np.mean(v[-20:]), 1e-6)
    vol_rank = np.searchsorted(np.sort(v[-20:]), v[-1]) / 20.0
    score = 0.0
    score += 40.0 * (1.0 / (1.0 + np.exp(-5.0 * vol_ratio + 2.0)))
    score += 30.0 * (1.0 / (1.0 + np.exp(-8.0 * ret - 0.5)))
    score += 30.0 * vol_rank
    return min(100.0, max(0.0, score))