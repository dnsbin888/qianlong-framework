"""Optuna 超参搜索 CatBoost — 对标蓝图 B1
用法: python optuna_tune_cb.py
"""
import sys, os, json, numpy as np
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catboost_model.cbm")


def tune_catboost(n_trials=30):
    import optuna
    from catboost import CatBoostRegressor
    from purged_cv import stock_group_kfold

    # 复用 train_catboost.py 的数据准备逻辑
    from train_catboost import train as _original_train
    from data_loader import load_stock_data_cache
    from lgbm_strategy import generate_lgbm_signals
    from xgb_factor_weight import generate_xgb_signals
    from market_regime import detect_regime

    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    sd = {k: v for k, v in sd.items() if not k.startswith(('sh000', 'sz399', 'bj')) and len(v) >= 30}
    l_sigs = generate_lgbm_signals(sd, top_k=100, min_score=5)
    x_sigs = generate_xgb_signals(sd, top_k=100, min_score=5)

    all_syms = {}
    for s in l_sigs + x_sigs:
        sym = s['symbol']
        if sym not in all_syms:
            all_syms[sym] = {'lgbm': None, 'xgb': None, 'close': s['close'], 'df': sd.get(sym)}
    for s in l_sigs: all_syms[s['symbol']]['lgbm'] = s['score']
    for s in x_sigs: all_syms[s['symbol']]['xgb'] = s['score']

    regime = detect_regime(sd)
    X_rows, y_rows = [], []
    for sym, info in all_syms.items():
        l = info['lgbm'] or 50
        x = info['xgb'] or 50
        df = info['df']
        if df is None or len(df) < 20: continue
        c = df['close'].values; n = len(c)
        rets = np.diff(c[max(0, n-21):n]) / (c[max(0, n-21):n-1] + 1e-9)
        vol = float(np.std(rets)) if len(rets) > 1 else 0.02
        mom = (float(c[-1]) - float(c[max(0, n-6)])) / max(float(c[max(0, n-6)]), 0.01) if n >= 6 else 0
        fwd = mom
        X_rows.append([l, x, vol, mom, float(regime.get("confidence", 0.5)),
                       0.0, 0.0, 0.0])  # P2-6 features placeholder
        y_rows.append(fwd)

    if len(X_rows) < 50:
        print(f"ERROR: {len(X_rows)} samples")
        return

    X, y = np.array(X_rows, dtype=np.float32), np.array(y_rows)
    print(f"[Optuna-CB] {len(X)} samples, 8 features")
    # B2: GroupKFold — 按股票分组防泄露
    _cb_stock_ids = [str(sym) for sym in all_syms.keys()][:len(X)]
    _cb_splits = stock_group_kfold(_cb_stock_ids, n_splits=3)

    def objective(trial):
        params = {
            'iterations': trial.suggest_int('iterations', 40, 200),
            'depth': trial.suggest_int('depth', 3, 8),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'l2_leaf_reg': trial.suggest_float('l2_leaf_reg', 1.0, 10.0),
            'random_seed': 42,
            'verbose': 0,
        }
        scores = []
        for train_idx, val_idx in _cb_splits:
            if len(train_idx) < 30 or len(val_idx) < 10:
                continue
            model = CatBoostRegressor(**params)
            model.fit(X[train_idx], y[train_idx], verbose=0)
            pred = model.predict(X[val_idx])
            # 用 spearman correlation 作为指标
            from scipy.stats import spearmanr
            corr, _ = spearmanr(pred, y[val_idx])
            scores.append(corr if not np.isnan(corr) else 0)
        return np.mean(scores) if scores else 0

    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\n[Optuna-CB] Best Spearman={study.best_value:.4f}")
    print(f"  Best params: {study.best_params}")

    # 用最优参数训练最终模型并保存
    best = study.best_params
    best['random_seed'] = 42
    best['verbose'] = 0
    model = CatBoostRegressor(**best)
    model.fit(X, y, verbose=0)

    if os.path.exists(MODEL_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = MODEL_PATH.replace(".cbm", f".{ts}.bak")
        os.rename(MODEL_PATH, bak)
    model.save_model(MODEL_PATH + ".tmp")
    os.replace(MODEL_PATH + ".tmp", MODEL_PATH)

    print(f"  Model saved: {MODEL_PATH}")
    return study


if __name__ == "__main__":
    print("=" * 50)
    print("  Optuna CatBoost Tuning")
    print("=" * 50)
    tune_catboost(n_trials=30)
    print("\n  Done")
