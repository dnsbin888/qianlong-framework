"""Event Bus — 发布-订阅事件系统 (蓝图 v5.0 P3-01)

对标: vnpy EventEngine / LEAN EventBus
用法:
    from quant_framework.core.event_bus import EventBus, EVENT_TYPES

    bus = EventBus()
    bus.subscribe("signal", my_handler)
    bus.start()
    bus.publish("signal", {"symbol": "600000", "score": 80})
    bus.stop()

事件类型:
    quote   — 行情更新
    signal  — 策略信号
    order   — 订单状态变更
    risk    — 风控事件
    system  — 系统/定时事件

线程安全: queue.Queue + threading.Thread
不改任何现有代码，新模块挂总线上。
"""
from __future__ import annotations

import queue
import threading
import logging
import time
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger("quant_framework.event_bus")

# ── 事件类型常量 ──
EVENT_TYPES = {
    "quote": "quote",      # 行情更新
    "signal": "signal",    # 策略信号
    "order": "order",      # 订单状态
    "risk": "risk",        # 风控事件
    "system": "system",    # 系统/定时
    "timer": "timer",      # 定时器 (每秒)
}


class EventBus:
    """线程安全的事件总线。

    单消费者线程从队列取事件，分发给注册的处理器。
    多生产者线程安全入队。
    """

    _instance = None  # 全局单例

    def __init__(self, maxsize: int = 1000):
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._general_handlers: list[Callable] = []
        self._thread: threading.Thread | None = None
        self._active: bool = False
        self._timer_thread: threading.Thread | None = None
        self._event_count: int = 0
        self._error_count: int = 0
        EventBus._instance = self

    # ── 订阅 ──

    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> "EventBus":
        """订阅特定事件类型。"""
        self._handlers[event_type].append(handler)
        return self

    def subscribe_all(self, handler: Callable[[str, dict], None]) -> "EventBus":
        """订阅所有事件类型。"""
        self._general_handlers.append(handler)
        return self

    def unsubscribe(self, event_type: str, handler: Callable) -> "EventBus":
        """取消订阅。"""
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h is not handler]
        self._general_handlers = [h for h in self._general_handlers if h is not handler]
        return self

    # ── 发布 ──

    def publish(self, event_type: str, data: dict | None = None) -> bool:
        """发布事件 (非阻塞)。"""
        if not self._active:
            return False
        try:
            event = {"type": event_type, "data": data or {}, "time": time.time()}
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            logger.warning(f"[EventBus] 队列满，丢弃事件: {event_type}")
            self._error_count += 1
            return False

    # ── 生命周期 ──

    def start(self) -> "EventBus":
        """启动事件循环线程。"""
        if self._active:
            return self
        self._active = True
        self._thread = threading.Thread(target=self._run, name="EventBus", daemon=True)
        self._thread.start()
        self._timer_thread = threading.Thread(target=self._timer_loop, name="EventBus-Timer", daemon=True)
        self._timer_thread.start()
        logger.info("[EventBus] 已启动")
        return self

    def stop(self) -> "EventBus":
        """停止事件循环。"""
        self._active = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._timer_thread:
            self._timer_thread.join(timeout=1)
        logger.info(f"[EventBus] 已停止 (事件{self._event_count}, 错误{self._error_count})")
        return self

    # ── 内部 ──

    def _run(self):
        """主事件循环。"""
        while self._active:
            try:
                event = self._queue.get(timeout=1)
                self._dispatch(event)
                self._event_count += 1
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[EventBus] 分发异常: {e}")
                self._error_count += 1

    def _dispatch(self, event: dict):
        """分发事件到注册的处理器。"""
        event_type = event["type"]
        data = event.get("data", {})

        # 特定类型处理器
        for handler in self._handlers.get(event_type, []):
            try:
                handler(data)
            except Exception as e:
                logger.error(f"[EventBus] {event_type} handler error: {e}")

        # 通用处理器
        for handler in self._general_handlers:
            try:
                handler(event_type, data)
            except Exception as e:
                logger.error(f"[EventBus] general handler error: {e}")

    def _timer_loop(self):
        """定时器: 每秒发布一次 timer 事件。"""
        while self._active:
            time.sleep(1)
            if self._active:
                self.publish("timer", {"ts": time.time()})

    # ── 统计 ──

    @property
    def stats(self) -> dict:
        return {
            "active": self._active,
            "event_count": self._event_count,
            "error_count": self._error_count,
            "queue_size": self._queue.qsize(),
            "handlers": {k: len(v) for k, v in self._handlers.items()},
        }
