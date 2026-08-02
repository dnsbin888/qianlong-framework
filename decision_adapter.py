"""决策适配器 v1.0 — 统一打包 B1-B8, 喂给 paper_engine

信号进入后, 自动经过:
  B2: ATR 滑点调整价格
  B3: 行业敞口检查 (超限丢弃)
  B5: HRP 仓位分配 (替代固定比例)
  B6: 市场状态自适应 (熊市减仓)

用法: paper_engine.auto_trade_check() → decision_adapter.process(signals) → 标准化下单
"""
import sys, os
import numpy as np

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")


def process_signals(signals, stock_data, paper_status):
    """将原始信号经过 B2-B6 处理后输出标准化订单

    Args:
        signals: [{symbol, buy_signal, close, score, ...}]
        stock_data: {symbol: DataFrame}
        paper_status: {"total_equity": float, "positions": [{symbol, market_value}]}

    Returns:
        [{symbol, buy_signal, close, position_pct, shares, stop_loss, ...}]
    """
    from atr_slippage import apply_slippage
    from sector_limit import check_sector_limit
    from hrp_sizer import allocate_hrp
    from market_regime import detect_regime, get_strategy_params

    if not signals:
        return []

    # B6: 市场状态 → 动态参数
    regime = detect_regime(stock_data)
    params = get_strategy_params(regime)
    max_positions = params["max_positions"]
    position_scale = params["position_scale"]

    # 过滤已有持仓的股票
    existing_syms = set()
    existing_positions = []
    for p in paper_status.get("positions", []):
        if isinstance(p, dict):
            existing_syms.add(p.get("symbol", ""))
            existing_positions.append(p)

    new_signals = [s for s in signals if s.get("symbol", "") not in existing_syms]
    if not new_signals:
        return []

    # B5: HRP 仓位分配
    total_equity = paper_status.get("total_equity", 1_000_000)
    allocs = allocate_hrp(new_signals, stock_data, total_equity, max_positions)

    orders = []
    for a in allocs:
        sym = a["symbol"]
        df = stock_data.get(sym)

        # B3: 行业敞口检查
        ok, reason = check_sector_limit(sym, existing_positions, total_equity)
        if not ok:
            continue

        # B2: ATR 滑点调整买入价
        raw_price = a["close"]
        adj_price = apply_slippage(raw_price, df, "buy", a["weight"], total_equity)

        # B6: 仓位系数
        scaled_weight = a["weight"] * position_scale
        amount = total_equity * scaled_weight
        shares = int(amount / adj_price / 100) * 100
        if shares < 100:
            continue

        orders.append({
            "symbol": sym,
            "buy_signal": a["signal_lv"],
            "close": adj_price,           # B2 调整后价格
            "raw_close": raw_price,       # 原始价格 (日志用)
            "slippage_pct": round((adj_price - raw_price) / raw_price * 100, 3),
            "position_pct": round(scaled_weight * 100, 1),
            "shares": shares,
            "amount": round(float(amount), 0),
            "score": a["score"],
            "stop_loss": round(adj_price * (1 + params["stop_loss"]), 2),
            "take_profit": [round(adj_price * (1 + tp), 2) for tp in params["take_profit"]],
            "strategy": f"B1-B6_v1",
            "regime": regime["regime"],
            "sector_checked": True,
            "slippage_applied": True,
        })

    if orders:
        bp = round(sum(o["position_pct"] for o in orders), 1)
        print(f"[DecisionAdapter] {len(orders)}订单 | 总仓位{bp}% | {regime['regime']}({regime['confidence']:.0%})")

    return orders


if __name__ == "__main__":
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    if sd:
        from lgbm_strategy import generate_lgbm_signals
        sigs = generate_lgbm_signals(sd, top_k=10)
        print(f"原始信号: {len(sigs)}只")
        orders = process_signals(sigs, sd, {"total_equity": 1_000_000, "positions": []})
        for o in orders[:5]:
            print(f"  {o['symbol']} 仓位{o['position_pct']}% {o['shares']}股 ¥{o['close']} 滑点{o['slippage_pct']:.2%}")
        print(f"\n✅ 决策适配器就绪 (B2+B3+B5+B6)\n")
