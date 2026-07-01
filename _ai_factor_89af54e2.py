def factor_volume_breakout(df):
    import numpy as np
    if len(df) < 20:
        return None
    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    recent_high = np.max(high[-20:])
    if close[-1] <= recent_high * 0.99:
        return None
    avg_vol_20 = np.mean(volume[-20:-1])
    if avg_vol_20 == 0:
        return None
    vol_ratio = volume[-1] / avg_vol_20
    if vol_ratio < 1.5:
        return None
    highest_20 = np.max(high[-20:])
    if close[-1] >= highest_20:
        return float(vol_ratio)
    return None