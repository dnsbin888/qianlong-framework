"""潜龙实盘交易引擎 — 键盘交易 + 同花顺持仓读取 + 程序化自动交易
依赖: pip install pyautogui pygetwindow keyboard pynput
"""
import os, sys, json, time, threading, re
from datetime import datetime
from pathlib import Path

# ── 可选依赖 ──
try:
    import pyautogui
    PYAUTOGUI_OK = True
except ImportError:
    PYAUTOGUI_OK = False
    print("[Trader] pyautogui not installed - keyboard trading disabled. pip install pyautogui")

try:
    import keyboard as kb
    KEYBOARD_OK = True
except ImportError:
    KEYBOARD_OK = False
    print("[Trader] keyboard not installed - hotkeys disabled. pip install keyboard")

try:
    import pygetwindow as gw
    GW_OK = True
except ImportError:
    GW_OK = False
    print("[Trader] pygetwindow not installed. pip install pygetwindow")

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════
CONFIG = {
    "ths_window_title": "同花顺",
    "ths_position_file": "",
    "hotkey_buy": "f1", "hotkey_sell": "f2", "hotkey_cancel": "f3",
    "hotkey_buy_up": "f5", "hotkey_sell_down": "f6",
    "hotkey_half_position": "f7", "hotkey_full_position": "f8",
    # ── 风控参数 (对标QMT/PTrade) ──
    # ── 用户自定义止盈规则 ──
    "tp1_profit_pct": 0.05,            # 移动止盈1: 盈利≥5%触发
    "tp1_trail_pct": -0.01,            # 移动止盈1: 回落1%卖出1/3
    "tp1_sell_ratio": 0.33,            # 移动止盈1: 卖1/3
    "tp1_stop_loss": -0.03,            # 移动止盈1: 止损-3%
    "tp2_profit_pct": 0.07,            # 移动止盈2: 盈利≥7%触发
    "tp2_trail_pct": -0.02,            # 移动止盈2: 回落2%卖出1/3
    "tp2_sell_ratio": 0.33,            # 移动止盈2: 卖1/3
    "tp2_stop_loss": -0.05,            # 移动止盈2: 止损-5%
    "limit_up_hold": True,             # 涨停持仓不卖(除非回落>3%)
    "limit_up_drop_sell": -0.03,       # 涨停开盘回落3%卖出
    "position_pct_lv3": 0.20,          # 信号3级: 1/5仓位
    "position_pct_lv4": 0.33,          # 信号4级: 1/3仓位
    "position_pct_lv5": 0.50,          # 信号5级: 1/2仓位
    "max_single_position_pct": 20,     # 单票最大仓位%
    "max_sector_pct": 30,              # 同行业最大仓位%
    "max_daily_trades": 5,             # 每天最多交易笔数
    "max_daily_loss": -5.0,            # 日内最大亏损%
    "max_consecutive_loss": 3,         # 连续亏损次数上限
    "max_hold_days": 5,                # 持仓超5天未盈利自动退出
    "max_cancel_rate": 0.50,           # 撤单率>50%触发风控
    "max_order_value": 1_000_000,      # 单笔最大金额(元)
    "auto_trade_enabled": False,
    "signal_min_strength": 5,
}
# 尝试从外部JSON文件加载覆盖配置，便于UI持久化和热更新
_cfg_file = r"d:\\quant_framework\\live_trader_config.json"
if os.path.exists(_cfg_file):
    try:
        with open(_cfg_file, 'r', encoding='utf-8') as _f:
            _data = json.load(_f)
        if isinstance(_data, dict):
            CONFIG.update(_data)
            print(f"[Trader] Loaded CONFIG overrides from {_cfg_file}")
    except Exception as _e:
        print(f"[Trader] Failed to load CONFIG overrides: {_e}")

# ═══════════════════════════════════════════════════════
# 交易状态
# ═══════════════════════════════════════════════════════
class TradeState:
    def __init__(self):
        self.positions = []            # 当前持仓
        self.orders = []               # 今日委托
        self.fills = []                # 今日成交
        self.pnl = {"daily": 0, "total": 0}
        self.risk = {"daily_loss": 0, "consecutive_loss": 0}
        self.auto_trade_log = []
        self.last_update = None

    def to_dict(self):
        return {
            "positions": self.positions,
            "orders": self.orders[-20:],
            "fills": self.fills[-20:],
            "pnl": self.pnl,
            "risk": self.risk,
            "auto_trade_enabled": CONFIG["auto_trade_enabled"],
            "auto_trade_log": self.auto_trade_log[-30:],
            "last_update": self.last_update.strftime("%H:%M:%S") if self.last_update else None,
        }

state = TradeState()

# ═══════════════════════════════════════════════════════
# 同花顺持仓读取
# ═══════════════════════════════════════════════════════
def read_ths_positions():
    """从同花顺读取当前持仓 — 使用多策略自动读取器。"""
    # 优先使用 THS 综合读取器
    try:
        from ths_position_reader import ths_reader
        result = ths_reader.read_all()
        if result and result.get("positions"):
            state.last_update = datetime.now()
            state.positions = result["positions"]
            return result["positions"]
    except ImportError:
        pass

    positions = []

    # 方式1: 尝试读取窗口标题中的持仓信息
    if GW_OK:
        try:
            windows = gw.getWindowsWithTitle(CONFIG["ths_window_title"])
            if windows:
                win = windows[0]
                title = win.title
                # 同花顺标题格式: "同花顺(v9.xx) - [持仓] 总资产: xxx 盈亏: xxx"
                state.last_update = datetime.now()
        except: pass

    # 方式2: 从导出的持仓文件读取
    # 直接读取同花顺导出文件
    pos_file = r"C:\Users\Administrator\Documents\table.xls"
    if not os.path.exists(pos_file):
        pos_file = r"d:\quant_framework\live_positions.csv"

    print(f"[Trader] Reading from: {pos_file} exists={os.path.exists(pos_file)} size={os.path.getsize(pos_file) if os.path.exists(pos_file) else 0}")

    if pos_file and os.path.exists(pos_file):
        print(f"[Trader] File found, reading...")
        try:
            import csv, io

            # 读取文件内容 — 用PowerShell绕过同花顺文件锁
            content = None
            # 方式A: 普通读取
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312']:
                try:
                    with open(pos_file, 'r', encoding=enc) as f:
                        content = f.read()
                    if content: break
                except: pass
            # 方式B: 二进制解码
            if not content:
                try:
                    with open(pos_file, 'rb') as f:
                        raw = f.read()
                    for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312']:
                        try: content = raw.decode(enc); break
                        except: pass
                except: pass
            # 方式C: PowerShell读取 (可读锁定文件)
            if not content:
                try:
                    import subprocess
                    r = subprocess.run(['powershell', '-Command',
                        f'Get-Content "{pos_file}" -Raw -Encoding UTF8'],
                        capture_output=True, text=True, timeout=5)
                    if r.stdout: content = r.stdout
                except: pass

            if content and len(content) > 10:
                # 自动检测格式: CSV / TSV / 固定宽度
                lines = content.strip().split('\n')
                if len(lines) < 2:
                    return positions

                # 检测格式
                first_line = lines[0]
                if '<table' in content.lower() or '<tr>' in content.lower():
                    delimiter = 'html'
                elif '\t' in first_line:
                    delimiter = '\t'
                elif ',' in first_line:
                    delimiter = ','
                elif '|' in first_line:
                    delimiter = '|'
                else:
                    delimiter = None  # 固定宽度

                if delimiter == 'html':
                    import re
                    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.I|re.S)
                    if rows:
                        headers = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0], re.I|re.S)
                        headers = [h.strip() for h in headers]
                        for row_html in rows[1:]:
                            cells = [c.strip() for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row_html, re.I|re.S)]
                            if len(cells) >= len(headers):
                                row_dict = dict(zip(headers, cells))
                                try:
                                    p = _parse_position_row(row_dict)
                                    if p["symbol"]:
                                        positions.append(p)
                                except: pass
                elif delimiter:
                    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
                    for row in reader:
                        try:
                            p = _parse_position_row(row)
                            if p["symbol"]:
                                positions.append(p)
                        except: pass
                else:
                    # 固定宽度格式: 代码(6) 名称(8) 数量(8) 成本价(8) 现价(8) ...
                    for line in lines[1:]:
                        try:
                            row = {
                                "代码": line[0:6].strip(),
                                "名称": line[6:14].strip(),
                                "数量": line[14:22].strip(),
                                "成本价": line[22:30].strip(),
                                "现价": line[30:38].strip(),
                            }
                            positions.append(_parse_position_row(row))
                        except: pass

                state.last_update = datetime.now()
                if positions:
                    print(f"[Trader] Loaded {len(positions)} positions from {pos_file}")
        except Exception as e:
            print(f"[Trader] Failed to read position file: {e}")

    # 方式3: 模拟数据兜底
    if not positions:
        positions = _generate_demo_positions()

    state.positions = positions
    return positions


def _parse_position_row(row):
    """解析持仓行数据，兼容多种列名格式"""
    def _cell(*keys):
        for k in keys:
            v = row.get(k, "")
            if v:
                return str(v).strip()
        return ""

    def _float(*keys):
        for k in keys:
            v = row.get(k, "")
            if v:
                try: return float(str(v).replace(",", ""))
                except: pass
        return 0.0

    def _int(*keys):
        return int(_float(*keys))

    return {
        "symbol": _cell("代码", "symbol", "证券代码"),
        "name": _cell("名称", "name", "证券名称"),
        "quantity": _int("数量", "quantity", "持仓数量", "股票余额", "可用余额"),
        "cost_price": _float("成本价", "cost", "成本", "摊薄成本价"),
        "current_price": _float("现价", "price", "最新价"),
        "market_value": _float("市值", "value", "参考市值"),
        "profit_pct": _float("盈亏%", "pnl_pct", "盈亏比例", "盈亏比例(%)"),
        "profit_amt": _float("盈亏", "pnl", "浮动盈亏", "摊薄盈亏"),
        "today_buy": _cell("今日买入") == "1",
    }


def _generate_demo_positions():
    """生成演示持仓数据"""
    import numpy as np
    positions = []
    for i in range(np.random.randint(2, 6)):
        cost = round(np.random.uniform(10, 60), 2)
        current = round(cost * (1 + np.random.uniform(-0.08, 0.12)), 2)
        qty = np.random.randint(500, 5000) * 100
        positions.append({
            "symbol": f"{np.random.choice(['600','000','300','688'])}{np.random.randint(100,999):03d}",
            "name": f"持仓股{i+1}",
            "quantity": qty,
            "cost_price": cost,
            "current_price": current,
            "market_value": round(current * qty, 0),
            "profit_pct": round((current/cost - 1) * 100, 2),
            "profit_amt": round((current - cost) * qty, 0),
            "today_buy": i == 0,
        })
    return positions

# ═══════════════════════════════════════════════════════
# 键盘交易引擎
# ═══════════════════════════════════════════════════════
class KeyboardTrader:
    def __init__(self):
        self.enabled = PYAUTOGUI_OK
        self.hotkeys_registered = False

    def focus_ths(self):
        """聚焦同花顺交易窗口"""
        if not GW_OK: return False
        try:
            # 优先找交易窗口(网上股票交易系统), 其次找同花顺主窗口
            for title in ["网上股票交易系统", "同花顺"]:
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    windows[0].activate()
                    time.sleep(0.1)
                    return True
        except: pass
        return False

    def send_buy_order(self, code=None, price=None, quantity=None):
        """发送买入指令 — 模拟键盘操作"""
        if not self.enabled:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            if not self.focus_ths():
                return {"success": False, "error": "同花顺窗口未找到，请打开交易窗口"}
            time.sleep(0.05)
            if code:
                # 去除sh/sz前缀(同花顺只认纯数字代码)
                clean_code = str(code).replace('sh','').replace('sz','').replace('SH','').replace('SZ','')
                pyautogui.press('f1')
                time.sleep(0.1)
                pyautogui.write(clean_code)
                time.sleep(0.05)
                pyautogui.press('enter')
                time.sleep(0.1)
                if price:
                    pyautogui.write(str(price))
                    pyautogui.press('enter')
                    time.sleep(0.05)
                if quantity:
                    pyautogui.write(str(quantity))
                    pyautogui.press('enter')
            else:
                pyautogui.press(CONFIG["hotkey_buy"])
            print(f"[Trader] BUY {clean_code} {price} x{quantity} — OK")
            return {"success": True, "action": "buy", "code": clean_code}
        except Exception as e:
            print(f"[Trader] BUY failed: {e}")
            return {"success": False, "error": str(e)}

    def send_sell_order(self, code=None, quantity=None):
        """发送卖出指令"""
        if not self.enabled:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            if not self.focus_ths():
                return {"success": False, "error": "同花顺交易窗口未打开"}
            time.sleep(0.05)
            if code:
                clean_code = str(code).replace('sh','').replace('sz','').replace('SH','').replace('SZ','')
                pyautogui.press('f2')
                time.sleep(0.1)
                pyautogui.write(clean_code)
                time.sleep(0.05)
                pyautogui.press('enter')
                if quantity:
                    time.sleep(0.1)
                    pyautogui.write(str(quantity))
                    pyautogui.press('enter')
            else:
                pyautogui.press(CONFIG["hotkey_sell"])
            print(f"[Trader] SELL {clean_code} x{quantity} — OK")
            return {"success": True, "action": "sell", "code": clean_code}
        except Exception as e:
            print(f"[Trader] SELL failed: {e}")
            return {"success": False, "error": str(e)}

    def cancel_all_orders(self):
        """撤单"""
        if not self.enabled: return {"success": False}
        try:
            self.focus_ths()
            time.sleep(0.05)
            pyautogui.press(CONFIG["hotkey_cancel"])
            return {"success": True, "action": "cancel_all"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def register_hotkeys(self):
        """注册全局热键"""
        if not KEYBOARD_OK or self.hotkeys_registered: return
        try:
            kb.add_hotkey(CONFIG["hotkey_buy"], lambda: self.send_buy_order())
            kb.add_hotkey(CONFIG["hotkey_sell"], lambda: self.send_sell_order())
            kb.add_hotkey(CONFIG["hotkey_cancel"], lambda: self.cancel_all_orders())
            kb.add_hotkey(CONFIG["hotkey_buy_up"], lambda: self.send_buy_order())
            kb.add_hotkey(CONFIG["hotkey_sell_down"], lambda: self.send_sell_order())
            self.hotkeys_registered = True
            print("[Trader] Hotkeys registered: F1=Buy F2=Sell F3=Cancel F5=BuyUp F6=SellDown")
        except Exception as e:
            print(f"[Trader] Failed to register hotkeys: {e}")

    def unregister_hotkeys(self):
        if KEYBOARD_OK and self.hotkeys_registered:
            kb.unhook_all()
            self.hotkeys_registered = False

trader = KeyboardTrader()

# ═══════════════════════════════════════════════════════
# 程序化自动交易规则引擎
# ═══════════════════════════════════════════════════════
class AutoTradeEngine:
    def __init__(self):
        self.running = False
        self.thread = None
        self.daily_trade_count = 0
        self.consecutive_losses = 0
        self.last_trade_date = None
        # 持仓峰值跟踪(移动止损用)
        self.position_peaks = {}

    def check_rules(self, signal_data=None):
        """检查自动交易规则 — 用户自定义版"""
        actions = []
        positions = state.positions
        now = datetime.now()

        if self.last_trade_date != now.date():
            self.daily_trade_count = 0
            self.last_trade_date = now.date()
        if self.daily_trade_count >= CONFIG["max_daily_trades"]:
            return actions

        for pos in positions:
            sym = pos["symbol"]
            pnl_pct = pos.get("profit_pct", 0)
            qty = pos.get("quantity", 0)

            # ── 涨停开盘: 回落>3%卖出 ──
            if pos.get("is_limit_up_open"):
                if pnl_pct <= CONFIG.get("limit_up_drop_sell", -0.03) * 100:
                    actions.append({"type": "sell", "symbol": sym, "reason": f"涨停开盘回落{pnl_pct:.1f}%卖出", "qty": qty})
                    if sym in self.position_peaks: del self.position_peaks[sym]
                continue  # 涨停开盘不触发其他规则

            # 更新峰值
            if sym not in self.position_peaks or pnl_pct > self.position_peaks[sym]:
                self.position_peaks[sym] = pnl_pct
            peak = self.position_peaks.get(sym, pnl_pct)

            # ── 移动止盈2: 峰值曾≥7%, 回落2%卖1/3, 止损-5% ──
            tp2_profit = CONFIG["tp2_profit_pct"] * 100
            tp2_trail = abs(CONFIG["tp2_trail_pct"]) * 100
            tp2_sell = CONFIG["tp2_sell_ratio"]
            tp2_stop = CONFIG["tp2_stop_loss"] * 100
            if peak >= tp2_profit and pnl_pct <= peak - tp2_trail:
                sell_qty = max(100, int(qty * tp2_sell) // 100 * 100)
                actions.append({"type": "sell", "symbol": sym, "reason": f"移动止盈2(峰值{peak:.1f}%回落≥{tp2_trail:.0f}%)", "qty": sell_qty})
                if pnl_pct <= tp2_stop:
                    actions.append({"type": "sell", "symbol": sym, "reason": f"止盈2止损({pnl_pct:.1f}%)", "qty": qty})
                if sym in self.position_peaks: del self.position_peaks[sym]
                continue

            # ── 移动止盈1: 峰值曾≥5%, 回落1%卖1/3, 止损-3% ──
            tp1_profit = CONFIG["tp1_profit_pct"] * 100
            tp1_trail = abs(CONFIG["tp1_trail_pct"]) * 100
            tp1_sell = CONFIG["tp1_sell_ratio"]
            tp1_stop = CONFIG["tp1_stop_loss"] * 100
            if peak >= tp1_profit and pnl_pct <= peak - tp1_trail:
                sell_qty = max(100, int(qty * tp1_sell) // 100 * 100)
                actions.append({"type": "sell", "symbol": sym, "reason": f"移动止盈1(峰值{peak:.1f}%回落≥{tp1_trail:.0f}%)", "qty": sell_qty})
                if sym in self.position_peaks: del self.position_peaks[sym]
                continue

            # ── 基本止损: 亏损超过-5%全卖 ──
            if pnl_pct <= -5:
                actions.append({"type": "sell", "symbol": sym, "reason": f"止损(-5%)", "qty": qty})
                if sym in self.position_peaks: del self.position_peaks[sym]

        # ── 信号买入 ──
        if signal_data and CONFIG["auto_trade_enabled"] and self.daily_trade_count < CONFIG["max_daily_trades"]:
            for sig in (signal_data or [])[:10]:
                bs = sig.get("buy_signal", 0)
                if bs < CONFIG["signal_min_strength"]: continue
                sym = sig.get("symbol", "")
                if any(p["symbol"] == sym for p in positions): continue
                if bs >= 5: pos_pct = CONFIG["position_pct_lv5"]
                elif bs >= 4: pos_pct = CONFIG["position_pct_lv4"]
                else: pos_pct = CONFIG["position_pct_lv3"]
                qty_label = {0.20:"1/5仓", 0.33:"1/3仓", 0.50:"1/2仓"}.get(pos_pct, "1/5仓")
                actions.append({"type": "buy", "symbol": sym, "reason": f"信号{bs}级({qty_label})", "qty": qty_label, "pos_pct": pos_pct})

        return actions

    def execute_actions(self, actions):
        """执行自动交易动作"""
        results = []
        for act in actions:
            if self.daily_trade_count >= CONFIG["max_daily_trades"]:
                break
            if act["type"] == "sell":
                r = trader.send_sell_order(code=act["symbol"], quantity=act.get("qty"))
            elif act["type"] == "buy":
                r = trader.send_buy_order(code=act["symbol"], quantity=act.get("qty"))
            else:
                r = {"success": False, "error": "unknown action"}
            results.append({**act, "result": r})
            if r.get("success"):
                self.daily_trade_count += 1
                if act["type"] == "sell":
                    pnl = act.get("profit_pct", 0)
                    if pnl < 0: self.consecutive_losses += 1
                    else: self.consecutive_losses = 0
            time.sleep(0.5)
        return results

    def start(self):
        if self.running: return
        self.running = True
        print("[AutoTrade] Engine started")

    def stop(self):
        self.running = False
        print("[AutoTrade] Engine stopped")

auto_engine = AutoTradeEngine()

# ═══════════════════════════════════════════════════════
# 后台持仓自动同步 (每30秒检查一次)
# ═══════════════════════════════════════════════════════
_sync_thread_started = False

def start_auto_sync():
    """启动后台自动同步线程"""
    global _sync_thread_started
    if _sync_thread_started:
        return
    _sync_thread_started = True

    def _sync_loop():
        while True:
            try:
                read_ths_positions()
                # 自动交易执行
                if CONFIG["auto_trade_enabled"] and PYAUTOGUI_OK:
                    # 熔断检查: 日亏损超限 → 只停买入，止损照常执行
                    if state.risk.get("daily_loss", 0) <= CONFIG["max_daily_loss"]:
                        print(f"[AutoTrade] Circuit breaker: daily_loss={state.risk['daily_loss']}% — 停止买入,止损继续")
                        # 只执行卖单(止损), 过滤掉买单
                        sell_only = [a for a in actions if a.get("type") == "sell"]
                        if sell_only:
                            results = auto_engine.execute_actions(sell_only)
                            for r in results:
                                state.auto_trade_log.append({
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "action": r.get("type", "?"),
                                    "code": r.get("symbol", ""),
                                    "reason": "(熔断中-仅止损)" + r.get("reason", ""),
                                    "result": "OK" if r.get("result", {}).get("success") else "FAIL",
                                })
                        state.auto_trade_log.append({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "action": "CIRCUIT_BREAKER",
                            "code": "",
                            "reason": f"日亏损{state.risk['daily_loss']}%触发熔断(停止买入,止损照常)",
                            "result": "BUY_STOPPED",
                        })
                    else:
                        signals = []
                        try: signals = store.get('signals', [])
                        except: pass
                        actions = auto_engine.check_rules(signals[:20] if signals else None)
                        if actions:
                            results = auto_engine.execute_actions(actions)
                            for r in results:
                                state.auto_trade_log.append({
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "action": r.get("type", "unknown"),
                                    "code": r.get("symbol", ""),
                                    "reason": r.get("reason", ""),
                                    "result": "OK" if r.get("result", {}).get("success") else "FAIL",
                                })
            except: pass
            time.sleep(30)

    t = threading.Thread(target=_sync_loop, daemon=True)
    t.start()
    print("[Trader] Auto-sync + Auto-trade started (30s interval)")


# ═══════════════════════════════════════════════════════
# 便捷接口
# ═══════════════════════════════════════════════════════
def get_trading_status():
    """获取完整交易状态"""
    return {
        "positions": state.positions,
        "orders": state.orders[-20:],
        "fills": state.fills[-20:],
        "pnl": state.pnl,
        "risk": state.risk,
        "auto_trade_enabled": CONFIG["auto_trade_enabled"],
        "auto_trade_log": state.auto_trade_log[-30:],
        "hotkeys_registered": trader.hotkeys_registered,
        "keyboard_enabled": PYAUTOGUI_OK,
        "last_update": state.last_update.strftime("%H:%M:%S") if state.last_update else None,
        "config": {
            "max_single_pct": CONFIG["max_single_position_pct"],
            "max_daily_loss": CONFIG["max_daily_loss"],
            "signal_min_strength": CONFIG["signal_min_strength"],
        }
    }

def execute_trade(action, code, price=None, quantity=None):
    """执行交易"""
    if action == "buy":
        return trader.send_buy_order(code, price, quantity)
    elif action == "sell":
        return trader.send_sell_order(code, quantity)
    elif action == "cancel":
        return trader.cancel_all_orders()
    return {"success": False, "error": "unknown action"}

def toggle_auto_trade(enabled=None):
    """开关自动交易"""
    if enabled is not None:
        CONFIG["auto_trade_enabled"] = bool(enabled)
    else:
        CONFIG["auto_trade_enabled"] = not CONFIG["auto_trade_enabled"]
    if CONFIG["auto_trade_enabled"]:
        auto_engine.start()
    else:
        auto_engine.stop()
    return {"auto_trade_enabled": CONFIG["auto_trade_enabled"]}
