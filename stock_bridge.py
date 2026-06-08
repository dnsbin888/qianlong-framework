"""股票代码桥接器 — 网站 ↔ 同花顺/通达信 ↔ 联动精灵
通过共享文件、剪贴板、键盘模拟实现实时联动
"""
import os, time, json, threading
from datetime import datetime

BRIDGE_FILE = r"d:\quant_framework\bridge_stock.json"

# ═══════════════════════════════════════════════
# 核心: 写入股票代码到桥接文件 (联动精灵监控此文件)
# ═══════════════════════════════════════════════
def send_stock_to_ths(symbol, name="", action="view"):
    """发送股票代码到同花顺
    action: view(查看), buy(买入), sell(卖出)
    """
    data = {
        "symbol": symbol,
        "name": name,
        "action": action,
        "timestamp": datetime.now().strftime("%H:%M:%S.%f"),
        "target": "同花顺",
    }
    with open(BRIDGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

    # 同时复制到剪贴板(联动精灵可直接读取)
    try:
        import pyperclip
        pyperclip.copy(symbol)
    except:
        pass

    # 也尝试直接激活同花顺并输入代码
    _send_to_window(symbol, "同花顺")

    return {"success": True, "symbol": symbol, "action": action}


def send_stock_to_tdx(symbol, name=""):
    """发送股票代码到通达信"""
    data = {
        "symbol": symbol,
        "name": name,
        "action": "view",
        "timestamp": datetime.now().strftime("%H:%M:%S.%f"),
        "target": "通达信",
    }
    with open(BRIDGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

    try:
        import pyperclip
        pyperclip.copy(symbol)
    except:
        pass

    _send_to_window(symbol, "通达信")

    return {"success": True, "symbol": symbol}


def _send_to_window(symbol, window_keyword):
    """激活目标窗口并输入股票代码"""
    try:
        import pygetwindow as gw
        import pyautogui

        windows = gw.getWindowsWithTitle(window_keyword)
        if windows:
            win = windows[0]
            win.activate()
            time.sleep(0.1)
            # 同花顺/通达信的代码输入框快捷键
            pyautogui.write(symbol, interval=0.02)
            pyautogui.press('enter')
    except Exception as e:
        pass  # 静默失败，不影响网页端


# ═══════════════════════════════════════════════
# 联动精灵文件监控建议配置
# ═══════════════════════════════════════════════
"""
联动精灵 → 文件监控 → 监控文件: d:\quant_framework\bridge_stock.json
当文件变化时 → 解析JSON → 根据action执行:
  - view: 切换到同花顺, 输入代码+回车
  - buy:  切换到同花顺, 打开买入面板, 输入代码
  - sell: 切换到同花顺, 打开卖出面板, 输入代码
"""


# ═══════════════════════════════════════════════
# 批量联动: 选股列表 → 同花顺批量导入
# ═══════════════════════════════════════════════
def send_batch_to_ths(symbols):
    """发送一批股票代码到同花顺(批量导入自选股)"""
    data = {
        "symbols": symbols,
        "action": "batch_import",
        "count": len(symbols),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "target": "同花顺",
    }
    with open(BRIDGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return {"success": True, "count": len(symbols)}


def get_bridge_status():
    """获取桥接状态"""
    status = {
        "bridge_file": BRIDGE_FILE,
        "bridge_exists": os.path.exists(BRIDGE_FILE),
        "ths_running": False,
        "tdx_running": False,
    }
    try:
        import pygetwindow as gw
        status["ths_running"] = len(gw.getWindowsWithTitle("同花顺")) > 0
        status["tdx_running"] = len(gw.getWindowsWithTitle("通达信")) > 0
    except:
        pass
    return status
