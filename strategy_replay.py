"""策略历史回放 — 用过去N天数据模拟策略每日选股+收益

API: GET /api/strategy/replay?days=30 返回收益曲线+关键指标
"""

import sys, os, pickle, json
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")


def load_data():
    """P2: DataManager统一入口加载数据。"""
    import sys as _sr_sys
    _sr_sys.path.insert(0, r"D:\quant_web")
    factor_cache_file = r"D:\quant_web\factor_cache.pkl"
    fc = []
    if os.path.exists(factor_cache_file):
        with open(factor_cache_file, "rb") as f:
            fc = pickle.load(f)
    from data_loader import load_stock_data_from_cache
    sd = load_stock_data_from_cache() or {}
    return fc, sd


def get_daily_signals(factor_cache, stock_data, date_str, max_picks=5):
    """模拟某一天策略选出的股票"""
    candidates = []
    for s in (factor_cache or [])[:500]:
        sym = getattr(s, "symbol", "")
        if sym not in stock_data:
            continue
        ps = getattr(s, "power_score", 0) or 0
        bs = getattr(s, "buy_signal", 0) or 0
        close = getattr(s, "close", 0) or 0
        if ps >= 48 and bs >= 3 and close > 0:
            candidates.append({"symbol": sym, "price": close, "power_score": ps, "buy_signal": bs})

    candidates.sort(key=lambda x: -x["power_score"])
    return candidates[:max_picks]


def simulate_pnl(sym, buy_price, stock_data, buy_date, sell_date):
    """模拟持仓盈亏"""
    df = stock_data.get(sym)
    if df is None or len(df) < 2:
        return {"pnl": 0, "return_pct": 0}

    try:
        # Find sell date index
        if hasattr(df.index, 'strftime'):
            buy_idx = df.index[df.index.astype(str).str.startswith(buy_date)]
            sell_idx = df.index[df.index.astype(str).str.startswith(sell_date)]
        else:
            buy_idx = [d for d in df.index if str(d).startswith(buy_date)]
            sell_idx = [d for d in df.index if str(d).startswith(sell_date)]

        if len(buy_idx) == 0:
            return {"pnl": 0, "return_pct": 0}

        if len(sell_idx) > 0:
            sell_price = float(df.loc[sell_idx[0], "open"])
            ret = (sell_price / buy_price - 1) * 100
            return {"pnl": round((sell_price - buy_price), 2), "return_pct": round(ret, 2)}

        # Position still open: use last available close
        last_close = float(df["close"].values[-1])
        ret = (last_close / buy_price - 1) * 100
        return {"pnl": round((last_close - buy_price), 2), "return_pct": round(ret, 2), "open": True}
    except Exception:
        return {"pnl": 0, "return_pct": 0}


def replay(days: int = 30, max_positions: int = 3):
    """回放过去N天的策略表现

    Returns:
        {
            "days": [...],          # 每日详情
            "equity_curve": [...],  # 权益曲线
            "metrics": {...},       # 关键指标
        }
    """
    factor_cache, stock_data = load_data()
    if not factor_cache or not stock_data:
        return {"error": "缓存数据不可用"}

    # 获取可用的交易日
    all_dates = set()
    for df in stock_data.values():
        if df is not None and len(df) > 0:
            for d in df.index:
                all_dates.add(str(d)[:10])
    trading_days = sorted(all_dates)[-days - 10:]  # 多取10天缓冲

    if len(trading_days) < days:
        days = len(trading_days) - 1

    start_idx = max(0, len(trading_days) - days)
    trading_days = trading_days[start_idx:]

    capital = 1_000_000
    cash = capital
    positions = []  # [{symbol, buy_price, qty, buy_date}]
    equity_curve = []
    trade_log = []
    daily_details = []

    for i, today in enumerate(trading_days):
        if i == 0:
            equity_curve.append({"date": today, "equity": capital})
            continue

        # ── 检查持仓退出 (T+1用开盘价) ──
        still_held = []
        for pos in positions:
            sym = pos["symbol"]
            result = simulate_pnl(sym, pos["buy_price"], stock_data, pos["buy_date"], today)
            if result.get("open") or result["return_pct"] < -5:
                # 止损或正常卖出
                cash += pos["buy_price"] * pos["qty"] * (1 + result["return_pct"] / 100)
                trade_log.append({
                    "symbol": sym, "buy_date": pos["buy_date"], "sell_date": today,
                    "buy_price": pos["buy_price"], "return_pct": result["return_pct"],
                    "pnl": result["pnl"], "exit": "stop" if result["return_pct"] < -5 else "normal",
                })
            else:
                still_held.append(pos)

        positions = still_held

        # ── 新信号买入 ──
        if len(positions) < max_positions and cash > 100_000:
            signals = get_daily_signals(factor_cache, stock_data, today, max_positions)
            for sig in signals:
                if len(positions) >= max_positions or cash < 100_000:
                    break
                sym = sig["symbol"]
                if sym in [p["symbol"] for p in positions]:
                    continue
                # 同时检查是否在之前买入过（避免重复）
                price = sig["price"]
                position_size = cash * 0.25
                qty = int(position_size / price / 100) * 100
                if qty < 100 or price <= 0:
                    continue
                cost = price * qty
                cash -= cost
                positions.append({"symbol": sym, "buy_price": price, "qty": qty, "buy_date": today})

        # ── 权益计算 ──
        position_value = 0
        for pos in positions:
            sym = pos["symbol"]
            result = simulate_pnl(sym, pos["buy_price"], stock_data, pos["buy_date"], today)
            position_value += pos["buy_price"] * pos["qty"] * (1 + result.get("return_pct", 0) / 100)

        total_equity = cash + position_value
        equity_curve.append({"date": today, "equity": round(total_equity, 0)})

        daily_details.append({
            "date": today,
            "equity": round(total_equity, 0),
            "positions": len(positions),
            "cash": round(cash, 0),
        })

    # ── 指标计算 ──
    eq_vals = [e["equity"] for e in equity_curve]
    total_return = (eq_vals[-1] / eq_vals[0] - 1) * 100 if eq_vals[0] > 0 else 0

    daily_rets = []
    for j in range(1, len(eq_vals)):
        if eq_vals[j - 1] > 0:
            daily_rets.append(eq_vals[j] / eq_vals[j - 1] - 1)

    dr = np.array(daily_rets) if daily_rets else np.array([0])
    sharpe = float(np.mean(dr) / np.std(dr) * np.sqrt(252)) if np.std(dr) > 0 else 0

    peak = eq_vals[0]
    max_dd = 0
    for v in eq_vals:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    wins = [t for t in trade_log if t["return_pct"] > 0]
    win_rate = len(wins) / len(trade_log) * 100 if trade_log else 0

    return {
        "days": daily_details,
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "metrics": {
            "total_return": round(total_return, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "win_rate": round(win_rate, 1),
            "n_trades": len(trade_log),
        },
    }
