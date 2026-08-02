"""QMT 实时策略引擎 v2.0 — 快速通道 + 审核通道 双道并行

架构 (双道并行, 非二选一):
  潜龙早盘 → auto_trade_plan.json (人工批准的执行计划)
           → qmt_trade_config.json (ML模型评分)

  QMT盘中信号 → 审核通道: ALWAYS POST 到潜龙 (记录/通知)
              → 快速通道: enabled=true → passorder() 直接下单 (<20ms)

用法:
  import sys
  sys.path.insert(0, r"D:\quant_framework")
  from qmt_strategies.qmt_engine import on_bar, load_pool, reset_daily, get_status
"""
import sys, os, json, time
import numpy as np

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

# ═══════════════════════════════════════════════════════
# 路径配置
# ═══════════════════════════════════════════════════════

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
CONFIG_PATH = r"D:\quant_web\data\qmt_trade_config.json"
FLASK_URL = "http://127.0.0.1:5002/api/qmt/signal"

# ═══════════════════════════════════════════════════════
# 日风控状态
# ═══════════════════════════════════════════════════════

_Daily = {"trade_count": 0, "pct_used": 0, "loss_pct": 0, "signal_used": {}}

# ═══════════════════════════════════════════════════════
# 缓存
# ═══════════════════════════════════════════════════════

_plan_cache = None
_plan_mtime = 0
_ma20_cache = {}
_avg_vol_cache = {}


def _load_plan():
    global _plan_cache, _plan_mtime
    if os.path.exists(PLAN_PATH):
        mtime = os.path.getmtime(PLAN_PATH)
        if mtime != _plan_mtime or _plan_cache is None:
            try:
                with open(PLAN_PATH, "r", encoding="utf-8") as f:
                    _plan_cache = json.load(f)
                _plan_mtime = mtime
            except Exception:
                pass
    return _plan_cache or {"stocks": {}, "global_limits": {}}


def load_pool():
    """加载早盘候选池 + ML评分"""
    if not os.path.exists(CONFIG_PATH):
        return
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        print(f"[QMT-Engine] Loaded {len(config)} candidates")
    except Exception as e:
        print(f"[QMT-Engine] Load error: {e}")


def get_ml_score(symbol):
    """查ML评分 (lgbm/xgb/cb)"""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return {}
    entry = config.get(symbol, {})
    return {"lgbm": entry.get("lgbm", 0), "xgb": entry.get("xgb", 0), "cb": entry.get("cb", 0)}


def _to_qmt_code(symbol):
    s = symbol.lower()
    if s.startswith('sh'): return s[2:] + '.SH'
    if s.startswith('sz'): return s[2:] + '.SZ'
    if s.startswith('bj'): return s[2:] + '.BJ'
    return symbol


def _calc_shares(pos_pct, price, total_asset=100000):
    if price <= 0: return 100
    amount = total_asset * pos_pct / 100.0
    return max(100, int(amount / price / 100) * 100)


# ═══════════════════════════════════════════════════════
# 审核通道
# ═══════════════════════════════════════════════════════

def _audit_post(symbol, signal_name, price, channel, extra=None):
    payload = {"symbol": symbol, "signal_type": signal_name, "price": price, "channel": channel}
    if extra: payload.update(extra)
    try:
        import requests
        requests.post(FLASK_URL, json=payload, timeout=3)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# 快速通道
# ═══════════════════════════════════════════════════════

def _fast_execute(symbol, signal_name, price, stock, account_id, total_asset):
    limits = _load_plan().get("global_limits", {})

    if limits.get("circuit_breaker"):
        return False, "熔断中"

    max_trades = limits.get("max_daily_trades", 5)
    if _Daily["trade_count"] >= max_trades:
        return False, f"日交易超限({_Daily['trade_count']}/{max_trades})"

    max_loss = limits.get("max_daily_loss_pct", 5)
    if _Daily["loss_pct"] >= max_loss:
        return False, f"日亏损超限"

    if not stock.get("enabled", False):
        return False, f"未批准"

    if signal_name not in stock.get("signal_types", []):
        return False, f"信号{signal_name}不在白名单"

    min_ml = stock.get("min_ml_score", 80)
    ml = get_ml_score(symbol)
    best_ml = max(ml.get("lgbm", 0), ml.get("xgb", 0), ml.get("cb", 0))
    if best_ml < min_ml:
        return False, f"ML={best_ml}<{min_ml}"

    sig = _Daily["signal_used"].get(signal_name, {"pct": 0, "count": 0})
    pos_pct = stock.get("max_position_pct", 3)
    if sig["pct"] + pos_pct > 30:
        return False, f"{signal_name}仓位超30%"
    if sig["count"] >= 3:
        return False, f"{signal_name}数量≥3"

    pos_pct = stock.get("max_position_pct", 3)
    stop_loss = stock.get("stop_loss", 0)
    take_profit = stock.get("take_profit", 0)
    qty = _calc_shares(pos_pct, price, total_asset)
    qmt_code = _to_qmt_code(symbol)

    try:
        passorder(23, 1101, account_id, qmt_code, 0, price, qty, "潜龙快速", symbol, 2)
        _Daily["trade_count"] += 1
        _Daily["pct_used"] += pos_pct
        sig["pct"] += pos_pct
        sig["count"] += 1
        _Daily["signal_used"][signal_name] = sig

        detail = (f"✅ {qmt_code} BUY {qty}股@{price} "
                  f"仓位{pos_pct}% 止损{stop_loss} 止盈{take_profit} "
                  f"({_Daily['trade_count']}笔/{_Daily['pct_used']:.1f}%)")

        try:
            from dingtalk_alerts import send_alert
            send_alert(f"⚡快速 {signal_name}",
                       f"{symbol}\n{qmt_code} {qty}股@{price}\n仓位{pos_pct}%", "info")
        except Exception:
            pass

        return True, detail
    except Exception as e:
        return False, f"passorder异常: {e}"


# ═══════════════════════════════════════════════════════
# 策略触发条件
# ═══════════════════════════════════════════════════════

def _ma20(symbol):
    return _ma20_cache.get(symbol, 999)


def _avg_vol(symbol):
    return _avg_vol_cache.get(symbol, 1)


def _check_limit_up(bar, prev):
    price = bar.get('close', bar.get('last_price', 0))
    pre_close = prev.get('close', 0)
    if not price or not pre_close: return False
    limit_up = round(pre_close * 1.10, 2)
    if price < limit_up: return False
    if prev.get('close', 0) >= limit_up: return False
    return bar.get('volume', 0) >= _avg_vol(bar.get('symbol', '')) * 2


TRIGGERS = {
    "竞价抢筹": {
        "check": lambda bar, prev: (
            bar.get('open', 0) > prev.get('close', 0) * 1.02
            and bar.get('volume', 0) > prev.get('volume', 0) * 3
        ),
        "priority": 1,
    },
    "盘中突破": {
        "check": lambda bar, prev: (
            bar.get('close', 0) > _ma20(bar.get('symbol', ''))
            and bar.get('volume', 0) > _avg_vol(bar.get('symbol', '')) * 2
        ),
        "priority": 2,
    },
    "尾盘急拉": {
        "check": lambda bar, prev: (
            time.strftime('%H%M') > '1430'
            and (bar.get('close', 0) / max(prev.get('close', 0), 0.01) - 1) > 0.03
        ),
        "priority": 3,
    },
    "打板追封": {
        "check": lambda bar, prev: _check_limit_up(bar, prev),
        "priority": 1,
    },
}


def check_triggers(symbol, bar, prev_bar):
    triggered = []
    for name, cfg in sorted(TRIGGERS.items(), key=lambda x: x[1].get("priority", 9)):
        try:
            if cfg["check"](bar, prev_bar):
                triggered.append(name)
        except Exception:
            pass
    return triggered


# ═══════════════════════════════════════════════════════
# 主回调: QMT行情推送 → 双道并行
# ═══════════════════════════════════════════════════════

def on_bar(symbol, bar_data, prev_bar_data, account_id="", total_asset=100000):
    """QMT行情回调 — 双道并行

    审核通道: ALWAYS POST → 所有信号都推送到潜龙记录
    快速通道: enabled=true → passorder() 直接下单
    """
    triggers = check_triggers(symbol, bar_data, prev_bar_data)
    if not triggers:
        return

    signal_name = triggers[0]
    price = bar_data.get('close', bar_data.get('last_price', 0))
    plan = _load_plan()
    stock = plan.get("stocks", {}).get(symbol, {})

    # 审核通道: 始终推送
    _audit_post(symbol, signal_name, price,
                channel="fast" if stock.get("enabled") else "review",
                extra={"enabled": stock.get("enabled", False),
                       "qmt_code": _to_qmt_code(symbol)})

    # 快速通道: enabled=true → passorder()
    if stock.get("enabled", False):
        ok, detail = _fast_execute(symbol, signal_name, price, stock, account_id, total_asset)
        if ok:
            print(f"[快速通道] {detail}")
        else:
            print(f"[快速通道] ❌ {symbol} {detail}")


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def preload_ma20_and_vol(stock_data):
    global _ma20_cache, _avg_vol_cache
    for sym, df in stock_data.items():
        if len(df) >= 20:
            _ma20_cache[sym] = float(df['close'].values[-20:].mean())
            _avg_vol_cache[sym] = float(df['volume'].values[-20:].mean())
    for sym in list(_ma20_cache.keys()):
        clean = sym.replace('sh', '').replace('sz', '').replace('bj', '')
        _ma20_cache[clean] = _ma20_cache[sym]
        _avg_vol_cache[clean] = _avg_vol_cache[sym]


def reset_daily():
    global _Daily
    _Daily = {"trade_count": 0, "pct_used": 0, "loss_pct": 0, "signal_used": {}}
    _load_plan()
    print("[QMT-Engine] 日风控已重置")


def get_status():
    plan = _load_plan()
    limits = plan.get("global_limits", {})
    enabled = sum(1 for s in plan.get("stocks", {}).values() if s.get("enabled"))
    return {
        "daily": _Daily.copy(),
        "limits": limits,
        "enabled_stocks": enabled,
        "total_stocks": len(plan.get("stocks", {})),
        "circuit_breaker": limits.get("circuit_breaker", False),
    }
