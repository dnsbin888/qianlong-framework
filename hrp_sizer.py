"""B5: HRP 层次风险平价仓位 — riskfolio 标准实现
用法: from hrp_sizer import allocate_hrp
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")


def _build_returns_matrix(symbols, stock_data, lookback=60):
    """从 stock_data 构建收益率矩阵 (riskfolio 输入格式)"""
    returns_dict = {}
    for sym in symbols:
        df = stock_data.get(sym)
        if df is None or len(df) < lookback:
            continue
        close = df["close"].values[-lookback:]
        rets = np.diff(close) / close[:-1]
        if len(rets) >= 30:
            returns_dict[sym] = rets
    if len(returns_dict) < 2:
        return None
    # 对齐长度
    min_len = min(len(r) for r in returns_dict.values())
    data = {s: r[-min_len:] for s, r in returns_dict.items()}
    return pd.DataFrame(data)


def hrp_weights(returns_df):
    """真 HRP 权重 — 递归二分法 (riskfolio.HCPortfolio)"""
    from riskfolio import HCPortfolio
    if returns_df is None or returns_df.shape[1] < 2:
        n = returns_df.shape[1] if returns_df is not None else 0
        return np.ones(n) / n if n > 0 else np.array([])

    rp = HCPortfolio(returns=returns_df)
    w = rp.optimization(
        model='HRP',
        codependence='pearson',
        method_cov='ledoit',     # Ledoit-Wolf 收缩估计, 更稳健
        linkage='ward',          # Ward聚类, 行业标准
    )
    return w.values.flatten()


def allocate_hrp(signals, stock_data, total_capital=1_000_000, max_positions=5):
    """HRP 仓位分配 (riskfolio 标准实现)

    Args:
        signals: [{symbol, buy_signal, score, close}]
        stock_data: {symbol: DataFrame}
        total_capital: 总资金
        max_positions: 最大持仓数

    Returns:
        [{symbol, weight, shares, amount, ...}]
    """
    if not signals:
        return []

    # 只取 top-N
    ranked = sorted(signals, key=lambda s: -s.get("score", 0))[:max_positions]
    syms = [s["symbol"] for s in ranked]

    # 构建收益率矩阵 → 调用 riskfolio 真 HRP
    returns_df = _build_returns_matrix(syms, stock_data)
    weights = np.ones(len(syms)) / len(syms)  # 兜底: 等权重
    if returns_df is not None and returns_df.shape[1] >= 2:
        w = hrp_weights(returns_df)
        # 映射回原始顺序
        weight_map = {s: w[i] for i, s in enumerate(returns_df.columns) if i < len(w)}
        weights = np.array([weight_map.get(s, 1.0 / len(syms)) for s in syms])
        weights = weights / weights.sum()

    # 动态仓位上限 (根据信号等级调整)
    signal_multiplier = {5: 1.0, 4: 0.8, 3: 0.6, 2: 0.4, 1: 0.2}

    allocations = []
    for i, s in enumerate(ranked):
        w = weights[i] * signal_multiplier.get(s.get("buy_signal", 3), 0.5)
        amount = total_capital * w
        close = s.get("close", 0)
        shares = int(amount / close / 100) * 100 if close > 0 else 0
        allocations.append({
            "symbol": s["symbol"],
            "score": s.get("score", 0),
            "signal_lv": s.get("buy_signal", 3),
            "weight": round(float(w), 4),
            "amount": round(float(amount), 0),
            "shares": shares,
            "close": close,
        })

    return allocations


if __name__ == "__main__":
    print("HRP 仓位分配模型 v1.0")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    if sd:
        # 用 LGBM 信号测试
        sys.path.insert(0, r"D:\quant_framework")
        from lgbm_strategy import generate_lgbm_signals
        sigs = generate_lgbm_signals(sd, top_k=10)
        if sigs:
            allocs = allocate_hrp(sigs, sd, 1_000_000, 5)
            print(f"\n  {len(sigs)} 信号 → HRP 分配 {len(allocs)} 只:")
            for a in allocs:
                print(f"    {a['symbol']} 权重={a['weight']:.1%} ¥{a['amount']:,.0f} {a['shares']}股 Lv{a['signal_lv']}")
        else:
            print("  无信号")
    print("\n✅ HRP 仓位模型就绪\n")
