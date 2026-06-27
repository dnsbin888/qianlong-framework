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


def _clean_code(symbol: str) -> str:
    """清洗股票代码为纯6位数字（联动精灵格式）"""
    code = str(symbol).replace('sh', '').replace('sz', '').replace('bj', '')
    code = code.replace('SH', '').replace('SZ', '').replace('BJ', '')
    return code


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
                          capture_output=True, text=True, timeout=3)
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
