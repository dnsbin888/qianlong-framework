
"""LightGBM 策略信号生成器 v1.0 — 批量评分 + 排名选股

替代加权求和: 用训练好的 LightGBM 模型对全市场股票打分，
取 Top-N 作为买入信号，直接喂给 paper_engine。

用法:
    from lgbm_strategy import generate_lgbm_signals
    signals = generate_lgbm_signals(stock_data, top_k=15)

管线:
    16因子计算 → LightGBM.predict(批量) → 0-100评分 → Top-K排名 → 信号列表
"""

import os
import sys
import pickle
import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(__file__), "lgbm_model.pkl")


def _load_model(model_path=None):
    """加载训练好的 LGBM 模型。返回 (model, factor_names) 或 (None, None)。
    P1-3: 支持指定模型路径, 消除 generate_signal_table 的文件交换竞态
    """
    path = model_path or MODEL_PATH
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        return data.get("model"), data.get("factors", [])
    except Exception as e:
        print(f"[LGBM-Strategy] 模型加载失败: {e}")
        return None, None


def _map_score_to_signal(score: float) -> int:
    """评分(0-100百分位) → 信号等级(0-5)

      90+ → Lv5强买  80+ → Lv4买入  70+ → Lv3关注
      60+ → Lv2观察  40+ → Lv1弱   <40 → 0不买
      仓位映射见 live_trader.py CONFIG: Lv5=12% Lv4=8% Lv3=6% Lv2=4% Lv1=2%
    """
    if score >= 90:
        return 5
    if score >= 80:
        return 4
    if score >= 70:
        return 3
    if score >= 60:
        return 2
    if score >= 40:
        return 1
    return 0


def compute_lgbm_scores(stock_data: dict, max_stocks: int = 500,
                        compute_fns: dict | None = None,
                        model_path: str | None = None) -> dict[str, float]:
    """批量计算全市场 LGBM 评分。

    Args:
        stock_data: {symbol: DataFrame} 预加载的K线数据
        max_stocks: 最多扫描股票数
        compute_fns: 预加载的因子计算函数字典 (None=自动加载)

    Returns:
        {symbol: lgbm_score (0-100)}
    """
    model, factor_names = _load_model(model_path)
    if model is None:
        print(f"[LGBM-Strategy] 模型未训练 ({model_path or MODEL_PATH}), 跳过")
        return {}

    # 加载因子计算函数
    if compute_fns is None:
        try:
            # 精简: 只用8有效因子 (65→8)
            from full_market_ic import (_factor_trend_old, _factor_def, _factor_qmt_composite,
                                         _factor_chase, _factor_chip, _factor_mom_old,
                                         _factor_bull_old, _factor_fund)
            compute_fns = {
                "trend_score": _factor_trend_old, "defensive_v2": _factor_def,
                "qmt_composite": _factor_qmt_composite, "chase_v2": _factor_chase,
                "chip_v2": _factor_chip, "momentum_score": _factor_mom_old,
                "bull_line": _factor_bull_old, "fund_v2": _factor_fund,
            }
        except Exception as e:
            print(f"[LGBM-Strategy] 因子函数加载失败: {e}")
            return {}

    if not compute_fns:
        print("[LGBM-Strategy] 无可用因子计算函数")
        return {}

    # 只取模型训练时用的因子
    active_fns = {fn: compute_fns[fn] for fn in factor_names if fn in compute_fns}
    if len(active_fns) < len(factor_names):
        missing = set(factor_names) - set(active_fns.keys())
        print(f"[LGBM-Strategy] 警告: {len(missing)}个因子缺失: {missing}")

    # 批量计算
    syms = [s for s in list(stock_data.keys())[:max_stocks]
            if not s.startswith(('sh000', 'sz399', 'bj'))]

    X_rows = []
    valid_syms = []

    for sym in syms:
        df = stock_data.get(sym)
        if df is None or not hasattr(df, '__len__') or len(df) < 20:
            continue

        row = []
        valid = True
        for fn in factor_names:
            func = active_fns.get(fn)
            if not func:
                row.append(0.0)
                continue
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
        print("[LGBM-Strategy] 无有效特征行")
        return {}

    X = np.array(X_rows, dtype=np.float32)

    # 截面 winsorize: 每列钳制到 [P0.5, P99.5]，防极端值拉偏预测
    for col in range(X.shape[1]):
        col_data = X[:, col]
        lo, hi = np.percentile(col_data, [0.5, 99.5])
        X[:, col] = np.clip(col_data, lo, hi)

    # 批量预测
    try:
        raw_preds = model.predict(X)
    except Exception as e:
        print(f"[LGBM-Strategy] 批量预测失败: {e}")
        return {}

    # 截面百分位排名 → 0-100评分 (对标私募/个人量化标准做法)
    # 不依赖预测值范围, 模型换了自动适配, 保留排序信息
    from scipy.stats import rankdata
    ranks = rankdata(raw_preds, method='average')
    pct_scores = ranks / len(raw_preds) * 100
    scores = {}
    for sym, pct in zip(valid_syms, pct_scores):
        scores[sym] = round(pct, 1)

    print(f"[LGBM-Strategy] 批量评分完成: {len(scores)}只股票, "
          f"range=[{min(scores.values()):.1f}, {max(scores.values()):.1f}]")
    return scores


def generate_lgbm_signals(stock_data: dict, top_k: int = 15,
                          min_score: int = 40,
                          model_path: str | None = None) -> list[dict]:
    """生成 LGBM 买入信号 — 与 strategy_engine.generate_for_paper() 接口兼容。

    Args:
        stock_data: {symbol: DataFrame}
        top_k: 返回前K个信号
        min_score: 最低LGBM评分阈值

    Returns:
        [{symbol, buy_signal, close, score, name, stop_loss, take_profit, strategy}]
    """
    scores = compute_lgbm_scores(stock_data, model_path=model_path)

    if not scores:
        return []

    # 过滤 + 排序
    ranked = sorted(
        [(sym, s) for sym, s in scores.items() if s >= min_score],
        key=lambda x: -x[1],
    )

    signals = []
    for sym, score in ranked[:top_k]:
        df = stock_data.get(sym)
        close = 0.0
        if df is not None and hasattr(df, '__len__') and len(df) > 0:
            try:
                close = float(df["close"].iloc[-1])
            except Exception:
                close = 0.0

        buy_signal = _map_score_to_signal(score)
        if buy_signal == 0:
            continue

        signals.append({
            "symbol": sym,
            "buy_signal": buy_signal,
            "close": close,
            "score": score,
            "name": "",
            "stop_loss": round(close * 0.97, 2) if close > 0 else 0,
            "take_profit": [round(close * 1.05, 2), round(close * 1.10, 2)] if close > 0 else [],
            "strategy": "LightGBM-v1",
        })

    if signals:
        print(f"[LGBM-Strategy] 生成 {len(signals)} 个买入信号 "
              f"(Top3: {[(s['symbol'][-6:], s['score']) for s in signals[:3]]})")
    else:
        print(f"[LGBM-Strategy] 无买入信号 (min_score={min_score}, ranked={len(ranked)}只)")

    return signals


def get_model_importance() -> list[dict]:
    """获取模型特征重要性 (用于前端展示)。"""
    _, _ = _load_model()
    try:
        from lgbm_weight import get_importance
        return get_importance()
    except Exception:
        return []


def is_model_ready() -> bool:
    """检查模型是否已训练就绪。"""
    return os.path.exists(MODEL_PATH)


# ═══════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    print("LightGBM 策略信号生成器 — 独立测试")
    print("=" * 50)

    if not is_model_ready():
        print("\n⚠️  模型未训练！请先运行 lgbm_weight.py 训练模型。")
        print("   python lgbm_weight.py")
        sys.exit(1)

    print(f"\n✅ 模型已就绪: {MODEL_PATH}")
    imp = get_model_importance()
    if imp:
        print(f"\n特征重要性 Top5:")
        for item in imp[:5]:
            print(f"  {item['factor']:30s} {item['importance']:.4f}")

    # 尝试加载数据测试
    print("\n加载股票数据...")
    try:
        sys.path.insert(0, r"D:\quant_web")
        from data_loader import load_stock_data_from_cache
        sd = load_stock_data_from_cache()
        if sd:
            sigs = generate_lgbm_signals(sd, top_k=10)
            print(f"\n最终信号 ({len(sigs)}只):")
            for i, s in enumerate(sigs):
                print(f"  #{i+1} {s['symbol']} "
                      f"score={s['score']:.1f} "
                      f"signal={s['buy_signal']} "
                      f"close={s['close']:.2f}")
        else:
            print("无法加载数据 (data_loader 返回空)")
    except Exception as e:
        print(f"数据加载失败: {e}")
        print("(这是正常的 — 模型文件存在即可)")
