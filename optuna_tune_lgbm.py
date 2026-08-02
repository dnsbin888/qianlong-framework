"""B1: Optuna 超参优化 LGBM — 预加载数据, 秒级搜索
用法: python optuna_tune_lgbm.py --trials 50
"""
import sys, os, pickle, numpy as np
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

import optuna
import lightgbm as lgb
from scipy.stats import spearmanr
from purged_cv import purged_walk_forward

# 全局训练数据（只加载一次）
_X, _y, _factor_names, _sample_dates = None, None, None, None


def load_training_data(days=90, sample=500):
    global _X, _y, _factor_names, _sample_dates
    if _X is not None and _sample_dates is not None:
        return _X, _y, _factor_names, _sample_dates

    import pandas as pd
    from data_loader import load_stock_data_cache
    from factor_registry import get_all_compute_fns

    print(f"  加载训练数据...")
    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=days)
    if not stock_data:
        return None, None, None, None
    # LGBM-Stock: 只保留A股，排除指数/ETF/转债/B股
    _skip = ('sh000','sh11','sh12','sh13','sh14','sh15','sh2','sh5','sz399','sz11','sz12','sz13','sz15','sz16','sz18','sz5','bj')
    stock_data = {k: v for k, v in stock_data.items() if not k.startswith(_skip)}
    _X, _y, _factor_names = None, None, None
    # 只保留 8 个有效因子 (精简: 65→8, 去哑因子)
    from full_market_ic import (_factor_trend_old, _factor_def, _factor_qmt_composite,
                                 _factor_chase, _factor_chip, _factor_mom_old,
                                 _factor_bull_old, _factor_fund)
    compute_fns = {
        "trend_score": _factor_trend_old, "defensive_v2": _factor_def,
        "qmt_composite": _factor_qmt_composite, "chase_v2": _factor_chase,
        "chip_v2": _factor_chip, "momentum_score": _factor_mom_old,
        "bull_line": _factor_bull_old, "fund_v2": _factor_fund,
    }

    dates = []
    for df in stock_data.values():
        if hasattr(df, 'index') and len(df) > 0:
            dates.extend(str(d)[:10] for d in df.index)
    dates = sorted(set(dates))[-days:]
    dates = dates[20:]

    factor_names = sorted(compute_fns.keys())
    all_syms = sorted(stock_data.keys())
    import random; random.seed(42); random.shuffle(all_syms)

    # 先收集所有样本 (按日期分组)
    from collections import defaultdict
    date_samples = defaultdict(list)  # date_str → [(features, fwd_ret)]
    for date_str in dates[-60:]:  # 60天→足够PurgedWalkForward(3折×purge5天)
        pool = []
        for s in all_syms:
            if s in stock_data:
                try:
                    stock_data[s].index.get_loc(pd.Timestamp(date_str))
                    pool.append(s)
                except Exception: continue
            if len(pool) >= sample: break
        for sym in pool[:sample]:
            df = stock_data[sym]
            try: idx = df.index.get_loc(pd.Timestamp(date_str))
            except Exception: continue
            if idx < 20 or idx + 5 >= len(df): continue
            past = df.iloc[max(0, idx - 60):idx + 1]
            fwd_ret = (float(df.iloc[idx + 5]["close"]) - float(df.iloc[idx]["close"])) / max(float(df.iloc[idx]["close"]), 0.01)
            row = []
            for fn in factor_names:
                func = compute_fns[fn]
                try: val = func(past)
                except: val = None
                row.append(float(np.clip(val, -100, 100)) if val is not None and np.isfinite(val) else 0.0)
            date_samples[date_str].append((row, fwd_ret))

    # CSRank: 每天截面排名→归一化标签 (广发金工2025标准)
    X_rows, y_rows, date_rows = [], [], []
    for date_str, samples in date_samples.items():
        if len(samples) < 30: continue
        rets = [s[1] for s in samples]
        from scipy.stats import rankdata
        ranks = rankdata(rets) / len(rets)
        for (row, _), rank in zip(samples, ranks):
            X_rows.append(row)
            y_rows.append(float(rank))
            date_rows.append(date_str)  # B2: 记录每个样本的日期

    X, y = np.array(X_rows), np.array(y_rows)
    date_arr = np.array(date_rows)
    mask = (y > -0.3) & (y < 0.3)
    X, y, date_arr = X[mask], y[mask], date_arr[mask]
    _X, _y, _factor_names, _sample_dates = X, y, factor_names, date_arr
    print(f"   {len(X)} 样本, {X.shape[1]} 因子, {len(set(date_arr))} 天\n")
    return X, y, factor_names, date_arr


def objective(trial):
    X, y, _, dates = _X, _y, _factor_names, _sample_dates
    if X is None or len(X) < 200:
        return -1.0

    params = {
        "n_estimators": trial.suggest_int("n_estimators", 100, 400),
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "num_leaves": trial.suggest_int("num_leaves", 15, 63),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
        "subsample": trial.suggest_float("subsample", 0.7, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "min_child_samples": trial.suggest_int("min_child_samples", 20, 80),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-6, 0.1, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-6, 0.1, log=True),
        "random_state": 42, "verbose": -1, "n_jobs": 1,
    }

    # B2: Purged Walk-Forward — 防5日标签重叠泄露
    _unique_dates = sorted(set(dates))
    _pwf_splits = purged_walk_forward(_unique_dates, n_splits=2, purge_days=5, min_train_size=15)
    # 日期索引→样本索引映射
    _date_to_indices = {}
    for i, d in enumerate(dates):
        _date_to_indices.setdefault(d, []).append(i)

    scores = []
    for date_train_idx, date_val_idx in _pwf_splits:
        # 日期映射到样本
        _train_samples = []
        for di in date_train_idx:
            _train_samples.extend(_date_to_indices.get(_unique_dates[di], []))
        _val_samples = []
        for di in date_val_idx:
            _val_samples.extend(_date_to_indices.get(_unique_dates[di], []))

        if len(_train_samples) < 50 or len(_val_samples) < 20:
            continue

        Xt, Xv = X[_train_samples], X[_val_samples]
        yt, yv = y[_train_samples], y[_val_samples]
        try:
            model = lgb.LGBMRegressor(**params)
            model.fit(Xt, yt)
            ic, _ = spearmanr(model.predict(Xv), yv)
            if not np.isnan(ic): scores.append(abs(ic))
        except: pass

    return float(np.mean(scores)) if scores else -1.0


def main(trials=50):
    print(f"{'='*60}")
    print(f"  Optuna LGBM 超参优化 — {trials} 次搜索")
    print(f"{'='*60}\n")

    load_training_data()  # 预加载

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=trials, show_progress_bar=True)

    print(f"\n✅ 最优 IC={study.best_value:.4f}")
    for k, v in study.best_params.items():
        print(f"   {k}: {v}")

    # 重训 (全量数据, 无需CV)
    X, y, factor_names, _ = _X, _y, _factor_names, _sample_dates
    best_params = {**study.best_params, "random_state": 42, "verbose": -1, "n_jobs": 1}
    model = lgb.LGBMRegressor(**best_params)
    model.fit(X, y)
    model_path = os.path.join(os.path.dirname(__file__), "lgbm_model_stock.pkl")
    imp = [{"factor": fn, "importance": round(float(model.feature_importances_[i]), 4)} for i, fn in enumerate(factor_names)]
    imp.sort(key=lambda x: -x["importance"])
    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "factors": factor_names, "importance": imp}, f)
    print(f"\n📄 模型已保存: {model_path}")
    print("Top5 特征重要性:")
    for item in imp[:5]:
        print(f"   {item['factor']:25s} {item['importance']:.4f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=50)
    args = p.parse_args()
    main(trials=args.trials)
