"""Optuna 超参搜索 XGBoost CSRank v2.0 — 排序回归替代分类
对标: 蓝图 B1 + 广发金工2025 CSRank标准
用法: python optuna_tune_xgb.py
"""
import sys, os, json, numpy as np
from datetime import datetime
from scipy.stats import spearmanr, rankdata

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xgb_model.json")


def tune_xgb(stock_data=None, n_trials=30):
    """Optuna 自动搜索 XGBoost — CSRank回归 (对齐LGBM排序任务)"""
    import optuna
    from xgboost import XGBRegressor
    from purged_cv import stock_group_kfold, get_stock_ids_from_rows

    if stock_data is None:
        from data_loader import load_stock_data_cache
        stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=90)

    from xgb_factor_weight import _compute_stock_proxies
    # B4: 去死因子fund_v2 → 7因子
    ACTIVE_FACTORS = ["trend_score","defensive_v2","qmt_composite","chase_v2",
                      "chip_v2","momentum_score","bull_line","fund_v2"]
    _forward_days = 5

    # 特征从T日截断, 标签=T→T+5 (防时间泄露)
    _truncated = {}
    for sym, df in stock_data.items():
        if df is not None and len(df) > _forward_days + 20:
            _truncated[sym] = df.iloc[:-_forward_days]

    rows = _compute_stock_proxies(_truncated)
    print(f"[Optuna-XGB] {len(rows)}只 (特征≤T, T日截断)")

    if len(rows) < 300:
        print(f"ERROR: 样本不足 ({len(rows)})")
        return

    X_all = np.array([[r.get(f, 0) for f in ACTIVE_FACTORS] for r in rows], dtype=np.float32)
    # CSRank标签: 5日收益→截面排名→0-1
    y_raw = np.zeros(len(rows))
    valid_mask = np.ones(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        sym = r.get("symbol", "")
        df_full = stock_data.get(sym)
        if df_full is not None and len(df_full) > _forward_days:
            c = df_full["close"].values
            t_price = c[-_forward_days - 1]
            fwd_price = c[-1]
            if t_price > 0:
                y_raw[i] = (fwd_price - t_price) / t_price
            else:
                valid_mask[i] = False
        else:
            valid_mask[i] = False

    X_all = X_all[valid_mask]; y_raw = y_raw[valid_mask]
    rows_valid = [r for i, r in enumerate(rows) if valid_mask[i]]
    y_all = rankdata(y_raw) / len(y_raw)  # CSRank: 0=最差, 1=最好

    print(f"[Optuna-XGB] {len(X_all)} samples, {len(ACTIVE_FACTORS)} factors "
          f"(CSRank标签, 特征≤T, 标签=T→T+{_forward_days})")

    _stock_ids = get_stock_ids_from_rows(rows_valid)
    _gkf_splits = stock_group_kfold(_stock_ids, n_splits=5)

    def objective(trial):
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 50, 300),
            'max_depth': trial.suggest_int('max_depth', 3, 8),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 1e-5, 1.0, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', 1e-5, 1.0, log=True),
            'random_state': 42, 'verbosity': 0,
        }
        scores = []
        for train_idx, val_idx in _gkf_splits:
            if len(train_idx) < 50 or len(val_idx) < 20:
                continue
            model = XGBRegressor(**params)
            model.fit(X_all[train_idx], y_all[train_idx])
            pred = model.predict(X_all[val_idx])
            ic, _ = spearmanr(pred, y_all[val_idx])
            if not np.isnan(ic): scores.append(abs(ic))
        return np.mean(scores) if scores else 0

    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\n[Optuna-XGB-CSRank] Best IC={study.best_value:.4f}")
    print(f"  Best params: {study.best_params}")

    best = study.best_params
    best['random_state'] = 42; best['verbosity'] = 0
    model = XGBRegressor(**best)
    model.fit(X_all, y_all)
    model.save_model(MODEL_PATH)
    print(f"  Model saved: {MODEL_PATH}")
    return study


if __name__ == "__main__":
    print("=" * 50)
    print("  Optuna XGBoost CSRank v2.0")
    print("=" * 50)
    tune_xgb(n_trials=30)
    print("\n  Done")
