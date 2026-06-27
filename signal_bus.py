"""统一信号总线 — 行业标准架构。

所有策略(旧/V1-5/用户)通过此总线发布信号，
PaperAutoLoop 统一消费。加新策略不改循环。
"""

import logging, threading
logger = logging.getLogger(__name__)

_bus = {"signals": [], "updated": None}
_lock = threading.Lock()


def publish(symbol: str, buy_signal: int, close: float, **kwargs):
    """发布买入信号。所有策略调此函数。"""
    with _lock:
        _bus["signals"].append({
            "symbol": symbol, "buy_signal": buy_signal,
            "close": close, **kwargs
        })


def consume(max_count: int = 50) -> list[dict]:
    """消费信号(去重)"""
    with _lock:
        seen = set()
        result = []
        for s in reversed(_bus["signals"]):
            if s["symbol"] not in seen:
                seen.add(s["symbol"])
                result.append(s)
            if len(result) >= max_count: break
        return result


def clear():
    with _lock:
        _bus["signals"] = []
        _bus["updated"] = None
