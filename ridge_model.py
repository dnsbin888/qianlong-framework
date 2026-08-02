"""Ridge 线性模型 v1.0 — 三模型集成第三票 (2026-07-13)
定位: 与 LGBM/XGB 形成最大算法多样性 (ρ < 0.2)
对标: CFA 2025 + Portfolio123 2025: 线性是最佳第三模型
原理: Ridge 捕捉全局线性趋势, 树模型捕捉局部非线性, 错误类型互不相关
"""
import sys, os, pickle, numpy as np
from datetime import datetime
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

MODEL_PATH = os.path.join(os.path.dirname(__file__), "ridge_model.pkl")


def load_training_data(days=250, sample=500):
    """从因子注册表 + parquet 数据构建训练集 (与 lgbm_weight 对齐)"""
    from data_loader import load_stock_data_cache
    from factor_registry import get_all_compute_fns

    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=days)
    if not stock_data:
        return None, None, None
    stock_data = {k: v for k, v in stock_data.items()
                  if not k.startswith(('sh000', 'sz399', 'bj'))}

    compute_fns = get_all_compute_fns(exclude_ml=True)
    if not compute_fns:
        return None, None, None

    factor_names = sorted(compute_fns.keys())
    dates = sorted(set(str(ts)[:10] for df in stock_data.values()
                       for ts in df.index))[-days:]
    dates = dates[20:]

    # CSRank: 先收集→按日期分组→截面排名→归一化标签 (广发金工2025)
    from collections import defaultdict
    from scipy.stats import rankdata
    import random as _rnd
    _rnd.seed(42)
    date_samples = defaultdict(list)

    for d in dates[-60:]:
        syms = list(stock_data.keys())
        _rnd.shuffle(syms)
        for sym in syms[:sample]:
            df = stock_data.get(sym)
            if df is None or len(df) < 21: continue
            try: idx = list(df.index).index(next(ts for ts in df.index if str(ts)[:10] >= d))
            except StopIteration: continue
            if idx < 20 or idx + 5 >= len(df): continue
            past = df.iloc[max(0, idx - 60):idx + 1]
            fwd_ret = (float(df.iloc[idx + 5]["close"]) - float(df.iloc[idx]["close"])) / max(float(df.iloc[idx]["close"]), 0.01)
            row = []
            for fn in factor_names:
                func = compute_fns.get(fn)
                if not func: row.append(0.0); continue
                try: val = func(past)
                except Exception: val = None
                row.append(float(np.clip(val, -100, 100)) if val is not None and np.isfinite(val) else 0.0)
            date_samples[d].append((row, fwd_ret))

    X_rows, y_rows = [], []
    for d, samples in date_samples.items():
        if len(samples) < 30: continue
        rets = [s[1] for s in samples]
        ranks = rankdata(rets) / len(rets)
        for (row, _), rank in zip(samples, ranks):
            X_rows.append(row)
            y_rows.append(float(rank))

    if len(X_rows) < 100:
        return None, None, None
    return np.array(X_rows, dtype=np.float32), np.array(y_rows), factor_names


def train():
    """训练 Ridge 模型 — 轻量, <1 秒"""
    result = load_training_data(days=250, sample=500)
    if result is None or result[0] is None:
        print("[Ridge] 训练数据不足")
        return None

    X, y, factor_names = result
    mask = (y > -0.3) & (y < 0.3)
    X, y = X[mask], y[mask]
    print(f"[Ridge] {len(X)} 样本, {X.shape[1]} 因子")

    # 标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 时序分割
    split = int(len(X) * 0.8)
    X_train, X_val = X_scaled[:split], X_scaled[split:]
    y_train, y_val = y[:split], y[split:]

    # Ridge 回归 (alpha=1.0, 行业标准)
    model = Ridge(alpha=1.0, random_state=42)
    model.fit(X_train, y_train)

    # 验证
    from sklearn.metrics import r2_score
    y_pred = model.predict(X_val)
    r2 = r2_score(y_val, y_pred)
    ic = np.corrcoef(y_pred, y_val)[0, 1] if len(y_val) > 1 else 0
    print(f"[Ridge] 验证集 R²={r2:.4f} IC={ic:.4f}")

    # 保存
    if os.path.exists(MODEL_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = MODEL_PATH.replace(".pkl", f".{ts}.bak")
        os.rename(MODEL_PATH, bak)
    pickle.dump({"model": model, "scaler": scaler,
                 "factor_names": factor_names}, open(MODEL_PATH, "wb"))
    print(f"[Ridge] 模型已保存: {MODEL_PATH}")
    return model


def predict_scores(stock_data, factor_cache=None):
    """批量预测 — 返回 {symbol: score (0-100 raw)}"""
    if not os.path.exists(MODEL_PATH):
        print("[Ridge] 模型未训练, 跳过")
        return {}

    saved = pickle.load(open(MODEL_PATH, "rb"))
    model = saved["model"]
    scaler = saved["scaler"]
    factor_names = saved["factor_names"]

    from factor_registry import get_all_compute_fns
    compute_fns = get_all_compute_fns(exclude_ml=True)

    X_rows, valid_syms = [], []
    for sym in list(stock_data.keys())[:300]:
        df = stock_data.get(sym)
        if df is None or len(df) < 20:
            continue
        row = []
        for fn in factor_names:
            func = compute_fns.get(fn)
            if not func:
                row.append(0.0); continue
            try:
                val = func(df)
            except Exception:
                val = None
            if val is not None and np.isfinite(val):
                row.append(float(np.clip(val, -100, 100)))
            else:
                row.append(0.0)
        X_rows.append(row)
        valid_syms.append(sym)

    if not X_rows:
        return {}
    X = np.array(X_rows, dtype=np.float32)
    X_scaled = scaler.transform(X)
    raw = model.predict(X_scaled)

    scores = {}
    for sym, r in zip(valid_syms, raw):
        scores[sym] = round(float(np.clip(r * 100 + 50, 0, 100)), 1)
    return scores


if __name__ == "__main__":
    print("训练 Ridge 模型...")
    train()
    print("测试预测...")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    scores = predict_scores(sd)
    top = sorted(scores.items(), key=lambda x: -x[1])[:5]
    print(f"预测: {len(scores)} 只, Top5: {top}")
