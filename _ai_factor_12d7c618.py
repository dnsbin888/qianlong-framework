```python
def factor_volume_shrink_stabilize_rebound(df):
    import numpy as np
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)
    if n < 21:
        return None
    ma20_vol = np.mean(volume[-21:-1])
    if ma20_vol == 0:
        return None
    vol_ratio = volume[-1] / ma20_vol
    if vol_ratio > 0.3:
        return None
    recent_low = np.min(close[-11:-1])
    if close[-1] <= recent_low:
        return None
    prev_low = np.min(close[-21:-11])
    if recent_low > prev_low:
        return None
    vol_confirm = volume[-1] > np.mean(volume[-6:-1]) * 1.1
    if not vol_confirm:
        return None
    rebound_strength = (close[-1] - recent_low) / recent_low
    return float(rebound_strength)
```