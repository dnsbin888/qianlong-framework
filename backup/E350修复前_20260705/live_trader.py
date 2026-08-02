"""潜龙实盘交易引擎 — 键盘交易 + 同花顺持仓读取 + 程序化自动交易 + QMT XtQuant 通道
依赖: pip install pyautogui pygetwindow keyboard pynput
"""
import os, sys, json, time, threading, re
from datetime import datetime
from pathlib import Path

# ── QMT XtQuant 交易通道 (E33: 模拟盘复活) ──
_qmt_broker = None
_qmt_available = False
try:
    sys.path.insert(0, r"D:\quant_web")
    from qmt_broker import QMTBroker as _QMTBrokerCls, XTQUANT_AVAILABLE as _QMT_XTQ_OK
    _qmt_available = _QMT_XTQ_OK
    if _qmt_available:
        print("[Trader] QMT XtQuant 交易通道可用")
except ImportError as _e:
    _QMTBrokerCls = None
    print(f"[Trader] QMT 交易通道不可用: {_e}")

# ── 弹窗杀手：自动关闭同花顺弹窗 ──
try:
    from ths_popup_killer import start as _start_popup_killer, is_running as _popup_killer_ok
except ImportError:
    _start_popup_killer = None
    _popup_killer_ok = lambda: False

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
    "tp3_profit_pct": 0.10,            # 移动止盈3: 盈利≥10%触发 (F5-修复)
    "tp3_trail_pct": -0.03,            # 移动止盈3: 回落3%卖出最后1/3 (F5-修复)
    "tp3_sell_ratio": 0.34,            # 移动止盈3: 卖最后1/3 (F5-修复)
    "tp3_stop_loss": -0.07,            # 移动止盈3: 止损-7% (F5-修复)
    "limit_up_hold": True,             # 涨停持仓不卖(除非回落>3%)
    "limit_up_drop_sell": -0.03,       # 涨停开盘回落3%卖出
    "position_mode": "kelly",          # kelly=动态半Kelly, fixed=固定比例
    "position_pct_lv1": 0.02,          # 信号1级: 2%仓位 (观察)
    "position_pct_lv2": 0.04,          # 信号2级: 4%仓位
    "position_pct_lv3": 0.06,          # 信号3级: 6%仓位
    "position_pct_lv4": 0.08,          # 信号4级: 8%仓位
    "position_pct_lv5": 0.12,          # 信号5级: 12%仓位 (强买)
    "max_single_position_pct": 15,     # 单票最大仓位%
    "max_sector_pct": 30,              # 同行业最大仓位%
    "max_daily_trades": 5,             # 每天最多交易笔数
    "max_daily_loss": -5.0,            # 日内最大亏损%
    "max_consecutive_loss": 3,         # 连续亏损次数上限
    "max_hold_days": 5,                # 持仓超5天未盈利自动退出
    "max_cancel_rate": 0.50,           # 撤单率>50%触发风控
    "max_order_value": 1_000_000,      # 单笔最大金额(元)
    "auto_trade_enabled": False,
    "signal_min_strength": 3,
    "trading_channel": "ths",  # "ths"=同花顺联动精灵, "qmt"=QMT XtQuant
}
# 尝试从外部JSON文件加载覆盖配置，便于UI持久化和热更新
_cfg_file = r"D:\quant_framework\live_trader_config.json"
if os.path.exists(_cfg_file):
    try:
        with open(_cfg_file, 'r', encoding='utf-8') as _f:
            _data = json.load(_f)
        if isinstance(_data, dict):
            # S17: 进化结果超过24小时自动应用（非清除）
            _pe = _data.get("_pending_evolution")
            if _pe:
                try:
                    _pts = _pe.get("timestamp", "")
                    if _pts:
                        _pt = datetime.strptime(_pts[:19], "%Y-%m-%dT%H:%M:%S")
                        if (datetime.now() - _pt).total_seconds() > 86400:
                            _evo_params = _pe.get("params", {})
                            _applied = []
                            for _pk, _pv in _evo_params.items():
                                _nv = _pv.get("new_value")
                                if _pk in CONFIG and _nv is not None:
                                    _ov = CONFIG[_pk]
                                    if _ov != _nv:
                                        CONFIG[_pk] = _nv
                                        _applied.append(f"{_pk}:{_ov}->{_nv}")
                                        # 记录参数变更历史
                                        _log_param_change(_pk, _ov, _nv, "evolution")
                            if _applied:
                                print(f"[Trader] ✅ 进化自动应用: {', '.join(_applied)}")
                            _data.pop("_pending_evolution", None)
                except:
                    print(f"[Trader] ❌ 进化应用异常，跳过")
            CONFIG.update(_data)
            # E35: 小账户(总资产<5万)自动降级Kelly→fixed
            if CONFIG.get("live_total_asset", 0) < 50000 and CONFIG.get("position_mode") == "kelly":
                CONFIG["position_mode"] = "fixed"
                print(f"[Trader] 小账户(¥{CONFIG['live_total_asset']})，Kelly→fixed")
            # S06: 配置参数自动校验，防非法值
            _VALIDATORS = {
                "max_positions": lambda v: max(1, min(10, int(v))),
                "signal_min_strength": lambda v: max(1, min(5, int(v))),
                "min_cash_reserve": lambda v: max(0, min(1000000, int(v))),
                "max_daily_loss": lambda v: max(-99.0, min(0.0, float(v))),
                "position_pct_lv5": lambda v: max(0.05, min(1.0, float(v))),
                "position_pct_lv4": lambda v: max(0.05, min(1.0, float(v))),
                "position_pct_lv3": lambda v: max(0.01, min(1.0, float(v))),
                "max_hold_days": lambda v: max(1, min(30, int(v))),
                "max_daily_trades": lambda v: max(1, min(20, int(v))),
                "max_single_position_pct": lambda v: max(5, min(50, int(v))),
            }
            for _k, _fn in _VALIDATORS.items():
                if _k in CONFIG:
                    try:
                        _old = CONFIG[_k]
                        CONFIG[_k] = _fn(CONFIG[_k])
                        if CONFIG[_k] != _old:
                            print(f"[Trader] 配置修正: {_k} {_old}→{CONFIG[_k]}")
                    except: pass
            print(f"[Trader] Loaded CONFIG overrides from {_cfg_file}")
    except Exception as _e:
        print(f"[Trader] Failed to load CONFIG overrides: {_e}")

# Phase 6e: EventBus 配置订阅 — 侧栏参数即时同步到实盘 (vnpy 模式)
def _on_config_change(data: dict):
    """EventBus 配置事件回调 — 更新内存 CONFIG 即时生效。"""
    key, value = data.get("key"), data.get("value")
    if key and key in CONFIG:
        old = CONFIG.get(key)
        if old != value:
            CONFIG[key] = value
            _log_param_change(key, old, value, "eventbus")
try:
    from quant_framework.core.event_bus import EventBus
    bus = EventBus._instance
    if bus:
        bus.subscribe("config", _on_config_change)
        print("[Trader] EventBus 配置订阅已就绪 (即时生效)")
    else:
        print("[Trader] EventBus 未启动, 配置同步仅文件模式")
except ImportError:
    print("[Trader] EventBus 不可用, 配置同步仅文件模式")

# P7-1: 多策略参数独立 — 不再覆盖, 按策略名存储
STRATEGY_PARAMS = {}  # {strategy_name: {stop_loss, take_profit, hold_days, min_score}}
try:
    import os as _os6
    sp = r"D:\quant_framework\user_customizations\user_strategies.json"
    if _os6.path.exists(sp):
        strategies = json.load(open(sp, "r", encoding="utf-8")).get("strategies", [])
        for s in strategies:
            if s.get("status") in ("real", "sim", "sim_running") and s.get("type") == "builder":
                tp = s.get("take_profit", [0.05, 0.07, 0.10])
                STRATEGY_PARAMS[s["name"]] = {
                    "stop_loss": s.get("stop_loss", -0.03),
                    "take_profit": tp,
                    "hold_days": s.get("hold_days", 3),
                    "min_score": s.get("trigger", {}).get("min_score", 60),
                }
        if STRATEGY_PARAMS:
            print(f"[Trader] 加载 {len(STRATEGY_PARAMS)} 个策略参数: {list(STRATEGY_PARAMS.keys())}")
except Exception as _e:
    print(f"[Trader] 策略参数加载失败: {_e}")

# S17: 参数变更历史（用于回滚）
_PARAM_HISTORY = []
_PARAM_HISTORY_FILE = r"d:\quant_framework\data\param_history.jsonl"

def _log_param_change(param, old_val, new_val, source="manual"):
    """记录参数变更到历史"""
    import json as _jh
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "param": param, "old": old_val, "new": new_val, "source": source,
    }
    _PARAM_HISTORY.append(entry)
    try:
        import os as _jhos
        _jhos.makedirs(_jhos.path.dirname(_PARAM_HISTORY_FILE), exist_ok=True)
        with open(_PARAM_HISTORY_FILE, "a") as _jf:
            _jf.write(_jh.dumps(entry, ensure_ascii=False) + "\n")
    except: pass

def _persist_config():
    """持久化CONFIG到配置文件"""
    try:
        import json as _jc, os as _jco
        _jcf = r"D:\quant_framework\live_trader_config.json"
        with open(_jcf, 'w', encoding='utf-8') as _jcfh:
            _jc.dump(CONFIG, _jcfh, ensure_ascii=False, indent=2)
    except Exception as _je:
        print(f"[Trader] 持久化失败: {_je}")

def rollback_params(steps=1):
    """回滚前N个版本的参数"""
    applied = 0
    for entry in reversed(_PARAM_HISTORY[-steps:]):
        param = entry.get("param")
        old_val = entry.get("old")
        if param in CONFIG and old_val is not None:
            CONFIG[param] = old_val
            applied += 1
    if applied > 0:
        _persist_config()
        print(f"[Trader] 回滚{applied}项参数，共{steps}步")
    return applied

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
    """从同花顺读取当前持仓 — 使用多策略自动读取器(带超时保护)。

    优化: THS不可用时缓存结果5分钟，避免每次超时5秒浪费资源。
    """
    import time as _rtp_time
    # 缓存检查: 如果上次超时在5分钟内，直接返回跟踪文件
    if not hasattr(read_ths_positions, '_last_fail'):
        read_ths_positions._last_fail = 0
    if read_ths_positions._last_fail > 0:
        if _rtp_time.time() - read_ths_positions._last_fail < 300:
            return _load_position_tracker_fallback()
    # C15: 超时保护 — 5秒读取超时则用跟踪文件兜底
    result = [None]
    def _do_read():
        try:
            result[0] = _read_ths_positions_impl()
        except Exception as _e:
            print(f"[Trader] read_ths_positions异常: {_e}")
    t = threading.Thread(target=_do_read, daemon=True)
    t.start()
    t.join(timeout=5)  # 最多等5秒
    if t.is_alive():
        print("[Trader] ⚠️ read_ths_positions超时(5s)，返回跟踪文件兜底(缓存5min)")
        read_ths_positions._last_fail = _rtp_time.time()
        return _load_position_tracker_fallback()
    read_ths_positions._last_fail = 0  # 成功则重置缓存
    return result[0] if result[0] is not None else _load_position_tracker_fallback()


def _load_position_tracker_fallback():
    """从跟踪文件加载兜底持仓"""
    positions = []
    try:
        track_file = r"D:\quant_framework\live_positions_track.json"
        if os.path.exists(track_file):
            with open(track_file) as _tf:
                track_data = json.load(_tf)
            for code, info in track_data.items():
                qty = int(info.get("qty", 0))
                cost = float(info.get("cost_price", 0) or info.get("avg_cost", 0))
                if qty > 0 and cost > 0:
                    positions.append({
                        "symbol": code, "name": "",
                        "qty": qty, "cost_price": cost,
                        "current_price": cost, "profit_pct": 0
                    })
            if positions:
                print(f"[Trader] ✅ 跟踪文件兜底: {len(positions)}只持仓")
    except Exception as _te:
        print(f"[Trader] 跟踪文件兜底失败: {_te}")
    return positions


def _read_ths_positions_impl():
    """同花顺持仓读取核心实现"""
    # C06-P0: 优先读联动精灵同花顺导出文件(最可靠)
    xls_file = r"C:\Users\Administrator\Documents\table.xls"
    if os.path.exists(xls_file) and os.path.getsize(xls_file) > 10:
        try:
            import csv, io as _io
            with open(xls_file, 'r', encoding='gbk') as f:
                content = f.read()
            if content and len(content) > 10:
                reader = csv.DictReader(_io.StringIO(content), delimiter='\t')
                for row in reader:
                    sym = str(row.get('证券代码', '')).strip()
                    if sym:
                        state.positions.append({
                            "symbol": sym, "name": str(row.get('证券名称', '')).strip(),
                            "quantity": int(float(row.get('股票余额', '0') or 0)),
                            "cost_price": float(row.get('摊薄成本价', '0') or 0),
                            "current_price": float(row.get('最新价', '0') or 0),
                            "market_value": float(row.get('市值', '0') or 0),
                            "profit_pct": float(row.get('盈亏比例(%)', '0') or 0),
                            "profit_amt": float(row.get('摊薄盈亏', '0') or 0),
                        })
                if state.positions:
                    state.last_update = datetime.now()
                    print(f"[Trader] 联动精灵导出: {len(state.positions)}只持仓")
                    return state.positions
        except Exception as _xls_e:
            print(f"[Trader] 联动精灵导出读取失败: {_xls_e}")
    # 次选：联动精灵剪贴板（零延迟，不需THS窗口）
    try:
        from clipper import read_positions as _clip_read
        clip_pos = _clip_read(use_cache=False)
        if clip_pos:
            state.positions = clip_pos
            state.last_update = datetime.now()
            print(f"[Trader] 剪贴板同步: {len(clip_pos)}只持仓")
            return clip_pos
    except Exception as _clip_e:
        print(f"[Trader] 剪贴板读取失败: {_clip_e}")
    # 其次：THS 综合读取器
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

    # 方式0: 优先从跟踪文件恢复（C01: DLL发单后已写入，优先级最高）
    try:
        track_file = r"D:\quant_framework\live_positions_track.json"
        if os.path.exists(track_file):
            with open(track_file) as _tf:
                track_data = json.load(_tf)
            for code, info in track_data.items():
                qty = int(info.get("qty", 0))
                cost = float(info.get("cost_price", 0))
                if qty > 0 and cost > 0:
                    positions.append({
                        "symbol": code, "name": "",
                        "qty": qty, "cost_price": cost,
                        "current_price": cost, "profit_pct": 0
                    })
            if positions:
                print(f"[Trader] ✅ 跟踪文件恢复: {len(positions)}只持仓")
    except Exception as _te:
        print(f"[Trader] 跟踪文件加载失败: {_te}")

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

    # D15: 板块判断
    def _get_board(code):
        c = re.sub(r'^(sh|sz|SH|SZ|bj|BJ)','',str(code))
        if c.startswith(('688','689')): return '科创板'
        if c.startswith(('300','301')): return '创业板'
        if c.startswith(('000','001','002','003')): return '深主板'
        if c.startswith(('600','601','603','605')): return '沪主板'
        if c.startswith(('8','4')): return '北交所'
        return '其他'

    # 方式4: 补全持仓名称+实时价+盈亏
    for p in positions:
        code = re.sub(r'^(sh|sz|SH|SZ)','',p.get("symbol",""))
        p["board"] = _get_board(code)  # D15
        if not p.get("name") or p.get("name") == code:
            p["name"] = code  # 兜底用代码
        try:
            from realtime_quotes import _quote_cache
            qc = _quote_cache.get("data",{}) if _quote_cache else {}
            if code in qc:
                live = float(qc[code].get("close",0) or 0)
                if live > 0:
                    p["current_price"] = live
                    cost_price = p.get("cost_price", p.get("avg_cost", 0))
                    qty = p.get("quantity", p.get("qty", 0))
                    p["market_value"] = round(live * qty, 2)
                    if cost_price > 0:
                        p["profit_amt"] = round((live - cost_price) * qty, 2)
                        p["profit_pct"] = round((live / cost_price - 1) * 100, 2)
        except: pass
    if not positions:
        # 方式5: 模拟数据兜底（仅演示用）
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
            # 优先找交易窗口 — 覆盖多种THS版本标题
            patterns = ["网上股票交易系统", "同花顺", "网上交易", "股票交易", "THS", "交易"]
            for pat in patterns:
                windows = gw.getWindowsWithTitle(pat)
                if windows:
                    windows[0].activate()
                    time.sleep(0.1)
                    return True
            # 调试：列出所有窗口标题帮助排查
            all_wins = gw.getAllTitles()
            ths_like = [w for w in all_wins if any(k in w for k in ['交易','同花顺','股票','THS'])]
            if ths_like:
                print(f"[Trader] 可能的THS窗口: {ths_like[:5]}")
        except Exception as e:
            if "0 -" not in str(e) and "成功" not in str(e):
                print(f"[Trader] focus_ths异常: {e}")
        return False

    def send_buy_order(self, code=None, price=None, quantity=None):
        """发送买入指令 — 联动精灵 linkorder.dll + 记录持仓"""
        from link_trader import buy as _link_buy, is_available as _link_ok
        if not _link_ok():
            return {"success": False, "error": "linkorder.dll 未加载，请确认联动精灵已启动"}
        clean_code = str(code).replace('sh','').replace('sz','').replace('SH','').replace('SZ','') if code else ''
        p = float(price) if price else 0
        q = int(quantity) if quantity else 10000
        # D02: 发单前价格/时间/资金检查(减少撤单率)
        try:
            from realtime_quotes import get_price as _get_live, is_trading_time as _is_trade
            if not _is_trade():
                return {"success": False, "error": "非交易时段，不发单"}
            if p > 0:
                live = _get_live(clean_code)
                if live and live > 0:
                    deviation = abs(p / live - 1)
                    if deviation > 0.02:  # 偏离市价>2%
                        return {"success": False, "error": f"委托价{p}偏离市价{live:.2f}超2%"}
        except Exception: pass
        # 资金检查：单笔不超过总资产50%
        cost = q * p
        total_asset = CONFIG.get("live_total_asset", 0)
        if total_asset > 0 and cost > total_asset * 0.5:
            return {"success": False, "error": f"单笔{cost}超总资产{total_asset}50%"}
        # F2-修复: 实盘PreTradeChecker — 买入前执行完整风控检查
        try:
            from risk_guard import PreTradeChecker
            _pos_dict = {}
            for _pos in state.positions:
                _sym = str(_pos.get("symbol", ""))
                _pos_dict[_sym] = {
                    "qty": _pos.get("qty", _pos.get("quantity", 0)),
                    "avg_cost": _pos.get("cost_price", _pos.get("avg_cost", 0)),
                    "last_price": _pos.get("current_price", 0),
                    "industry": _pos.get("board", ""),
                }
            _cash = CONFIG.get("live_cash", 0) or (total_asset - sum(
                p.get("qty", p.get("quantity", 0)) * p.get("cost_price", p.get("avg_cost", 0))
                for p in state.positions))
            _checker = PreTradeChecker(config=CONFIG, positions=_pos_dict,
                                       cash=_cash, total_equity=total_asset)
            _ok, _reason = _checker.check_buy(clean_code, p, q, signal_level=3)
            if not _ok:
                print(f"[Trader] PreTrade {clean_code} 买入被拒: {_reason}")
                return {"success": False, "error": f"风控拒绝: {_reason}"}
        except ImportError:
            pass  # risk_guard模块不存在时静默跳过
        except Exception as _pte:
            print(f"[Trader] PreTrade check_buy异常(跳过): {_pte}")
        amount = int(q * p) if q > 0 and p > 0 else 10000  # DLL用金额(元), 非股数
        result = _link_buy(clean_code, p, amount)
        # C01: DLL下单成功后立即写入持仓，不等同花顺同步
        if result.get("success"):
            from datetime import datetime as _dt
            try:
                from link_trader import state as _state
            except:
                pass
            # 记录到 auto_trade_log
            state.auto_trade_log.append({
                "time": _dt.now().strftime("%H:%M:%S"),
                "action": "buy", "code": code,
                "reason": f"DLL下单 {q}股 @{p} = ¥{amount:,}",
                "result": "OK"
            })
            # 更新 positions
            existing = [p for p in state.positions if p.get("symbol") == code]
            if existing:
                existing[0]["qty"] = existing[0].get("qty", 0) + q
                oq=existing[0].get("qty",0);oc=existing[0].get("cost_price",p);existing[0]["cost_price"]=(oc*oq+p*q)/(oq+q) if oq+q>0 else p
            else:
                state.positions.append({
                    "symbol": code, "qty": q, "cost_price": p,
                    "current_price": p, "profit_pct": 0
                })
            # 持久化到跟踪文件（只保留6位数字A股代码，过滤虚胖）
            try:
                import json as _jj, re
                pf = r"D:\quant_framework\live_positions_track.json"
                track = {}
                for pos in state.positions:
                    code = str(pos.get("symbol", ""))
                    # 只保留纯6位数字代码（如603976），过滤sh688xxx/非标代码
                    if re.match(r'^\d{6}$', code):
                        track[code] = {"qty": pos.get("qty",0), "cost_price": pos.get("cost_price",0), "buy_date": datetime.now().strftime("%Y-%m-%d")}  # E21
                _jj.dump(track, open(pf, "w"))
            except: pass
        return result

    def send_sell_order(self, code=None, quantity=None):
        """发送卖出指令 — 联动精灵 linkorder.dll"""
        from link_trader import sell as _link_sell, is_available as _link_ok
        if not _link_ok():
            return {"success": False, "error": "linkorder.dll 未加载，请确认联动精灵已启动"}
        clean_code = str(code).replace('sh','').replace('sz','').replace('SH','').replace('SZ','') if code else ''
        # D06: T+1检查 — 今日买入不可卖出
        today = datetime.now().strftime("%Y-%m-%d")
        tracked = _load_position_tracker()
        if clean_code in tracked:
            # E21: 无buy_date默认今日（保守，防绕过T+1）
            buy_date = tracked[clean_code].get("buy_date", today)
            if buy_date == today:
                return {"success": False, "error": f"T+1锁定：{clean_code}今日买入不可卖出"}
        # D01: qty参数传给DLL — 0=全仓卖出, >0=指定数量
        q = int(quantity) if quantity else 0
        return _link_sell(clean_code, 0, q if q > 0 else 1)  # qty=1全卖,>1按指定数量

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
# QMT XtQuant 交易通道 (E33: 模拟盘复活)
# ═══════════════════════════════════════════════════════

def _to_qmt_code(symbol: str) -> str:
    """转换代码格式: 600000→600000.SH, 000001→000001.SZ"""
    s = str(symbol).strip().lower().replace("sh", "").replace("sz", "").replace(".sh", "").replace(".sz", "")
    if len(s) == 6 and s.isdigit():
        if s.startswith("6"):
            return f"{s}.SH"
        elif s.startswith(("0", "3")):
            return f"{s}.SZ"
        elif s.startswith(("4", "8")):
            return f"{s}.BJ"
    return symbol


class QMTTrader:
    """QMT XtQuant 交易通道 — 替代键盘交易，直连 QMT xttrader。

    接口与 KeyboardTrader 对齐，作为 drop-in replacement。
    """

    def __init__(self):
        self._broker = None
        self._connected = False
        self._last_connect_attempt = 0

    def _ensure_connected(self) -> bool:
        """确保 QMT 连接状态，失败降级返回 False。"""
        if self._connected and self._broker and self._broker.connected:
            return True

        # 限频：10秒内不重复尝试
        if time.time() - self._last_connect_attempt < 10:
            return False

        self._last_connect_attempt = time.time()
        if not _qmt_available or _QMTBrokerCls is None:
            print("[QMTTrader] QMT 不可用，请先启动模拟版 mini QMT 并登录")
            return False

        try:
            path = CONFIG.get("qmt_path", r"D:\国金QMT交易端模拟\userdata_mini")
            acc = CONFIG.get("qmt_account", "66638720")
            sid = int(CONFIG.get("qmt_session_id", 9999))
            self._broker = _QMTBrokerCls(userdata_path=path, session_id=sid, account_id=acc)
            self._broker.connect()
            if self._broker.connected:
                self._connected = True
                print(f"[QMTTrader] ✅ 已连接 QMT | account={acc}")
                return True
            else:
                print(f"[QMTTrader] ❌ QMT 连接失败")
                return False
        except Exception as e:
            print(f"[QMTTrader] 连接异常: {e}")
            return False

    def send_buy_order(self, code=None, price=None, quantity=None):
        """QMT 买入 — 与 KeyboardTrader.send_buy_order 接口对齐。"""
        if not self._ensure_connected():
            return {"success": False, "error": "QMT 未连接"}

        qmt_code = _to_qmt_code(code)
        qty = int(quantity) if quantity else 100

        # 数量必须是100的整数倍
        if qty % 100 != 0:
            qty = max(100, (qty // 100) * 100)

        try:
            from realtime_quotes import get_price, is_trading_time
            if not is_trading_time():
                return {"success": False, "error": "非交易时段"}
        except Exception:
            pass

        try:
            order_id = self._broker.buy(qmt_code, qty, float(price) if price else 0.0)
            if order_id > 0:
                print(f"[QMTTrader] ✅ 买入 {qmt_code} {qty}股 @{price} order_id={order_id}")
                return {"success": True, "order_id": order_id, "code": qmt_code,
                        "quantity": qty, "price": price, "channel": "qmt"}
            else:
                return {"success": False, "error": f"下单失败 order_id={order_id}"}
        except Exception as e:
            print(f"[QMTTrader] 买入异常: {e}")
            return {"success": False, "error": str(e)}

    def send_sell_order(self, code=None, quantity=None):
        """QMT 卖出 — 与 KeyboardTrader.send_sell_order 接口对齐。"""
        if not self._ensure_connected():
            return {"success": False, "error": "QMT 未连接"}

        qmt_code = _to_qmt_code(code)
        qty = int(quantity) if quantity else 0

        # qty=0 表示全部卖出，但 QMT 需要明确数量
        # 先从持仓中查
        if qty <= 0:
            try:
                positions = self.get_positions()
                for pos in positions:
                    if pos.get("symbol") == code or pos.get("stock_code") == qmt_code:
                        qty = int(pos.get("can_use_volume", pos.get("volume", 0)))
                        break
            except Exception:
                pass
        if qty <= 0:
            return {"success": False, "error": f"无法确定卖出数量: {code}"}

        if qty % 100 != 0:
            qty = max(100, (qty // 100) * 100)

        try:
            order_id = self._broker.sell(qmt_code, qty, 0.0)
            if order_id > 0:
                print(f"[QMTTrader] ✅ 卖出 {qmt_code} {qty}股 order_id={order_id}")
                return {"success": True, "order_id": order_id, "code": qmt_code,
                        "quantity": qty, "channel": "qmt"}
            else:
                return {"success": False, "error": f"下单失败 order_id={order_id}"}
        except Exception as e:
            print(f"[QMTTrader] 卖出异常: {e}")
            return {"success": False, "error": str(e)}

    def cancel_all_orders(self):
        """QMT 撤单 — 撤所有可撤委托。"""
        if not self._ensure_connected():
            return {"success": False, "error": "QMT 未连接"}
        try:
            orders = self._broker.query_stock_orders(cancelable_only=True)
            cancelled = 0
            for o in (orders or []):
                oid = o.get("order_id", 0)
                if oid > 0:
                    ret = self._broker.cancel_order(oid)
                    if ret == 0:
                        cancelled += 1
            return {"success": True, "cancelled": cancelled, "channel": "qmt"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_positions(self) -> list[dict]:
        """QMT 查询持仓。"""
        if not self._ensure_connected():
            return []
        try:
            return self._broker.query_stock_positions()
        except Exception as e:
            print(f"[QMTTrader] 查询持仓异常: {e}")
            return []

    def get_asset(self) -> dict:
        """QMT 查询资产。"""
        if not self._ensure_connected():
            return {}
        try:
            return self._broker.query_asset()
        except Exception as e:
            print(f"[QMTTrader] 查询资产异常: {e}")
            return {}

    def disconnect(self):
        """断开 QMT 连接。"""
        if self._broker:
            try:
                self._broker.disconnect()
            except Exception:
                pass
        self._connected = False
        self._broker = None

    # ── 别名（兼容 S32 测试脚本 + 外部调用） ──

    def connect(self) -> bool:
        """公开连接方法（S32 测试接口）。"""
        return self._ensure_connected()

    def query_asset(self) -> dict:
        """查询资产（S32 测试接口，get_asset 的别名）。"""
        return self.get_asset()

    def query_stock_positions(self) -> list[dict]:
        """查询持仓（S32 测试接口，get_positions 的别名）。"""
        return self.get_positions()


qmt_trader = QMTTrader()


# ═══════════════════════════════════════════════════════
# 程序化自动交易规则引擎
# ═══════════════════════════════════════════════════════
# E246: 持仓跟踪 — 联动精灵无持仓查询API，自行记录
_TRACKER_FILE = r"D:\quant_framework\live_positions_track.json"

def _load_position_tracker():
    """加载持仓跟踪文件"""
    if os.path.exists(_TRACKER_FILE):
        try:
            with open(_TRACKER_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {}

def _save_position_tracker(positions):
    """保存持仓跟踪文件"""
    try:
        # C11: 只保存6位数字A股代码
        clean = {str(k): v for k, v in positions.items() if re.match(r'^\d{6}$', str(k))}
        with open(_TRACKER_FILE, "w") as f:
            json.dump(clean, f, ensure_ascii=False)
    except: pass

def _update_position_tracker(action, price=0):
    """根据成交更新持仓跟踪"""
    try:
        pos = _load_position_tracker()
        sym = action.get("symbol", "")
        # C11: 只保留6位数字A股代码, 去sh/sz前缀
        sym = re.sub(r'^(sh|sz|SH|SZ)', '', sym)
        if not re.match(r'^\d{6}$', sym):
            print(f"[Trader] ⚠️ 跳过非标代码写入跟踪文件: {action.get('symbol','')}")
            return
        act_type = action.get("type", "")
        qty = action.get("qty", 0)
        if act_type == "buy":
            # 买入：新增或累加持仓
            old = pos.get(sym, {"qty": 0, "avg_cost": 0})
            pos_pct = action.get("pos_pct", 0.2)
            # qty 可能是仓位比例字符串，暂时跳过
            if isinstance(qty, (int, float)) and qty > 0:
                tq = old["qty"] + qty
                old["avg_cost"] = (old["avg_cost"] * old["qty"] + price * qty) / tq if tq > 0 else price
                old["qty"] = tq
                old["buy_date"] = datetime.now().strftime("%Y-%m-%d")
                pos[sym] = old
        elif act_type == "sell":
            old = pos.get(sym, {"qty": 0, "avg_cost": 0})
            if isinstance(qty, (int, float)) and qty > 0:
                old["qty"] = max(0, old["qty"] - qty)
                if old["qty"] <= 0:
                    pos.pop(sym, None)
                else:
                    pos[sym] = old
        _save_position_tracker(pos)
    except Exception as e:
        print(f"[Trader] 持仓跟踪更新失败: {e}")

# ═══════════════════════════════════════════════════════
class AutoTradeEngine:
    def __init__(self):
        self.running = False
        self.thread = None
        self.daily_trade_count = 0
        self.consecutive_losses = 0
        # R1-3/4/5: RuleEngine 集成 — 替代内联止损/止盈/熔断
        try:
            from quant_framework.execution.rules.engine import RuleEngine
            self._rule_engine = RuleEngine()
        except Exception:
            self._rule_engine = None
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
            # C04: 跳过虚报(零持仓/零盈亏) — 防止对假数据触发止损
            if qty <= 0 or pos.get("market_value", 0) <= 0: continue

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

            # ── 移动止盈3 (F5-修复): 峰值曾≥10%, 回落3%卖最后1/3, 止损-7% ──
            tp3_profit = CONFIG["tp3_profit_pct"] * 100
            tp3_trail = abs(CONFIG["tp3_trail_pct"]) * 100
            tp3_sell = CONFIG["tp3_sell_ratio"]
            tp3_stop = CONFIG["tp3_stop_loss"] * 100
            if peak >= tp3_profit and pnl_pct <= peak - tp3_trail:
                sell_qty = max(100, int(qty * tp3_sell) // 100 * 100)
                actions.append({"type": "sell", "symbol": sym, "reason": f"移动止盈3(峰值{peak:.1f}%回落≥{tp3_trail:.0f}%)", "qty": sell_qty})
                if pnl_pct <= tp3_stop:
                    actions.append({"type": "sell", "symbol": sym, "reason": f"止盈3止损({pnl_pct:.1f}%)", "qty": qty})
                if sym in self.position_peaks: del self.position_peaks[sym]
                continue

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

            # ── R1-3: 基本止损 → RuleEngine统一 ──
            # FIX: 盈亏接近0（±1%）→ 虚报数据，跳过
            if abs(pnl_pct) < 1.0 and qty > 0:
                continue
            if self._rule_engine:
                stop_result = self._rule_engine.check_stop_loss({
                    "symbol": sym, "avg_cost": pos.get("cost_price", 0),
                    "last_price": pos.get("current_price", pos.get("market_value", 0) / max(qty, 1)),
                    "qty": qty,
                })
                if stop_result:
                    actions.append({"type": "sell", "symbol": sym,
                                    "reason": stop_result["reason"],
                                    "qty": stop_result["qty"]})
                    if sym in self.position_peaks: del self.position_peaks[sym]

        # ── 信号买入 ──
        if signal_data and CONFIG["auto_trade_enabled"] and self.daily_trade_count < CONFIG["max_daily_trades"]:
            for sig in (signal_data or [])[:10]:
                bs = sig.get("buy_signal", 0)
                if bs < CONFIG["signal_min_strength"]: continue
                sym = sig.get("symbol", "")
                if any(p["symbol"] == sym for p in positions): continue
                # E266: 黑名单检查
                # B03: 买入限频 — 30分钟内不重复
                global _BUY_COOLDOWN, _BUY_COOLDOWN_LOCK
                with _BUY_COOLDOWN_LOCK:  # E01: 防竞态
                    if sym in _BUY_COOLDOWN:
                        if time.time() - _BUY_COOLDOWN[sym] < 1800:
                            print(f"[AutoTrade] 跳过{sym}: 30分钟冷却中")
                            continue
                        else: del _BUY_COOLDOWN[sym]
                # E266: 黑名单检查
                try:
                    from blacklist import is_blocked
                    if is_blocked(sym): continue
                except: pass
                if CONFIG.get("position_mode", "kelly") == "kelly":
                    try:
                        from kelly import KellyCriterion
                        _kc = KellyCriterion.from_paper_account()
                        pos_pct = _kc.get_position_pct(bs)
                    except Exception:
                        pos_pct = CONFIG.get(f"position_pct_lv{bs}", 0.20)
                else:
                    if bs >= 5: pos_pct = CONFIG["position_pct_lv5"]
                    elif bs >= 4: pos_pct = CONFIG["position_pct_lv4"]
                    elif bs >= 3: pos_pct = CONFIG["position_pct_lv3"]
                    else: pos_pct = CONFIG.get("position_pct_lv2", 0.20)
                # R1-5: RuleEngine 熔断检查 (补充现有连续亏损逻辑)
                if self._rule_engine:
                    _cb_triggered, _cb_reason = self._rule_engine.check_circuit_breaker({
                        "consecutive_losses": self.consecutive_losses,
                        "daily_pnl": state.risk.get("daily_loss", 0),
                        "total_asset": CONFIG.get("live_total_asset", 0),
                    })
                    if _cb_triggered:
                        print(f"[AutoTrade] RuleEngine熔断: {_cb_reason}")
                        break

                # E34-3: 连续亏损自动降仓 + 停买
                _max_cl = CONFIG.get("max_consecutive_loss", 3)
                if self.consecutive_losses >= _max_cl:
                    print(f"[AutoTrade] 连续亏损{self.consecutive_losses}笔≥{_max_cl}，停止买入")
                    break  # 跳出信号循环，当日不再买入
                elif self.consecutive_losses >= _max_cl - 1:
                    _extra = self.consecutive_losses - _max_cl + 2
                    _factor = 0.5 ** _extra
                    pos_pct *= _factor
                    print(f"[AutoTrade] 连续亏损{self.consecutive_losses}笔, 仓位×{_factor:.2f} = {pos_pct*100:.1f}%")
                price = max(sig.get("close", 0), 1)
                # E32-1: 实盘买入前 PreTradeChecker（行业集中度+涨跌停+仓位比例）
                try:
                    from risk_guard import PreTradeChecker
                    _pos_dict = {}
                    for _p in state.positions:
                        _psym = str(_p.get("symbol", ""))
                        _pos_dict[_psym] = {
                            "qty": _p.get("qty", _p.get("quantity", 0)),
                            "avg_cost": _p.get("cost_price", _p.get("avg_cost", 0)),
                            "last_price": _p.get("current_price", _p.get("market_value", 0) / max(_p.get("qty", 1), 1)),
                            "industry": _p.get("board", _p.get("industry", "")),
                        }
                    _total_eq = CONFIG.get("live_total_asset", 0) or sum(
                        pp.get("qty", pp.get("quantity", 0)) * pp.get("cost_price", pp.get("avg_cost", 0))
                        for pp in state.positions) + (_cfg_cash or CONFIG.get("live_cash", 0))
                    _checker = PreTradeChecker(
                        config=CONFIG, positions=_pos_dict,
                        cash=_cfg_cash, total_equity=_total_eq
                    )
                    _industry = sig.get("industry", "") or ""
                    _ok, _reason = _checker.check_buy(sym, price, 100, _industry, bs)
                    if not _ok:
                        print(f"[AutoTrade] PreTrade拒绝 {sym}: {_reason}")
                        continue
                except Exception as _pte:
                    print(f"[AutoTrade] PreTradeChecker异常: {_pte}")
                # C07: 从配置读取可用资金（不再硬编码80万）
                _cfg_cash = CONFIG.get("live_cash", 0) or 0
                # 计算已占用的持仓市值
                _pos_value = sum(int(p.get("qty",0)) * float(p.get("cost_price",0)) for p in getattr(state,"positions",[]))
                _total = CONFIG.get("live_total_asset", 0) or 0
                _avail = max(0, _total - _pos_value)
                _avail = min(_avail, _cfg_cash)  # 取两者的较小值
                # E29-4: 小资金管理 — 资金校验
                _min_cash = CONFIG.get("min_cash_reserve", 500)
                _safe_ratio = CONFIG.get("cash_safe_ratio", 0.7)
                if _avail < _min_cash:
                    print(f"[AutoTrade] 跳过{sym}: 可用资金¥{_avail:.0f}<最低¥{_min_cash}")
                    continue
                _max_order = _avail * _safe_ratio
                if price * 100 > _max_order:
                    print(f"[AutoTrade] 跳过{sym}: 1手¥{price*100:.0f}>安全上限¥{_max_order:.0f} (可用¥{_avail:.0f})")
                    continue
                qty = max(100, int(min(_avail * pos_pct, _max_order) / price / 100) * 100)
                actions.append({"type": "buy", "symbol": sym, "reason": f"信号{bs}级({int(pos_pct*100)}%仓)", "qty": qty, "pos_pct": pos_pct, "price": price})

        return actions

    def execute_actions(self, actions):
        """执行自动交易动作，成功后更新持仓跟踪。
        QMT 通道: trading_channel="qmt" 时走 QMT xttrader，否则走联动精灵 DLL。
        """
        _use_qmt = CONFIG.get("trading_channel") == "qmt" and _qmt_available
        if _use_qmt:
            print("[AutoTrade] 使用 QMT 交易通道")
        results = []
        for act in actions:
            if self.daily_trade_count >= CONFIG["max_daily_trades"]:
                break
            if act["type"] == "sell":
                if _use_qmt:
                    r = qmt_trader.send_sell_order(code=act["symbol"], quantity=act.get("qty"))
                else:
                    r = trader.send_sell_order(code=act["symbol"], quantity=act.get("qty"))
            elif act["type"] == "buy":
                if _use_qmt:
                    r = qmt_trader.send_buy_order(code=act["symbol"], price=act.get("price", 0), quantity=act.get("qty"))
                else:
                    r = trader.send_buy_order(code=act["symbol"], price=act.get("price", 0), quantity=act.get("qty"))
            else:
                r = {"success": False, "error": "unknown action"}
            results.append({**act, "result": r})
            if r.get("success"):
                self.daily_trade_count += 1
                sym=act["symbol"];qty=act.get("qty",0)
                if act["type"]=="buy":
                    # C12: DLL买入标记待确认, 不立即写跟踪文件
                    state.positions.append({"symbol":sym,"name":act.get("name",sym),
                        "quantity":qty,"avg_cost":act.get("price",0),"current_price":act.get("price",0),
                        "cost_price":act.get("price",0),"market_value":act.get("price",0)*qty,
                        "profit_pct":0,"profit_amt":0,"buy_date":datetime.now().strftime("%Y-%m-%d"),
                        "_pending":True})  # C12: 待确认标记
                elif act["type"]=="sell":
                    old=next((p for p in state.positions if p.get("symbol")==sym),None)
                    if old:
                        old["quantity"]=max(0,old["quantity"]-qty)
                        if old["quantity"]<=0:state.positions.remove(old)
                    _update_position_tracker(act, r.get("price", 0))  # 卖出即时写入
            # B03: 买入后进入30分钟冷却(防止重复下单)
            if act["type"] == "buy":
                global _BUY_COOLDOWN, _BUY_COOLDOWN_LOCK
                with _BUY_COOLDOWN_LOCK:  # E01: 防竞态
                    _BUY_COOLDOWN[act["symbol"]] = time.time()
                # A04: 成交确认
                sym = act.get("symbol","")
                def _confirm():
                    time.sleep(30)
                    read_ths_positions()
                    # C12: 成交确认后移除_pending并写入跟踪文件; 未成交则清除
                    ths_match = any(p.get("symbol")==sym for p in read_ths_positions())
                    if ths_match:
                        print(f"[AutoTrade] ✅ {sym} 成交确认, 写入跟踪文件")
                        _update_position_tracker({"symbol":sym,"type":"buy","qty":act.get("qty",0)}, act.get("price",0))
                        for p in state.positions:
                            if p.get("symbol")==sym: p.pop("_pending",None)
                    else:
                        print(f"[AutoTrade] ⚠️ {sym} 委托未成交, 清除待确认持仓")
                        state.auto_trade_log.append({"time":datetime.now().strftime("%H:%M:%S"),"action":"unconfirmed","code":sym,"reason":"委托30s后未成交"})
                        state.positions = [p for p in state.positions if not (p.get("symbol")==sym and p.get("_pending"))]
                # E18: 线程池限制最多10个确认线程
                _CONFIRM_POOL = [t for t in getattr(auto_engine, '_confirm_pool', []) if t.is_alive()]
                if len(_CONFIRM_POOL) < 10:
                    t = threading.Thread(target=_confirm, daemon=True)
                    t.start(); _CONFIRM_POOL.append(t)
                    auto_engine._confirm_pool = _CONFIRM_POOL
                else:
                    print(f"[AutoTrade] 确认线程池满，跳过{sym}确认")
            # 卖出后更新连续亏损追踪
            if act["type"] == "sell":
                pnl = act.get("profit_pct", 0)
                if pnl < 0: self.consecutive_losses += 1
                else: self.consecutive_losses = 0
            # 钉钉推送（买卖都推送）
            try:
                from dingtalk_alerts import trade_signal
                trade_signal(act.get("symbol",""), "", act.get("type",""),
                             str(act.get("reason","")), r.get("price", 0))
            except Exception: pass
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
_BUY_COOLDOWN = {}  # B03: 买入限频 {symbol: timestamp}
_BUY_COOLDOWN_LOCK = threading.Lock()  # E01: 防竞态
_STATE_LOCK = threading.Lock()          # E16: state并发安全
_CONFIG_LOCK = threading.Lock()         # E16: CONFIG并发安全

def start_auto_sync():
    """启动后台自动同步+交易线程 (E246: 联动精灵DLL + 保活)"""
    global _sync_thread_started
    if _sync_thread_started:
        return
    _sync_thread_started = True

    # ── 启动弹窗杀手：自动关闭同花顺弹窗 ──
    if _start_popup_killer and not _popup_killer_ok():
        try:
            _start_popup_killer(interval=2.0)
            print("[Trader] 弹窗杀手已启动（2秒扫描间隔）")
        except Exception as _pke:
            print(f"[Trader] 弹窗杀手启动失败: {_pke}")

    # E248: 使用已有的持仓跟踪数据，不在启动时同步(避免剪贴板死锁)
    tracked = _load_position_tracker()
    if tracked:
        print(f"[Trader] 持仓跟踪已加载: {len(tracked)}只 (启动时不同步，30秒后自动更新)")

    # S12: 价格异常检测
    def _validate_price(code, price, pre_close=0):
        if price <= 0: return False, "价格<=0"
        if price > 10000: return False, f"价格{price}过高"
        if pre_close > 0 and abs(price / pre_close - 1) > 0.5:
            return False, f"涨跌幅异常({abs(price/pre_close-1):.0%})"
        return True, "ok"

    # 检查交易能力：联动精灵 linkorder.dll
    def _can_trade():
        try:
            from link_trader import is_available as _link_ok
            return _link_ok()
        except Exception as _e:
            print(f"[Trader] _can_trade检测失败: {_e}")
        return False

    def _sync_loop_once():
        """单次同步+交易检查 — 被保活循环调用"""
        now = datetime.now()  # P1-4修复: 函数内now变量定义
        # 非交易时段跳过（盘后不操作，防休市弹窗）
        try:
            from realtime_quotes import is_trading_time
            if not is_trading_time():
                time.sleep(60)
                return
        except Exception as _e:
            print(f"[Trader] 交易时段检测失败，继续运行: {_e}")
        # E265: 热更新配置（每轮重读JSON，修改参数无需重启）
        global CONFIG, _CONFIG_LOCK
        _cfg_file = r"D:\quant_framework\live_trader_config.json"
        if os.path.exists(_cfg_file):
            try:
                with open(_cfg_file, 'r', encoding='utf-8') as _f:
                    _new_cfg = json.load(_f)
                with _CONFIG_LOCK:  # E16
                    CONFIG.update(_new_cfg)
            except Exception as _e:
                print(f"[Trader] 配置热更新失败: {_e}")
        read_ths_positions()
        # FIX: 持仓跟踪兜底，连续3次空仓确认后才跳过（防旧数据虚报）
        if not state.positions:
            state.empty_cycles = getattr(state, 'empty_cycles', 0) + 1
            # P0修复: 不再清空跟踪文件。空仓可能是THS导出中断，不销毁历史数据
            tracked = _load_position_tracker()
            if tracked:
                fb = []
                for sym, pos in tracked.items():
                    fb.append({"symbol": sym, "quantity": pos.get("qty", 0),
                               "cost_price": pos.get("avg_cost", 0),
                               "market_value": 0, "profit_pct": 0, "profit_amt": 0})
                state.positions = fb
                print(f"[AutoTrade] THS不可用，持仓跟踪兜底: {len(fb)}只 (连续{state.empty_cycles}次)")
            else:
                if state.empty_cycles >= 3:
                    print(f"[AutoTrade] ⚠️ 连续{state.empty_cycles}次空仓 + 跟踪文件为空，请检查THS/联动精灵")
        else:
            state.empty_cycles = 0  # 有持仓了，重置计数器
        # E205: 更新日亏损(基于持仓市值变化)
        try:
            total_cost = sum(p.get("cost_price", 0) * p.get("quantity", 0)
                             for p in state.positions)
            if total_cost <= 0 or not state.positions:
                state.risk["daily_loss"] = 0  # 空仓 → 清零，解锁熔断
            else:
                total_market = sum(p.get("market_value", 0) for p in state.positions)
                state.risk["daily_loss"] = round(
                    (total_market - total_cost) / total_cost * 100, 2)
        except Exception as _e:
            print(f"[Trader] 日亏损计算失败: {_e}")
            state.risk["daily_loss"] = 0
        # P7-2: 实盘权益记录 — 用配置值+当日已实现盈亏
        try:
            eq_file = r"D:\quant_framework\live_equity_log.json"
            eq_log = {}
            if os.path.exists(eq_file):
                eq_log = json.load(open(eq_file, "r", encoding="utf-8"))
            today = now.strftime("%Y-%m-%d")
            if today not in {e[0] for e in eq_log.get("log", [])}:
                # 基准权益 = 配置值 (非QMT全账户)
                total_eq = CONFIG.get("live_total_asset", 0)
                if not total_eq or total_eq <= 0:
                    total_eq = CONFIG.get("live_cash", 0)
                if total_eq > 0:
                    eq_log.setdefault("log", []).append([today, round(float(total_eq), 2)])
                    eq_log["updated"] = now.isoformat()
                    json.dump(eq_log, open(eq_file, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception: pass

        # 自动交易执行 (E246: 联动精灵DLL 或 pyautogui)
        if CONFIG["auto_trade_enabled"] and _can_trade():
            signals = []
            # Plan I: 统一信号源 — 策略构建器驱动
            try:
                sys.path.insert(0, r"D:\quant_web")
                from data_loader import load_stock_data_from_cache as _lds
                sd = _lds()
                if not sd:
                    import pickle, gzip, os as _os5
                    sp = r"D:\quant_web\stock_data.pkl.gz"
                    if not _os5.path.exists(sp): sp = r"D:\quant_web\stock_data.pkl"
                    sd = pickle.load(gzip.open(sp, "rb")) if sp.endswith(".gz") else pickle.load(open(sp, "rb"))
                if sd:
                    sys.path.insert(0, r"D:\quant_framework")
                    from strategy_engine import generate_for_live
                    raw = generate_for_live(sd, top_k=15)
                    for r in raw:
                        signals.append({
                            'symbol': r.get('symbol', ''),
                            'name': r.get('name', ''),
                            'buy_signal': r.get('buy_signal', 0),
                            'close': r.get('close', 0),
                            'score': r.get('score', 0),
                            'change_pct': 0,
                            'vol_ratio': 1,
                            'industry': '',
                            'power_score': r.get('score', 0),
                        })
                    if signals: print(f"[AutoTrade] 策略信号: {len(signals)}只 (source=strategy_engine)")
            except Exception as _e:
                print(f"[AutoTrade] 策略信号失败: {_e}, 回退老源")
                try:
                    import app as _app
                    cache = getattr(_app, '_FACTOR_CACHE', None)
                    if cache and getattr(_app, '_CACHE_READY', False):
                        for s in cache[:200]:
                            sym = getattr(s, 'symbol', '')
                            if not sym: continue
                            signals.append({
                                'symbol': sym, 'name': getattr(s, 'name', '') or '',
                                'buy_signal': getattr(s, 'buy_signal', 0) or 0,
                                'close': getattr(s, 'close', 0) or 0,
                                'change_pct': getattr(s, 'change_pct', 0) or 0,
                                'vol_ratio': getattr(s, 'vol_ratio', 1) or 1,
                                'industry': getattr(s, 'industry', '') or '',
                                'power_score': getattr(s, 'power_score', 0) or 0,
                            })
                        if signals: print(f"[AutoTrade] 老信号兜底: {len(signals)}只")
                except Exception: pass
            actions = auto_engine.check_rules(signals[:20] if signals else None)
            # A2: 熔断自动恢复 — 新的一天或日亏恢复到-3%以内
            state.circuit_breaker = getattr(state, 'circuit_breaker', False)
            if state.circuit_breaker and getattr(state, '_cb_date', '') != now.strftime("%Y-%m-%d"):
                state.circuit_breaker = False  # 新的一天，自动恢复
                print("[AutoTrade] 新的一天，熔断自动解除")
            elif state.circuit_breaker and state.risk.get("daily_loss", 0) > -3.0:
                state.circuit_breaker = False  # 日亏恢复到-3%以内
                try:
                    from dingtalk_alerts import send_alert
                    send_alert("✅ 熔断解除", f"日亏损已恢复至{state.risk['daily_loss']}%，自动恢复买入", "info")
                except: pass
            # 熔断检查: 日亏损超限 → 只停买入，止损照常执行
            # CONFIG用小数(-0.05=-5%), daily_loss用百分比(-5.0), 统一乘100
            if state.risk.get("daily_loss", 0) <= CONFIG["max_daily_loss"]:
                state.circuit_breaker = True
                state._cb_date = now.strftime("%Y-%m-%d")
                print(f"[AutoTrade] Circuit breaker: daily_loss={state.risk['daily_loss']}% — 停止买入,止损继续")
                try:
                    from dingtalk_alerts import send_alert
                    send_alert("⚠️ 日亏熔断", f"日亏损{state.risk['daily_loss']}%触发熔断，已停止买入，止损继续执行", "warning")
                except: pass
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

    def _sync_loop():
        """E246: 保活循环 — 任何异常都不退出，10秒后重试"""
        time.sleep(10)  # D03: 首次休眠10秒(从60秒缩短)，加速冷启动
        while True:
            try:
                _sync_loop_once()
            except Exception as e:
                print(f"[AutoTrade] 循环异常，10秒后重试: {e}")
                time.sleep(10)
            time.sleep(10)  # 每10秒同步同花顺持仓

    t = threading.Thread(target=_sync_loop, daemon=True)
    t.start()
    print("[Trader] Auto-sync + Auto-trade started (30s interval, link_trader enabled)")


# ═══════════════════════════════════════════════════════
# 便捷接口
# ═══════════════════════════════════════════════════════
def get_trading_status():
    """获取完整交易状态"""
    _use_qmt = CONFIG.get("trading_channel") == "qmt" and _qmt_available
    # QMT 通道: 合并 QMT 持仓和资产
    _qmt_positions = []
    _qmt_asset = {}
    if _use_qmt:
        try:
            _qmt_positions = qmt_trader.get_positions()
            _qmt_asset = qmt_trader.get_asset()
        except Exception:
            pass
    # 计算实际单票最大仓位 (持仓市值/总资产)
    _max_single_actual = 0
    _max_single_sym = ""
    _all_positions = _qmt_positions if _qmt_positions else state.positions
    _total_eq = _qmt_asset.get("total_asset", 0) if _qmt_asset else sum(
        p.get("market_value", 0) for p in _all_positions)
    if _total_eq > 0:
        for _p in _all_positions:
            _mv = _p.get("market_value", 0)
            _pct = round(_mv / _total_eq * 100, 1)
            if _pct > _max_single_actual:
                _max_single_actual = _pct
                _max_single_sym = _p.get("symbol", "")
    # 连续亏损次数 (从自动交易引擎)
    _cons_loss = getattr(auto_engine, 'consecutive_losses', 0)
    # 合并到 risk 对象
    state.risk["max_single_pct"] = _max_single_actual
    state.risk["max_single_sym"] = _max_single_sym
    state.risk["consecutive_losses"] = _cons_loss

    return {
        "positions": _all_positions,
        "orders": state.orders[-20:],
        "fills": state.fills[-20:],
        "pnl": state.pnl,
        "risk": state.risk,
        "auto_trade_enabled": CONFIG["auto_trade_enabled"],
        "auto_trade_log": state.auto_trade_log[-30:],
        "hotkeys_registered": trader.hotkeys_registered,
        "keyboard_enabled": PYAUTOGUI_OK,
        "last_update": state.last_update.strftime("%H:%M:%S") if state.last_update else None,
        "trading_channel": "qmt" if _use_qmt else "ths",
        "qmt_available": _qmt_available,
        "qmt_connected": qmt_trader._connected if _use_qmt else False,
        "qmt_asset": _qmt_asset,
        "config": {
            "max_single_pct": CONFIG["max_single_position_pct"],
            "max_daily_loss": CONFIG["max_daily_loss"],
            "signal_min_strength": CONFIG["signal_min_strength"],
            "trading_channel": CONFIG.get("trading_channel", "ths"),
        }
    }

def execute_trade(action, code, price=None, quantity=None):
    """执行交易 — 根据 trading_channel 自动路由。"""
    _use_qmt = CONFIG.get("trading_channel") == "qmt" and _qmt_available
    if _use_qmt:
        if action == "buy":
            return qmt_trader.send_buy_order(code, price, quantity)
        elif action == "sell":
            return qmt_trader.send_sell_order(code, quantity)
        elif action == "cancel":
            return qmt_trader.cancel_all_orders()
    else:
        if action == "buy":
            return trader.send_buy_order(code, price, quantity)
        elif action == "sell":
            return trader.send_sell_order(code, quantity)
        elif action == "cancel":
            return trader.cancel_all_orders()
    return {"success": False, "error": "unknown action"}

def toggle_auto_trade(enabled=None):
    """开关自动交易"""
    global _CONFIG_LOCK
    with _CONFIG_LOCK:  # E16
        if enabled is not None:
            CONFIG["auto_trade_enabled"] = bool(enabled)
        else:
            CONFIG["auto_trade_enabled"] = not CONFIG["auto_trade_enabled"]
        if CONFIG["auto_trade_enabled"]:
            auto_engine.start()
        else:
            auto_engine.stop()
    # 持久化到配置文件，避免重启后丢失
    try:
        import json as _json, os as _os
        _cfg_file = r"D:\quant_framework\live_trader_config.json"
        with open(_cfg_file, 'w', encoding='utf-8') as _f:
            _json.dump(CONFIG, _f, ensure_ascii=False, indent=2)
    except Exception as _e:
        print(f"[Trader] Failed to persist auto_trade config: {_e}")
    return {"auto_trade_enabled": CONFIG["auto_trade_enabled"]}
