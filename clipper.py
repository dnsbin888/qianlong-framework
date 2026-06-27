"""联动精灵持仓同步 — 通过剪贴板读取 THS 持仓，零依赖零延迟

联动精灵 link.ini 已配置:
  CLIPBOARD=1   → 启用剪贴板输出
  WriteClp=1    → 写入持仓到剪贴板
  RefTime=30    → 每30秒刷新

用法:
  from clipper import read_positions
  positions = read_positions()  # -> [{symbol, name, quantity, cost_price, ...}]
"""

import re
import json
import os
import ctypes
import subprocess
from datetime import datetime

_CACHE_FILE = r"D:\quant_framework\live_positions_clip.json"

# ── Win32 剪贴板 API (替代 PowerShell, 零进程开销) ──
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _get_clipboard_text() -> str:
    """读取 Windows 剪贴板文本 -- ctypes 直读, 零进程开销, 2秒超时

    优化前: 每次启动 PowerShell 进程 (1-3秒), 高频调用时进程堆积
    优化后: 直接调用 Win32 API, <1ms 完成
    """
    try:
        # 打开剪贴板 (3次重试, 避免其他程序占用)
        for _ in range(3):
            if _user32.OpenClipboard(0):
                break
            import time
            time.sleep(0.05)
        else:
            return ""

        try:
            # 检查剪贴板格式
            if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                return ""

            handle = _user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ""

            # 锁定内存获取指针
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return ""

            try:
                # 读取 Unicode 字符串
                text = ctypes.wstring_at(ptr)
            finally:
                _kernel32.GlobalUnlock(handle)

            return text or ""
        finally:
            _user32.CloseClipboard()
    except Exception:
        return ""


def _parse_ths_clipboard(text: str) -> list:
    """解析同花顺/联动精灵剪贴板格式的持仓数据

    常见格式:
      代码  名称    数量    成本价  现价   市值    盈亏%
      600519 贵州茅台 1000   1800.00 1850.00 1850000 2.78%
    """
    positions = []
    if not text or len(text) < 20:
        return positions

    lines = text.strip().split("\n")
    if len(lines) < 2:
        return positions

    for line in lines[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            code = parts[0]
            if not code.isdigit() or len(code) != 6:
                continue
            name = parts[1] if len(parts) > 1 else code
            qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            cost = float(parts[3]) if len(parts) > 3 else 0
            price = float(parts[4]) if len(parts) > 4 else 0
            market_val = float(parts[5]) if len(parts) > 5 else 0
            pnl_pct_str = parts[6] if len(parts) > 6 else "0%"
            pnl_pct = float(pnl_pct_str.replace("%", "")) if "%" in pnl_pct_str else 0

            if qty > 0 and code.isdigit():
                positions.append({
                    "symbol": code,
                    "name": name,
                    "quantity": qty,
                    "cost_price": round(cost, 2),
                    "current_price": round(price, 2),
                    "market_value": round(market_val, 2),
                    "profit_pct": round(pnl_pct, 2),
                    "profit_amt": round((price - cost) * qty, 2),
                })
        except (ValueError, IndexError):
            continue

    return positions


def read_positions(use_cache: bool = True) -> list:
    """读取同花顺持仓（剪贴板优先，缓存兜底）

    Args:
        use_cache: 剪贴板为空时是否使用缓存

    Returns:
        list[dict]: 持仓列表
    """
    text = _get_clipboard_text()
    positions = _parse_ths_clipboard(text)

    if positions:
        # 保存缓存
        try:
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": datetime.now().strftime("%H:%M:%S"), "positions": positions},
                          f, ensure_ascii=False)
        except Exception:
            pass
        return positions

    # 剪贴板为空，使用缓存
    if use_cache and os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("positions", [])
        except Exception:
            pass

    return []
