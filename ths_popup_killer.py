"""同花顺弹窗杀手 — 后台守护线程，自动关闭 THS 弹窗，零依赖（仅用 ctypes 调 Win32 API）

原理:
  - EnumWindows 遍历所有顶层窗口
  - 匹配 THS 弹窗标题（提示/确认/错误/消息/升级/新闻等）
  - 发送 WM_CLOSE 或 ESC 关闭
  - 对阻塞式对话框（如交易确认），ESC 比关闭更安全（不改变交易意图）

集成方式:
  from ths_popup_killer import start, stop
  start()   # 启动后台守护
  stop()    # 停止
"""

import ctypes
import ctypes.wintypes
import threading
import time
from datetime import datetime

# ── THS Win32 操作互斥锁 ──
from ths_lock import THS_WIN32_LOCK

# ── Win32 API 常量 ──
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
SW_MINIMIZE = 6
SW_RESTORE = 9
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── 弹窗匹配规则 ──
# 标题包含这些关键词的窗口将被自动关闭
# 注意: 只保留明确是弹窗的标题, 移除 "提示/确认/消息/信息/通知" 等过于宽泛的词
#       (这些词会匹配到交易确认对话框, 导致误关或误触交易)
POPUP_TITLE_PATTERNS = [
    # 同花顺主程序弹窗 (明确标题)
    "同花顺提示",
    "同花顺消息",
    "同花顺通知",
    "同花顺升级",

    # 升级/更新 (明确弹窗)
    "自动更新",
    "检测到新版本",
    "新版可用",

    # 新闻/广告 (明确弹窗)
    "重要公告",
    "新股申购",
    "中签",

    # 行情/系统连接 (明确弹窗)
    "行情连接断开",
    "连接失败",
    "登录超时",
    "会话过期",

    # 弹窗广告
    "弹幕",
    "推广",
    "活动",
]

# 绝对不能关闭的窗口（白名单关键词）
NEVER_CLOSE_PATTERNS = [
    "潜龙",
    "Claude",
    "VS Code",
    "Visual Studio",
    "Windows PowerShell",
    "cmd.exe",
    "量化",
    "Flask",
    "Python",
]

# 主THS窗口标题（保留不杀）
MAIN_THS_TITLES = [
    "网上股票交易系统5.0",
    "网上股票交易系统",
    "同花顺(v",
    "同花顺V",
]

# ── 统计 ──
_stats = {
    "total_closed": 0,
    "last_close_time": None,
    "last_closed_title": "",
    "log": [],  # 最近20条关闭记录
    "running": False,
}

def _get_window_title(hwnd):
    """获取窗口标题"""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_class_name(hwnd):
    """获取窗口类名"""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_window_visible(hwnd):
    """检查窗口是否可见"""
    return bool(user32.IsWindowVisible(hwnd))


def _is_popup_window(hwnd):
    """判断窗口是否为需要关闭的弹窗"""
    # 快速跳过：不可见窗口
    if not _is_window_visible(hwnd):
        return False

    title = _get_window_title(hwnd)
    if not title or len(title) < 1:
        return False

    # ── 白名单检查：绝对不能碰的窗口 ──
    for pattern in NEVER_CLOSE_PATTERNS:
        if pattern in title:
            return False

    # ── 主THS交易窗口保留 ──
    # 标题如 "网上股票交易系统5.0" 是主窗口，不能关
    for main_title in MAIN_THS_TITLES:
        if title == main_title or title.startswith(main_title):
            return False

    # ── 弹窗匹配 ──
    for pattern in POPUP_TITLE_PATTERNS:
        if pattern in title:
            return True

    return False


def _close_window(hwnd, title=""):
    """关闭弹窗 -- 首选 ESC，无效则 WM_CLOSE

    安全原则: 绝对不发送 ENTER (VK_RETURN)
      ENTER 可能误触交易确认对话框的"确定"按钮, 导致意外下单/撤单
      只用 ESC (取消操作) 和 WM_CLOSE (强制关闭)
    """
    # 方式1: 发送 ESC 键（安全，取消操作不改变交易意图）
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
    time.sleep(0.05)

    # 检查是否已关闭
    if not _is_window_visible(hwnd):
        return True, "ESC"

    # 方式2: WM_CLOSE (强制关闭, 不触发任何按钮)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.05)

    if not _is_window_visible(hwnd):
        return True, "WM_CLOSE"

    return False, "failed"


def _kill_popups_once():
    """单次弹窗扫描+关闭 (加锁, 与持仓读取互斥)"""
    closed = []

    def enum_callback(hwnd, _lparam):
        try:
            if _is_popup_window(hwnd):
                title = _get_window_title(hwnd)
                cls = _get_class_name(hwnd)
                ok, method = _close_window(hwnd, title)
                if ok:
                    closed.append((title, cls, method))
        except Exception:
            pass
        return True  # 继续枚举

    # 定义回调类型
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    callback = WNDENUMPROC(enum_callback)

    # 加锁: 与持仓读取/键盘交易的 Win32 操作互斥, 防止消息泵死锁
    acquired = THS_WIN32_LOCK.acquire(timeout=3.0)
    if not acquired:
        print("[PopupKiller] 获取 THS 锁超时, 跳过本轮扫描", flush=True)
        return []
    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        pass
    finally:
        THS_WIN32_LOCK.release()

    # 更新统计
    if closed:
        _stats["total_closed"] += len(closed)
        _stats["last_close_time"] = datetime.now().strftime("%H:%M:%S")
        _stats["last_closed_title"] = closed[-1][0][:60]
        for title, cls, method in closed:
            _stats["log"].append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "title": title[:80],
                "class": cls,
                "method": method,
            })
        # 只保留最近 50 条
        if len(_stats["log"]) > 50:
            _stats["log"] = _stats["log"][-50:]

    return closed


# ── 后台守护 ──
_killer_thread = None
_stop_event = None


def _killer_loop(interval: float = 2.0):
    """后台守护循环：每隔 interval 秒扫描一次"""
    _stop_event.clear()
    # 首次延迟5秒，等系统稳定后再开始杀弹窗
    time.sleep(5)

    while not _stop_event.is_set():
        try:
            closed = _kill_popups_once()
            if closed:
                names = ", ".join([c[0][:30] for c in closed[:3]])
                print(f"[PopupKiller] 关闭 {len(closed)} 个弹窗: {names}", flush=True)
        except Exception as e:
            print(f"[PopupKiller] 扫描异常: {e}", flush=True)

        # 分段等待，便于快速响应停止信号
        for _ in range(int(interval * 2)):
            if _stop_event.is_set():
                break
            time.sleep(0.5)


def start(interval: float = 2.0):
    """启动弹窗杀手守护线程

    Args:
        interval: 扫描间隔（秒），默认2秒
    """
    global _killer_thread, _stop_event

    if _killer_thread and _killer_thread.is_alive():
        print("[PopupKiller] 已在运行中")
        return False

    _stop_event = threading.Event()
    _killer_thread = threading.Thread(
        target=_killer_loop,
        args=(interval,),
        daemon=True,
        name="THS-PopupKiller"
    )
    _killer_thread.start()
    _stats["running"] = True
    print(f"[PopupKiller] 启动成功（扫描间隔 {interval}s）")
    return True


def stop():
    """停止弹窗杀手"""
    global _killer_thread, _stop_event

    if _stop_event:
        _stop_event.set()

    if _killer_thread and _killer_thread.is_alive():
        _killer_thread.join(timeout=3.0)

    _stats["running"] = False
    print(f"[PopupKiller] 已停止（累计关闭 {_stats['total_closed']} 个弹窗）")


def get_stats():
    """获取运行统计"""
    return {
        **{k: v for k, v in _stats.items() if k != "log"},
        "recent_log": _stats["log"][-10:],
    }


def is_running():
    """检查是否在运行"""
    return _stats.get("running", False) and \
           _killer_thread is not None and \
           _killer_thread.is_alive()


# ── 便捷：弹窗静默模式（自动启动） ──
def auto_start_if_trading():
    """交易时段自动启动弹窗杀手"""
    try:
        from datetime import datetime
        now = datetime.now()
        t = now.hour * 100 + now.minute
        # 9:00-15:30 交易相关时段
        if 900 <= t <= 1530 and now.weekday() < 5:
            if not is_running():
                start(interval=1.5)  # 交易时段扫描更频繁
                return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    # 测试：手动扫描一次
    print("[PopupKiller] 手动扫描...")
    closed = _kill_popups_once()
    if closed:
        for title, cls, method in closed:
            print(f"  关闭: [{cls}] {title[:60]} (via {method})")
        print(f"\n共关闭 {len(closed)} 个弹窗")
    else:
        print("  未发现需要关闭的弹窗")
