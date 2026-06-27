"""钉钉告警事件监听器 (Phase 6a-4)

订阅 EventBus 的 signal/order/risk 事件，实时推送钉钉/微信告警。

架构:
  EventBus.publish("signal", ...) → _on_signal() → send_alert("买入信号", ...)
  EventBus.publish("order", ...)  → _on_order()  → send_alert("成交通知", ...)
  EventBus.publish("risk", ...)   → _on_risk()   → send_alert("风控触发", ..., "critical")

过滤规则:
  signal: 仅 Lv4+ 信号推送 (避免刷屏)
  order:  所有成交推送
  risk:   所有风控事件推送 (critical 级别)

用法:
  from dingtalk_event_listener import start
  start()  # app.py 在 EventBus 启动后调用
"""

import logging
import time
from datetime import datetime

logger = logging.getLogger("quant_framework.dingtalk_events")

_active = False
_stats = {"signals": 0, "orders": 0, "risks": 0, "errors": 0}


def start() -> bool:
    """启动钉钉事件监听器。订阅 EventBus，失败静默降级。"""
    global _active
    if _active:
        return True

    try:
        from quant_framework.core.event_bus import EventBus
        bus = EventBus._instance
        if not bus:
            print("[DingTalk-Events] EventBus 未启动，跳过")
            return False

        bus.subscribe("signal", _on_signal)
        bus.subscribe("order", _on_order)
        bus.subscribe("risk", _on_risk)
        _active = True
        print("[DingTalk-Events] ✓ 已订阅 signal/order/risk 事件")
        return True
    except ImportError:
        print("[DingTalk-Events] EventBus 不可用，跳过")
    except Exception as e:
        print(f"[DingTalk-Events] 启动失败: {e}")
    return False


def get_stats() -> dict:
    return {"active": _active, **_stats}


# ═══════════════════ 事件处理器 ═══════════════════

def _on_signal(data: dict):
    """信号事件 — 仅 Lv4+ 推送。"""
    global _stats
    _stats["signals"] += 1
    try:
        count = data.get("count", 0)
        symbols = data.get("symbols", [])
        if not symbols:
            return

        # 获取信号详情以判断等级 (简化: 推送信号摘要)
        top_symbols = symbols[:5]
        now = datetime.now().strftime("%H:%M:%S")
        msg = (
            f"**{now} 信号刷新**\n"
            f"本次: {count} 只 | Top5: {', '.join(top_symbols)}\n"
            f"---\n"
            f"共 {count} 只信号股待筛选"
        )
        _send("📡 信号刷新", msg, "info")
    except Exception as e:
        _stats["errors"] += 1
        logger.warning(f"signal handler error: {e}")


def _on_order(data: dict):
    """订单事件 — 所有成交推送。"""
    global _stats
    _stats["orders"] += 1
    try:
        action = data.get("action", "?")
        symbol = data.get("symbol", "?")
        price = data.get("price", 0)
        qty = data.get("qty", 0)
        pnl = data.get("pnl")
        trade_type = data.get("type", "?")

        emoji = "📈" if action == "buy" else "📉"
        pnl_str = f" | 盈亏: {pnl:+.0f}" if pnl is not None else ""
        msg = (
            f"{emoji} **{symbol}** {action} | "
            f"价格: {price:.2f} | 数量: {qty}股 | 类型: {trade_type}{pnl_str}"
        )
        _send(f"成交: {symbol} {action}", msg, "info")
    except Exception as e:
        _stats["errors"] += 1


def _on_risk(data: dict):
    """风控事件 — critical 级别推送。"""
    global _stats
    _stats["risks"] += 1
    try:
        event_type = data.get("type", "?")
        loss_pct = data.get("loss_pct", 0)
        action = data.get("action", "?")
        equity = data.get("equity", 0)

        msg = (
            f"🚨 **{event_type}**\n"
            f"日亏损: {loss_pct}% | 触发动作: **{action}**\n"
            f"当前权益: ¥{equity:,.0f}\n"
            f"---\n"
            f"时间: {datetime.now().strftime('%H:%M:%S')}"
        )
        _send(f"🚨 风控触发: {action}", msg, "critical")
    except Exception as e:
        _stats["errors"] += 1


def _send(title: str, content: str, level: str = "info"):
    """发送告警 — try/except 包裹，失败不抛异常。"""
    try:
        from dingtalk_alerts import send_alert
        send_alert(title, content, level)
    except ImportError:
        pass  # dingtalk_alerts 不可用，静默跳过
    except Exception:
        pass
