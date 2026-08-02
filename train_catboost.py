"""CatBoost Meta v5 — 直接用最新一期的LGBM/XGBoost评分训练, 不自己算因子"""
import sys, os, json, pickle, numpy as np
from datetime import datetime
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")
MODEL_PATH = r"D:\quant_framework\catboost_model.cbm"


def train():
    from data_loader import load_stock_data_cache
    from catboost import CatBoostRegressor
    from market_regime import detect_regime

    print("[1/2] Load...")
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    sd = {k: v for k, v in sd.items() if not k.startswith(('sh000', 'sz399', 'bj')) and len(v) >= 30}

    # 生成 LGBM 和 XGBoost 信号
    from lgbm_strategy import generate_lgbm_signals
    from xgb_factor_weight import generate_xgb_signals
    l_sigs = generate_lgbm_signals(sd, top_k=100, min_score=5)
    x_sigs = generate_xgb_signals(sd, top_k=100, min_score=5)
    print(f"LGBM: {len(l_sigs)} sigs, XGB: {len(x_sigs)} sigs")

    # 合并所有信号的 symbol
    all_syms = {}
    for s in l_sigs + x_sigs:
        sym = s['symbol']
        if sym not in all_syms:
            all_syms[sym] = {'lgbm': None, 'xgb': None, 'close': s['close'], 'df': sd.get(sym)}
    for s in l_sigs: all_syms[s['symbol']]['lgbm'] = s['score']
    for s in x_sigs: all_syms[s['symbol']]['xgb'] = s['score']

    print(f"Unique stocks: {len(all_syms)}")
    regime = detect_regime(sd)

    X_rows, y_rows = [], []
    for sym, info in all_syms.items():
        l = info['lgbm'] or 50
        x = info['xgb'] or 50
        df = info['df']
        if df is None or len(df) < 20: continue

        c = df['close'].values; n = len(c)
        o = df['open'].values if 'open' in df.columns else c
        h = df['high'].values if 'high' in df.columns else c
        lo = df['low'].values if 'low' in df.columns else c
        v = df['volume'].values if 'volume' in df.columns else None

        vol = float(np.std(np.diff(c[max(0, n - 21):n]) / (c[max(0, n - 21):n - 1] + 1e-9))) if n >= 21 else 0.02
        mom = (float(c[-1]) - float(c[max(0, n - 6)])) / max(float(c[max(0, n - 6)]), 0.01) if n >= 6 else 0

        # P2-6: 3个新独立特征 (纯OHLCV, 零外部依赖)
        # 换手率异常: 近5日量 / 20日均量 - 1
        turnover_anomaly = 0.0
        if v is not None and n >= 20:
            avg_vol_20 = float(np.mean(v[max(0, n - 20):n]))
            avg_vol_5 = float(np.mean(v[max(0, n - 5):n]))
            if avg_vol_20 > 0:
                turnover_anomaly = (avg_vol_5 / avg_vol_20) - 1.0

        # 量价背离: 负相关=背离, 正相关=共振
        pwr_divergence = 0.0
        if v is not None and n >= 10:
            price_chg = np.sign(np.diff(c[max(0, n - 11):n]))
            vol_chg = np.sign(np.diff(v[max(0, n - 11):n]))
            if len(price_chg) >= 5 and len(vol_chg) >= 5 and np.std(price_chg) > 0 and np.std(vol_chg) > 0:
                corr_mat = np.corrcoef(price_chg, vol_chg)
                corr = float(corr_mat[0, 1]) if corr_mat.shape == (2, 2) else 0.0
                pwr_divergence = -corr if not np.isnan(corr) else 0.0

        # 盘中买卖力道: avg((close-open)/(high-low)) 5日
        intraday_power = 0.0
        if n >= 5:
            powers = []
            for i in range(max(0, n - 5), n):
                denom = float(h[i]) - float(lo[i])
                if abs(denom) > 0.001:
                    powers.append((float(c[i]) - float(o[i])) / denom)
            intraday_power = float(np.mean(powers)) if powers else 0.0

        # target: 5日 forward return (approximate from last 5 bars)
        fwd = (float(c[-1]) - float(c[max(0, n - 6)])) / max(float(c[max(0, n - 6)]), 0.01) if n >= 6 else 0

        X_rows.append([l, x, vol, mom, float(regime.get("confidence", 0.5)),
                       turnover_anomaly, pwr_divergence, intraday_power])
        y_rows.append(fwd)

    if len(X_rows) < 100:
        print(f"ERROR: {len(X_rows)} samples"); return
    X, y = np.array(X_rows, dtype=np.float32), np.array(y_rows)

    print(f"[2/2] Train: {len(X)} samples")
    # P1-1: 时间序列split + 原子写入
    split = int(len(X) * 0.8)
    model = CatBoostRegressor(iterations=80, depth=5, learning_rate=0.1, random_seed=42, verbose=40)
    if split > 0 and len(X) - split >= 20:
        model.fit(X[:split], y[:split],
                  eval_set=(X[split:], y[split:]),
                  early_stopping_rounds=10, verbose=40)
    else:
        model.fit(X, y, verbose=40)
    # P1-2: 版本备份 — 保留最近5个版本, 防坏模型覆盖好模型
    if os.path.exists(MODEL_PATH):
        import shutil as _sh
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        _bak = MODEL_PATH.replace(".cbm", f".{_ts}.bak")
        _sh.copy2(MODEL_PATH, _bak)
        _dir = os.path.dirname(MODEL_PATH)
        _prefix = os.path.basename(MODEL_PATH).replace(".cbm", "")
        _baks = sorted([f for f in os.listdir(_dir) if f.startswith(_prefix) and f.endswith(".bak")])
        for _old in _baks[:-5]:
            os.remove(os.path.join(_dir, _old))
    # P1-1: 原子写入
    tmp = MODEL_PATH + ".tmp"
    model.save_model(tmp)
    os.replace(tmp, MODEL_PATH)
    print(f"✅ {MODEL_PATH}")


def generate_meta_score(lgbm_score, xgb_score, stock_data, sym, market_regime):
    from catboost import CatBoostRegressor
    if not os.path.exists(MODEL_PATH):
        s = [v for v in [lgbm_score, xgb_score] if v is not None]
        return round(sum(s) / len(s), 1) if s else 0
    model = CatBoostRegressor(); model.load_model(MODEL_PATH)
    df = stock_data.get(sym)
    vol, mom = 0.02, 0
    turnover_anomaly, pwr_divergence, intraday_power = 0.0, 0.0, 0.0
    if df is not None and len(df) >= 21:
        c = df["close"].values; n = len(c)
        o = df['open'].values if 'open' in df.columns else c
        h = df['high'].values if 'high' in df.columns else c
        l_lo = df['low'].values if 'low' in df.columns else c
        v = df['volume'].values if 'volume' in df.columns else None
        rets = np.diff(c[max(0, n - 21):n]) / (c[max(0, n - 21):n - 1] + 1e-9)
        vol = float(np.std(rets)) if len(rets) > 1 else 0.02
        mom = (float(c[-1]) - float(c[max(0, n - 6)])) / max(float(c[max(0, n - 6)]), 0.01) if n >= 6 else 0
        # P2-6: 3个新特征 (与train()保持一致)
        if v is not None and n >= 20:
            avg_vol_20 = float(np.mean(v[max(0, n - 20):n]))
            avg_vol_5 = float(np.mean(v[max(0, n - 5):n]))
            if avg_vol_20 > 0:
                turnover_anomaly = (avg_vol_5 / avg_vol_20) - 1.0
        if v is not None and n >= 10:
            price_chg = np.sign(np.diff(c[max(0, n - 11):n]))
            vol_chg = np.sign(np.diff(v[max(0, n - 11):n]))
            if len(price_chg) >= 5 and len(vol_chg) >= 5 and np.std(price_chg) > 0 and np.std(vol_chg) > 0:
                corr_mat = np.corrcoef(price_chg, vol_chg)
                corr = float(corr_mat[0, 1]) if corr_mat.shape == (2, 2) else 0.0
                pwr_divergence = -corr if not np.isnan(corr) else 0.0
        if n >= 5:
            powers = [(float(c[i]) - float(o[i])) / max(float(h[i]) - float(l_lo[i]), 0.01)
                      for i in range(max(0, n - 5), n)
                      if abs(float(h[i]) - float(l_lo[i])) > 0.001]
            intraday_power = float(np.mean(powers)) if powers else 0.0
    conf = market_regime.get("confidence", 0.5) if isinstance(market_regime, dict) else 0.5
    X = np.array([[lgbm_score or 50, xgb_score or 50, vol, mom, conf,
                   turnover_anomaly, pwr_divergence, intraday_power]], dtype=np.float32)
    pred = float(model.predict(X)[0])
    return round(min(100, max(0, (pred + 0.05) * 1000)), 1)


def generate_catboost_signals(stock_data, top_k=15, min_score=20):
    from market_regime import detect_regime
    regime = detect_regime(stock_data)
    signals = {}
    try:
        from lgbm_strategy import generate_lgbm_signals
        for s in generate_lgbm_signals(stock_data, top_k=30, min_score=20):
            sym = s["symbol"]; signals.setdefault(sym, {})["lgbm"] = s
    except: pass
    try:
        from xgb_factor_weight import generate_xgb_signals
        for s in generate_xgb_signals(stock_data, top_k=30, min_score=20):
            sym = s["symbol"]; signals.setdefault(sym, {})["xgb"] = s
    except: pass
    result = []
    for sym, models in signals.items():
        l = models.get("lgbm", {}); x = models.get("xgb", {})
        meta_score = generate_meta_score(l.get("score"), x.get("score"), stock_data, sym, regime)
        if meta_score < min_score: continue
        df = stock_data.get(sym)
        close = float(df["close"].iloc[-1]) if df is not None and len(df) > 0 else 0
        if close <= 0: continue
        lv = 5 if meta_score >= 90 else 4 if meta_score >= 80 else 3 if meta_score >= 70 else 2 if meta_score >= 60 else 1
        if lv == 0: continue
        result.append({"symbol": sym, "buy_signal": lv, "close": close, "score": meta_score, "name": "",
                        "stop_loss": round(close * 0.97, 2), "take_profit": [round(close * 1.05, 2), round(close * 1.10, 2)], "strategy": "CatBoost-Meta-v5"})
    result.sort(key=lambda x: -x["score"])
    out = result[:top_k]
    if out: print(f"[CatBoost-Meta] {len(out)} signals")
    return out


if __name__ == "__main__":
    train()
