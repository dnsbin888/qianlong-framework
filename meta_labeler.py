"""Meta-Labeling v1.0 — ML信号可信度打分 (Prado 2018)
对标: Prado "Advances in Financial ML" Chapter 3

原理:
  主模型(LGBM/XGBoost)选股 → Meta-Labeler评估"该不该实际下单"
  学习历史上哪些信号真的赚了钱 → 输出0-1可信度 → 低分信号过滤

用法:
  from meta_labeler import MetaLabeler
  ml = MetaLabeler()
  ml.train()                         # 从trade_log.csv训练
  confidence = ml.predict(signal)    # 预测单信号可信度
  if confidence > 0.5: place_order() # 可信才下单
"""
import sys, os, json, pickle
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

MODEL_PATH = r"D:\quant_framework\meta_labeler_model.pkl"
TRADE_LOG = r"d:\quant_framework\trade_log.csv"
PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
CFG_PATH = r"D:\quant_web\data\qmt_trade_config.json"

FEATURE_NAMES = [
    "lgbm_score", "xgb_score", "cb_score",    # 主模型评分
    "signal_level",                             # 信号等级 1-5
    "volatility_20d",                           # 20日波动率
    "regime_bull", "regime_bear",               # 市场状态 (one-hot)
    "position_pct",                             # 建议仓位%
    "n_models",                                 # 共识模型数
    "turnover_ratio",                           # 换手率
]


class MetaLabeler:
    """Meta-Labeling 信号过滤器"""

    def __init__(self):
        self.model = None
        self._load()

    def _load(self):
        if os.path.exists(MODEL_PATH):
            try:
                with open(MODEL_PATH, "rb") as f:
                    self.model = pickle.load(f)
            except Exception:
                self.model = None

    def _save(self):
        if self.model:
            with open(MODEL_PATH + ".tmp", "wb") as f:
                pickle.dump(self.model, f)
            os.replace(MODEL_PATH + ".tmp", MODEL_PATH)

    # ═══════════════════════════════════════
    # 训练
    # ═══════════════════════════════════════

    def train(self, min_samples=20):
        """从 trade_log.csv 构建训练集 → 训练 LightGBM 二分类器

        标签: net_profit > 0 → 1 (盈利), else → 0 (亏损)
        """
        if not os.path.exists(TRADE_LOG):
            print(f"[MetaLabel] trade_log.csv 不存在")
            return False

        # 读取交易记录
        trades = pd.read_csv(TRADE_LOG, encoding="utf-8-sig")
        if len(trades) < min_samples:
            print(f"[MetaLabel] 样本不足 ({len(trades)} < {min_samples})")
            return False

        # 加载 ML 评分和市场状态
        ml_cfg = {}
        if os.path.exists(CFG_PATH):
            ml_cfg = json.load(open(CFG_PATH, encoding="utf-8"))

        # 构建特征矩阵
        X_rows, y_rows = [], []
        win_count = 0

        for _, t in trades.iterrows():
            sym = str(t.get("symbol", ""))
            net = float(t.get("net_profit", 0) or 0)

            # 特征
            ml = ml_cfg.get(sym, {})
            lgbm = float(ml.get("lgbm", 0) or 0)
            xgb = float(ml.get("xgb", 0) or 0)
            cb = float(ml.get("cb", 0) or 0)
            best = max(lgbm, xgb, cb)
            lv = 5 if best >= 90 else 4 if best >= 80 else 3 if best >= 70 else 2 if best >= 60 else 1

            pos_pct = float(t.get("position_pct", 3) or 3)
            vol_r = float(t.get("vol_ratio", 1.0) or 1.0)
            n_models = int(t.get("n_models", 1) or 1)
            turnover = float(t.get("turnover", 0) or 0)

            # 市场状态 (简化: 从波动率推断)
            regime_bull = 1 if vol_r < 0.8 else 0
            regime_bear = 1 if vol_r > 1.5 else 0

            X_rows.append([
                lgbm, xgb, cb, lv,
                vol_r * 0.02,  # vol approximation
                regime_bull, regime_bear,
                pos_pct, n_models, turnover,
            ])
            y_rows.append(1 if net > 0 else 0)
            if net > 0:
                win_count += 1

        if len(X_rows) < min_samples:
            print(f"[MetaLabel] 有效样本不足 ({len(X_rows)})")
            return False

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_rows, dtype=np.int32)

        win_rate = win_count / len(y_rows) * 100
        print(f"[MetaLabel] {len(X_rows)} samples, win_rate={win_rate:.1f}%")

        # LightGBM 二分类 (小模型, 防过拟合)
        try:
            from lightgbm import LGBMClassifier

            # 80/20 time split
            split = int(len(X) * 0.8)
            if split > 10 and len(X) - split > 5:
                X_tr, X_val = X[:split], X[split:]
                y_tr, y_val = y[:split], y[split:]
                model = LGBMClassifier(
                    n_estimators=50, max_depth=3, num_leaves=15,
                    learning_rate=0.05, random_state=42, verbose=-1,
                )
                model.fit(X_tr, y_tr)
                # 验证
                y_prob = model.predict_proba(X_val)[:, 1]
                from sklearn.metrics import roc_auc_score
                auc = roc_auc_score(y_val, y_prob) if len(set(y_val)) > 1 else 0.5
                print(f"[MetaLabel] Val AUC={auc:.3f}, features={FEATURE_NAMES}")
            else:
                model = LGBMClassifier(
                    n_estimators=30, max_depth=3, num_leaves=10,
                    learning_rate=0.05, random_state=42, verbose=-1,
                )
                model.fit(X, y)
                print(f"[MetaLabel] Trained on all {len(X)} samples (no split)")

            self.model = model
            self._save()
            print(f"[MetaLabel] Model saved: {MODEL_PATH}")
            return True

        except ImportError:
            print("[MetaLabel] LightGBM not available, using logistic regression")
            from sklearn.linear_model import LogisticRegression
            self.model = LogisticRegression(max_iter=1000)
            self.model.fit(X, y)
            self._save()
            return True

    # ═══════════════════════════════════════
    # 预测
    # ═══════════════════════════════════════

    def predict(self, signal: dict) -> float:
        """预测单个信号的可信度 (0-1)

        Args:
            signal: {symbol, lgbm_score, xgb_score, cb_score, signal_level,
                     position_pct, volatility, n_models, turnover, ...}

        Returns:
            float: 0-1 可信度 (越高越可信)
        """
        if self.model is None:
            return 0.5  # 无模型 → 中性

        try:
            lgbm = float(signal.get("lgbm_score", 50) or 50)
            xgb = float(signal.get("xgb_score", 50) or 50)
            cb = float(signal.get("cb_score", 50) or 50)
            lv = int(signal.get("signal_level", 3) or 3)
            vol = float(signal.get("volatility", 0.02) or 0.02)
            regime_bull = int(signal.get("regime", "") == "bull")
            regime_bear = int(signal.get("regime", "") == "bear")
            pos = float(signal.get("position_pct", 3) or 3)
            n_models = int(signal.get("n_models", 1) or 1)
            turnover = float(signal.get("turnover", 0) or 0)

            X = np.array([[lgbm, xgb, cb, lv, vol, regime_bull, regime_bear,
                           pos, n_models, turnover]], dtype=np.float32)

            prob = self.model.predict_proba(X)[0, 1]
            return round(float(prob), 3)

        except Exception as e:
            return 0.5

    def filter_signals(self, signals: list, threshold=0.5) -> list:
        """批量过滤信号, 仅返回可信度 > threshold 的"""
        if not signals:
            return []
        passed, rejected = [], []
        for s in signals:
            conf = self.predict(s)
            s["meta_confidence"] = conf
            if conf >= threshold:
                passed.append(s)
            else:
                rejected.append(s)
        if rejected:
            print(f"[MetaLabel] Filtered {len(rejected)}/{len(signals)} signals "
                  f"(threshold={threshold})")
        return passed

    def is_ready(self) -> bool:
        return self.model is not None


# ═══════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════

_meta_labeler = None


def get_labeler() -> MetaLabeler:
    global _meta_labeler
    if _meta_labeler is None:
        _meta_labeler = MetaLabeler()
    return _meta_labeler


if __name__ == "__main__":
    print("=" * 50)
    print("  Meta-Labeling v1.0")
    print("=" * 50)

    ml = MetaLabeler()
    ml.train()

    if ml.is_ready():
        # 测试预测
        test_sig = {
            "symbol": "sh600519", "lgbm_score": 85, "xgb_score": 80,
            "cb_score": 90, "signal_level": 4, "volatility": 0.02,
            "regime": "bull", "position_pct": 5, "n_models": 2, "turnover": 0.03,
        }
        conf = ml.predict(test_sig)
        print(f"\n  Test signal: confidence={conf:.3f}")

    print("\n  Done")
