"""同花顺持仓自动导出 — 通过 UI 自动化触发导出并读取"""
import os, time, csv, io, threading
from datetime import datetime

EXPORT_FILE = r"d:\quant_framework\live_positions.csv"
_ths_window_missing = False  # E40: 去重标记，避免重复刷屏

def export_ths_positions():
    """通过 UI 自动化导出同花顺持仓 — 后台静默模式，不抢焦点"""
    try:
        from pywinauto import Application, findwindows
        from pywinauto.keyboard import send_keys

        # 查找 xiadan 交易窗口 "网上股票交易系统5.0"
        dlg = findwindows.find_window(title_re=".*网上股票交易系统.*")
        if not dlg:
            global _ths_window_missing
            if not _ths_window_missing:
                print("[AutoExport] THS window not found (suppressing repeats)")
                _ths_window_missing = True
            return False
        _ths_window_missing = False  # 窗口恢复了，重置标记

        app = Application().connect(handle=dlg)
        window = app.window(handle=dlg)

        # 最小化窗口再操作，避免抢焦点弹窗
        was_minimized = window.is_minimized()
        if not was_minimized:
            window.minimize()
            time.sleep(0.3)

        # 方式A: Ctrl+S 导出 (后台窗口也能接收)
        send_keys('^s')
        time.sleep(0.5)
        if _check_save_dialog():
            if not was_minimized:
                try: window.restore()
                except: pass
            return True

        if not was_minimized:
            try: window.restore()
            except: pass
        return os.path.exists(EXPORT_FILE) and os.path.getsize(EXPORT_FILE) > 50

    except ImportError:
        print("[AutoExport] pywinauto not installed")
        return False
    except Exception as e:
        print(f"[AutoExport] Error [{type(e).__name__}]: {e}")
        return False


def _check_save_dialog():
    """检查导出对话框并输入文件名保存"""
    try:
        from pywinauto import findwindows
        from pywinauto.keyboard import send_keys
        # 查找保存对话框
        save_dlg = findwindows.find_window(title_re=".*(另存为|保存|导出|Save).*")
        if save_dlg:
            app2 = Application().connect(handle=save_dlg)
            save_window = app2.window(handle=save_dlg)
            save_window.set_focus()
            time.sleep(0.1)
            send_keys(EXPORT_FILE)
            time.sleep(0.1)
            send_keys('{ENTER}')
            time.sleep(0.5)
            return True
    except:
        pass
    return False


def read_exported_file():
    """读取导出的 CSV 文件"""
    if not os.path.exists(EXPORT_FILE):
        return []

    positions = []
    try:
        for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312']:
            try:
                with open(EXPORT_FILE, 'r', encoding=enc) as f:
                    content = f.read()
                break
            except: pass

        if not content: return []

        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            try:
                positions.append({
                    "symbol": str(row.get("代码", row.get("symbol", ""))).strip(),
                    "name": str(row.get("名称", row.get("name", ""))).strip(),
                    "quantity": int(float(row.get("数量", row.get("quantity", "0")) or 0)),
                    "cost_price": float(row.get("成本价", row.get("cost", "0")) or 0),
                    "current_price": float(row.get("现价", row.get("price", "0")) or 0),
                    "market_value": float(row.get("市值", row.get("value", "0")) or 0),
                    "profit_pct": float(row.get("盈亏%", row.get("pnl_pct", "0")) or 0),
                    "profit_amt": float(row.get("盈亏", row.get("pnl", "0")) or 0),
                    "today_buy": str(row.get("今日买入", "0")).strip() == "1",
                })
            except: pass
    except: pass

    return positions


# ═══════════════════════════════════════════════
# 定时自动导出 (仅交易时段: 9:25-15:05)
# ═══════════════════════════════════════════════
_auto_export_running = False

def is_trading_time():
    """判断是否在交易时段 (含盘前竞价)"""
    now = datetime.now()
    t = now.hour * 100 + now.minute
    # 9:15-15:10 交易时段，周一至周五
    return 915 <= t <= 1510 and now.weekday() < 5

def start_auto_export(interval_seconds=60):
    """启动定时自动导出线程"""
    global _auto_export_running
    if _auto_export_running:
        return
    _auto_export_running = True

    def _loop():
        while True:
            try:
                if is_trading_time():
                    export_ths_positions()
            except Exception as e:
                print(f"[AutoExport] Loop error [{type(e).__name__}]: {e}")
            time.sleep(interval_seconds)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[AutoExport] Started (interval={interval_seconds}s, trading hours only)")
