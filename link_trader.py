"""联动精灵交易桥接 — 通过 linkorder.dll 直接下单，替代 pywinauto 键盘模拟。

稳定 · 安全 · 无弹窗 · 无焦点抢占

接口:
    BUY(code, price, amount)   -> {"success": bool, "action": "buy"}
    SELL(code, price, amount)  -> {"success": bool, "action": "sell"}
    CANCEL(code)               -> {"success": bool, "action": "cancel"}

参数规则 (与联动精灵一致):
    price: 委托价格
    amount: <20 = 仓位比例 (如10=1/10), >=100 = 金额(元)
"""

import ctypes
import os
import struct
import sys

# ── DLL 路径 ──
_DLL_DIR = r"D:\联动精灵\第三方交易接口"
if struct.calcsize("P") == 8:  # 64-bit Python
    _DLL_PATH = os.path.join(_DLL_DIR, "Win64", "linkorder.dll")
else:
    _DLL_PATH = os.path.join(_DLL_DIR, "Win32", "linkorder.dll")

_dll = None
_loaded = False


def _load():
    """加载 DLL（延迟加载，不阻塞无 DLL 的环境）"""
    global _dll, _loaded
    if _loaded:
        return _dll is not None
    _loaded = True
    if not os.path.exists(_DLL_PATH):
        print(f"[LinkTrader] DLL not found: {_DLL_PATH}")
        return False
    try:
        _dll = ctypes.CDLL(_DLL_PATH)
        # 设置函数签名
        _dll.BUY.argtypes = [ctypes.c_char_p, ctypes.c_float, ctypes.c_int]
        _dll.BUY.restype = None
        _dll.SELL.argtypes = [ctypes.c_char_p, ctypes.c_float, ctypes.c_int]
        _dll.SELL.restype = None
        _dll.CANCEL.argtypes = [ctypes.c_char_p]
        _dll.CANCEL.restype = None
        print(f"[LinkTrader] DLL loaded: {_DLL_PATH}")
        return True
    except Exception as e:
        print(f"[LinkTrader] DLL load failed: {e}")
        return False


def normalize_code(symbol: str) -> str:
    """口径归一: 任意格式 → sh/sz+6位数字 (唯一真相格式)

    600001 → sh600001   000001 → sz000001
    sh600001 → sh600001  SZ300 → sz300
    600001.SH → sh600001
    """
    import re
    s = str(symbol).strip().lower()
    # 去掉 QMT 后缀 .sh/.sz
    s = re.sub(r'\.(sh|sz)$', '', s)
    # 提取字母前缀和数字
    m = re.search(r'^(sh|sz)?(\d{5,6})', s)
    if not m:
        return s  # 无法识别，原样返回
    pref, num = m.group(1), m.group(2)
    if pref:
        return pref + num
    # 按第一位数字判断市场
    if num[0] in ('6', '5', '8'):
        return 'sh' + num
    return 'sz' + num


def to_qmt_code(symbol: str) -> str:
    """转为 QMT/xtquant 格式: sh600001 → 600001.SH"""
    code = normalize_code(symbol)
    if code.startswith('sh'):
        return code[2:] + '.SH'
    return code[2:] + '.SZ'


def _clean_code(symbol: str) -> str:
    """提取纯6位数字（联动精灵 DLL 格式）"""
    import re
    code = normalize_code(symbol)
    m = re.search(r'\d{5,6}', code)
    return m.group(0) if m else str(symbol)


# ── 联动精灵/同花顺 路径 ──
_LJ_EXE = r"D:\联动精灵\link.exe"
_THS_XIADAN = r"D:\同花顺软件\同花顺\xiadan.exe"
_THS_EXE = r"D:\同花顺软件\同花顺\hexin.exe"


def _ensure_running(exe_path: str, name: str, timeout: int = 5) -> bool:
    """检查进程是否在运行（只检测不自动启动）

    安全原则: 不再用 subprocess.Popen 自动启动联动精灵/同花顺
      原因: 自动启动 xiadan.exe 会弹出登录窗口, 被弹窗杀手关闭 -> DLL加载失败 -> 连锁崩溃
      修复后: 只检测进程是否在运行, 不在则返回 False, 由用户手动启动
    """
    exe_name = os.path.basename(exe_path)
    try:
        import subprocess
        r = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe_name}'],
                          capture_output=True, text=True, encoding='gbk', errors='replace', timeout=3)
        if exe_name.lower() in r.stdout.lower():
            return True
    except:
        pass
    return False


def is_available() -> bool:
    """检查联动精灵 DLL 是否可用（只检测不自动启动进程）

    安全原则: 不自动启动联动精灵/同花顺, 避免弹窗杀手关闭登录窗口引发连锁崩溃
    用户需要手动启动联动精灵和同花顺交易端并登录
    """
    if not _load():
        return False
    # 只检测进程是否在运行, 不自动启动
    lj_ok = _ensure_running(_LJ_EXE, "联动精灵")
    if not lj_ok:
        print("[LinkTrader] ⚠️ 联动精灵未运行, 请手动启动并登录")
    ths_ok = _ensure_running(_THS_XIADAN, "同花顺交易")
    if not ths_ok:
        print("[LinkTrader] ⚠️ 同花顺交易端未运行, 请手动启动并登录")
    return _dll is not None

def diagnose() -> dict:
    """启动诊断：DLL可用性 + 联动精灵/同花顺运行状态（只检测不启动）"""
    result = {
        "dll_ok": _load(), "dll_path": _DLL_PATH,
        "dll_exists": os.path.exists(_DLL_PATH),
    }
    # 只检测进程状态, 不自动启动
    lj_ok = _ensure_running(_LJ_EXE, "联动精灵")
    result["link_running"] = lj_ok
    ths_ok = _ensure_running(_THS_XIADAN, "同花顺交易") or \
             _ensure_running(_THS_EXE, "同花顺")
    result["ths_running"] = ths_ok
    # 输出摘要
    if not result["dll_ok"]:
        print(f"[LinkTrader] ⚠️ DLL不可用: {_DLL_PATH}")
    if not lj_ok:
        print("[LinkTrader] ⚠️ 联动精灵未运行，请手动启动")
    if not ths_ok:
        print("[LinkTrader] ⚠️ 同花顺未运行，请手动打开并登录交易")
    print(f"[LinkTrader] 诊断: DLL={'✅' if result['dll_ok'] else '❌'} "
          f"联动精灵={'✅' if lj_ok else '❌'} "
          f"同花顺={'✅' if ths_ok else '❌'}")
    return result


def buy(code: str, price: float, amount: int = 10000) -> dict:
    """买入

    Args:
        code: 股票代码 (如 '600055' 或 'sh600055')
        price: 委托价格
        amount: 金额(>=100)或仓位比例(<20)
    """
    if not _load():
        return {"success": False, "error": "linkorder.dll 未加载"}
    try:
        clean = _clean_code(code)
        _dll.BUY(clean.encode('gbk'), ctypes.c_float(price), ctypes.c_int(amount))
        print(f"[LinkTrader] BUY {clean} @{price} amount={amount}")
        return {"success": True, "action": "buy", "code": clean, "price": price, "amount": amount}
    except Exception as e:
        print(f"[LinkTrader] BUY failed: {e}")
        return {"success": False, "error": str(e)}


def sell(code: str, price: float, ratio_or_amount: int = 1) -> dict:
    """卖出

    Args:
        code: 股票代码
        price: 委托价格
        ratio_or_amount: 仓位比例(<20, 如1=全仓卖出) 或 金额(>=100)
    """
    if not _load():
        return {"success": False, "error": "linkorder.dll 未加载"}
    try:
        clean = _clean_code(code)
        _dll.SELL(clean.encode('gbk'), ctypes.c_float(price), ctypes.c_int(ratio_or_amount))
        print(f"[LinkTrader] SELL {clean} @{price} ratio/amount={ratio_or_amount}")
        return {"success": True, "action": "sell", "code": clean, "price": price, "amount": ratio_or_amount}
    except Exception as e:
        print(f"[LinkTrader] SELL failed: {e}")
        return {"success": False, "error": str(e)}


def cancel(code: str) -> dict:
    """撤单"""
    if not _load():
        return {"success": False, "error": "linkorder.dll 未加载"}
    try:
        clean = _clean_code(code)
        _dll.CANCEL(clean.encode('gbk'))
        print(f"[LinkTrader] CANCEL {clean}")
        return {"success": True, "action": "cancel", "code": clean}
    except Exception as e:
        print(f"[LinkTrader] CANCEL failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════ 窗口联动 (Win32 API) ═══════════════════

# 看盘软件窗口检测关键词
_WINDOW_KEYWORDS = ["通达信", "同花顺", "TdxW", "Hexin", "THS"]
_user32 = None


def _get_user32():
    global _user32
    if _user32 is None:
        import ctypes.wintypes as _w
        _user32 = ctypes.windll.user32
        _user32.EnumWindows = _user32.EnumWindows
        _user32.GetWindowTextW = _user32.GetWindowTextW
        _user32.GetWindowTextLengthW = _user32.GetWindowTextLengthW
        _user32.IsWindowVisible = _user32.IsWindowVisible
        _user32.SetForegroundWindow = _user32.SetForegroundWindow
        _user32.ShowWindow = _user32.ShowWindow
        _user32.SW_RESTORE = 9
    return _user32


def _find_trade_window():
    """找第一个可见的看盘软件窗口，返回 (hwnd, title)。"""
    u32 = _get_user32()
    result = [None, ""]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 2)
        u32.GetWindowTextW(hwnd, buf, length + 2)
        title = buf.value
        if any(kw in title for kw in _WINDOW_KEYWORDS):
            result[0] = hwnd
            result[1] = title
            return False  # 找到，停止枚举
        return True

    u32.EnumWindows(_enum, 0)
    return result[0], result[1]


def lookup(code: str) -> str:
    """正向联动: 激活看盘软件窗口，输入代码打开K线。

    Returns:
        软件名称 (如 "通达信"/"同花顺") 或空字符串。
    """
    import time as _t
    hwnd, title = _find_trade_window()
    if not hwnd:
        print("[LinkTrader] 未找到看盘软件窗口")
        return ""

    u32 = _get_user32()
    clean = _clean_code(code)

    try:
        # 激活窗口
        u32.ShowWindow(hwnd, u32.SW_RESTORE)
        _t.sleep(0.1)
        u32.SetForegroundWindow(hwnd)
        _t.sleep(0.15)

        # 模拟键盘输入: 输入代码 + 回车
        import ctypes.wintypes as _w
        _keybd = ctypes.windll.user32.keybd_event
        # 先清空输入 (按 Escape)
        _keybd(0x1B, 0, 0, 0)  # VK_ESCAPE
        _t.sleep(0.05)

        # 逐字符输入代码
        for ch in clean:
            vk = _char_to_vk(ch)
            if vk:
                _keybd(vk, 0, 0, 0)
                _t.sleep(0.02)
                _keybd(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 2

        _t.sleep(0.1)
        # 回车
        _keybd(0x0D, 0, 0, 0)
        _t.sleep(0.02)
        _keybd(0x0D, 0, 2, 0)

        # 识别软件名
        if "通达信" in title or "TdxW" in title:
            sw = "通达信"
        elif "同花顺" in title or "Hexin" in title:
            sw = "同花顺"
        else:
            sw = title[:10]

        print(f"[LinkTrader] LOOKUP {clean} → {sw}")
        return sw
    except Exception as e:
        print(f"[LinkTrader] LOOKUP failed: {e}")
        return ""


def active_stock() -> str:
    """反向联动: 读取看盘软件窗口标题，提取当前股票代码。

    Returns:
        字符串如 "sh600228" 或空字符串。
    """
    import re
    hwnd, title = _find_trade_window()
    if not hwnd or not title:
        return ""

    # 从标题提取代码: 匹配 6位数字 模式
    m = re.search(r'([Ss][Hh]|[Ss][Zz])?([6][0-9]{5}|[03][0-9]{5}|[8][0-9]{5})', title)
    if m:
        code = (m.group(1) or 'sh').lower() + m.group(2)
        return code
    return ""


def _char_to_vk(ch: str) -> int:
    """字符 → 虚拟键码 (A-Z, 0-9, 小数点)"""
    if 'A' <= ch <= 'Z':
        return ord(ch)
    if 'a' <= ch <= 'z':
        return ord(ch.upper())
    if '0' <= ch <= '9':
        return ord(ch)
    return 0
