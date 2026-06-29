"""xgb_factor_weight.py — XGBoost 因子动态加权 (Phase A, 对标 BigQuant AI选股)

替代线性 Σ(IC_weight × factor_zscore)，学习因子间非线性组合关系。

架构:
  训练: 过去60天截面 → 7因子z-score → XGBoost → 预测 forward_5d 收益分位
  预测: 今日截面 → 7因子z-score → 模型 → 0-99评分 → buy_signal(1-5)
  更新: 每周重训练，模型持久化到 xgb_model.json (XGBoost JSON格式)

对标: BigQuant AI选股模板 / 聚宽因子合成 / WorldQuant ML组合
"""

import json
import logging
import os
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger("quant_framework.xgb")

# ── 活跃因子列表 (从 factor_registry 自动读取) ──
ACTIVE_FACTORS = [
    "trend_score", "defensive_v2", "qmt_composite", "chase_v2",
    "chip_v2", "momentum_score", "bull_line", "fund_v2",
]

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xgb_model.json")
MIN_TRAIN_SAMPLES = 5000   # 最少训练样本数
TRAIN_DAYS = 60            # 训练窗口(天)
FORWARD_DAYS = 5           # 预测未来N天收益
TOP_PCT = 0.2              # 训练目标: top 20% = 1, bottom 80% = 0


def _safe_div(a, b, default=0.0):
    return a / b if b else default


def compute_factor_zscore(factor_values: dict, all_stocks_data: list[dict]) -> dict:
    """对当前截面的所有股票计算每个因子的z-score。"""
    n = len(all_stocks_data)
    if n < 30:
        return {}
    scores = {f: [] for f in ACTIVE_FACTORS}
    symbols = []
    for row in all_stocks_data:
        symbols.append(row.get("symbol", ""))
        for f in ACTIVE_FACTORS:
            v = row.get(f, 0) or 0
            try:
                scores[f].append(float(v))
            except (ValueError, TypeError):
                scores[f].append(0.0)

    result = {}
    for f in ACTIVE_FACTORS:
        arr = np.array(scores[f], dtype=np.float64)
        mu = np.nanmean(arr)
        sigma = np.nanstd(arr)
        if sigma == 0 or np.isnan(sigma):
            z = np.zeros(n)
        else:
            z = (arr - mu) / sigma
        result[f] = {symbols[i]: round(float(z[i]), 6) for i in range(n)}
    return result


def build_training_data(stock_data: dict, factor_cache: list,
                        start_date: str, end_date: str) -> tuple:
    """从 STOCK_DATA 历史OHLCV 直接构建训练集（不依赖factor_cache日期）。

    STOCK_DATA = dict[str, DataFrame(120天OHLCV)], 数据源可靠。
    对每个交易日:
      X: 从 OHLCV 计算的 7个因子代理z-score
      y: forward_5d 收益率 (top 20% = 1, rest = 0)

    Returns:
        X_train: np.array (n_samples, n_features)
        y_train: np.array (n_samples,)
    """
    if not stock_data:
        return np.array([]), np.array([]), {"error": "STOCK_DATA 为空", "stock_count": 0}

    # 诊断: 采样第一只股票
    _sample_sym, _sample_df, _sample_idx_type = None, None, "N/A"
    _stock_count, _df_count = 0, 0
    for sym, df in stock_data.items():
        _stock_count += 1
        if df is not None and len(df) > 0:
            _df_count += 1
            if _sample_sym is None:
                _sample_sym, _sample_df = sym, df
                _sample_idx_type = str(type(df.index[0])).split("'")[1] if len(df) > 0 else "empty"

    if _df_count == 0:
        return np.array([]), np.array([]), {
            "error": f"STOCK_DATA 中 {_stock_count} 个key但无有效DataFrame",
            "stock_count": _stock_count, "valid_df": 0
        }

    # 找到所有股票共有的交易日
    all_dates = set()
    for sym, df in stock_data.items():
        if df is None or len(df) < 60:
            continue
        try:
            for d in df.index:
                ds = str(d)[:10]
                if start_date <= ds <= end_date:
                    all_dates.add(ds)
        except Exception:
            continue

    dates = sorted(all_dates)
    if len(dates) < 30:
        return np.array([]), np.array([]), {
            "error": f"共同交易日不足: {len(dates)}天 (需要≥30)",
            "sample_sym": _sample_sym, "index_type": _sample_idx_type,
            "sample_dates": [str(d)[:10] for d in (_sample_df.index[:5].tolist() if _sample_df is not None and len(_sample_df) > 0 else [])],
            "stock_count": _stock_count, "valid_df": _df_count,
            "date_range": f"{start_date} ~ {end_date}"
        }

    logger.info(f"[XGB] {len(stock_data)}只股票, {len(dates)}个交易日, 构建训练数据...")

    all_X, all_y = [], []
    processed = 0

    for i, day in enumerate(dates[:-FORWARD_DAYS]):
        target_day = dates[i + FORWARD_DAYS]

        day_features = []
        day_returns = []

        for sym, df in stock_data.items():
            if df is None or len(df) < 60:
                continue
            try:
                # 找当日行
                day_mask = df.index.astype(str).str.startswith(day)
                tgt_mask = df.index.astype(str).str.startswith(target_day)
                if not day_mask.any() or not tgt_mask.any():
                    continue

                day_row = df.loc[day_mask].iloc[-1]
                tgt_row = df.loc[tgt_mask].iloc[-1]

                close = float(day_row['close'])
                if close <= 0:
                    continue

                # ── 从OHLCV计算因子代理z-score的原始值 ──
                close_hist = df['close'].values
                vol_hist = df['volume'].values
                day_idx = int(day_mask.values.argmax()) if hasattr(day_mask, 'values') else 0

                # 1. trend_score proxy: (close - MA20) / MA20
                ma20 = np.mean(close_hist[max(0, day_idx-20):day_idx+1]) if day_idx >= 0 else close
                trend = (close - ma20) / ma20 if ma20 > 0 else 0

                # 2. defensive_v2 proxy: -volatility (20d std of returns)
                rets_20 = np.diff(close_hist[max(0, day_idx-21):day_idx+1]) / (close_hist[max(0, day_idx-21):day_idx] + 1e-9)
                vol20 = np.std(rets_20) if len(rets_20) > 1 else 0
                defensive = -vol20

                # 3. qmt_composite proxy: 0.5*momentum + 0.5*volume_ratio
                vol_ma5 = np.mean(vol_hist[max(0, day_idx-5):day_idx+1]) if day_idx >= 0 else float(vol_hist[-1])
                vol_ma20_v = np.mean(vol_hist[max(0, day_idx-20):day_idx+1]) if day_idx >= 0 else vol_ma5
                vol_ratio = vol_ma5 / (vol_ma20_v + 1e-9)

                # 4. chase_v2 proxy: 5d momentum
                close_5d_ago = close_hist[max(0, day_idx-5)] if day_idx >= 5 else close_hist[0]
                chase = (close - close_5d_ago) / (close_5d_ago + 1e-9)

                # 5. chip_v2 proxy: volume ratio
                chip = vol_ratio

                # 6. momentum_score proxy: 10d momentum
                close_10d_ago = close_hist[max(0, day_idx-10)] if day_idx >= 10 else close_hist[0]
                momentum = (close - close_10d_ago) / (close_10d_ago + 1e-9)

                # 7. bull_line proxy: close / MA60
                ma60 = np.mean(close_hist[max(0, day_idx-60):day_idx+1]) if day_idx >= 0 else close
                bull = (close - ma60) / ma60 if ma60 > 0 else 0

                # 8. fund_v2 proxy: -volume_ratio (资金流出)
                fund = -vol_ratio

                features = [trend, defensive, vol_ratio * 0.5 + chase * 0.5,
                           chase, chip, momentum, bull, fund]

                # forward return
                fwd_close = float(tgt_row['close'])
                fwd_ret = (fwd_close - close) / close

                day_features.append(features)
                day_returns.append(fwd_ret)

            except Exception:
                continue

        if len(day_features) < 50:
            continue

        # 截面z-score标准化
        arr = np.array(day_features, dtype=np.float64)
        n_cols = arr.shape[1]
        z_arr = np.zeros_like(arr)
        for j in range(n_cols):
            col = arr[:, j]
            mu = np.nanmean(col)
            sigma = np.nanstd(col)
            if sigma > 0:
                z_arr[:, j] = (col - mu) / sigma

        # 目标: top 20% forward return = 1
        ret_arr = np.array(day_returns)
        threshold = np.percentile(ret_arr, 100 * (1 - TOP_PCT))
        targets = (ret_arr >= threshold).astype(int)

        all_X.extend(z_arr.tolist())
        all_y.extend(targets.tolist())
        processed += 1

    if len(all_X) < MIN_TRAIN_SAMPLES:
        logger.warning(f"[XGB] 训练样本不足: {len(all_X)} < {MIN_TRAIN_SAMPLES} (处理了{processed}天)")
        return np.array([]), np.array([])

    logger.info(f"[XGB] 训练集: {len(all_X)} 样本 × {len(ACTIVE_FACTORS)} 特征 ({processed}个交易日)")
    return np.array(all_X, dtype=np.float32), np.array(all_y, dtype=np.int32), {"processed_days": processed}


def _ensure_xgb():
    """确保 xgboost 可用，否则抛出明确错误。"""
    try:
        import xgboost as xgb
        return xgb
    except ImportError:
        raise ImportError(
            "[XGB] xgboost 未安装。XGBoost 加权是 Phase A 核心升级，不可跳过。\n"
            "请运行: pip install xgboost scikit-learn\n"
            "安装后调用 POST /api/factor/xgb-train 训练模型"
        )


def train_model(X_train: np.ndarray, y_train: np.ndarray) -> Optional[object]:
    """训练 XGBoost 分类模型。xgboost 不可用时抛出 ImportError。"""
    if len(X_train) < MIN_TRAIN_SAMPLES:
        return None

    xgb = _ensure_xgb()

    pos = int(y_train.sum())
    neg = len(y_train) - pos
    scale_pos_weight = neg / max(pos, 1)

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        objective='binary:logistic',
        eval_metric='auc',
        random_state=42,
        n_jobs=1,
    )

    logger.info(f"[XGB] 训练中... 正样本={pos} 负样本={neg} scale={scale_pos_weight:.1f}")
    model.fit(X_train, y_train, verbose=False)

    # 评估
    try:
        from sklearn.metrics import roc_auc_score
        y_prob = model.predict_proba(X_train)[:, 1]
        auc = roc_auc_score(y_train, y_prob)
        logger.info(f"[XGB] 训练完成 AUC={auc:.4f}")
    except Exception:
        logger.info("[XGB] 训练完成")

    return model


def predict_scores(model, factor_rows: list[dict]) -> list[float]:
    """对当前截面股票列表预测评分（0-1概率 → 0-99分数）。"""
    if model is None:
        return [50.0] * len(factor_rows)

    # 计算z-score
    z = compute_factor_zscore({}, factor_rows)
    X = []
    symbols = []
    for row in factor_rows:
        sym = row.get("symbol", "")
        features = [z[f].get(sym, 0) for f in ACTIVE_FACTORS]
        X.append(features)
        symbols.append(sym)

    X_arr = np.array(X, dtype=np.float32)

    try:
        proba = model.predict_proba(X_arr)[:, 1]  # top-20%概率
        scores = [round(float(p) * 99, 1) for p in proba]
        return scores
    except Exception as e:
        logger.warning(f"[XGB] 预测失败: {e}, 回退线性评分")
        return [50.0] * len(factor_rows)


def save_model(model) -> str:
    """保存模型到 JSON 文件。"""
    if model is None:
        return ""
    try:
        model.save_model(MODEL_PATH)
        logger.info(f"[XGB] 模型已保存: {MODEL_PATH}")
        return MODEL_PATH
    except Exception as e:
        logger.error(f"[XGB] 保存失败: {e}")
        return ""


def load_model() -> Optional[object]:
    """加载持久化的模型。xgboost 不可用时抛出 ImportError。"""
    if not os.path.exists(MODEL_PATH):
        return None
    xgb = _ensure_xgb()
    try:
        model = xgb.XGBClassifier()
        model.load_model(MODEL_PATH)
        logger.info(f"[XGB] 模型已加载: {MODEL_PATH}")
        return model
    except Exception as e:
        logger.warning(f"[XGB] 加载失败: {e}")
        return None


# ── 顶层API ──

_xgb_model = None
_xgb_ready = False
_xgb_last_train = None


def get_model():
    """获取当前模型（懒加载）。xgboost 不可用时返回 None。"""
    global _xgb_model
    if _xgb_model is None:
        try:
            _xgb_model = load_model()
        except ImportError:
            return None
    return _xgb_model


def is_ready() -> bool:
    """XGBoost 模型是否就绪。"""
    return _xgb_ready or get_model() is not None


def get_status() -> dict:
    """获取 XGBoost 模块状态。"""
    try:
        _ensure_xgb()
        xgb_installed = True
    except ImportError:
        xgb_installed = False

    m = get_model()
    return {
        "xgboost_installed": xgb_installed,
        "model_trained": m is not None,
        "ready": m is not None,  # 安装+训练都完成才算 ready
        "last_train": _xgb_last_train,
        "features": ACTIVE_FACTORS,
        "model_path": MODEL_PATH if m else None,
        "action": None if m else (
            "pip install xgboost scikit-learn" if not xgb_installed
            else "POST /api/factor/xgb-train 训练模型"
        ),
    }


def run_training(stock_data: dict, factor_cache=None,
                 start_date: str = None, end_date: str = None) -> dict:
    """完整训练流程: 从STOCK_DATA构建数据 → 训练 → 保存。"""
    global _xgb_model, _xgb_ready, _xgb_last_train

    if start_date is None:
        start_date = (datetime.now() - timedelta(days=TRAIN_DAYS + 30)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"[XGB] 开始训练 {start_date} → {end_date}")
    result = build_training_data(stock_data, factor_cache, start_date, end_date)
    X, y = result[0], result[1]
    diag = result[2] if len(result) > 2 else {}

    if len(X) < MIN_TRAIN_SAMPLES:
        return {"success": False, "error": f"训练样本不足 ({len(X)} < {MIN_TRAIN_SAMPLES})",
                "samples": len(X), "diagnostic": diag}

    model = train_model(X, y)
    if model is None:
        return {"success": False, "error": "训练失败"}

    path = save_model(model)
    _xgb_model = model
    _xgb_ready = True
    _xgb_last_train = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "success": True,
        "samples": len(X),
        "features": len(ACTIVE_FACTORS),
        "model_path": path,
        "trained_at": _xgb_last_train,
    }


def _compute_stock_proxies(stock_data: dict) -> list[dict]:
    """从 STOCK_DATA 最新一天为所有股票计算因子代理特征。

    Returns:
        [{"symbol": "600001", "trend_score": 0.05, ...}, ...]
    """
    rows = []
    for sym, df in stock_data.items():
        if df is None or len(df) < 60:
            continue
        try:
            close_hist = df['close'].values
            vol_hist = df['volume'].values
            close = float(close_hist[-1])
            if close <= 0:
                continue
            n = len(close_hist)

            ma20 = np.mean(close_hist[max(0, n-20):])
            trend = (close - ma20) / ma20 if ma20 > 0 else 0

            rets_20 = np.diff(close_hist[max(0, n-21):]) / (close_hist[max(0, n-21):n-1] + 1e-9)
            defensive = -float(np.std(rets_20)) if len(rets_20) > 1 else 0

            vol_ma5 = np.mean(vol_hist[max(0, n-5):])
            vol_ma20 = np.mean(vol_hist[max(0, n-20):])
            vol_ratio = vol_ma5 / (vol_ma20 + 1e-9)

            close_5d = close_hist[max(0, n-6)] if n >= 6 else close_hist[0]
            chase = (close - close_5d) / (close_5d + 1e-9)

            close_10d = close_hist[max(0, n-11)] if n >= 11 else close_hist[0]
            momentum = (close - close_10d) / (close_10d + 1e-9)

            ma60 = np.mean(close_hist[max(0, n-60):])
            bull = (close - ma60) / ma60 if ma60 > 0 else 0

            fund = -vol_ratio

            rows.append({
                "symbol": sym,
                "trend_score": trend, "defensive_v2": defensive,
                "qmt_composite": vol_ratio * 0.5 + chase * 0.5,
                "chase_v2": chase, "chip_v2": vol_ratio,
                "momentum_score": momentum, "bull_line": bull, "fund_v2": fund,
            })
        except Exception:
            continue
    return rows


def score_stocks(factor_rows: list[dict], stock_data: dict = None) -> list[dict]:
    """对股票列表用 XGBoost 打分。

    优先用 STOCK_DATA 计算代理特征（与训练一致）。
    如果模型未训练，xgb_score 设为 null。

    Args:
        factor_rows: 可选，用于取 symbol 列表
        stock_data: STOCK_DATA dict

    Returns:
        每行增加 "xgb_score" (0-99, 或 null 表示不可用)
    """
    model = get_model()
    if model is None:
        for row in factor_rows:
            row["xgb_score"] = None
        return factor_rows

    # 用 STOCK_DATA 计算特征（与训练一致），避免 factor_cache 日期问题
    if stock_data:
        rows = _compute_stock_proxies(stock_data)
        scores = predict_scores(model, rows)
        score_map = {r["symbol"]: scores[i] if i < len(scores) else None
                     for i, r in enumerate(rows)}
    else:
        scores = predict_scores(model, factor_rows)
        score_map = {factor_rows[i].get("symbol", ""): scores[i] if i < len(scores) else None
                     for i in range(len(factor_rows))}

    for row in factor_rows:
        sym = row.get("symbol", "")
        row["xgb_score"] = score_map.get(sym, None)
    return factor_rows
