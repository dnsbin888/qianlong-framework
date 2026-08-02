"""QMT 快速通道轻量模块 — 信号批处理

用法:
    from qmt_strategies.qmt_quick_trade import quick_trade
    result = quick_trade(signals, account_id, total_asset)
    # result["fast"] = 已自动下单的
    # result["review"] = 推送到潜龙审核的

速度: <20ms (本地文件+DLL下单)
"""
import json, os

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
_plan_cache = None
_plan_mtime = 0


def _load_plan():
    global _plan_cache, _plan_mtime
    if os.path.exists(PLAN_PATH):
        mtime = os.path.getmtime(PLAN_PATH)
        if mtime != _plan_mtime or _plan_cache is None:
            try:
                with open(PLAN_PATH, "r", encoding="utf-8") as f:
                    _plan_cache = json.load(f)
                _plan_mtime = mtime
            except:
                pass
    return _plan_cache or {"stocks": {}, "global_limits": {}}


def _to_qmt_code(symbol):
    s = symbol.lower()
    if s.startswith('sh'): return s[2:] + '.SH'
    if s.startswith('sz'): return s[2:] + '.SZ'
    if s.startswith('bj'): return s[2:] + '.BJ'
    return symbol


def _calc_qty(pos_pct, price, total_asset=100000):
    if price <= 0: return 100
    amount = total_asset * pos_pct / 100.0
    qty = int(amount / price / 100) * 100
    return max(100, qty)


def quick_trade(signals, account_id="", total_asset=100000):
    """批量处理信号 — 双道并行

    Args:
        signals: [{"symbol":"sh600530","type":"盘中突破","price":5.78}, ...]
        account_id: QMT资金账号
        total_asset: 账户总资产

    Returns:
        {"fast": [...], "review": [...]}
    """
    plan = _load_plan()
    limits = plan.get("global_limits", {})

    if limits.get("circuit_breaker", False):
        print("[QMT快速] 熔断中, 全部走审核")
        return {"fast": [], "review": signals}

    fast_done = []
    review_list = []

    for sig in signals:
        sym = sig.get("symbol", "")
        sig_type = sig.get("type", "unknown")
        price = sig.get("price", 0)
        stock = plan.get("stocks", {}).get(sym, {})

        # ── 审核通道: 始终推送 ──
        try:
            import requests
            requests.post("http://127.0.0.1:5002/api/qmt/signal",
                json={"symbol": sym, "signal_type": sig_type, "price": price,
                      "channel": "fast" if stock.get("enabled") else "review"},
                timeout=3)
        except:
            pass

        # ── 快速通道: enabled → passorder() ──
        if (stock.get("enabled", False)
            and sig_type in stock.get("signal_types", [])
            and price > 0):

            pos_pct = stock.get("max_position_pct", 3)
            stop_loss = stock.get("stop_loss", 0)
            take_profit = stock.get("take_profit", 0)
            qty = _calc_qty(pos_pct, price, total_asset)
            qmt_code = _to_qmt_code(sym)

            try:
                passorder(23, 1101, account_id, qmt_code, 0, price, qty, "潜龙快速", sym, 2)
                fast_done.append({
                    "symbol": sym, "type": sig_type, "qmt_code": qmt_code,
                    "price": price, "qty": qty, "pos_pct": pos_pct,
                    "stop": stop_loss, "tp": take_profit, "status": "DONE"
                })
                print(f"[QMT快速] ✅ {qmt_code} BUY {qty}股 @{price} ({pos_pct}%)")
            except Exception as e:
                print(f"[QMT快速] ❌ {sym} 下单失败: {e}")
                review_list.append(sig)
        else:
            review_list.append(sig)

    return {"fast": fast_done, "review": review_list}
