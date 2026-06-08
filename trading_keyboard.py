"""潜龙交易键盘适配 — 支持打版键盘 / 联动精灵 / 自定义快捷键
专业交易键盘键位扫描 → 映射到同花顺交易操作
"""
import time, json, os, threading
from datetime import datetime

# ═══════════════════════════════════════════════════════
# 打版键盘 / 联动精灵 默认键位映射
# ═══════════════════════════════════════════════════════
DEFAULT_KEYMAP = {
    # ── 打版键盘标准布局 ──
    "f13": {"action": "buy_limit_up",  "label": "涨停买入",   "group": "打版"},
    "f14": {"action": "sell_bid1",      "label": "卖一价卖出", "group": "打版"},
    "f15": {"action": "buy_ask1",       "label": "买一价买入", "group": "打版"},
    "f16": {"action": "sell_limit_down","label": "跌停卖出",   "group": "打版"},
    "f17": {"action": "sell_bid2",      "label": "卖二价卖出", "group": "打版"},
    "f18": {"action": "sell_bid3",      "label": "卖三价卖出", "group": "打版"},
    "f19": {"action": "buy_ask3",       "label": "买三价买入", "group": "打版"},
    "f20": {"action": "buy_ask2",       "label": "买二价买入", "group": "打版"},

    # ── 联动精灵扩展 ──
    "f21": {"action": "buy_half",       "label": "半仓买入",   "group": "仓位"},
    "f22": {"action": "buy_full",       "label": "全仓买入",   "group": "仓位"},
    "f23": {"action": "sell_full",      "label": "全仓卖出",   "group": "仓位"},
    "f24": {"action": "sell_half",      "label": "半仓卖出",   "group": "仓位"},

    # ── 标准键盘备用 ──
    "f1":  {"action": "buy_market",     "label": "市价买入",   "group": "标准"},
    "f2":  {"action": "sell_market",    "label": "市价卖出",   "group": "标准"},
    "f3":  {"action": "cancel_all",     "label": "一键撤单",   "group": "标准"},
    "f5":  {"action": "buy_ask1",       "label": "买一买入",   "group": "标准"},
    "f6":  {"action": "sell_bid1",      "label": "卖一卖出",   "group": "标准"},
    "f7":  {"action": "buy_half",       "label": "半仓买入",   "group": "标准"},
    "f8":  {"action": "buy_full",       "label": "全仓买入",   "group": "标准"},

    # ── 数字小键盘映射 (NumPad) ──
    "num_divide":   {"action": "sell_bid1",   "label": "卖一", "group": "小键盘"},
    "num_multiply": {"action": "buy_ask1",    "label": "买一", "group": "小键盘"},
    "num_subtract": {"action": "sell_market", "label": "市卖", "group": "小键盘"},
    "num_add":      {"action": "buy_market",  "label": "市买", "group": "小键盘"},
    "num_decimal":  {"action": "cancel_all",  "label": "撤单", "group": "小键盘"},
    "num0": {"action": "buy_full",    "label": "全买", "group": "小键盘"},
    "num1": {"action": "sell_bid3",   "label": "卖三", "group": "小键盘"},
    "num2": {"action": "sell_bid2",   "label": "卖二", "group": "小键盘"},
    "num3": {"action": "sell_bid1",   "label": "卖一", "group": "小键盘"},
    "num4": {"action": "buy_ask3",    "label": "买三", "group": "小键盘"},
    "num5": {"action": "buy_ask2",    "label": "买二", "group": "小键盘"},
    "num6": {"action": "buy_ask1",    "label": "买一", "group": "小键盘"},
    "num7": {"action": "sell_half",   "label": "半卖", "group": "小键盘"},
    "num8": {"action": "buy_half",    "label": "半买", "group": "小键盘"},
    "num9": {"action": "sell_full",   "label": "全卖", "group": "小键盘"},
}

# ═══════════════════════════════════════════════════════
# 键盘管理器
# ═══════════════════════════════════════════════════════
class TradingKeyboardManager:
    def __init__(self):
        self.keymap = dict(DEFAULT_KEYMAP)
        self.enabled = True
        self.hotkeys_registered = False
        self.key_log = []       # 最近按键记录
        self.active_code = None  # 当前输入的股票代码
        self._kb = None
        self._listener = None

        # 尝试导入
        try:
            import keyboard as _kb
            self._kb = _kb
            self.kb_ok = True
        except ImportError:
            self.kb_ok = False
            print("[TradeKB] keyboard not installed. pip install keyboard")

        try:
            from pynput import keyboard as _pkb
            self._pkb = _pkb
            self.pkb_ok = True
        except ImportError:
            self.pkb_ok = False

    def start(self):
        """启动键盘监听"""
        if not self.kb_ok: return False
        try:
            # 注册所有热键
            for key_name, cfg in self.keymap.items():
                try:
                    self._kb.add_hotkey(key_name, lambda k=key_name, c=cfg: self._on_hotkey(k, c),
                                        suppress=False, trigger_on_release=False)
                except Exception as e:
                    pass  # 某些键可能不存在

            self.hotkeys_registered = True
            print(f"[TradeKB] {len(self.keymap)} hotkeys registered")
            return True
        except Exception as e:
            print(f"[TradeKB] Failed to register hotkeys: {e}")
            return False

    def _on_hotkey(self, key_name, config):
        """热键触发时执行交易"""
        if not self.enabled: return

        action = config["action"]
        label = config["label"]
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # 记录日志
        self.key_log.append({"time": timestamp, "key": key_name, "action": action, "label": label})
        if len(self.key_log) > 100:
            self.key_log = self.key_log[-50:]

        # 执行交易操作
        self._execute_trade_action(action)

    def _execute_trade_action(self, action):
        """执行具体的交易操作 — 通过live_trader调用"""
        try:
            from live_trader import trader, state
            if not hasattr(trader, 'enabled') or not trader.enabled:
                return  # pyautogui not available

            trader.focus_ths()
            time.sleep(0.03)

            if action == "buy_limit_up":
                # 涨停价买入: 先获取涨停价, 再输入
                import pyautogui
                pyautogui.press('f1'); time.sleep(0.05)
                # 同花顺快捷键: 涨停买入
                pyautogui.hotkey('ctrl', 'b')  # 买入面板
            elif action == "sell_limit_down":
                import pyautogui
                pyautogui.hotkey('ctrl', 's')
            elif action == "buy_ask1":
                import pyautogui
                pyautogui.press('f5')  # 买一价
            elif action == "sell_bid1":
                import pyautogui
                pyautogui.press('f6')  # 卖一价
            elif action == "buy_ask2":
                import pyautogui
                pyautogui.press('f5'); time.sleep(0.02); pyautogui.press('f5')
            elif action == "sell_bid2":
                pyautogui.press('f6'); time.sleep(0.02); pyautogui.press('f6')
            elif action == "buy_ask3":
                pyautogui.press('f5'); time.sleep(0.02); pyautogui.press('f5'); time.sleep(0.02); pyautogui.press('f5')
            elif action == "sell_bid3":
                pyautogui.press('f6'); time.sleep(0.02); pyautogui.press('f6'); time.sleep(0.02); pyautogui.press('f6')
            elif action == "buy_market":
                import pyautogui
                pyautogui.press('f1')  # 买入面板
            elif action == "sell_market":
                pyautogui.press('f2')  # 卖出面板
            elif action == "buy_half":
                pyautogui.press('f7')
            elif action == "buy_full":
                pyautogui.press('f8')
            elif action == "sell_half":
                pyautogui.press('f7'); time.sleep(0.02); pyautogui.press('f7')
            elif action == "sell_full":
                pyautogui.press('f8'); time.sleep(0.02); pyautogui.press('f8')
            elif action == "cancel_all":
                pyautogui.press('f3')

            # 记录到交易日志
            state.auto_trade_log.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "action": action,
                "source": "keyboard",
            })
        except Exception as e:
            pass  # 静默失败

    def stop(self):
        """停止键盘监听"""
        if self.kb_ok and self.hotkeys_registered:
            try:
                self._kb.unhook_all()
            except: pass
            self.hotkeys_registered = False

    def get_status(self):
        """获取键盘状态"""
        return {
            "enabled": self.enabled,
            "kb_ok": self.kb_ok,
            "pkb_ok": self.pkb_ok,
            "hotkeys_registered": self.hotkeys_registered,
            "total_hotkeys": len(self.keymap),
            "recent_keys": self.key_log[-10:],
            "active_code": self.active_code,
        }

    def set_keymap(self, key_name, action, label, group="custom"):
        """自定义键位映射"""
        self.keymap[key_name] = {"action": action, "label": label, "group": group}
        return True

    def get_keymap(self):
        """获取当前键位映射"""
        groups = {}
        for key_name, cfg in self.keymap.items():
            g = cfg.get("group", "other")
            if g not in groups: groups[g] = []
            groups[g].append({"key": key_name, "action": cfg["action"], "label": cfg["label"]})
        return groups


# 全局实例
kb_manager = TradingKeyboardManager()
