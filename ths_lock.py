"""THS Win32 操作互斥锁 -- 防止多线程同时操作同花顺窗口导致消息泵死锁

使用方式:
    from ths_lock import THS_WIN32_LOCK

    with THS_WIN32_LOCK:
        # 在此执行所有 Win32 窗口操作 (EnumWindows, UIA, keybd_event 等)
        pass

设计原因:
    弹窗杀手(2s轮询) + 持仓读取(10s轮询) + 键盘交易(事件触发)
    三个线程同时对同花顺窗口做 Win32 操作, 无互斥 -> 消息泵死锁 -> 系统死机

    此锁强制串行化所有 THS 窗口操作, 彻底消除竞争条件。
"""
import threading

# 全局锁: 所有对同花顺窗口的 Win32 操作都必须先获取此锁
THS_WIN32_LOCK = threading.Lock()
