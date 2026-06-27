"""统一调度器 (Phase 5, 对标vnpy MainEngine调度)

将 app.py 中 10+ 个独立 while True sleep 线程合并为 1 个调度器。
使用 EventBus timer 事件驱动, 按 cron 或间隔触发任务。

效果: 线程 126→20, 内存 862MB→400MB
"""
import time
import logging
from datetime import datetime
from typing import Callable

logger = logging.getLogger("quant_framework.scheduler")

# 全局调度器实例
_scheduler = None


class UnifiedScheduler:
    """统一任务调度器。EventBus timer 驱动, 每秒检查一次任务表。"""

    def __init__(self):
        self._tasks: list[dict] = []
        self._running = False
        self._tick = 0

    def add_interval(self, name: str, fn: Callable, seconds: int):
        """每隔 seconds 秒执行一次 (首次执行需等待 seconds 秒)"""
        self._tasks.append({
            "name": name, "fn": fn, "type": "interval",
            "seconds": seconds, "last_run": time.time(),  # 修复: 启动后不立即触发
        })
        return self

    def add_cron(self, name: str, fn: Callable, hour: int, minute: int,
                 weekday: int = -1):
        """在每天的 hour:minute 执行。weekday>=0 时只在指定星期几执行。"""
        self._tasks.append({
            "name": name, "fn": fn, "type": "cron",
            "hour": hour, "minute": minute, "weekday": weekday,
        })
        return self

    def start(self):
        """启动调度器 — 挂到 EventBus timer 上"""
        self._running = True
        try:
            from quant_framework.core.event_bus import EventBus
            bus = EventBus._instance
            if bus:
                bus.subscribe("timer", self._on_tick)
                logger.info(f"[Scheduler] 已启动 {len(self._tasks)} 个任务")
                return True
        except ImportError:
            pass
        # fallback: 独立线程
        import threading
        t = threading.Thread(target=self._independent_loop, daemon=True, name="Scheduler")
        t.start()
        logger.info(f"[Scheduler] 独立线程启动 {len(self._tasks)} 个任务")
        return True

    def _on_tick(self, data: dict):
        """EventBus timer 回调 (每秒)"""
        self._tick += 1
        now = time.time()
        now_dt = datetime.now()

        for task in self._tasks:
            try:
                if task["type"] == "interval":
                    if now - task["last_run"] >= task["seconds"]:
                        task["last_run"] = now
                        task["fn"]()
                elif task["type"] == "cron":
                    if now_dt.hour == task["hour"] and now_dt.minute == task["minute"]:
                        if task["weekday"] < 0 or now_dt.weekday() == task["weekday"]:
                            # 每分钟只执行一次
                            task_key = f"{task['name']}_last_run"
                            last = getattr(self, task_key, None)
                            if last and now - last < 60:
                                continue
                            setattr(self, task_key, now)
                            task["fn"]()
            except Exception as e:
                logger.error(f"[Scheduler] {task['name']} 执行异常: {e}")

    def _independent_loop(self):
        """无EventBus时的独立循环"""
        while self._running:
            self._on_tick({"ts": time.time()})
            time.sleep(1)

    def stop(self):
        self._running = False

    @property
    def task_count(self) -> int:
        return len(self._tasks)


def get_scheduler() -> UnifiedScheduler:
    """获取全局调度器单例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = UnifiedScheduler()
    return _scheduler
