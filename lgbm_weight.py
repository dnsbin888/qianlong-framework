"""LightGBM 因子组合模型 v1.0

替代加权求和: 用GBDT学习16个因子间非线性关系
训练: 120天×500只截面数据
预测: 返回每只股票的LightGBM评分(0-100)
"""
import sys, os, json, pickle, shutil, numpy as np
from datetime import datetime
import lightgbm as lgb

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "lgbm_model_stock.pkl")


def load_training_data(days=120, sample=500):
    """从因子注册表 + parquet数据构建训练集"""
    import pandas as pd
    from data_loader import load_stock_data_cache
    from factor_registry import get_all_compute_fns

    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=days)
    if not stock_data:
        return None, None
    stock_data = {k: v for k, v in stock_data.items() if not k.startswith(('sh000', 'sz399', 'bj'))}

    compute_fns = get_all_compute_fns()
    if not compute_fns:
        return None, None

    # 日期轴
    best = max(stock_data.values(), key=lambda df: len(df) if hasattr(df, '__len__') else 0)
    dates = sorted(set(str(ts)[:10] for ts in best.index))[-days:]
    dates = dates[20:]  # 跳过前20天(无历史)

    all_syms = sorted(stock_data.keys())
    import random as _rnd
    _rnd.seed(42)
    _rnd.shuffle(all_syms)

    X_rows, y_rows = [], []
    factor_names = sorted(compute_fns.keys())

    for date_str in dates[-30:]:  # 最近30天
        pool = []
        for s in all_syms:
            if s in stock_data:
                try:
                    stock_data[s].index.get_loc(pd.Timestamp(date_str))
                    pool.append(s)
                except:
                    continue
            if len(pool) >= sample:
                break
        for sym in pool[:sample]:
            df = stock_data[sym]
            try:
                idx = df.index.get_loc(pd.Timestamp(date_str))
            except:
                continue
            if idx < 20 or idx + 5 >= len(df):
                continue
            past = df.iloc[max(0, idx - 60):idx + 1]
            fwd_ret = (float(df.iloc[idx + 5]["close"]) - float(df.iloc[idx]["close"])) / max(
                float(df.iloc[idx]["close"]), 0.01)

            row = []
            for fn in factor_names:
                func = compute_fns[fn]
                try:
                    val = func(past)
                except:
                    val = None
                if val is not None and np.isfinite(val):
                    row.append(float(np.clip(val, -100, 100)))
                else:
                    row.append(0.0)
            X_rows.append(row)
            y_rows.append(fwd_ret)

    if len(X_rows) < 100:
        return None, None
    X = np.array(X_rows, dtype=np.float32)
    # 截面 winsorize: 每列钳制到 [P0.5, P99.5] (与预测端对齐)
    for col in range(X.shape[1]):
        col_data = X[:, col]
        lo, hi = np.percentile(col_data, [0.5, 99.5])
        X[:, col] = np.clip(col_data, lo, hi)
    return X, np.array(y_rows), factor_names


def train(save=True):
    """训练LightGBM模型"""
    result = load_training_data(days=90, sample=200)
    if result is None or result[0] is None:
        print("[LGBM] 训练数据不足")
        return None

    X, y, factor_names = result[0], result[1], result[2] if len(result) > 2 else []
    print(f"[LGBM] {len(X)} 样本, {X.shape[1]} 因子")

    # 过滤y的极端值
    mask = (y > -0.3) & (y < 0.3)
    X, y = X[mask], y[mask]
    print(f"[LGBM] 过滤后 {len(X)} 样本")

    model = lgb.LGBMRegressor(
        n_estimators=200, max_depth=5, num_leaves=31,
        learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1,
    )
    # P1-1: 时间序列split + 早停验证
    split = int(len(X) * 0.8)
    if split > 0 and len(X) - split >= 20:
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  eval_metric='rmse',
                  callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)])
        # 验证集评估
        from sklearn.metrics import r2_score
        y_pred = model.predict(X_val)
        r2 = r2_score(y_val, y_pred)
        print(f"[LGBM] 验证集 R²={r2:.4f}, best_iter={model.best_iteration_}")
    else:
        model.fit(X, y)
        print("[LGBM] 样本不足, 全量训练 (无验证)")

    # 特征重要性
    importance = []
    if len(factor_names) == X.shape[1]:
        for i, name in enumerate(factor_names):
            importance.append({"factor": name, "importance": round(float(model.feature_importances_[i]), 4)})
        importance.sort(key=lambda x: -x["importance"])

    if save:
        # P1-1: 版本备份 — 保留最近5个版本, 防坏模型覆盖好模型
        if os.path.exists(MODEL_PATH):
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _bak = MODEL_PATH.replace(".pkl", f".{_ts}.bak")
            try:
                shutil.copy2(MODEL_PATH, _bak)
                # 清理旧版本 (保留最近5个)
                _dir = os.path.dirname(MODEL_PATH)
                _prefix = os.path.basename(MODEL_PATH).replace(".pkl", "")
                _baks = sorted([f for f in os.listdir(_dir) if f.startswith(_prefix) and f.endswith(".bak")])
                for _old in _baks[:-5]:
                    os.remove(os.path.join(_dir, _old))
            except Exception as e:
                print(f"[LGBM] 版本备份失败: {e}")
        # P1-1: 原子写入 (tmp + os.replace, 防崩溃损坏模型)
        tmp = MODEL_PATH + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"model": model, "factors": factor_names, "importance": importance,
                         "r2_val": round(r2, 4) if 'r2' in dir() else None}, f)
        os.replace(tmp, MODEL_PATH)
        print(f"[LGBM] 模型已保存: {MODEL_PATH}")
        if importance:
            print("[LGBM] Top5 特征重要性:")
            for imp in importance[:5]:
                print(f"  {imp['factor']}: {imp['importance']:.4f}")

    return model


def predict(factor_values: dict) -> float:
    """单只股票预测评分 0-100"""
    if not os.path.exists(MODEL_PATH):
        return None
    with open(MODEL_PATH, "rb") as f:
        model_data = pickle.load(f)
    model = model_data["model"]
    factor_names = model_data["factors"]

    row = []
    for fn in factor_names:
        val = factor_values.get(fn)
        if val is not None and np.isfinite(val):
            row.append(float(np.clip(val, -100, 100)))
        else:
            row.append(0.0)
    pred = float(model.predict(np.array([row]))[0])
    return round(min(100, max(0, (pred + 0.05) * 1000)), 1)


def get_importance() -> list:
    """获取特征重要性 — 从模型文件提取"""
    json_path = r"D:\quant_web\data\lgbm_importance.json"
    if os.path.exists(json_path):
        return json.load(open(json_path, encoding="utf-8"))
    if not os.path.exists(MODEL_PATH):
        return []
    with open(MODEL_PATH, "rb") as f:
        data = pickle.load(f)
    imp = data.get("importance", [])
    if imp:
        return imp
    # 兜底: 从模型对象提取
    model = data.get("model")
    if model and hasattr(model, 'feature_importances_'):
        factors = data.get("factors", [])
        raw = list(model.feature_importances_)
        return [{"factor": factors[i] if i < len(factors) else f"f{i}",
                 "importance": round(float(raw[i]), 4)} for i in range(len(raw))]
    return []


def factor_lgbm(df) -> float | None:
    """LightGBM因子: 16因子非线性组合评分 (可作为独立因子注册)"""
    if not os.path.exists(MODEL_PATH):
        return None
    with open(MODEL_PATH, "rb") as f:
        model_data = pickle.load(f)
    model = model_data["model"]
    factor_names = model_data["factors"]

    from factor_registry import get_all_compute_fns
    compute_fns = get_all_compute_fns()
    if not compute_fns:
        return None

    if len(df) < 20:
        return None

    row = []
    for fn in factor_names:
        func = compute_fns.get(fn)
        if not func:
            row.append(0.0)
            continue
        try:
            val = func(df)
        except:
            val = None
        if val is not None and np.isfinite(val):
            row.append(float(np.clip(val, -100, 100)))
        else:
            row.append(0.0)

    pred = float(model.predict(np.array([row]))[0])
    return round(min(100, max(0, (pred + 0.05) * 1000)), 1)


if __name__ == "__main__":
    import pandas as pd
    m = train()
    if m:
        print("\n[LGBM] 训练完成")
        imp = get_importance()
        for i in imp[:10]:
            print(f"  {i['factor']:30s} {i['importance']:.4f}")
