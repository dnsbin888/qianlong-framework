def factor_xxx(df):
    import numpy as np
    volume = df["volume"].values
    close = df["close"].values
    open_ = df["open"].values
    if len(volume) < 20:
        return None
    ma20_vol = np.mean(volume[-20:])
    if ma20_vol == 0:
        return None
    cond1 = volume[-1] > 1.5 * ma20_vol
    cond2 = close[-1] > open_[-1]
    return 1.0 if cond1 and cond2 else 0.0