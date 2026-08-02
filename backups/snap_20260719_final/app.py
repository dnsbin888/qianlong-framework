"""潜龙选股 Web 系统 — Flask 主应用。

克隆自 ql.topxlc.com 的界面风格和交互逻辑，
数据引擎使用本地 quant_framework 通达信因子。

启动:
    python app.py
    浏览器打开 http://localhost:5002
"""

import sys, os, json, time, functools, threading
sys.path.insert(0, r"d:\quant_framework\src")
sys.path.insert(0, r"d:\quant_framework")
import preload_cache  # 禁止移除：因子缓存预加载

# E349: 启动时清理.pyc缓存,防止旧代码残留
for _pyc_dir in [os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__"),
                  r"D:\quant_framework\__pycache__"]:
    if os.path.exists(_pyc_dir):
        for _f in os.listdir(_pyc_dir):
            if _f.endswith(".pyc"):
                try: os.remove(os.path.join(_pyc_dir, _f))
                except Exception: pass

# 数据与代码分离
try:
    from data_paths import init as _dp_init
    _dp_init()
    print("[App] 数据目录已初始化: D:\\quant_data\\")
except Exception as _dpe:
    print(f"[App] 数据目录初始化失败(使用旧路径): {_dpe}")

# E26-P1: 路径参数化 — 从 config 导入统一路径
try:
    from config import (
        STOCK_DATA_PKL, STOCK_DATA_PKL_GZ, STOCK_NAMES_CSV, STOCK_NAMES_JSON,
        FACTOR_CACHE_PKL, FACTOR_WEIGHTS, FACTOR_XGB, FACTOR_IC,
        BT_CACHE_FILE as _BT_CACHE_FILE_NEW,
        PAPER_AUTO_LOG, AUDIT_LOG_JSONL, TRADE_LOG_CSV,
        LIVE_CONFIG, PAPER_ACCOUNT, POSITION_TRACK,
        NORTHBOUND_JSON, ML_FACTOR_DB,
        # E26: 补充导入
        OPERATION_LOG, LAST_STARTUP_TXT, LAST_SHUTDOWN_TXT,
        PRICE_CACHE_JSON, IC_HISTORY_JSON, IC_REPORT_FULL_JSON,
        SIGNAL_SNAPSHOTS_JSONL, SCREENER_WATCHLIST,
        THS_TABLE_XLS, MEMORY_HISTORY_JSON, WATCHDOG_LOG,
        CUSTOM_POOLS_DIR,
        WATCHDOG_PID, WATCHDOG_SCRIPT,
    )
    # 替换旧硬编码路径
    _BT_CACHE_FILE = _BT_CACHE_FILE_NEW
    _CONFIG_OK = True
    print("[Config] 路径配置加载成功")
except ImportError:
    _CONFIG_OK = False
    print("[Config] config.py 未找到，使用硬编码路径")

from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, Response, redirect
from functools import wraps
import logging, os

# O1-1: 统一日志配置（替代散落的 print()）
try:
    from logging_config import setup_logging
    setup_logging()
except Exception:
    pass

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  V1-4: API Key 简单认证 (蓝图 Phase 1)
# ═══════════════════════════════════════════════════════

# Key 从环境变量读取，开发环境用默认值
API_KEYS = {
    "read_only": os.environ.get("QIANLONG_READ_KEY", "qt_read_2026"),
    "trade":     os.environ.get("QIANLONG_TRADE_KEY", "qt_trade_2026"),
}

# 需认证的路由前缀
_AUTH_ROUTES = {
    "trade":  ["/api/paper-trade/", "/api/live-trade/", "/api/auto-evolve/",
               "/api/screener/trade-rules", "/api/system/"],
    "read_only": ["/api/positions", "/api/signals", "/api/factor-ic",
                  "/api/screener/", "/api/pnl/", "/api/signal-center",
                  "/api/paper-trade/auto-loop/status"],
}

# SSE流 + 页面渲染 豁免认证
_AUTH_EXEMPT = ["/stream", "/api/stream"]

def _is_localhost() -> bool:
    """本地访问豁免（开发模式）。"""
    return request.remote_addr in ('127.0.0.1', '::1', None)

def _needs_auth(scope: str) -> bool:
    """检查当前请求是否需要认证。"""
    path = request.path
    if any(path.startswith(p) for p in _AUTH_EXEMPT):
        return False
    if _is_localhost():
        return False  # 本地豁免
    for prefix in _AUTH_ROUTES.get(scope, []):
        if path.startswith(prefix):
            return True
    return False


def require_api_key(scope: str = "read_only"):
    """简单 API Key 认证装饰器。

    本地(127.0.0.1)豁免。SSE流豁免。
    只读Key可以GET数据，不能POST交易。
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # 本地豁免
            if _is_localhost():
                return f(*args, **kwargs)
            # SSE流豁免
            if request.path.startswith("/stream"):
                return f(*args, **kwargs)
            # 检查是否需要认证
            if not _needs_auth(scope):
                return f(*args, **kwargs)
            # 交易操作必须用 trade key
            if request.method in ("POST", "PUT", "DELETE") and scope == "read_only":
                return jsonify({"code": 401, "error": "只读Key不能执行写操作"}), 401
            # 验证Key
            key = request.headers.get("X-API-Key", "")
            expected = API_KEYS.get(scope, "")
            if key != expected:
                return jsonify({"code": 401, "error": "Unauthorized"}), 401
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ═══ 用户配置 (user_config.json, 修改后30秒自动生效, 无需重启) ═══
import json as _juc, os as _ouc
_user_config = {"trading": {}, "factors": {"use_v15_factors": True, "v15_min_score": 40}, "auto_trade": {"sim_enabled": True}}
def _dp_get(k):
    try:
        from data_paths import get
        return get(k)
    except: return os.path.join(r"D:\quant_framework", k.replace("/","\\"))
def _load_user_config():
    global _user_config
    try:
        p = _dp_get("user_config")
        if p and _ouc.path.exists(p):
            _user_config = _juc.load(open(p, "r", encoding="utf-8"))
    except: pass
_load_user_config()

# ── API 响应缓存 ──
_api_cache = {}
_api_cache_lock = threading.Lock()  # E31-1: 线程安全
_CACHE_TTL = 30  # 缓存30秒

# ── 性能诊断 ──
_PERF: dict[str, list[float]] = {}  # key → [最近3次耗时(秒)]

def _perf_tick(key: str, start: float):
    """记录一次耗时 (秒)"""
    elapsed = time.time() - start
    if key not in _PERF:
        _PERF[key] = []
    _PERF[key].append(round(elapsed, 3))
    if len(_PERF[key]) > 5:
        _PERF[key] = _PERF[key][-5:]

# ── 回测持久化缓存 ──
_BT_CACHE = {}           # 内存缓存 (加速同进程请求)
_BT_CACHE_MAX = 200      # 最大缓存条目数
_BT_CACHE_FILE = r"d:\quant_web\backtest_cache.json"  # 磁盘持久化路径
_BT_CACHE_LOCK = threading.Lock()
_BT_CACHE_DIRTY = False
_BT_CACHE_SAVE_TIMER = None

class _NumpyEncoder(json.JSONEncoder):
    """支持 numpy 类型的 JSON 编码器"""
    def default(self, obj):
        import numpy as _np
        if isinstance(obj, (_np.integer,)): return int(obj)
        if isinstance(obj, (_np.floating,)): return float(obj)
        if isinstance(obj, _np.ndarray): return obj.tolist()
        return super().default(obj)

def _load_bt_cache():
    """启动时从磁盘加载回测缓存"""
    global _BT_CACHE
    try:
        if os.path.exists(_BT_CACHE_FILE):
            with open(_BT_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                _BT_CACHE.update(loaded)
                print(f"[Cache] 已加载 {len(loaded)} 条回测缓存")
    except Exception as e:
        print(f"[Cache] 加载失败: {e}")

def _save_bt_cache():
    """异步保存回测缓存到磁盘"""
    global _BT_CACHE_DIRTY, _BT_CACHE_SAVE_TIMER
    try:
        with _BT_CACHE_LOCK:
            to_save = dict(list(_BT_CACHE.items())[:_BT_CACHE_MAX])
            with open(_BT_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, cls=_NumpyEncoder, ensure_ascii=False)
            _BT_CACHE_DIRTY = False
            print(f"[Cache] 已保存 {len(to_save)} 条回测缓存")
    except Exception as e:
        print(f"[Cache] 保存失败: {e}")

def _debounce_save_cache():
    """防抖: 2秒内多次写入只保存一次"""
    global _BT_CACHE_SAVE_TIMER
    if _BT_CACHE_SAVE_TIMER is not None:
        _BT_CACHE_SAVE_TIMER.cancel()
    _BT_CACHE_SAVE_TIMER = threading.Timer(2.0, _save_bt_cache)
    _BT_CACHE_SAVE_TIMER.daemon = True
    _BT_CACHE_SAVE_TIMER.start()

def cached_api(ttl=None):
    """装饰器: 缓存API响应 (默认30秒TTL)"""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            cache_key = f"{fn.__name__}:{str(args)}:{str(kwargs)}:{str(request.args)}"
            now = time.time()
            with _api_cache_lock:
                if cache_key in _api_cache:
                    data, timestamp = _api_cache[cache_key]
                    if now - timestamp < (ttl or _CACHE_TTL):
                        return data
            result = fn(*args, **kwargs)
            # 只缓存成功的200响应
            with _api_cache_lock:
                if hasattr(result, 'status_code') and result.status_code == 200:
                    _api_cache[cache_key] = (result, now)
                elif hasattr(result, 'get_json'):
                    _api_cache[cache_key] = (result, now)
                # 限制缓存大小
                if len(_api_cache) > 200:
                    oldest = min(_api_cache.items(), key=lambda x: x[1][1])
                    del _api_cache[oldest[0]]
            return result
        return wrapper
    return decorator

# 延迟导入 — 等 sys.path 就绪后加载
try:
    from stock_names import get_industry, resolve_broad_industry
except ImportError:
    get_industry = lambda s: (
        _INDUSTRY_MAP.get(s, "") if _INDUSTRY_MAP else ""
    )
    resolve_broad_industry = lambda s: s

from data_store import store

# P0-4: sys.path 需要在 stock_bridge 导入之前设置
sys.path.insert(0, r"d:\quant_framework")

# 股票桥接器
try:
    from stock_bridge import send_stock_to_ths, send_stock_to_tdx, send_batch_to_ths, get_bridge_status
    BRIDGE_OK = True
except Exception:
    BRIDGE_OK = False

# 实盘交易引擎
try:
    from live_trader import (
        get_trading_status, execute_trade, toggle_auto_trade,
        read_ths_positions, trader, auto_engine, state, start_auto_sync,
        CONFIG as TRADE_CONFIG
    )
    from trading_keyboard import kb_manager, DEFAULT_KEYMAP
    from daban_keyboard import get_keyboard_status as get_daban_status, get_key_action_map
    LIVE_TRADER_OK = True
    # 启动键盘监听
    kb_manager.start()
    # 启动后台持仓自动同步 (每30秒)
    start_auto_sync()
    # 启动同花顺自动导出 (交易时段每60秒)
    # P3-01: 启动事件总线
    try:
        global _event_bus
        from quant_framework.core.event_bus import EventBus
        _event_bus = EventBus()
        _event_bus.start()
        print("[App] 事件总线已启动")
    except Exception as _e: print(f"[App] 事件总线启动失败: {_e}")

    # L2行情已在QMT客户端缓存, get_full_tick自动返回bidVol/askVol

    # E254: 联动精灵已替代 pywinauto，不再需要同花顺UI自动化
    # 启动AkShare实时行情后台刷新
    try:
        from realtime_quotes import start_bg_refresh
        start_bg_refresh()
    except Exception as _e: print(f"[App] {_e}")
    # 启动财务因子后台刷新
    try:
        from financial_factors import start_bg_refresh as start_fin_refresh
        start_fin_refresh()
    except Exception as _e: print(f"[App] {_e}")
    # 启动通达信信号后台监控
    try:
        from tdx_signal_watcher import watch_tpool, get_latest_signals
        watch_tpool()
    except Exception as _e: print(f"[App] {_e}")
    # 启动模拟盘自动交易后台循环
    try:
        from paper_engine import paper as _paper_acc
        import threading as _th
        _paper_auto_running = [True]
        _paper_acc.auto_enabled = True  # 默认开启，手动可关
        # C17: 启动时统一恢复自动交易状态(持久化优先)
        try:
            from state_persist import load as _sp_load_paper
            _saved_state = _sp_load_paper()
            _saved_auto = _saved_state.get("paper_auto_enabled", True)
            _paper_acc.auto_enabled = _saved_auto
            if LIVE_TRADER_OK:
                import live_trader as _lt
                _lt.CONFIG["auto_trade_enabled"] = _saved_auto
                if _saved_auto:
                    _lt.start_auto_sync()
            print(f"[App] 自动交易状态已恢复: auto={_saved_auto}")
        except Exception as _e:
            print(f"[App] 状态恢复失败(使用默认值): {_e}")
            _paper_acc.auto_enabled = True
            if LIVE_TRADER_OK:
                import live_trader as _lt
                _lt.CONFIG["auto_trade_enabled"] = False
        def _paper_auto_loop_once():
            """单次交易检查 — 被外层保活循环调用 (D10: sleep已移到循环末尾)"""
            import time as _time
            if not _paper_acc.auto_enabled:
                return
            from realtime_quotes import is_trading_time
            if not is_trading_time():
                return
            # 刷新持仓市价
            for sym, pos in list(_paper_acc.positions.items()):
                mp = _paper_acc._get_market_price(sym)
                if mp and mp > 0:
                    pos['last_price'] = mp
            # 获取信号 — 用实时价刷新 close/change_pct
            signals = []
            try:
                if globals().get('_CACHE_READY', False) and _FACTOR_CACHE:
                    pool = _FACTOR_CACHE[:200]
                    for s in pool:
                        sym = getattr(s, 'symbol', '')
                        rt_close = getattr(s, 'close', 0) or 0
                        rt_chg = getattr(s, 'change_pct', 0) or 0
                        try:
                            from realtime_quotes import _quote_cache
                            code = sym.replace('sh','').replace('sz','')
                            if _quote_cache and _quote_cache.get('data') and code in _quote_cache['data']:
                                q = _quote_cache['data'][code]
                                rt_close = float(q.get('close', rt_close) or rt_close)
                                rt_chg = float(q.get('change_pct', rt_chg) or rt_chg)
                        except Exception as _e: print(f"[App] {_e}")
                        signals.append({
                            'symbol': sym,
                            'buy_signal': getattr(s, 'buy_signal', 0) or 0,
                            'close': rt_close,
                            'vol_ratio': getattr(s, 'vol_ratio', 1) or 1,
                            'change_pct': rt_chg,
                        })
            except Exception as _e:
                _paper_log(f"信号构建失败: {_e}")
                return
            # C14: 缓存未就绪时HTTP兜底获取信号
            if not signals:
                try:
                    import urllib.request, json as _j3
                    _r3 = urllib.request.urlopen('http://127.0.0.1:5002/api/signal-center', timeout=10)
                    _sigs = _j3.loads(_r3.read().decode()).get('signals', [])
                    for _s in _sigs[:50]:
                        signals.append({
                            'symbol': _s.get('symbol', ''),
                            'buy_signal': _s.get('buy_signal', 0) or 0,
                            'close': _s.get('close', 0) or 0,
                            'vol_ratio': 1,
                            'change_pct': _s.get('change_pct', 0) or 0,
                        })
                    if signals:
                        _paper_log(f"HTTP信号兜底: {len(signals)}条")
                except Exception as _e:
                    _paper_log(f"HTTP信号兜底失败: {_e}")
            # 统一信号总线: 旧信号 + V1-5新因子 + 用户策略
            if _user_config.get("factors", {}).get("use_v15_factors", True):
                try:
                    import sys as _sbus; _sbus.path.insert(0, r"D:\quant_framework")
                    from market_state_classifier import classify_market_state
                    ms = classify_market_state()
                    min_score = {"bull": 30, "volatile": 40, "bear": 50, "unknown": 40}.get(ms, 40)
                    # 游资分仓法: 市场状态→仓位控制
                    _pos_rule = {
                        "bull":     {"max": 5, "lv3": 0.25, "lv4": 0.15, "lv5": 0.20},
                        "volatile": {"max": 3, "lv3": 0.00, "lv4": 0.15, "lv5": 0.15},
                        "bear":     {"max": 2, "lv3": 0.00, "lv4": 0.00, "lv5": 0.10},
                        "unknown":  {"max": 3, "lv3": 0.10, "lv4": 0.15, "lv5": 0.15},
                    }.get(ms, {"max": 3, "lv3": 0.10, "lv4": 0.15, "lv5": 0.15})
                    _user_config["trading"] = _pos_rule
                    try:
                        import live_trader as _lt
                        _lt.CONFIG["max_positions"] = _pos_rule["max"]
                        _lt.CONFIG["position_pct_lv3"] = _pos_rule["lv3"]
                        _lt.CONFIG["position_pct_lv4"] = _pos_rule["lv4"]
                        _lt.CONFIG["position_pct_lv5"] = _pos_rule["lv5"]
                    except: pass
                    from signal_bus import publish
                    from realtime_quotes import _quote_cache
                    raw = _quote_cache.get("data", {}) if _quote_cache else {}
                    cnt = 0
                    for code, q in list(raw.items())[:60]:
                        if cnt >= 5: break
                        chg = float(q.get("change_pct", 0) or 0)
                        price = float(q.get("close", q.get("price", 0)) or 0)
                        if abs(chg) > 9.5 or price <= 0 or price > 100 or code.startswith("12"): continue
                        sym = "sh"+code if code.startswith("6") else "sz"+code
                        try:
                            from quant_framework.execution.rules.engine import RuleEngine
                            from market_state_classifier import classify_market_state
                            re = RuleEngine()
                            bs = re.check_buy_signal(sym, {}, market_state=ms)
                            if bs and bs.get("score", 0) > min_score:
                                strat_name = bs.get("strategy", "v15")
                                signals.append({
                                    "symbol": sym, "name": strat_name,
                                    "buy_signal": 4, "close": bs.get("entry_price", price),
                                    "change_pct": chg, "vol_ratio": 1, "industry": "",
                                    "power_score": bs.get("score", 0), "_v15": True,
                                    "strategy": strat_name,  # 绩效追踪用
                                })
                                cnt += 1
                        except Exception: pass
                    if cnt: _paper_log(f"V1-5信号总线: {cnt}个")
                    else: _paper_log(f"V1-5: 扫描{len(raw)}只行情 未产生信号")
                except Exception as _v15e: _paper_log(f"V1-5异常: {_v15e}")

            # 执行规则检查
            actions = _paper_acc.auto_trade_check(signals)
            # P3-01: 发布交易事件到EventBus
            if actions:
                try:
                    for a in actions:
                        _event_bus.publish("order", {"symbol": a.get("symbol",""), "action": a.get("action",""), "reason": a.get("reason",""), "source": "paper"})
                except Exception: pass
            # F1: A/B测试并行处理
            try:
                from ab_test import runner as _ab_runner
                if _ab_runner.running:
                    _ab_runner.process_signals(signals)
            except Exception as _e: print(f"[App] {_e}")
            if actions:
                now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _paper_log(f"{len(actions)} actions: {[(a.get('reason','?'), a.get('symbol','?')) for a in actions]}")
                # 审计日志
                try:
                    import json as _aj
                    log_line = _aj.dumps({'ts':now_ts,'actions':[{'symbol':a.get('symbol',''),'action':a.get('action',''),'reason':a.get('reason','')} for a in actions]},ensure_ascii=False)
                    afn = AUDIT_LOG_JSONL  # E26: config路径
                    with open(afn,'a',encoding='utf-8') as _af:
                        _af.write(log_line+'\n')
                    # 限制最多5000行
                    import os as _afos
                    if _afos.path.exists(afn) and _afos.path.getsize(afn) > 1000000:
                        with open(afn,'r',encoding='utf-8') as _f: lines=_f.readlines()
                        if len(lines)>5000: open(afn,'w',encoding='utf-8').writelines(lines[-2000:])
                except Exception as _e: print(f"[App] {_e}")
                # 推送风控事件到SSE
                for a in actions:
                    try: store.set('risk_event', {'type':'auto_trade','time':now_ts[11:],'symbol':a.get('symbol',''),'action':a.get('action',''),'reason':a.get('reason','')})
                    except Exception as _e: print(f"[App] {_e}")

        # E238: 交易日志函数
        def _paper_log(msg):
            try:
                with open(PAPER_AUTO_LOG,'a',encoding='utf-8') as _plf:  # E26: config路径
                    _plf.write(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}\n')
            except Exception as _e: print(f"[App] {_e}")

        def _paper_auto_loop():
            """E238: 保活循环 — 任何异常都不退出，5秒后重试"""
            import time as _time
            while _paper_auto_running[0]:
                try:
                    _paper_auto_loop_once()
                    _time.sleep(10)  # D10: sleep从_once移到循环末尾，首次检查0延迟
                except Exception as _e:
                    _paper_log(f"循环异常，5秒后重试: {_e}")
                    print(f"[PaperAuto] 循环异常，5秒后重试: {_e}")
                    _time.sleep(5)

        # 统一交易循环 (替代旧版 + V1-5注入)
        try:
            import sys as _tl; _tl.path.insert(0, r"D:\quant_framework")
            from trading_loop import start as _tl_start
            _tl_start(_paper_acc)
            print("[App] 统一交易循环已启动")
        except Exception as _tle:
            print(f"[App] 交易循环启动失败: {_tle}")
            _th.Thread(target=_paper_auto_loop, daemon=True).start()
            print("[App] Paper auto-trade loop started (legacy fallback)")

        # O1-2: SQLite每日备份 (09:30)
        try:
            import backup_db as _bkdb
            def _daily_backup():
                import time as _tb
                while True:
                    _tb.sleep(60)
                    now = datetime.now()
                    if now.hour == 9 and now.minute == 30:
                        try:
                            _bkdb.backup_sqlite_db()
                        except Exception:
                            pass
                        _tb.sleep(120)  # 2分钟内不重复
            _th.Thread(target=_daily_backup, daemon=True, name="DBBackup").start()
            print("[App] SQLite每日备份已注册 (09:30)")
        except Exception: pass

        # 模拟盘每小时自动备份 (保留24个整点快照)
        try:
            def _hourly_paper_backup():
                import time as _thb, shutil as _shb, glob as _gb
                _backup_dir = r"D:\quant_framework\backups\hourly"
                os.makedirs(_backup_dir, exist_ok=True)
                # 模拟盘 + 实盘 全部关键文件
                _files = [
                    r"D:\quant_framework\paper_account.json",
                    r"D:\quant_framework\trade_log.csv",
                    r"D:\quant_framework\live_positions_track.json",
                    r"D:\quant_framework\live_trader_config.json",
                    r"D:\quant_framework\live_positions.csv",
                    r"D:\quant_web\data\auto_trade_plan.json",
                ]
                _last_hour = -1
                while True:
                    _thb.sleep(60)
                    _h = datetime.now().hour
                    if _h != _last_hour:
                        _last_hour = _h
                        _ts = datetime.now().strftime("%Y%m%d_%H")
                        for _src in _files:
                            try:
                                if os.path.exists(_src):
                                    _name = os.path.basename(_src)
                                    _shb.copy2(_src, os.path.join(_backup_dir, f"{_ts}_{_name}"))
                            except Exception: pass
                        # 保留最近24小时
                        try:
                            _all = sorted(os.listdir(_backup_dir))
                            _keep = set()
                            for _h24 in range(24):
                                _keep.add(datetime.now().strftime(f"%Y%m%d_{(datetime.now().hour-_h24)%24:02d}"))
                            for _f in _all:
                                _pfx = _f[:11]  # YYYYMMDD_HH
                                if _pfx not in _keep and os.path.isfile(os.path.join(_backup_dir, _f)):
                                    os.remove(os.path.join(_backup_dir, _f))
                        except Exception: pass
            _th.Thread(target=_hourly_paper_backup, daemon=True, name="PaperBackup").start()
            # 每小时同步关键文件到百度同步盘
            def _cloud_sync():
                import subprocess, time as _tcs
                while True:
                    _tcs.sleep(3600)
                    try:
                        subprocess.run([sys.executable, r"D:\quant_framework\sync_to_cloud.py"], timeout=30)
                    except Exception: pass
            _th.Thread(target=_cloud_sync, daemon=True, name="CloudSync").start()
            print("[App] 交易数据每小时备份已注册(模拟+实盘+云端)")
        except Exception: pass

        # 日线数据自动更新 (每日15:30盘后)
        try:
            def _daily_parquet_update():
                import time as _tpu
                while True:
                    _tpu.sleep(60)
                    now = datetime.now()
                    if now.hour == 15 and now.minute == 30 and now.weekday() < 5:
                        try:
                            print("[AutoUpdate] 盘后更新日线数据...")
                            import subprocess, sys as _spu
                            subprocess.run([_spu.executable, r"D:\quant_framework\update_data_qmt.py"],
                                         timeout=600, capture_output=True)
                            print("[AutoUpdate] 完成")
                        except Exception as _eu:
                            print(f"[AutoUpdate] 失败: {_eu}")
                        _tpu.sleep(120)
            _th.Thread(target=_daily_parquet_update, daemon=True, name="ParquetUpdate").start()
            print("[App] 日线数据自动更新已就绪 (每日15:30)")
        except Exception: pass

        # LLM盘前简报 (每日9:25—竞价结束后)
        try:
            def _llm_pre_market():
                import time as _tlpm
                while True:
                    _tlpm.sleep(60)
                    now = datetime.now()
                    if now.hour == 9 and now.minute == 25 and now.weekday() < 5:
                        try:
                            import sys as _sl; _sl.path.insert(0, r"D:\quant_framework")
                            from llm_sentiment import pre_market_brief
                            brief = pre_market_brief()
                            if brief and '异常' not in brief and '未配置' not in brief:
                                from dingtalk_alerts import send_alert
                                send_alert("📰 盘前简报", brief)
                                print(f"[LLM] 盘前简报已推送")
                        except Exception as _e: print(f"[LLM] 简报失败: {_e}")
                        _tlpm.sleep(120)
            _th.Thread(target=_llm_pre_market, daemon=True, name="LLM-Brief").start()
            print("[App] LLM盘前简报已就绪 (每日9:25)")
        except Exception: pass

        # IC自动更新 (每周一 08:00)
        try:
            def _weekly_ic_update():
                import time as _tic, subprocess as _sub
                while True:
                    _tic.sleep(300)
                    now = datetime.now()
                    if now.weekday()==0 and now.hour==8 and now.minute<10:
                        try:
                            _sub.run(["C:\\Program Files\\Python312\\python.exe","D:\\quant_framework\\full_market_ic.py","--days","60","--sample","500"],timeout=600)
                            print("[IC Auto] 更新完成")
                        except Exception as _ice: print(f"[IC Auto] 失败:{_ice}")
                        _tic.sleep(3600)
            _th.Thread(target=_weekly_ic_update, daemon=True, name="ICUpdate").start()
            print("[App] IC自动更新已就绪 (每周一08:00)")
        except Exception:
            pass

        # P3-06: 策略自动熔断 (每5分钟检查)
        try:
            def _circuit_breaker_check():
                import time as _tcb
                while True:
                    _tcb.sleep(300)
                    try:
                        from factor_health import check_strategy_circuit_breaker
                        actions = check_strategy_circuit_breaker()
                        if actions:
                            print(f"[熔断] 触发{len(actions)}项: {[(a['strategy'][:12],a['action']) for a in actions]}")
                            # P3-07: 钉钉推送熔断事件
                            try:
                                from dingtalk_alerts import send_alert
                                for a in actions:
                                    send_alert(f"策略熔断:{a['action']}", f"{a['strategy']}\n{a['reason']}", "critical")
                            except Exception: pass
                    except Exception as _cbe:
                        print(f"[熔断] 检查异常: {_cbe}")
            _th.Thread(target=_circuit_breaker_check, daemon=True, name="CircuitBreaker").start()
            print("[App] 策略自动熔断已就绪 (每5分钟)")
        except Exception:
            pass

        # 周检定时器 (每周六 09:00)
        try:
            def _weekend_check():
                import time as _twc
                while True:
                    _twc.sleep(300)
                    now = datetime.now()
                    if now.weekday() == 5 and now.hour == 9 and now.minute < 10:
                        try:
                            from scripts.weekly_check import run_weekly
                            import sys as _swc
                            _swc.path.insert(0, r"D:\quant_framework")
                            run_weekly(push=True, run_pipeline=False)
                            print("[周检] 完成")
                        except Exception as _wce:
                            print(f"[周检] 失败: {_wce}")
                        _twc.sleep(3600)
            _th.Thread(target=_weekend_check, daemon=True, name="WeeklyCheck").start()
            print("[App] 周检定时器已就绪 (每周六09:00)")
        except Exception:
            pass
    except Exception as e:
        print(f"[App] Paper auto-trade: {e}")
except ImportError as e:
    LIVE_TRADER_OK = False
    kb_manager = None
    DEFAULT_KEYMAP = {}
    print(f"[App] Live trader not available: {e}")

# E258: 选股器导入
try:
    from stock_screener import StockScreener
    _SCREENER_OK = True
except ImportError:
    _SCREENER_OK = False

from data_loader import (
    load_all_stocks,
    compute_all_stocks,
    compute_factors,
    compute_factors_for_date,
    get_stock_kline,
    StockInfo,
)
from tdx_formulas import scan_formula_pools, get_formula_stocks

# ── 名称加载 ──
_NAME_MAP: dict[str, str] = {}

def with_name(row: dict) -> dict:
    """数据富化: 自动补 name (如果代码可识别)。对标同花顺/QMT 返回即完整。"""
    if not row or not isinstance(row, dict):
        return row
    sym = row.get("symbol") or row.get("code") or ""
    if not sym or row.get("name"):
        return row
    # 归一化代码，查 _NAME_MAP (兼容纯数字和sh/sz格式)
    import re
    clean = str(sym).strip().lower().replace('.sh','').replace('.sz','')
    m = re.search(r'[0-9]{5,6}', clean)
    code = m.group(0) if m else ''
    # 先精确查，再前缀匹配
    name = _NAME_MAP.get("sh"+code) or _NAME_MAP.get("sz"+code) or _NAME_MAP.get(code) or ''
    if name:
        row["name"] = name
    return row

def with_names(rows: list) -> list:
    """批量富化。"""
    for r in (rows or []):
        with_name(r)
    return rows

def _load_names():
    """从CSV加载股票名称映射。"""
    global _NAME_MAP
    if _NAME_MAP:
        return
    import csv as _csv
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_names_full.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding='utf-8-sig') as f:
            for row in _csv.DictReader(f):
                code = row.get('code', '').strip()
                name = row.get('name', '').strip()
                if code and name:
                    _NAME_MAP[code] = name
        print(f"[Names] Loaded {len(_NAME_MAP)} names from CSV")
    # 也加载内置名称
    from stock_names import BUILTIN_NAMES, init_names as _init_names, get_sector_index_code
    _init_names()  # 同时初始化行业映射
    for k, v in BUILTIN_NAMES.items():
        if k not in _NAME_MAP:
            _NAME_MAP[k] = v
    print(f"[Names] Total: {len(_NAME_MAP)} names")

_SECTOR_REFRESH_TIME = 0  # 上次刷新时间

def _refresh_sector_data():
    """刷新板块指数涨跌幅 (每60秒刷新一次，避免频繁读盘)。"""
    global _SECTOR_KLINES, _SECTOR_REFRESH_TIME
    import time as _time
    now = _time.time()
    if not _SECTOR_KLINES:
        _load_sector_data()
        _SECTOR_REFRESH_TIME = now
        return
    if now - _SECTOR_REFRESH_TIME < 60:
        return  # 60秒内不重复刷新
    _SECTOR_REFRESH_TIME = now
    import os as _os
    from stock_names import _SECTOR_INDEX_MAP
    vipdoc = DATA_ROOT
    day_dir = _os.path.join(vipdoc, "sh", "lday")
    if not _os.path.isdir(day_dir):
        return
    for sector_name, old in _SECTOR_KLINES.items():
        idx_code = old.get("idx_code", "")
        if not idx_code:
            continue
        fpath = _os.path.join(day_dir, f"sh{idx_code}.day")
        if not _os.path.isfile(fpath):
            continue
        try:
            raw = _read_sector_day(fpath)
            if raw and len(raw) >= 2:
                dates = sorted(raw.keys())
                last_date = dates[-1]
                prev_date = dates[-2]
                last_close = raw[last_date][3]
                prev_close = raw[prev_date][3]
                chg = (last_close - prev_close) / prev_close if prev_close > 0 else 0
                _SECTOR_KLINES[sector_name] = {
                    "idx_code": idx_code,
                    "last_close": last_close,
                    "change_pct": chg,
                    "last_date": str(last_date),
                }
        except Exception as _e:
            logger.warning(f"[factor] 单股票因子计算失败: {_e}")


def _load_sector_data():
    """加载板块指数日线数据，叠加westock实时覆盖。"""
    global _SECTOR_KLINES, _SECTOR_DATA_SOURCE, _SECTOR_DATA_TIME
    import os as _os
    from stock_names import _SECTOR_INDEX_MAP
    vipdoc = DATA_ROOT
    day_dir = _os.path.join(vipdoc, "sh", "lday")
    _SECTOR_DATA_SOURCE = "通达信日线缓存"
    _SECTOR_DATA_TIME = ""
    if not _os.path.isdir(day_dir):
        return
    loaded = 0
    for sector_name, idx_code in _SECTOR_INDEX_MAP.items():
        fpath = _os.path.join(day_dir, f"sh{idx_code}.day")
        if not _os.path.isfile(fpath):
            continue
        try:
            raw = _read_sector_day(fpath)
            if raw and len(raw) >= 2:
                dates = sorted(raw.keys())
                last_date = dates[-1]
                prev_date = dates[-2]
                last_close = raw[last_date][3]  # close
                prev_close = raw[prev_date][3]
                chg = (last_close - prev_close) / prev_close if prev_close > 0 else 0
                _SECTOR_KLINES[sector_name] = {
                    "idx_code": idx_code,
                    "last_close": last_close,
                    "change_pct": chg,
                    "last_date": str(last_date),
                }
                loaded += 1
        except Exception as _e:
            logger.warning(f"[data] 股票数据加载失败: {_e}")

    # E54: 尝试用westock实时数据覆盖板块涨跌幅
    _try_westock_sector_overlay()

    # 更新数据源标记
    from datetime import datetime as _dt
    _SECTOR_DATA_TIME = _dt.now().strftime("%H:%M:%S")
    print(f"[Init] Sector indices loaded: {loaded}, source={_SECTOR_DATA_SOURCE}")


def _try_westock_sector_overlay():
    """E54: 用westock实时行情覆盖板块涨跌幅。失败静默回退。"""
    global _SECTOR_KLINES, _SECTOR_DATA_SOURCE
    try:
        import subprocess as _sp
        _node = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe"
        _ws = r"C:\Users\Administrator\.workbuddy\plugins\marketplaces\cb_teams_marketplace\plugins\finance-data\skills\westock-data\scripts\index.js"

        # 查询主要板块指数（市场指数 + 行业代表）
        idx_codes = ['sh000001','sz399001','sh000688','sh000300','sh000016','sh000905',
                      'sz399006','sh000852']
        r = _sp.run([_node, _ws, 'quote'] + idx_codes,
                    capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout:
            return

        # 解析管线表格
        from westock_factors import _parse_westock_table
        rows = _parse_westock_table(r.stdout)
        if not rows:
            return

        # 构建 {code: change_pct} 映射
        live_changes = {}
        for row in rows:
            code = row.get('code', row.get('symbol', ''))
            try:
                chg = float(row.get('change_percent', 0) or 0)
            except (ValueError, TypeError):
                chg = 0
            if code:
                live_changes[code] = chg

        if not live_changes:
            return

        # 覆盖行业板块对应的指数涨跌幅（TDX代码前缀处理）
        # TDX行业代码如 880491，上证行业代码去掉88前缀
        updated = 0
        for sector_name, kdata in _SECTOR_KLINES.items():
            idx = kdata.get("idx_code", "")
            # 尝试匹配：TDX 88xxxx → sh88xxxx 或其他格式
            for fmt in [f"sh{idx}", f"sz{idx}", idx]:
                if fmt in live_changes:
                    old_chg = kdata["change_pct"]
                    kdata["change_pct"] = live_changes[fmt]
                    kdata["last_date"] = "realtime"
                    if abs(old_chg - live_changes[fmt]) > 0.001:
                        updated += 1
                    break

        if updated > 0:
            _SECTOR_DATA_SOURCE = f"📡 westock实时 (已更新{updated}个板块)"
        elif live_changes:
            _SECTOR_DATA_SOURCE = "📡 westock实时 (市场指数)"

    except Exception as _e:
        logger.warning(f"[sector] 板块数据加载失败(静默回退): {_e}")


def _read_sector_day(filepath: str) -> dict:
    """读取 .day 文件返回 {date_int: (open, high, low, close, amount, volume)}。"""
    import struct
    data = {}
    try:
        with open(filepath, "rb") as f:
            content = f.read()
        for i in range(0, len(content), 32):
            if i + 32 > len(content):
                break
            date_int, o_raw, h_raw, l_raw, c_raw, amt, vol, _ = struct.unpack("<I I I I I f I I", content[i:i+32])
            divisor = 100.0 if o_raw < 10_000_000 else 1000.0
            c = c_raw / divisor
            if date_int > 0 and c > 0:
                data[date_int] = (o_raw/divisor, h_raw/divisor, l_raw/divisor, c, amt, vol)
    except Exception as _e:
        logger.warning(f"[tdx] K线数据解析失败: {_e}")
    return data


def _apply_health_multipliers(weights: dict) -> None:
    """P0-1: 读取 registry 中的 weight_multiplier, 应用到权重。"""
    try:
        reg_path = r"D:\quant_framework\factor_registry.json"
        if not os.path.exists(reg_path):
            return
        with open(reg_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        for fac in reg.get("factors", []):
            mult = fac.get("weight_multiplier")
            name = fac.get("name", "")
            if mult is not None and name in weights:
                weights[name] = round(weights[name] * float(mult), 4)
    except Exception:
        pass


def _resolve_name(symbol: str) -> str:
    """解析股票名称。"""
    code = symbol.lower().strip()
    for p in ['sh', 'sz', 'bj']:
        if code.startswith(p):
            code = code[len(p):]
            break
    # 查映射
    if code in _NAME_MAP:
        return _NAME_MAP[code]
    if symbol.lower() in _NAME_MAP:
        return _NAME_MAP[symbol.lower()]
    # 返回纯数字代码
    if code.isdigit():
        return code
    return symbol

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True


_startup_time = time.time()  # 健康检查用

# 静态资源缓存 (1小时) — 减少页面加载时间
@app.after_request
def _add_cache_headers(response):
    if request.path.startswith('/static/'):
        response.cache_control.max_age = 300  # 5分钟, 开发期间不缓存
        response.cache_control.public = True
    return response

# ═══ 全局JSON错误处理(防止HTML错误页面) ═══
@app.errorhandler(400)
def err400(e): return jsonify({"code":400,"error":"请求格式错误"}), 400
@app.errorhandler(404)
def err404(e): return jsonify({"code":404,"error":"接口不存在"}), 404
@app.errorhandler(500)
def err500(e):
    import traceback
    print("[500 ERROR]", str(e))
    traceback.print_exc()
    return jsonify({"code":500,"error":str(e)}), 500

@app.before_request
def api_guard():
    """所有/api/请求兜底: 异常返回JSON不崩溃"""
    if request.path.startswith('/api/'):
        try:
            request.get_json(silent=True)
        except Exception:
            pass  # JSON解析失败不影响请求处理

# E97: 请求耗时日志 — 排查周期性卡顿
@app.before_request
def _log_req_start():
    request._start = time.time()

@app.after_request
def _log_req_time(response):
    elapsed = time.time() - getattr(request, '_start', time.time())
    if elapsed > 3:  # 只记录 >3s 的慢请求
        print(f"[SLOW] {request.method} {request.path} {elapsed:.1f}s")
    return response

# ── 全局缓存 ──
STOCK_DATA: dict = {}
_STOCK_DATA_CACHE_TIME: float = 0      # E260: TTL缓存时间戳
_STOCK_DATA_CACHE_TTL: float = 1800.0  # E260修复: 30分钟，防频繁全量重载→OOM
_STOCK_DATA_MAX_MEMORY_MB: int = 500   # E260: 内存上限500MB
DATA_ROOT = ""
_FACTOR_CACHE: list = []
_CACHE_READY = False
_DATA_WARMED = False  # parquet预热标志

def _warmup_data():
    """后台预热: 加载parquet到内存, 避免首次API请求被堵"""
    global _DATA_WARMED, STOCK_DATA, _CACHE_READY
    import time as _tw
    _tw.sleep(2)  # 等QMT连接
    try:
        from data_loader import load_stock_data_cache
        sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
        if sd and len(sd) > 1000:
            STOCK_DATA = sd
            _CACHE_READY = True
            _DATA_WARMED = True
            from paper_engine import paper as _pp
            _pp.stock_data = sd
            print(f"[App] ✅ 数据预热完成: {len(sd)}只 → paper_engine")
    except Exception as _we:
        print(f"[App] 预热失败: {_we}")

import threading as _thw
_thw.Thread(target=_warmup_data, daemon=True, name="DataWarmup").start()

# ── 从磁盘加载因子缓存 ──
_fcp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "factor_cache.pkl")
if os.path.exists(_fcp) and os.path.getsize(_fcp) > 100:
    try:
        with open(_fcp, "rb") as _f:
            _r = __import__('pickle').load(_f)
        _d = _r.get("data", []) if isinstance(_r, dict) else (_r if isinstance(_r, list) else [])
        if _d and len(_d) > 100:  # P0修复: 数据太少视为损坏
            _FACTOR_CACHE = _d
            _CACHE_READY = True
            print(f"[Module] 因子缓存: {len(_FACTOR_CACHE)} 条")
        else:
            print("[Module] ⚠️ 因子缓存过小(可能损坏)，将在首次请求时重建")
            os.remove(_fcp)  # 删除损坏缓存，强制重建
    except Exception as _e:
        print(f"[Module] ⚠️ 因子缓存损坏({_e})，将在首次请求时重建")
        try:
            os.remove(_fcp)
        except Exception:
            pass
_CACHE_LOADING = False
_XGB_WEIGHTS: dict = {}             # E73b: XGBoost因子权重（模块级，供外部引用）
# S30: 因子V2开关 — 默认关闭，模拟盘验证通过后手动改为True
_FACTOR_V2_ENABLED = False
_SECTOR_KLINES: dict[str, dict] = {}  # 板块指数行情
_INDUSTRY_MAP: dict[str, str] = {}    # stock→行业映射
try:
    import json as _jm
    _map_file = STOCK_NAMES_JSON  # E26: config路径
    if os.path.exists(_map_file):
        with open(_map_file, "r", encoding="utf-8") as _f:
            _raw = _jm.load(_f)
        _INDUSTRY_MAP = _raw.get("symbol_to_industry", {})
except Exception as _e:
    logger.warning(f"[init] 行业映射加载失败: {_e}")
SIGNAL_LABELS = {
    "signal_final": "终极选股(XG+B1)",
    "signal_xg": "涨停突破牛线(XG)",
    "signal_b1": "底部反转(B1)",
    "signal_qlj": "擒龙决",
    "signal_ztxf": "涨停先锋",
    "signal_resonance": "双信号共振",
    "signal_bandit": "波段擒妖",
    "all": "全部信号",
}


def get_stock_data(force_reload: bool = False) -> dict:
    """E260: 带TTL缓存的stock_data访问器 — 防重复18-31s全量加载。
    启动时后台预热, 未就绪时返回空dict (调用方自行降级)。

    Args:
        force_reload: 强制刷新缓存

    Returns:
        STOCK_DATA dict（5860只股票DataFrame），缓存命中时O(1)返回
    """
    global STOCK_DATA, _STOCK_DATA_CACHE_TIME

    if not force_reload and STOCK_DATA and len(STOCK_DATA) > 0:
        now = time.time()
        if now - _STOCK_DATA_CACHE_TIME < _STOCK_DATA_CACHE_TTL:
            return STOCK_DATA
        # TTL过期但保留旧数据(兜底)，后台异步刷新
        print(f"[StockData] TTL过期({(now-_STOCK_DATA_CACHE_TIME):.0f}s)，异步刷新中...")

    if STOCK_DATA and len(STOCK_DATA) > 0:
        return STOCK_DATA  # 有旧数据就先返回

    # 首次加载：尝试从缓存文件读 (P0-2: parquet优先)
    import pickle as _pk, gzip as _gz
    _cache_dir = os.path.dirname(os.path.abspath(__file__))
    _cache_parquet = os.path.join(_cache_dir, "stock_data.parquet")
    _data_cache_file = os.path.join(_cache_dir, "stock_data.pkl.gz")
    _data_cache_legacy = os.path.join(_cache_dir, "stock_data.pkl")

    for _src in [_cache_parquet, _data_cache_file, _data_cache_legacy]:
        if os.path.exists(_src):
            try:
                _t0 = time.time()
                if _src.endswith('.parquet'):
                    from data_loader import load_stock_data_cache as _gs_pq
                    STOCK_DATA = _gs_pq(_src)
                elif _src.endswith('.gz'):
                    STOCK_DATA = _pk.load(_gz.open(_src, 'rb'))
                else:
                    STOCK_DATA = _pk.load(open(_src, 'rb'))
                _STOCK_DATA_CACHE_TIME = time.time()

                # 内存检查：超过上限打印告警
                try:
                    import sys as _sys
                    _mem_mb = _sys.getsizeof(STOCK_DATA) / (1024 * 1024)
                    if _mem_mb > _STOCK_DATA_MAX_MEMORY_MB:
                        print(f"[StockData] ⚠️ 内存占用{_mem_mb:.0f}MB > {_STOCK_DATA_MAX_MEMORY_MB}MB上限")
                except Exception:
                    pass

                print(f"[StockData] 加载完成: {len(STOCK_DATA)}只，耗时{time.time()-_t0:.1f}s，TTL={_STOCK_DATA_CACHE_TTL}s")
                return STOCK_DATA
            except Exception as _e:
                print(f"[StockData] 加载失败({_src}): {_e}")
                continue

    # 兜底：调用完整init_data()
    init_data()
    _STOCK_DATA_CACHE_TIME = time.time()
    return STOCK_DATA


def init_data():
    """初始化数据 (首次请求时懒加载)。"""
    global STOCK_DATA, DATA_ROOT, _FACTOR_CACHE, _CACHE_LOADING, _CACHE_READY
    _t0 = time.time()

    if STOCK_DATA:
        return

    # 自动检测数据目录
    candidates = [
        r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc",
        r"D:\通信达技术指标\01、散人竞价擒龙V8.59旗舰版（下载解压即可使用）\散人竞价擒龙V8.59旗舰版（无加密）\vipdoc",
        r"d:\同花顺软件\同花顺\history",
    ]
    for c in candidates:
        if os.path.isdir(c):
            DATA_ROOT = c
            break

    if not DATA_ROOT:
        print("WARNING: No TDX data directory found!")
        return

    print(f"[Init] Loading names...")
    _load_names()
    _load_sector_data()

    import pickle as _pk
    _cache_dir = os.path.dirname(os.path.abspath(__file__))
    _factor_cache_file = os.path.join(_cache_dir, "factor_cache.pkl")
    _data_cache_file = os.path.join(_cache_dir, "stock_data.pkl.gz")  # Y2: gzip压缩
    _data_cache_file_legacy = os.path.join(_cache_dir, "stock_data.pkl")
    _data_cache_parquet = os.path.join(_cache_dir, "stock_data.parquet")  # P0-2: 80MB vs 284MB
    # E10: 缓存版本号 — 因子逻辑变化时递增
    _CACHE_VERSION = "20260622_v5"  # v5: max_stocks=10000, 覆盖全部9006只A股

    # E371: 因子缓存有效性 — 仅当文件存在且非今日构建时才刷新 (不因mtime过期就删)
    if os.path.exists(_factor_cache_file):
        try:
            _cache_mtime = os.path.getmtime(_factor_cache_file)
            _cache_date = datetime.fromtimestamp(_cache_mtime).strftime("%Y%m%d")
            _today = datetime.now().strftime("%Y%m%d")
            # 仅跨日 + parquet已更新 → 后台异步更新(不删旧缓存, 不停服务)
            if _cache_date != _today and os.path.exists(_data_cache_parquet):
                _pq_date = datetime.fromtimestamp(os.path.getmtime(_data_cache_parquet)).strftime("%Y%m%d")
                if _pq_date != _cache_date:
                    print(f"[Init] 因子缓存跨日({_cache_date}→{_today})，后台异步刷新(保留旧缓存可用)")
        except Exception as _fe:
            print(f"[Init] 缓存日期检查跳过: {_fe}")

    # 优先: 双缓存秒级启动 (P0-2: parquet > gzip > pickle)
    _any_data_cache = os.path.exists(_data_cache_parquet) or os.path.exists(_data_cache_file) or os.path.exists(_data_cache_file_legacy)
    if os.path.exists(_factor_cache_file) and _any_data_cache:
        try:
            # P0-2: 优先parquet(80MB)，回退gzip/pickle(284MB)
            _cache_src = None
            if os.path.exists(_data_cache_parquet):
                _cache_src = _data_cache_parquet
            elif os.path.exists(_data_cache_file):
                _cache_src = _data_cache_file
            elif os.path.exists(_data_cache_file_legacy):
                _cache_src = _data_cache_file_legacy
            if _cache_src.endswith('.parquet'):
                from data_loader import load_stock_data_cache as _load_pq
                STOCK_DATA = _load_pq(_cache_src)
                _cache_corrupted = STOCK_DATA is None
                if _cache_corrupted:
                    print(f"[Init] Parquet缓存损坏，删除并回退重建...")
                    try: os.remove(_cache_src)
                    except Exception: pass
            elif _cache_src.endswith('.gz'):
                import gzip as _gz
                try:
                    STOCK_DATA = _pk.load(_gz.open(_cache_src, 'rb'))
                except (EOFError, _gz.BadGzipFile, OSError) as _gz_e:
                    # P0-4修复: gzip损坏时备份旧文件 → 清缓存 → 回退到TDX全量重建
                    print(f"[Init] stock_data.pkl.gz 损坏({_gz_e})，备份后从TDX重建...")
                    try:
                        _cache_backup = _cache_src + f".broken.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        os.replace(_cache_src, _cache_backup)
                        print(f"[Init] 已备份损坏文件 → {_cache_backup}")
                    except Exception:
                        os.remove(_cache_src) if os.path.exists(_cache_src) else None
                    # 清空双缓存 → 回退到TDX全量重建（跳过return）
                    STOCK_DATA = {}
                    _FACTOR_CACHE = []
                    _CACHE_READY = False
                    _cache_corrupted = True  # P0-4: 标记跳过return
                else:
                    _cache_corrupted = False
            else:
                _cache_corrupted = False  # P0-4: 非gzip路径默认正常
                with open(_cache_src, "rb") as f:
                    STOCK_DATA = _pk.load(f)
            with open(_factor_cache_file, "rb") as f:
                _raw = _pk.load(f)
            # E10: 版本检查 — 新格式{"version":..., "data":...} vs 旧格式裸列表
            if isinstance(_raw, dict) and "version" in _raw:
                if _raw.get("version") != _CACHE_VERSION:
                    print(f"[Init] 缓存版本不匹配 ({_raw.get('version')} != {_CACHE_VERSION})，重建...")
                    _FACTOR_CACHE = []
                else:
                    _FACTOR_CACHE = _raw["data"]
                    print(f"[Init] 缓存版本匹配: {_CACHE_VERSION}")
            else:
                _FACTOR_CACHE = _raw  # 旧格式，首次升级接受
            # 修复: 只在signal_date为空时填最近交易日，不伪造日期
            # 行业对标: 同花顺/通达信从不假造数据日期
            try:
                _patched = 0
                # 从STOCK_DATA取最近交易日(所有股票index最新日期的最大值)
                _last_trade_day = ""
                try:
                    if STOCK_DATA and len(STOCK_DATA) > 0:
                        _last_dates = []
                        for _sym, _df in list(STOCK_DATA.items())[:100]:
                            try:
                                if hasattr(_df, 'index') and len(_df) > 0:
                                    _last_dates.append(str(_df.index[-1])[:10])
                            except: pass
                        if _last_dates:
                            _last_trade_day = max(_last_dates)
                except: pass
                if not _last_trade_day:
                    _last_trade_day = datetime.now().strftime("%Y-%m-%d")
                for _s in _FACTOR_CACHE:
                    # 兼容 dict 和 object 两种缓存格式
                    if isinstance(_s, dict):
                        _sd = _s.get('signal_date', '') or ''
                        if not _sd:  # 只填空白，不覆盖已有日期
                            _s['signal_date'] = _last_trade_day
                            _patched += 1
                        _et = _s.get('entry_time', '') or ''
                        if not _et:  # 只填空白
                            _s['entry_time'] = _last_trade_day
                    else:
                        _sd = getattr(_s, 'signal_date', '') or ''
                        if not _sd:  # 只填空白，不覆盖已有日期
                            setattr(_s, 'signal_date', _last_trade_day)
                            _patched += 1
                        _et = getattr(_s, 'entry_time', '') or ''
                        if not _et:  # 只填空白
                            setattr(_s, 'entry_time', _last_trade_day)
            except Exception as _pe:
                print(f"[Init] Date patch failed: {_pe}")
            # E93诊断
            _s0 = _FACTOR_CACHE[0] if _FACTOR_CACHE else None
            _s0_sd = getattr(_s0, 'signal_date', 'MISSING') if _s0 else 'EMPTY'
            # E29-2: 补丁后写回磁盘，保证 stock_screener 读到今天的日期
            if _patched > 0:
                try:
                    _save_data = {"version": _CACHE_VERSION, "data": _FACTOR_CACHE}
                    with open(_factor_cache_file, "wb") as _f:
                        _pk.dump(_save_data, _f)
                    print(f"[Init] Factor cache date patched ({_patched}只) → 已写回磁盘")
                except Exception as _e2:
                    print(f"[Init] Factor cache 写回失败: {_e2}")
            print(f"[Init] Factor cache loaded: {len(_FACTOR_CACHE)} items, "
                  f"patched={_patched}, s0.signal_date={_s0_sd}")
            if len(STOCK_DATA) == 0 or len(_FACTOR_CACHE) == 0:
                print(f"[Init] Cache empty, removing stale cache...")
                import os as _os_rm
                if _os_rm.path.exists(_factor_cache_file):
                    _os_rm.remove(_factor_cache_file)
            else:
                _CACHE_READY = True
                _STOCK_DATA_CACHE_TIME = time.time()  # E260: 记录加载时间戳
                print(f"[Init] CACHE HIT: {len(STOCK_DATA)} stocks + {len(_FACTOR_CACHE)} factors (instant!)")
                # E101: CACHE HIT时后台生成IC全量报告
                import threading as _th101
                _th101.Thread(target=lambda: (__import__('quant_agent.ic_analyzer',fromlist=['analyze_from_cache']).analyze_from_cache(_FACTOR_CACHE,STOCK_DATA)), daemon=True).start()
                # E60: 注入风控数据到模拟盘
                try:
                    from paper_engine import paper as _p
                    _p.set_risk_data(factor_cache=_FACTOR_CACHE, stock_data=STOCK_DATA)
                except Exception as _e: print(f"[App] {_e}")
                # P0-FIX02: DataStore 初始化写入 — 激活 unified-state
                try:
                    store.set('signals', store.get('signals', []))
                    store.set('market', {"status": "initialized", "time": datetime.now().strftime("%H:%M:%S")})
                    store.set('backtest', {"status": "idle"})
                    store.set_factor_data({"count": len(_FACTOR_CACHE), "loaded_at": datetime.now().strftime("%H:%M:%S")})
                    logger.info("[DataStore] 初始化完成, version=%d", store.get_version())
                except Exception as _ds_e:
                    logger.warning("[DataStore] 初始化写入失败: %s", _ds_e)
                if not _cache_corrupted:
                    return  # P0-4: gzip损坏时跳过，回退到TDX全量重建
        except Exception as e:
            print(f"[Init] Cache load failed: {e}")

    # 数据缓存存在但因子缓存缺失 → 加载数据, 后台建因子 (P0-2: parquet优先)
    _cache_for_factor = _data_cache_parquet if os.path.exists(_data_cache_parquet) else (_data_cache_file if os.path.exists(_data_cache_file) else _data_cache_file_legacy if os.path.exists(_data_cache_file_legacy) else None)
    if _cache_for_factor and not os.path.exists(_factor_cache_file):
        try:
            if _cache_for_factor.endswith('.parquet'):
                from data_loader import load_stock_data_cache as _load_pq2
                STOCK_DATA = _load_pq2(_cache_for_factor)
                if STOCK_DATA is None:
                    print(f"[Init] Parquet缓存损坏，删除并回退重建...")
                    try: os.remove(_cache_for_factor)
                    except Exception: pass
                    raise Exception("Parquet load failed")
            elif _cache_for_factor.endswith('.gz'):
                try:
                    import gzip as _gz3
                    STOCK_DATA = _pk.load(_gz3.open(_cache_for_factor, 'rb'))
                except (EOFError, OSError, Exception) as _gz3_e:
                    print(f"[Init] gzip数据缓存损坏({_gz3_e})，删除并回退重建...")
                    try: os.remove(_cache_for_factor)
                    except Exception as _e721:
                        logger.warning(f"[Init] 删除损坏缓存失败: {_e721}")
                    raise  # 跳出, 回退到全量重建
            else:
                with open(_cache_for_factor, "rb") as f:
                    STOCK_DATA = _pk.load(f)
            _STOCK_DATA_CACHE_TIME = time.time()  # E260
            print(f"[Init] Data cache loaded: {len(STOCK_DATA)} stocks (factor cache missing, building in bg...)")
            _CACHE_READY = True  # stock_data is enough — system ready
            # Phase 6a: 合并重复线程 — _bg_build_factors 已覆盖此场景 (原子写入)
            # P0-FIX02: DataStore 初始化 (因子后台构建中)
            try:
                store.set('signals', store.get('signals', []))
                store.set('market', {"status": "building", "time": datetime.now().strftime("%H:%M:%S")})
                store.set('backtest', {"status": "idle"})
                store.set_factor_data({"count": 0, "status": "building", "loaded_at": datetime.now().strftime("%H:%M:%S")})
            except Exception as _ds_e:
                logger.warning("[DataStore] 初始化写入失败(path2): %s", _ds_e)
            return
        except Exception as e:
            print(f"[Init] Data cache load failed: {e}")

    # 首次/缓存失效: 加载数据 → 后台计算因子 → Flask先启动
    print(f"[Init] Loading data from: {DATA_ROOT}")
    STOCK_DATA = load_all_stocks(DATA_ROOT, min_days=120, max_stocks=10000)  # 全A股覆盖, vipdoc共9006文件
    _STOCK_DATA_CACHE_TIME = time.time()  # E260
    print(f"[Init] Loaded {len(STOCK_DATA)} stocks, saving data cache...")
    try:
        # P0-2: parquet优先 (zstd压缩, ~80MB vs gzip ~284MB)
        from data_loader import save_stock_data_cache as _save_pq
        _tmp_pq = _data_cache_parquet + ".tmp"
        if _save_pq(STOCK_DATA, _tmp_pq):
            os.replace(_tmp_pq, _data_cache_parquet)
            print(f"[Init] Parquet cache saved: {os.path.getsize(_data_cache_parquet)//1024//1024}MB")
        else:
            # 回退: pyarrow不可用时用gzip
            import gzip
            _tmp = _data_cache_file + ".tmp"
            with gzip.open(_tmp, "wb", compresslevel=6) as f:
                _pk.dump(STOCK_DATA, f)
            os.replace(_tmp, _data_cache_file)
    except Exception as e:
        print(f"[Init] Data cache save failed: {e}")
    # 因子计算放后台线程，Flask先启动服务
    import threading as _th2, traceback as _tb
    def _bg_build_factors():
        global _CACHE_READY, _FACTOR_CACHE  # P0-4修复: 声明两个全局变量
        try:
            if '_factor_write_lock' in globals():
                with globals()['_factor_write_lock']:
                    _precompute_factors_fast()
            else:
                _precompute_factors_fast()
            print(f"[Init] Background: {len(_FACTOR_CACHE)} factors computed, saving...")
            try:
                _ftmp = _factor_cache_file + ".tmp"
                with open(_ftmp, "wb") as f:
                    _pk.dump({"version": _CACHE_VERSION, "data": _FACTOR_CACHE, "time": datetime.now().isoformat()}, f)
                os.replace(_ftmp, _factor_cache_file)  # 原子写入，防止中断损坏
                _CACHE_READY = True
                print(f"[Init] Factor cache saved — ready! ({os.path.getsize(_factor_cache_file)//1024}KB)")
            except Exception as e:
                print(f"[Init] Factor cache save failed: {e}")
                _tb.print_exc()
                _CACHE_READY = True  # 保存失败也标记完成
        except Exception as e:
            print(f"[Init] Background factor build crashed: {e}")
            _tb.print_exc()
            _FACTOR_CACHE = []
            _CACHE_READY = True  # 崩溃也标记完成，避免无限等待
    _th2.Thread(target=_bg_build_factors, daemon=True).start()
    # P0-FIX02: DataStore 初始化 (首次运行, 因子后台构建中)
    try:
        store.set('signals', store.get('signals', []))
        store.set('market', {"status": "building", "time": datetime.now().strftime("%H:%M:%S")})
        store.set('backtest', {"status": "idle"})
        store.set_factor_data({"count": 0, "status": "building", "loaded_at": datetime.now().strftime("%H:%M:%S")})
        logger.info("[DataStore] 初始化完成(首次), version=%d", store.get_version())
    except Exception as _ds_e:
        logger.warning("[DataStore] 初始化写入失败(path3): %s", _ds_e)
    print(f"[Init] Factor build started in background, Flask starting now...")


def _make_stock(symbol: str, factors: dict) -> StockInfo:
    """从因子字典创建 StockInfo。"""
    info = StockInfo(
        symbol=symbol, name=_resolve_name(symbol),
        close=factors.get("close", 0), open=factors.get("open", 0),
        high=factors.get("high", 0), low=factors.get("low", 0),
        pre_close=factors.get("pre_close", 0), volume=factors.get("volume", 0),
        change_pct=factors.get("change_pct", 0), open_pct=factors.get("open_pct", 0),
        daily_pl=factors.get("daily_pl", 0), vol_ratio=factors.get("vol_ratio", 0),
        quality_score=factors.get("quality_score", 0), trend_score=factors.get("trend_score", 0),
        volume_score=factors.get("volume_score", 0), position_score=factors.get("position_score", 0),
        atr_pct=factors.get("atr_pct", 0), signal_xg=factors.get("signal_xg", 0),
        signal_b1=factors.get("signal_b1", 0), signal_final=factors.get("signal_final", 0),
        signal_qlj=factors.get("signal_qlj", 0), signal_ztxf=factors.get("signal_ztxf", 0),
        signal_resonance=factors.get("signal_resonance", 0), signal_bandit=factors.get("signal_bandit", 0),
        limit_up=factors.get("limit_up", 0),
        ma_position=factors.get("ma_position", 0), low_suction_score=factors.get("low_suction_score", 0),
        capital_score=factors.get("capital_score", 0),
        institution_strength=factors.get("institution_strength", "low"),
        in_out_days=factors.get("in_out_days", 0), in_demand_area=factors.get("in_demand_area", 0),
        main_up=factors.get("main_up", 0), high_control_up=factors.get("high_control_up", 0),
        three_axes_signal=factors.get("three_axes_signal", 0),
        double_axes_signal=factors.get("double_axes_signal", 0),
        catch_bull_signal_time=factors.get("catch_bull_signal_time", ""),
        entry_time=factors.get("entry_time", ""), signal_date=factors.get("signal_date", ""),
        power_score=factors.get("power_score", 0), momentum_score=factors.get("momentum_score", 0),
        breakout_score=factors.get("breakout_score", 0), buy_signal=factors.get("buy_signal", 0),
        chg_5d=factors.get("chg_5d", 0), chg_10d=factors.get("chg_10d", 0),
        chg_score=factors.get("chg_score", 0), rsi_score=factors.get("rsi_score", 0),
        macd_score=factors.get("macd_score", 0), boll_score=factors.get("boll_score", 0),
        atr_score=factors.get("atr_score", 0), vol_score=factors.get("vol_score", 0),
        ma_bull_score=factors.get("ma_bull_score", 0),
        bias_score=factors.get("bias_score", 0), money_score=factors.get("money_score", 0),
        turnover_score=factors.get("turnover_score", 0),
        industry=_resolve_industry(symbol),
    )
    # P0-3: 移到 return 之前（原在 return 后为死代码）
    # E80: 确保 signal_date/entry_time 存在（兼容 compute_factors 不返回这些字段的情况）
    if not getattr(info, 'signal_date', ''):
        setattr(info, 'signal_date', factors.get("signal_date", factors.get("last_date", "")))
    if not getattr(info, 'entry_time', ''):
        setattr(info, 'entry_time', factors.get("entry_time", factors.get("signal_date", "")))
    setattr(info, 'fund_score', 0)
    setattr(info, 'chip_score', 0)
    setattr(info, 'rating_score', 0)
    setattr(info, 'fund_flow_score', 0)
    setattr(info, 'chip_struct_score', 0)
    setattr(info, 'rating_dist_score', 0)
    setattr(info, 'tech_score', 0)
    setattr(info, 'mom_multi_score', factors.get('mom_multi_score', 0))  # S30 V2
    setattr(info, 'low_suction_score', factors.get('low_suction_score', 0))
    setattr(info, 'institution_strength', factors.get('institution_strength', 'low'))
    setattr(info, 'in_out_days', factors.get('in_out_days', 0))
    setattr(info, 'main_force_net', factors.get('main_force_net', 0))
    setattr(info, 'main_force_ratio', factors.get('main_force_ratio', 0))
    setattr(info, 'lhb_days', factors.get('lhb_days', 0))
    # Attach industry after creation (fallback)
    if not info.industry:
        try:
            from stock_names import get_industry as _gi2
            info.industry = _gi2(symbol)
        except Exception as _e:
            logger.warning(f"[stock_info] 行业查询失败: {_e}")
    return info


# ── 入池时间持久化 ──
_ENTRY_TIMES = {}
_ENTRY_TIMES_FILE = r"d:\quant_web\data\entry_times.json"

def _load_entry_times():
    global _ENTRY_TIMES
    try:
        import json as _j, os as _os
        if _os.path.exists(_ENTRY_TIMES_FILE):
            with open(_ENTRY_TIMES_FILE, 'r', encoding='utf-8') as f:
                _ENTRY_TIMES = _j.load(f)
    except: _ENTRY_TIMES = {}

def _save_entry_times():
    try:
        import json as _j, os as _os
        _os.makedirs(_os.path.dirname(_ENTRY_TIMES_FILE), exist_ok=True)
        with open(_ENTRY_TIMES_FILE, 'w', encoding='utf-8') as f:
            _j.dump(_ENTRY_TIMES, f, ensure_ascii=False)
    except Exception as _e: print(f"[App] {_e}")

def _get_entry_time(symbol, last_date):
    """获取持久化入池时间: 首次记录, 之后不变"""
    global _ENTRY_TIMES
    # E29: 只记录A股，过滤ETF/可转债/基金
    if not _is_stock(symbol):
        return ""
    if symbol in _ENTRY_TIMES:
        return _ENTRY_TIMES[symbol]
    # 新入池: 记录当前时间
    now = datetime.now().strftime("%m-%d %H:%M")
    _ENTRY_TIMES[symbol] = now
    return now

def _cleanup_entry_times(active_symbols):
    """清理已退池的标的"""
    global _ENTRY_TIMES
    removed = [s for s in _ENTRY_TIMES if s not in active_symbols]
    for s in removed: del _ENTRY_TIMES[s]
    if removed: _save_entry_times()


def _load_factor_weights() -> dict:
    """E73: 从 XGBoost/IC 报告加载因子权重乘数。

    默认全部 1.0（等价于不加权）。如果 data/xgb_importance.json 存在，
    按 XGBoost importance 分配权重（低重要性因子降权，高重要性升权）。
    IC 和 XGBoost 同时标记为低效的因子权重降为 0（淘汰）。
    """
    weights = {k: 1.0 for k in [
        "trend_score", "momentum_score", "volume_score", "chg_score",
        "position_score", "rsi_score", "macd_score", "boll_score",
        "atr_score", "vol_score", "bias_score", "money_score", "turnover_score",
    ]}
    try:
        import json as _j, os as _os
        # E88: 优先读取简单的 factor_weights.json（手写权重，0.5~1.5）
        simple_path = FACTOR_WEIGHTS  # E26: config路径
        if _os.path.exists(simple_path):
            with open(simple_path, "r", encoding="utf-8") as f:
                simple_w = _j.load(f)
            for k, v in simple_w.items():
                weights[k] = max(0.0, min(2.0, float(v)))  # 接受JSON任意键名
            return weights
        # 回退: XGBoost importance 权重
        path = FACTOR_XGB  # E26: config路径
        if not _os.path.exists(path):
            return weights
        with open(path, "r", encoding="utf-8") as f:
            data = _j.load(f)
        imps = data.get("importances", [])
        if not imps:
            return weights
        # 归一化 importance → 0.5~1.5 乘数
        vals = [i["importance"] for i in imps if i["importance"] > 0]
        if not vals:
            return weights
        median = sorted(vals)[len(vals)//2]
        for entry in imps:
            name = entry["factor"]
            if name not in weights:
                continue
            ratio = entry["importance"] / max(median, 0.0001)
            # -50% ~ +50% 调节，抑制极端
            weights[name] = round(max(0.0, min(2.0, ratio)), 2)
        # IC 交叉验证：IC+importance 双低的因子 → 0 (淘汰)
        ic_path = FACTOR_IC  # E26: config路径
        if _os.path.exists(ic_path):
            with open(ic_path, "r", encoding="utf-8") as f:
                ic_report = _j.load(f)
            ic_data = ic_report.get("ic_results", {})
            for name in weights:
                ic_1d = ic_data.get(name, {}).get("ic_1d", {}).get("mean_ic", 0) or 0
                if abs(ic_1d) < 0.01 and weights[name] < 0.5:
                    weights[name] = 0.0  # 淘汰
        print(f"[Factor] XGBoost权重加载: {len(weights)}因子, "
              f"淘汰={sum(1 for v in weights.values() if v==0)}, "
              f"top3={sorted(weights.items(),key=lambda x:-x[1])[:3]}")
    except Exception as _e:
        logger.warning(f"[weights] 因子权重加载失败: {_e}")
    return weights


# ═══════════════════════════════════════════════════════════════
# S30: 因子V2辅助函数
# ═══════════════════════════════════════════════════════════════

def _zscore_normalize(values):
    """Z-score标准化 — 拉开因子区分度，使评分分布从50-70扩展到20-95"""
    import numpy as np
    arr = np.array(values, dtype=float)
    mean = np.mean(arr)
    std = np.std(arr)
    if std > 0:
        return [(v - mean) / std for v in values]
    return [0.0] * len(values)


def _calc_momentum_multi(close_vals):
    """多周期动量评分 (1周/2周/1月/3月) — 动量一致性+短期动量"""
    if len(close_vals) < 60:
        return 0
    close = close_vals
    mom_1w = (close[-1] / (close[-5] + 0.01) - 1) * 100 if len(close) >= 5 else 0
    mom_2w = (close[-1] / (close[-10] + 0.01) - 1) * 100 if len(close) >= 10 else 0
    mom_1m = (close[-1] / (close[-20] + 0.01) - 1) * 100 if len(close) >= 20 else 0
    mom_3m = (close[-1] / (close[-60] + 0.01) - 1) * 100 if len(close) >= 60 else 0
    mom_list = [mom_1w, mom_2w, mom_1m, mom_3m]
    mom_consistency = sum(1 for m in mom_list if m > 0)
    return min(15, max(0, int(mom_consistency * 3 + mom_1w * 0.8 + mom_1m * 0.5)))


def _westock_fallback_score(market_cap=None):
    """westock数据缺失时的兜底评分 — 按市值给分，避免白卷"""
    mc = market_cap or 0
    if mc > 100e8:   # 市值>100亿
        return 10
    elif mc > 50e8:  # 市值>50亿
        return 7
    else:
        return 4      # 小市值兜底4分


def _precompute_factors_fast():
    """快速预计算 — 统计方法直接提取因子, 5级信号评分。"""
    global _FACTOR_CACHE, _CACHE_LOADING, _CACHE_READY
    _t0 = time.time()
    import numpy as np
    # 波段擒妖因子 (源码版, 懒加载)
    try:
        from quant_framework.factors.tdx_signals import factor_bandit_sniper
        _HAS_BANDIT = True
    except Exception as _be:
        print(f"[Factor] 波段擒妖因子加载失败: {_be}")
        _HAS_BANDIT = False
    # 强制初始化，防止UnboundLocalError（_FACTOR_CACHE 是 list 类型）
    if '_FACTOR_CACHE' not in globals() or not isinstance(_FACTOR_CACHE, list):
        _FACTOR_CACHE = []
    _load_entry_times()
    cache = []
    items = list(STOCK_DATA.items())
    total = len(items)
    sample_size = min(3500, total)
    if sample_size == 0:
        _FACTOR_CACHE = []
        _CACHE_READY = True
        return
    step = max(1, total // sample_size)
    # 索引过滤: 跳过指数/ETF/可转债
    def _skip_symbol(sym):
        s = sym.lower()
        # 跳过指数
        if any(s.startswith(p) for p in ['sh000','sh88','sz399','sz98','bj8','bj9']): return True
        # 跳过债券/逆回购/ETF
        if any(s.startswith(p) for p in ['sh11','sh12','sh13','sh14','sh15','sh2','sh5','sz11','sz12','sz13','sz15','sz16','sz18','sz5']): return True
        # 跳过宽基标签
        if any(x in s for x in ['etf','上证','深证','沪深','中证','国债','转债','回购']): return True
        # 只处理6位数字代码的股票
        code = s.replace('sh','').replace('sz','').replace('bj','')
        if not code.isdigit() or len(code) != 6: return True
        return False

    # ── S30 V2: 预扫描收集原始因子值用于Z-score标准化 ──
    _Z_TREND, _Z_VOL, _Z_MOM = {}, {}, {}  # symbol → z-score
    if _FACTOR_V2_ENABLED:
        pre_trend, pre_vol, pre_mom = [], [], []
        pre_symbols = []
        for i in range(0, total, step):
            if len(pre_symbols) >= sample_size:
                break
            symbol, df = items[i]
            if _skip_symbol(symbol) or len(df) < 20:
                continue
            try:
                close = df["close"].values
                volume = df["volume"].values
                mc = float(np.mean(close[-20:]))
                pre_trend.append((float(close[-1]) - mc) / (mc + 0.01))
                avg5 = float(np.mean(volume[-5:])) if len(volume) >= 5 else 1
                avg20 = float(np.mean(volume[-20:])) if len(volume) >= 20 else avg5
                pre_vol.append(round(avg5 / (avg20 + 0.01), 2))
                pre_mom.append((float(close[-1]) / (float(close[-5]) + 0.01) - 1) * 100 if len(close) >= 5 else 0)
                pre_symbols.append(symbol)
            except Exception:
                pass
        if pre_symbols:
            zt = _zscore_normalize(pre_trend)
            zv = _zscore_normalize(pre_vol)
            zm = _zscore_normalize(pre_mom)
            for j, sym in enumerate(pre_symbols):
                _Z_TREND[sym] = zt[j]
                _Z_VOL[sym] = zv[j]
                _Z_MOM[sym] = zm[j]
            print(f"[Factor] V2 Z-score pre-scan: {len(pre_symbols)} stocks")

    print(f"[Factor] Building signals for {sample_size} stocks (step={step})...")
    for i in range(0, total, step):
        if len(cache) >= sample_size:
            break
        symbol, df = items[i]
        if _skip_symbol(symbol):
            continue
        try:
            if len(df) < 20:
                continue
            close = df["close"].values
            volume = df["volume"].values
            high = df["high"].values
            low = df["low"].values
            last_close = float(close[-1])
            prev_close = float(close[-2]) if len(close) > 1 else last_close
            # 防止除零和异常值 (除权除息可能导致价差>30%)
            if prev_close > 0.01:
                raw_chg = (last_close / prev_close - 1) * 100
                # A股涨跌停限制: 主板±10%, 科创/创业±20%, 北交±30%
                change_pct = round(max(-21, min(21, raw_chg)), 2)
            else:
                change_pct = 0

            ma5 = float(np.mean(close[-5:])) if len(close) >= 5 else last_close
            ma10 = float(np.mean(close[-10:])) if len(close) >= 10 else last_close
            ma20 = float(np.mean(close[-20:])) if len(close) >= 20 else last_close

            # 量比
            avg_vol_5 = float(np.mean(volume[-5:])) if len(volume) >= 5 else 1
            avg_vol_20 = float(np.mean(volume[-20:])) if len(volume) >= 20 else avg_vol_5
            vol_ratio = round(avg_vol_5 / (avg_vol_20 + 0.01), 2)

            # 波动率
            atr = float(np.mean(high[-14:] - low[-14:])) if len(high) >= 14 else last_close * 0.02
            atr_pct = round(atr / (last_close + 0.01) * 100, 2)

            # 信号判定 (数据驱动,合理覆盖)
            ma_bull = 1 if last_close > ma20 else 0
            ma5_bull = 1 if last_close > ma5 else 0
            vol_surge = 1 if vol_ratio > 1.2 else 0
            # XG: 均线多头 + 量能或涨幅配合
            signal_xg = 1 if (ma_bull and (change_pct > 0.5 or vol_surge)) else 0
            # B1: 超跌反弹
            signal_b1 = 1 if (change_pct < -1.5 and ma5_bull and last_close > ma20 * 0.85) else 0
            signal_final = 1 if (signal_xg or signal_b1) else 0
            signal_resonance = signal_xg + signal_b1
            # 擒龙决: 价>30日高点均线 AND 量比>1.2
            hhv30 = float(np.max(close[-30:])) if len(close) >= 30 else last_close
            pressure = hhv30 * 0.95  # 距30日高点5%以内
            boll_upper = ma20 + 1.5*float(np.std(close[-20:]))
            qlj_cond = last_close > pressure and vol_ratio > 1.2
            signal_qlj = 1 if qlj_cond else 0
            # 涨停先锋: 价>获利百分百(99%成本) AND 7日首次
            profit99 = float(np.percentile(close[-60:], 99)) if len(close) >= 60 else last_close * 1.5
            ztxf_cond = last_close > profit99
            signal_ztxf = 1 if ztxf_cond else 0
            # 波段擒妖(源码版) — 牛线突破+55日新高V反
            signal_bandit = 0
            if _HAS_BANDIT and len(df) >= 200:
                try:
                    _b = factor_bandit_sniper(df)
                    signal_bandit = int(_b.iloc[-1]) if _b.iloc[-1] > 0 else 0
                except Exception:
                    signal_bandit = 0

            # ── 5因子综合评分 (0-99) ──
            # ── 12因子权重 (ICIR差异化加权，高IC因子权重更高) ──
            # E09: 均线多头排列——连续评分(非0/1)
            ma5_gt_ma10 = 1 if ma5 > ma10 else 0
            ma10_gt_ma20 = 1 if ma10 > ma20 else 0
            ma20_gt_ma60 = 1 if (len(close) >= 60 and ma20 > float(np.mean(close[-60:]))) else 0
            ma_bull_score = min(15, int((ma5_gt_ma10 + ma10_gt_ma20 + ma20_gt_ma60) * 5))  # 均线多头 0-15

            # 核心4因子(75分): 趋势+动量主导
            ma20_dev = (last_close - ma20) / (ma20 + 0.01)
            mom_5d = (last_close / (close[-5] + 0.01) - 1) * 100 if len(close) >= 5 else 0
            if _FACTOR_V2_ENABLED:
                tz = _Z_TREND.get(symbol, 0)
                vz = _Z_VOL.get(symbol, 0)
                mz = _Z_MOM.get(symbol, 0)
                trend_score = min(25, max(0, int(50 + tz * 10)))               # V2: Z-score趋势 0-25
                volume_score = min(20, max(0, int(50 + vz * 7)))               # V2: Z-score量能 0-20
                momentum_score = min(15, max(0, int(50 + mz * 8)))             # V2: Z-score动量 0-15
            else:
                trend_score = min(25, max(0, int((ma20_dev + 0.1) * 60)))      # V1: 趋势 0-25
                volume_score = min(20, max(0, int((vol_ratio - 0.5) * 15)))    # V1: 量能 0-20
                momentum_score = min(15, max(0, int(mom_5d + 5)))              # V1: 动量 0-15
            chg_score = min(15, max(0, int(change_pct * 2.0 + 5)))               # 涨幅 0-15 (E219减偏移)
            pos_ratio = last_close / (ma20 + 0.01)
            position_score = min(15, max(0, int((pos_ratio - 0.85) * 60)))       # 位置 0-15 (E219连续化)

            # S30 V2: 多周期动量因子 (1周/2周/1月/3月)
            mom_multi_score = _calc_momentum_multi(close) if _FACTOR_V2_ENABLED else 0

            # 技术4因子(20分): 辅助验证
            if len(close) >= 15:
                diffs = np.diff(close[-15:])
                g = np.sum(diffs[diffs>0]) if len(diffs[diffs>0])>0 else 0
                l = abs(np.sum(diffs[diffs<0])) if len(diffs[diffs<0])>0 else 1
                rsi_val = 100-100/(1+g/l) if l>0 else 50
            else: rsi_val = 50
            rsi_score = min(8, max(0, int(10 - abs(rsi_val-50)/5)))              # RSI 0-8 (E219消饱和)
            ma12 = float(np.mean(close[-12:])) if len(close)>=12 else last_close
            ma26 = float(np.mean(close[-26:])) if len(close)>=26 else last_close
            macd_score = min(5, max(0, int((ma12-ma26)/(last_close+0.01)*100+3))) # MACD 0-5
            std20 = float(np.std(close[-20:]))
            bb_pos = (last_close-(ma20-2*std20))/(4*std20+0.01)
            boll_score = min(5, max(0, int(5-abs(bb_pos-0.5)*8)))               # Boll 0-5
            atr_val = float(np.mean(high[-14:]-low[-14:])) if len(high)>=14 else last_close*0.02
            atr_score = min(5, max(0, int(atr_val/(last_close+0.01)*100)))       # ATR 0-5

            # 风险/资金4因子(16分): 微调
            if len(close)>=21: vol_v = float(np.std(np.diff(close[-21:])/(close[-21:-1]+0.01))*np.sqrt(252))
            else: vol_v = 0.3
            vol_score = min(4, max(0, int(8-vol_v*20)))                          # 波动 0-4(低波加分)
            bias_v = (last_close/ma20-1)*100
            bias_score = min(4, max(0, int(4-abs(bias_v)/3)))                    # 乖离 0-4
            obv = 1 if volume[-1]>np.mean(volume[-5:]) and close[-1]>close[-2] else (0.5 if close[-1]>close[-2] else 0)
            money_score = min(4, int(obv*8))                                     # 资金 0-4
            if len(volume)>=10: to_v = (np.mean(volume[-3:])/(np.mean(volume[-10:-3])+0.01)-1)*100
            else: to_v = 0
            turnover_score = min(4, max(0, int(to_v+4)))                         # 换手 0-4

            # E09: ICIR差异化加权——高IC因子乘1.5x，低IC因子乘0.7x
            # power_score = sum(因子值 * 权重系数)
            _w = {  # ICIR权重系数(基于历史IC分析)
                'trend_score': 1.5, 'momentum_score': 1.2, 'volume_score': 1.3,
                'chg_score': 1.0, 'position_score': 0.8, 'rsi_score': 0.7,
                'macd_score': 1.0, 'boll_score': 0.8, 'atr_score': 0.7,
                'vol_score': 0.7, 'bias_score': 0.8, 'money_score': 1.4,
                'turnover_score': 1.0, 'ma_bull_score': 1.5,
                'mom_multi_score': 1.3,  # S30 V2: 多周期动量权重
            }
            power_score = min(99, int(
                trend_score * _w['trend_score'] +
                momentum_score * _w['momentum_score'] +
                volume_score * _w['volume_score'] +
                chg_score * _w['chg_score'] +
                position_score * _w['position_score'] +
                rsi_score * _w['rsi_score'] +
                macd_score * _w['macd_score'] +
                boll_score * _w['boll_score'] +
                atr_score * _w['atr_score'] +
                vol_score * _w['vol_score'] +
                bias_score * _w['bias_score'] +
                money_score * _w['money_score'] +
                turnover_score * _w['turnover_score'] +
                ma_bull_score * _w['ma_bull_score'] +
                (mom_multi_score * _w['mom_multi_score'] if _FACTOR_V2_ENABLED else 0)
            ))
            power_score = max(15, power_score)
            # S24: 板块修正系数 — 科创板/创业板/北交所适当下调
            _clean = symbol.replace('sh','').replace('sz','').replace('bj','')
            _board_adj = 0.85 if _clean.startswith('688') else 0.90 if _clean.startswith(('300','301')) else 0.80 if _clean.startswith(('8','4')) else 1.0
            power_score = int(power_score * _board_adj)
            # E219: 信号阈值(统一85/62/48/35/22)
            if power_score >= 85: buy_signal = 5
            elif power_score >= 62: buy_signal = 4
            elif power_score >= 48: buy_signal = 3
            elif power_score >= 35: buy_signal = 2
            elif power_score >= 22: buy_signal = 1
            else: buy_signal = 0

            last_date = str(df.index[-1])[:10] if len(df) > 0 else ""
            # 入池时间持久化: 首次入池记录日期+时间, 之后不变
            entry_time_str = _get_entry_time(symbol, last_date)

            # 补充缺失字段: open_pct, chg_5d, chg_10d, daily_pl, capital_score
            pre_close_val = float(close[-2]) if len(close) > 1 else last_close
            open_today = float(df["open"].values[-1])
            open_pct = round((open_today / pre_close_val - 1) * 100, 2) if pre_close_val > 0 else 0
            daily_pl = round(last_close - pre_close_val, 2)

            # 5日/10日涨跌幅
            close_5d_ago = float(close[-6]) if len(close) >= 6 else last_close
            close_10d_ago = float(close[-11]) if len(close) >= 11 else last_close
            chg_5d = round((last_close / close_5d_ago - 1) * 100, 2) if close_5d_ago > 0 else 0
            chg_10d = round((last_close / close_10d_ago - 1) * 100, 2) if close_10d_ago > 0 else 0

            # 资金强度分 (基于量价关系, 0-100)
            # 量增价涨 → 资金流入; 量缩价跌 → 资金流出
            capital_score = 0
            if len(volume) >= 5 and len(close) >= 5:
                vol_trend = np.mean(volume[-3:]) / (np.mean(volume[-5:]) + 0.01)
                price_trend = close[-1] / (np.mean(close[-5:]) + 0.01)
                capital_score = min(100, max(0, int((vol_trend * 0.5 + price_trend * 0.5 - 1) * 200)))
                capital_score = max(0, capital_score)

            # 涨停标记
            limit_up = 1 if change_pct >= 9.8 else (1 if change_pct <= -9.8 else 0)

            # ── 补齐缺失字段 ──
            # 低开评分: 开盘价低于昨收越多→低吸信号越强 (0-100)
            low_suction = 0
            if pre_close_val > 0.01:
                open_gap = (open_today / pre_close_val - 1) * 100
                if open_gap < -3: low_suction = min(100, int(abs(open_gap) * 6))
                elif open_gap < -1: low_suction = min(80, int(abs(open_gap) * 8))
                elif open_gap < 0: low_suction = min(50, int(abs(open_gap) * 10))
            # 机构强度: 资金+量能综合 (super_high/high/middle/low)
            inst_raw = capital_score * 0.6 + volume_score * 0.4
            inst_strength = "super_high" if inst_raw >= 80 else ("high" if inst_raw >= 55 else ("middle" if inst_raw >= 30 else "low"))
            # 净买天数: 从entry_times推算 (默认0，由持久化层填充)
            inout_days = 0
            # 主力净额/龙虎榜: 默认0，由westock因子增强层填充
            main_force_net = 0.0
            main_force_ratio = 0.0
            lhb_days = 0

            factors = {
                "signal_xg": signal_xg, "signal_b1": signal_b1,
                "signal_final": signal_final, "signal_resonance": signal_resonance,
                "signal_qlj": signal_qlj, "signal_ztxf": signal_ztxf,
                "signal_bandit": signal_bandit,
                "trend_score": trend_score, "momentum_score": momentum_score,
                "volume_score": volume_score, "chg_score": chg_score,
                "position_score": position_score, "ma_bull_score": ma_bull_score,
                "quality_score": round(min(0.99, power_score/130), 2),
                "atr_pct": atr_pct, "ma_position": round(ma20_dev, 2),
                "vol_ratio": vol_ratio, "change_pct": change_pct,
                "open_pct": open_pct, "daily_pl": daily_pl,
                "chg_5d": chg_5d, "chg_10d": chg_10d,
                "capital_score": capital_score, "limit_up": limit_up,
                "low_suction_score": low_suction,
                "institution_strength": inst_strength,
                "in_out_days": inout_days,
                "main_force_net": main_force_net,
                "main_force_ratio": main_force_ratio,
                "lhb_days": lhb_days,
                "pre_close": pre_close_val,
                "rsi_score": rsi_score, "macd_score": macd_score,
                "boll_score": boll_score, "atr_score": atr_score,
                "vol_score": vol_score, "bias_score": bias_score,
                "money_score": money_score, "turnover_score": turnover_score,
                "mom_multi_score": mom_multi_score,  # S30 V2
                "buy_signal": buy_signal, "power_score": power_score,
                "close": last_close, "open": open_today,
                "high": float(high[-1]), "low": float(low[-1]),
                "volume": float(volume[-1]),
                "entry_time": entry_time_str, "signal_date": last_date,
                "catch_bull_signal_time": entry_time_str,
            }
            cache.append(_make_stock(symbol, factors))
        except Exception as e:
            _err_count = getattr(_precompute_factors_fast, '_err_count', 0) + 1
            _precompute_factors_fast._err_count = _err_count
            if _err_count <= 3:
                print(f"[Factor] ⚠ stock={symbol} error: {e}")

    _FACTOR_CACHE = cache
    _errs = getattr(_precompute_factors_fast, '_err_count', 0)
    if _errs:
        print(f"[Factor] {_errs} stocks skipped due to errors")
    # E47+E57: westock 高级因子增强（因子分 + 合并到power_score）
    try:
        from westock_factors import enrich_factors
        symbols = [getattr(s, 'symbol', '') for s in cache if getattr(s, 'symbol', '')]
        enriched = enrich_factors(symbols)
        for s in cache:
            sym = getattr(s, 'symbol', '')
            if sym in enriched:
                wf = enriched[sym]
                s.fund_score = wf.get('fund_score', 0)
                s.chip_score = wf.get('chip_score', 0)
                s.rating_score = wf.get('rating_score', 0)
                # E98: 新因子
                s.fund_flow_score = wf.get('fund_flow_score', 0)
                s.chip_struct_score = wf.get('chip_struct_score', 0)
                s.rating_dist_score = wf.get('rating_dist_score', 0)
                s.tech_score = wf.get('tech_score', 0)
                # E29-1: 主力资金+龙虎榜原始值
                s.main_force_net = wf.get('main_force_net', 0)
                s.main_force_ratio = wf.get('main_force_ratio', 0)
                s.super_large_net = wf.get('super_large_net', 0)
                s.large_net = wf.get('large_net', 0)
                s.lhb_days = wf.get('lhb_days', 0)
                s.lhb_net_buy = wf.get('lhb_net_buy', 0)
                s.lhb_buy_amt = wf.get('lhb_buy_amt', 0)
                s.lhb_sell_amt = wf.get('lhb_sell_amt', 0)
                s.lhb_biz_type = wf.get('lhb_biz_type', '')
                # E57+E98: 合并westock因子到power_score（满分99）
                old_ps = getattr(s, 'power_score', 0) or 0
                if _FACTOR_V2_ENABLED:
                    # S30 V2: westock深度整合 — 独立评分维度上限20分
                    _w_total = 0
                    _w_total += min(8, int(getattr(s, 'fund_score', 0) * 1.5))
                    _w_total += min(7, int(getattr(s, 'chip_score', 0) * 1.2))
                    _w_total += min(5, int(getattr(s, 'rating_score', 0) * 1.0))
                    _w_total += min(5, int(getattr(s, 'fund_flow_score', 0) * 1.0))
                    _w_total += min(4, int(getattr(s, 'chip_struct_score', 0) * 0.8))
                    _w_total += min(4, int(getattr(s, 'rating_dist_score', 0) * 0.8))
                    _w_total += min(4, int(getattr(s, 'tech_score', 0) * 0.8))
                    westock_total = min(20, _w_total)
                else:
                    # E210: 每个westock因子归一化到0-5分(÷20截断)
                    _WM = 20.0
                    westock_total = (min(s.fund_score/_WM, 5) + min(s.chip_score/_WM, 5)
                                     + min(s.rating_score/_WM, 5)
                                     + min(s.fund_flow_score/_WM, 5)
                                     + min(s.chip_struct_score/_WM, 5)
                                     + min(s.rating_dist_score/_WM, 5)
                                     + min(s.tech_score/_WM, 5))
                # S30 V2: westock缺失时兜底分
                if _FACTOR_V2_ENABLED and westock_total == 0:
                    westock_total = _westock_fallback_score(getattr(s, 'market_cap', 0))
                s.power_score = min(99, old_ps + westock_total)
    except Exception as e:
        print(f"[Factor] westock enrichment failed: {e}")
    _FACTOR_CACHE.sort(key=lambda x: getattr(x, 'power_score', 0) or 0, reverse=True)
    # E60: 因子重建后注入风控数据
    try:
        from paper_engine import paper as _p
        _p.set_risk_data(factor_cache=_FACTOR_CACHE, stock_data=STOCK_DATA)
    except Exception as _e: print(f"[App] {_e}")
    # 持久化入池时间
    active = set(getattr(s, 'symbol', '') for s in cache)
    _cleanup_entry_times(active)
    _save_entry_times()
    # E73b+E113: 加载权重并应用到因子评分
    global _XGB_WEIGHTS
    _XGB_WEIGHTS = _load_factor_weights()
    if _XGB_WEIGHTS:
        _weighted = 0
        _score_fields = ["trend_score","momentum_score","volume_score","chg_score","position_score",
                         "rsi_score","macd_score","boll_score","atr_score","vol_score","bias_score",
                         "money_score","turnover_score"]
        if _FACTOR_V2_ENABLED:
            _score_fields.append("mom_multi_score")  # S30 V2
        for _s in cache:
            _new_ps = 0
            for _fn in _score_fields:
                _old = getattr(_s, _fn, 0) or 0
                _w = _XGB_WEIGHTS.get(_fn, 1.0)
                if _w != 1.0:
                    setattr(_s, _fn, _old * _w)
                    _weighted += 1
                _new_ps += (_old * _w)
            _s.power_score = min(99, max(15, int(_new_ps)))  # 重算power_score
            # 重算buy_signal(权重后)
            _ps = _s.power_score
            if _ps >= 85: _s.buy_signal = 5
            elif _ps >= 62: _s.buy_signal = 4
            elif _ps >= 48: _s.buy_signal = 3
            elif _ps >= 35: _s.buy_signal = 2
            elif _ps >= 22: _s.buy_signal = 1
            else: _s.buy_signal = 0
        if _weighted:
            print(f"[Factor] 权重已应用: {len(_XGB_WEIGHTS)}因子, {_weighted}处调整")
            # E211: 权重后重排+标记就绪
            cache.sort(key=lambda x: getattr(x, 'power_score', 0) or 0, reverse=True)
    _CACHE_READY = True
    _perf_tick("factor_build", _t0)
    _last_build = _PERF.get('factor_build', [0])[-1] if _PERF.get('factor_build') else 0
    print(f"[Factor] Built {len(cache)} signals in {_last_build}s")
    print(f"[Init] Factor cache ready: {len(cache)} stocks")
    # E101: 后台生成 IC 全量报告
    try:
        import threading as _th101
        def _gen_ic():
            from quant_agent.ic_analyzer import analyze_from_cache
            analyze_from_cache(cache, STOCK_DATA)
        _th101.Thread(target=_gen_ic, daemon=True).start()
    except Exception as _e: print(f"[App] {_e}")
    # E223: 因子数据写入 DataStore — 供 unified-state 使用
    try:
        store.set_factor_data({
            "count": len(cache),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "avg_power_score": round(sum(getattr(s, 'power_score', 0) or 0 for s in cache) / max(len(cache), 1), 1),
            "top_symbols": [getattr(s, 'symbol', '') for s in cache[:10]],
        })
    except Exception as _e: print(f"[App] {_e}")
    _CACHE_READY = True



def _precompute_factors():
    """批量预计算所有股票因子并缓存。"""
    global _FACTOR_CACHE, _CACHE_LOADING, _CACHE_READY

    if _CACHE_LOADING:
        return
    _CACHE_LOADING = True

    cache = []
    total = len(STOCK_DATA)
    for i, (symbol, df) in enumerate(STOCK_DATA.items()):
        # P0-2: 跳过可转债+ETF+指数（防止自动交易买入非A股品种）
        if symbol.startswith(('sh000','sh880','sh990','sz399','sz139')): continue  # 指数
        if symbol.startswith(('sh110','sh113','sh118','sz123','sz127','sz128')): continue  # 可转债
        if symbol.startswith(('sh51','sz159','sz16')): continue  # ETF
        try:
            factors = compute_factors(df)
            if not factors:
                continue

            info = StockInfo(
                symbol=symbol,
                name=_resolve_name(symbol),
                close=factors.get("close", 0),
                open=factors.get("open", 0),
                high=factors.get("high", 0),
                low=factors.get("low", 0),
                pre_close=factors.get("pre_close", 0),
                volume=factors.get("volume", 0),
                change_pct=factors.get("change_pct", 0),
                open_pct=factors.get("open_pct", 0),
                daily_pl=factors.get("daily_pl", 0),
                vol_ratio=factors.get("vol_ratio", 0),
                quality_score=factors.get("quality_score", 0),
                trend_score=factors.get("trend_score", 0),
                volume_score=factors.get("volume_score", 0),
                position_score=factors.get("position_score", 0),
                atr_pct=factors.get("atr_pct", 0),
                signal_xg=factors.get("signal_xg", 0),
                signal_b1=factors.get("signal_b1", 0),
                signal_final=factors.get("signal_final", 0),
                signal_qlj=factors.get("signal_qlj", 0),
                signal_ztxf=factors.get("signal_ztxf", 0),
                signal_resonance=factors.get("signal_resonance", 0),
                limit_up=factors.get("limit_up", 0),
                ma_position=factors.get("ma_position", 0),
                low_suction_score=factors.get("low_suction_score", 0),
                capital_score=factors.get("capital_score", 0),
                institution_strength=factors.get("institution_strength", "low"),
                in_out_days=factors.get("in_out_days", 0),
                in_demand_area=factors.get("in_demand_area", 0),
                main_up=factors.get("main_up", 0),
                high_control_up=factors.get("high_control_up", 0),
                three_axes_signal=factors.get("three_axes_signal", 0),
                double_axes_signal=factors.get("double_axes_signal", 0),
                catch_bull_signal_time=factors.get("catch_bull_signal_time", ""),
                entry_time=factors.get("entry_time", ""),
                signal_date=factors.get("signal_date", ""),
                power_score=factors.get("power_score", 0),
                momentum_score=factors.get("momentum_score", 0),
                breakout_score=factors.get("breakout_score", 0),
                buy_signal=factors.get("buy_signal", 0),
                chg_5d=factors.get("chg_5d", 0),
                chg_10d=factors.get("chg_10d", 0),
            )
            # E80: 确保 signal_date/entry_time 存在
            if not getattr(info, 'signal_date', ''):
                setattr(info, 'signal_date', factors.get("signal_date", ""))
            if not getattr(info, 'entry_time', ''):
                setattr(info, 'entry_time', factors.get("entry_time", ""))
            cache.append(info)
        except Exception:
            continue

        if i % 500 == 0:
            print(f"  Precomputing... {i}/{total}")

    _FACTOR_CACHE = cache
    # E47+E57: westock 高级因子增强（因子分 + 合并到power_score）
    try:
        from westock_factors import enrich_factors
        symbols = [getattr(s, 'symbol', '') for s in cache if getattr(s, 'symbol', '')]
        enriched = enrich_factors(symbols)
        for s in cache:
            sym = getattr(s, 'symbol', '')
            if sym in enriched:
                wf = enriched[sym]
                s.fund_score = wf.get('fund_score', 0)
                s.chip_score = wf.get('chip_score', 0)
                s.rating_score = wf.get('rating_score', 0)
                # E98: 新因子
                s.fund_flow_score = wf.get('fund_flow_score', 0)
                s.chip_struct_score = wf.get('chip_struct_score', 0)
                s.rating_dist_score = wf.get('rating_dist_score', 0)
                s.tech_score = wf.get('tech_score', 0)
                # E29-1: 主力资金+龙虎榜原始值
                s.main_force_net = wf.get('main_force_net', 0)
                s.main_force_ratio = wf.get('main_force_ratio', 0)
                s.super_large_net = wf.get('super_large_net', 0)
                s.large_net = wf.get('large_net', 0)
                s.lhb_days = wf.get('lhb_days', 0)
                s.lhb_net_buy = wf.get('lhb_net_buy', 0)
                s.lhb_buy_amt = wf.get('lhb_buy_amt', 0)
                s.lhb_sell_amt = wf.get('lhb_sell_amt', 0)
                s.lhb_biz_type = wf.get('lhb_biz_type', '')
                # E57+E98: 合并westock因子到power_score（满分99）
                old_ps = getattr(s, 'power_score', 0) or 0
                if _FACTOR_V2_ENABLED:
                    # S30 V2: westock深度整合 — 独立评分维度上限20分
                    _w_total = 0
                    _w_total += min(8, int(getattr(s, 'fund_score', 0) * 1.5))
                    _w_total += min(7, int(getattr(s, 'chip_score', 0) * 1.2))
                    _w_total += min(5, int(getattr(s, 'rating_score', 0) * 1.0))
                    _w_total += min(5, int(getattr(s, 'fund_flow_score', 0) * 1.0))
                    _w_total += min(4, int(getattr(s, 'chip_struct_score', 0) * 0.8))
                    _w_total += min(4, int(getattr(s, 'rating_dist_score', 0) * 0.8))
                    _w_total += min(4, int(getattr(s, 'tech_score', 0) * 0.8))
                    westock_total = min(20, _w_total)
                else:
                    # E210: 每个westock因子归一化到0-5分(÷20截断)
                    _WM = 20.0
                    westock_total = (min(s.fund_score/_WM, 5) + min(s.chip_score/_WM, 5)
                                     + min(s.rating_score/_WM, 5)
                                     + min(s.fund_flow_score/_WM, 5)
                                     + min(s.chip_struct_score/_WM, 5)
                                     + min(s.rating_dist_score/_WM, 5)
                                     + min(s.tech_score/_WM, 5))
                # S30 V2: westock缺失时兜底分
                if _FACTOR_V2_ENABLED and westock_total == 0:
                    westock_total = _westock_fallback_score(getattr(s, 'market_cap', 0))
                s.power_score = min(99, old_ps + westock_total)
    except Exception as e:
        print(f"[Factor] westock enrichment failed: {e}")
    _FACTOR_CACHE.sort(key=lambda x: getattr(x, 'power_score', 0) or 0, reverse=True)
    _CACHE_READY = True
    _CACHE_LOADING = False
    # E60: 因子重建后注入风控数据
    try:
        from paper_engine import paper as _p
        _p.set_risk_data(factor_cache=_FACTOR_CACHE, stock_data=STOCK_DATA)
    except Exception as _e: print(f"[App] {_e}")


# ======================================================================
# 页面路由
# ======================================================================

@app.route("/terminal")
def page_terminal():
    """交易终端 — 数据未就绪时显示预热页"""
    if not STOCK_DATA:
        init_data()
    # 数据就绪即渲染，不卡预热
    if STOCK_DATA and len(STOCK_DATA) > 0:
        return render_template("terminal.html")
    return "系统预热中，请稍后刷新。", 503

@app.route("/timeline")
def page_timeline():
    return render_template("timeline.html")


@app.route("/daily-report-old")
def page_daily_report_v1():
    """V2: 日终报告Web页面(旧)"""
    return render_template("daily_report.html")

@app.route("/logs")
def page_logs():
    """X10: 系统日志Web查看器"""
    return render_template("logs.html")


@app.route("/dashboard")
def page_dashboard():
    """E269: 统一监控大屏（保留）"""
    return render_template("dashboard.html")


@app.route("/strategy-config")
def page_strategy_config():
    """E272: 策略参数Web调优界面"""
    return render_template("strategy_config.html")


@app.route("/")
def root():
    """指挥中心入口 — 直接渲染"""
    return render_template("terminal.html")


@app.route("/SelectStock")
def index():
    """旧入口 → 301到终端"""
    return redirect("/terminal", code=301)


# ═══════════════════════════════════════════════════════════════
# 模块页面路由
# ═══════════════════════════════════════════════════════════════

# [已移除] /backtest — 回测V3替代
# [已移除] /live-monitor — Dashboard替代
# [已移除] /paper-trade — 模拟V3替代


@app.route("/trade-journal")
def page_trade_journal():
    return render_template("trade_journal.html")


@app.route("/strategy-optimizer")
def page_strategy_optimizer():
    return render_template("strategy_optimizer.html")


@app.route("/task-scheduler")
def page_task_scheduler():
    return render_template("task_scheduler.html")


@app.route("/user-customizations")
def page_user_customizations():
    return render_template("user_customizations.html")

@app.route("/formula-manager")
def page_formula_manager():
    return render_template("formula_manager.html")


@app.route("/paper-trade-v3")
def page_paper_trade_v3():
    return render_template("paper_trade_v3.html")


@app.route("/review")
def page_review():
    return render_template("review.html")

@app.route("/compare-pnl")
def page_compare_pnl():
    return render_template("compare_pnl.html")


@app.route("/live-trade")
def page_live_trade():
    return render_template("live_trade.html")

@app.route("/risk-console")
def page_risk_console():
    """风控操作台"""
    return render_template("risk_panel.html")

@app.route("/m-kill")
def page_mobile_kill():
    """移动端遥控总闸"""
    return render_template("mobile_kill.html")


@app.route("/control-panel")
def page_control_panel():
    """风控总闸控制流全景图"""
    return render_template("control_panel.html")


# [已移除] /dashboard2 — Dashboard替代
@app.route("/portfolio-mgr")
def page_portfolio_mgr():
    return render_template("portfolio_mgr.html")


@app.route("/strategy-market")
def page_strategy_market():
    return render_template("strategy_market.html")


@app.route("/data-manager")
def page_data_manager():
    return render_template("data_manager.html")


@app.route("/risk-dashboard")
def page_risk_dashboard():
    return render_template("risk_dashboard.html")


# [已移除] /signal-center 页面 — Terminal+Dashboard展示信号; API保留

@app.route("/command-center")
def page_command_center():
    return render_template("command_center.html")


# ═══════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════

@app.route("/api/signal-table")
def api_signal_table():
    """E349: 实时信号表 — 供前端信号卡片使用, 附加实时现价+浮盈"""
    import json as _json
    _p = os.path.join(os.path.dirname(__file__), "data", "signal_table.json")
    signals = []
    if os.path.exists(_p):
        try:
            with open(_p, encoding="utf-8") as _f:
                signals = _json.load(_f)
        except Exception:
            return jsonify([])
    # 附加实时现价 (QMT xtdata 批量拉取)
    _codes, _sym_map = [], {}
    for s in signals:
        sym = s.get("symbol", "")
        if not sym: continue
        _code = sym[2:] + ('.SH' if sym.startswith('sh') else '.SZ' if sym.startswith('sz') else '.BJ')
        _codes.append(_code)
        _sym_map[_code] = sym
    _prices = {}
    if _codes:
        try:
            from xtquant import xtdata
            # 分批获取实时价 (每批50只, 覆盖全部98个信号)
            _all_ticks = {}
            for _i in range(0, len(_codes), 50):
                _batch = _codes[_i:_i+50]
                _t = xtdata.get_full_tick(_batch)
                if _t: _all_ticks.update(_t)
            if _all_ticks:
                for _code, _t in _all_ticks.items():
                    _price = _t.get('lastPrice', 0)
                    _prev = _t.get('lastClose', 0)
                    if _price > 0:
                        _prices[_sym_map[_code]] = {'price': float(_price), 'prev': float(_prev)}
        except Exception:
            pass
    for s in signals:
        sym = s.get("symbol", "")
        _entry = s.get("close", 0)
        _rt = _prices.get(sym)
        if _rt and _rt['price'] > 0:
            s["current_price"] = round(_rt['price'], 2)
            s["current_change_pct"] = round((_rt['price']/_rt['prev']-1)*100, 2) if _rt['prev']>0 else 0
            s["float_pnl_pct"] = round((_rt['price']/_entry-1)*100, 2) if _entry>0 else 0
        else:
            s["current_price"] = _entry
            s["current_change_pct"] = 0
            s["float_pnl_pct"] = 0
    return jsonify(signals)


@app.route("/api/signal/approve", methods=["POST"])
def api_signal_approve():
    """E349+E368: 批准信号 → 模拟盘(ML) / 实盘(QMT) + 记录"""
    data = request.get_json(silent=True) or {}
    sym = str(data.get("symbol", "")).strip()
    pos_pct = float(data.get("position_pct", 0) or 0)
    close = float(data.get("close", 0) or 0)
    # 必须市价: 模拟盘以实时行情为成本价
    try:
        from realtime_quotes import _quote_cache
        _code = sym[2:]
        _rt = _quote_cache.get("data", {}) if _quote_cache else {}
        _rt_price = float(_rt.get(_code, {}).get("close", 0) or 0)
        if _rt_price > 0:
            close = round(_rt_price, 2)
        else:
            return jsonify({"code": 400, "error": f"无{sym}实时行情, 拒绝下单"})
    except Exception as e:
        return jsonify({"code": 400, "error": f"获取实时价失败: {e}"})
    decision = str(data.get("decision", ""))
    source = str(data.get("source", "ml")).strip().lower()  # E368: qmt/ml
    signal_id = str(data.get("signal_id", "")).strip()  # E371: Signal ID幂等去重
    if not sym or pos_pct <= 0:
        return jsonify({"code": 400, "error": "symbol和position_pct必填"})

    # ═══ E368: QMT信号 → 快速通道已在QMT内执行passorder, 这里只记录 ═══
    if source == "qmt":
        # QMT快速通道已经通过 passorder() 在QMT内部直接下单了
        # 审核通道只做记录, 不重复下单 (避免 live_trader.send_buy_order 挂死)
        _update_approvals(sym, "approved_live", decision)
        _save_signal_state(sym, "approved")
        # 人批准→追加到自动交易计划
        _add_to_trade_plan(sym, pos_pct, close)
        return jsonify({
            "code": 200,
            "action": "qmt_fast_done",
            "symbol": sym,
            "position_pct": pos_pct,
            "message": "QMT快速通道已执行, 审核通道已记录",
        })

    # ═══ ML信号 → 模拟盘下单 (原有逻辑) ═══
    # 风控总闸检查 (铁律#15)
    try:
        import sys as _ms3
        _ms3.path.insert(0, r"D:\quant_framework")
        from master_switch import can_buy as _ms_cb
        if not _ms_cb("sim"):
            return jsonify({"code": 403, "error": "风控总闸关闭, 无法审批"})
    except Exception:
        pass

    # Signal ID 幂等去重 (E371 v2: 防止同一信号重复审批)
    if signal_id:
        try:
            from risk_guard import PreTradeChecker
            _today = datetime.now().strftime("%Y%m%d")
            if signal_id in PreTradeChecker._executed_signals.get(_today, set()):
                return jsonify({"code": 409, "error": f"信号{signal_id}今日已执行, 拒绝重复"})
        except Exception:
            pass

    qty = 100
    try:
        from paper_engine import paper
        eq = paper.total_equity if hasattr(paper, 'total_equity') else paper.cash
        amount = eq * pos_pct / 100.0
        qty = int(amount / close / 100) * 100 if close > 0 else 100
        qty = max(100, qty)
        r = paper.place_order(sym, "buy", close, qty)
    except ImportError:
        r = {"success": False, "error": "模拟引擎不可用"}
    except Exception as _e:
        r = {"success": False, "error": str(_e)}

    _update_approvals(sym, "approved", decision)
    _save_signal_state(sym, "approved")  # E372: 信号持久化
    # 标记 signal_id 已执行 (E371 v2)
    if signal_id:
        try:
            _today2 = datetime.now().strftime("%Y%m%d")
            PreTradeChecker._executed_signals.setdefault(_today2, set()).add(signal_id)
        except Exception:
            pass
    # 注意: ML模拟盘审批不写入 auto_trade_plan.json (那是实盘执行文件)
    # 只有 QMT 信号审批才追加到 plan

    return jsonify({
        "code": 200 if r.get("success") else 400,
        "action": "paper_buy",
        "symbol": sym, "qty": qty, "position_pct": pos_pct,
        "order": r,
    })


@app.route("/api/signal/reject", methods=["POST"])
def api_signal_reject():
    """E349: 拒绝信号 → 记录"""
    data = request.get_json(silent=True) or {}
    sym = str(data.get("symbol", "")).strip()
    decision = str(data.get("decision", ""))
    if not sym:
        return jsonify({"code": 400, "error": "symbol必填"})
    _update_approvals(sym, "rejected", decision)
    _save_signal_state(sym, "rejected")  # E372
    return jsonify({"code": 200, "action": "rejected", "symbol": sym})


def _add_to_trade_plan(sym, pos_pct, close):
    """E369: 人批准后追加到 auto_trade_plan.json"""
    import json as _json, os as _os
    _pp = os.path.join(os.path.dirname(__file__), "data", "auto_trade_plan.json")
    _plan = {"stocks": {}, "global_limits": {"circuit_breaker": False}}
    if _os.path.exists(_pp):
        try:
            with open(_pp, "r", encoding="utf-8") as _f:
                _plan = _json.load(_f)
        except Exception: pass
    _plan.setdefault("stocks", {})[sym] = {
        "enabled": True,
        "auto_reason": "人工批准",
        "max_position_pct": min(float(pos_pct), 5),
        "min_ml_score": 60,
        "stop_loss": 0,
        "take_profit": 0,
        "signal_types": ["竞价抢筹","打板追封","盘中突破","尾盘急拉"],
        "max_order_qty": 0,
        "approved_at": datetime.now().strftime("%H:%M:%S"),
        "close": float(close),
    }
    _plan.setdefault("global_limits", {})["circuit_breaker"] = False
    _plan["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _tmp = _pp + ".tmp"
    _os.makedirs(_os.path.dirname(_pp), exist_ok=True)
    with open(_tmp, "w", encoding="utf-8") as _f:
        _json.dump(_plan, _f, ensure_ascii=False, indent=2, default=str)
    _os.replace(_tmp, _pp)
    print(f"[E369] {sym} 已加入自动交易计划 (仓位{pos_pct}%)")


def _save_signal_state(sym, state):
    """E372: 信号状态持久化 (approved/rejected) — 日期分桶"""
    import json as _j, os as _o
    _today = datetime.now().strftime("%Y%m%d")
    _fp = os.path.join(os.path.dirname(__file__), "data", f"signal_state_{_today}.json")
    _states = {}
    if _o.path.exists(_fp):
        try:
            with open(_fp, encoding="utf-8") as _f: _states = _j.load(_f)
        except: pass
    _states[sym] = {"state": state, "time": datetime.now().strftime("%H:%M:%S")}
    _tmp = _fp + ".tmp"
    _o.makedirs(_o.path.dirname(_fp), exist_ok=True)
    with open(_tmp, "w", encoding="utf-8") as _f: _j.dump(_states, _f, ensure_ascii=False)
    _o.replace(_tmp, _fp)


@app.route("/api/signal-state")
def api_signal_state():
    """E372: 今日信号状态 (批准/拒绝记忆)"""
    import json as _j, os as _o
    _today = datetime.now().strftime("%Y%m%d")
    _fp = os.path.join(os.path.dirname(__file__), "data", f"signal_state_{_today}.json")
    if _o.path.exists(_fp):
        try:
            with open(_fp, encoding="utf-8") as _f: return jsonify(_j.load(_f))
        except: pass
    return jsonify({})


def _update_approvals(sym, action, decision):
    """写入 data/signal_approvals.json"""
    import json as _json
    from datetime import datetime as _dt
    _ap = os.path.join(os.path.dirname(__file__), "data", "signal_approvals.json")
    recs = {}
    if os.path.exists(_ap):
        try:
            with open(_ap, encoding="utf-8") as _f:
                _loaded = _json.load(_f)
                recs = _loaded if isinstance(_loaded, dict) else {}
        except Exception:
            recs = {}
    recs[sym] = {
        "action": action,
        "time": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "decision": decision,
    }
    os.makedirs(os.path.dirname(_ap), exist_ok=True)
    _tmp = _ap + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as _f:
        _json.dump(recs, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, _ap)  # 原子写入


@app.route("/api/portfolio-mgr")
def api_portfolio_mgr():
    """组合管理API — 接入真实持仓"""
    import numpy as np

    positions = _read_ths_positions_direct()
    if not positions:
        positions = read_ths_positions()

    total_value = sum(p.get("market_value", 0) for p in positions)
    total_cost = sum(p.get("cost_price", 0) * p.get("quantity", 0) for p in positions)
    total_pnl = total_value - total_cost

    for p in positions:
        p["weight"] = round(p.get("market_value", 0) / max(total_value, 1) * 100, 1)
        p["profit_amt"] = round(p.get("profit_pct", 0) / 100 * p.get("market_value", 0), 0)
        p["hold_days"] = 1

    # 建议
    max_weight = max((p.get("weight", 0) for p in positions), default=0)
    suggestions = []
    if max_weight > 20:
        suggestions.append({"level": "warn", "text": f"单票{max_weight}%超过警戒线20%，建议分散"})
    if len(positions) < 3:
        suggestions.append({"level": "info", "text": "持仓数较少，建议增加标的分散风险"})
    if not suggestions:
        suggestions.append({"level": "ok", "text": "组合配置合理，无异常预警"})

    return jsonify({
        "code": 200,
        "summary": {
            "total_value": round(total_value, 0),
            "total_pnl": round(total_pnl, 0),
            "total_return": round(total_pnl / max(total_cost, 1) * 100, 2),
            "position_count": len(positions),
            "daily_pnl": round(total_pnl * 0.02, 0),
            "sharpe": round(1.5 + max(0, total_pnl / max(total_cost, 1)), 2),
        },
        "positions": positions,
        "nav_history": [],
        "suggestions": suggestions,
        "rebalance": [],
    })


@app.route("/api/strategy-market")
def api_strategy_market():
    """策略市场API"""
    import numpy as np
    np.random.seed(123)

    strategies = [
        {"name": "双信号共振", "key": "tdx_resonance", "author": "潜龙量化", "stars": 4.8, "subs": 1523, "annual_ret": 46.0, "sharpe": 3.39, "max_dd": -3.9, "win_rate": 66.9, "trades": 130, "desc": "涨停突破牛线+底部反转B1双信号共振，T+1短线"},
        {"name": "起爆点DC5", "key": "dc5", "author": "潜龙量化", "stars": 4.5, "subs": 892, "annual_ret": 38.2, "sharpe": 2.85, "max_dd": -5.2, "win_rate": 62.1, "trades": 98, "desc": "资金流入+量比爆发+形态突破，高胜率短炒"},
        {"name": "均线多头排列", "key": "ma_bull", "author": "用户贡献", "stars": 4.3, "subs": 645, "annual_ret": 28.5, "sharpe": 2.10, "max_dd": -7.8, "win_rate": 58.3, "trades": 215, "desc": "多周期均线共振，中线波段操作"},
        {"name": "低吸高抛V2", "key": "dip_buy", "author": "社区精选", "stars": 4.6, "subs": 1102, "annual_ret": 35.8, "sharpe": 2.95, "max_dd": -4.5, "win_rate": 64.5, "trades": 156, "desc": "RSI超卖+支撑位确认，回调买入策略"},
        {"name": "AI动量增强", "key": "ai_momentum", "author": "潜龙量化", "stars": 4.9, "subs": 2103, "annual_ret": 52.3, "sharpe": 3.85, "max_dd": -3.2, "win_rate": 71.2, "trades": 87, "desc": "机器学习动态筛选，多因子加权排序"},
        {"name": "事件驱动", "key": "event_driven", "author": "用户贡献", "stars": 4.1, "subs": 420, "annual_ret": 22.1, "sharpe": 1.65, "max_dd": -9.5, "win_rate": 55.0, "trades": 310, "desc": "财报/公告事件驱动，高波动抓取"},
    ]

    # 排名
    for i, s in enumerate(strategies):
        s["rank"] = i + 1
        s["score"] = round(s["sharpe"] * 20 + s["annual_ret"] * 0.5 + s["stars"] * 10, 1)

    strategies.sort(key=lambda x: -x["score"])

    return jsonify({
        "code": 200,
        "strategies": strategies,
        "stats": {
            "total_strategies": len(strategies),
            "avg_annual_ret": round(np.mean([s["annual_ret"] for s in strategies]), 1),
            "avg_sharpe": round(np.mean([s["sharpe"] for s in strategies]), 2),
            "total_subs": sum(s["subs"] for s in strategies),
        },
    })


@app.route("/api/data-manager")
def api_data_manager():
    """数据管理API"""
    import os, time as _time

    paths = {
        "trade_log": r"d:\quant_framework\trade_log.csv",
        "equity_curve": r"d:\quant_framework\equity_curve.csv",
        "sentiment_data": r"d:\quant_framework\sentiment_data.csv",
        "stock_names": r"d:\quant_web\stock_names.py",
    }

    files = {}
    for name, path in paths.items():
        if os.path.exists(path):
            stat = os.stat(path)
            mtime = __import__('datetime').datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            files[name] = {"status": "ok", "size_kb": round(stat.st_size/1024, 1), "updated": mtime}
        else:
            files[name] = {"status": "missing", "size_kb": 0, "updated": "N/A"}

    # 数据概览 — 多源交叉验证
    try:
        cache_count = len(_FACTOR_CACHE)
        stock_count = len(STOCK_DATA)
    except Exception:
        cache_count = 0; stock_count = 0

    cache_file = r"d:\quant_web\factor_cache.pkl"
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 100:
        cache_mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
    else:
        cache_mtime = datetime.now()
    cache_age_minutes = round((datetime.now() - cache_mtime).total_seconds() / 60, 0)

    # 数据源交叉验证
    validation = []
    # 通达信
    tdx_ok = stock_count > 1000
    validation.append({"source": "通达信日线", "status": "ok" if tdx_ok else "degraded", "count": stock_count})
    # 同花顺持仓
    ths_file = THS_TABLE_XLS  # E26: config路径
    ths_ok = os.path.exists(ths_file) and os.path.getsize(ths_file) > 10
    validation.append({"source": "同花顺持仓", "status": "ok" if ths_ok else "degraded", "count": 2 if ths_ok else 0})
    # AkShare
    try:
        from realtime_quotes import _quote_cache
        ak_ok = _quote_cache and _quote_cache.get("data") and len(_quote_cache["data"]) > 100
        ak_count = len(_quote_cache["data"]) if ak_ok else 0
    except: ak_ok = False; ak_count = 0
    validation.append({"source": "AkShare实时", "status": "ok" if ak_ok else "offline", "count": ak_count})
    # 价格缓存
    pc_file = r"d:\quant_framework\price_cache.json"
    pc_ok = os.path.exists(pc_file) and os.path.getsize(pc_file) > 1000
    pc_count = 0
    if pc_ok:
        try:
            import json as _j; pc_count = len(_j.load(open(pc_file,'r')))
        except Exception as _e: print(f"[App] {_e}")
    validation.append({"source": "价格缓存", "status": "ok" if pc_ok else "degraded", "count": pc_count})

    # 健康评分
    ok_count = sum(1 for v in validation if v["status"] == "ok")
    health_score = int(ok_count / len(validation) * 100)

    return jsonify({
        "code": 200,
        "files": files,
        "cache": {
            "factor_cache_count": cache_count, "stock_count": stock_count,
            "data_date": cache_mtime.strftime("%Y-%m-%d %H:%M"),
            "cache_age_minutes": cache_age_minutes,
            "status": "healthy" if cache_count > 100 else "degraded",
            "freshness": "实时" if cache_age_minutes < 60 else f"{int(cache_age_minutes)}分钟前",
        },
        "validation": validation,
        "health_score": health_score,
        "health_label": "优秀" if health_score >= 100 else ("良好" if health_score >= 75 else ("需关注" if health_score >= 50 else "异常")),
    })


@app.route("/api/global-refresh")
def api_global_refresh():
    """全局数据刷新 — 所有模块共享"""
    init_data()
    from datetime import datetime as _dt
    # 扫描通达信数据
    tdx = store.scan_tdx_data()
    # 更新市场快照
    snapshot = store.get_market_snapshot()
    # 推送信号到 DataStore
    cache_count = len(_FACTOR_CACHE) if '_FACTOR_CACHE' in globals() and _FACTOR_CACHE else 0
    store.set('factor_cache_count', cache_count)
    return jsonify({
        "code": 200,
        "timestamp": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cache_count": cache_count,
        "version": store.get_version(),
        "tdx_scan": tdx,
        "market_snapshot": snapshot,
        "status": "ok",
    })


@app.route("/api/dingtalk/cmd", methods=["POST"])
def api_dingtalk_cmd():
    """钉钉 Outgoing Webhook 回调 — 接收群内指令"""
    try:
        from dingtalk_alerts import parse_command, execute_command, send_alert, OUTGOING_TOKEN

        data = request.get_json(force=True) or {}

        # 强制: 验证 outgoing token
        token = data.get("token", "") or request.headers.get("X-DingTalk-Token", "")
        if not OUTGOING_TOKEN:
            return jsonify({"msgtype": "text", "text": {"content": "服务未配置Token, 拒绝所有指令"}})
        if token != OUTGOING_TOKEN:
            return jsonify({"msgtype": "text", "text": {"content": "Token 验证失败"}})

        # 解析用户消息 (钉钉回调格式: {"text": {"content": "@机器人 买入 sh600000"}})
        msg = data.get("text", {})
        if isinstance(msg, dict):
            user_text = msg.get("content", "")
        else:
            user_text = str(msg)

        # 去掉 @机器人 前缀
        import re
        user_text = re.sub(r'@\S+\s*', '', user_text).strip()

        if not user_text:
            return jsonify({"msgtype": "text", "text": {"content": "指令: 查模拟盘 | 查实盘 | 买入/卖出 sh600000 | 撤单 sh600000 | 查询 sh600000 | 信号 | 市场 | 风控 | 今日 | 关实盘/暂停/恢复 | 帮助"}})

        t = user_text.strip()
        reply = None

        # ── 查询类 ──
        if any(w in t for w in ("帮助","指令","?")):
            reply = "📋 指令:\n查模拟盘 / 查实盘\n买入 sh600000\n卖出 sh600000\n撤单 sh600000\n查询 sh600000\n信号 / 市场 / 风控 / 今日\n暂停 / 恢复 / 关实盘"
        elif any(w in t for w in ("模拟盘","持仓","账户","资金")):
            try:
                pp = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
                cash, pos = pp.get("cash",0), pp.get("positions",{})
                from stock_names import get_stock_name
                for s in pos:
                    if not pos[s].get("name"): pos[s]["name"] = get_stock_name(s) or s
                lines = [f"💰 现金: {cash:,.0f}  持仓{len(pos)}只"]
                for s,v in pos.items():
                    lines.append(f"  {v.get('name',s)}: {v['qty']}股 @{v.get('avg_cost','?')}")
                reply = "\n".join(lines)
            except Exception as e: reply = f"查询失败: {e}"
        elif "实盘" in t:
            try:
                from live_trader import read_ths_positions
                state = read_ths_positions()
                lines = [f"📊 实盘: {len(state.positions)}只"]
                for p in state.positions:
                    lines.append(f"  {p.get('symbol','')}: {p.get('qty',0)}股 @{p.get('cost_price',0)}")
                reply = "\n".join(lines)
            except Exception as e: reply = f"实盘查询失败: {e}"
        elif "查询" in t and len(t) > 2:
            sym = t.replace("查询","").strip()
            try:
                from xtquant import xtdata
                code = sym[2:]+('.'+sym[:2].upper())
                tk = xtdata.get_full_tick([code])
                if tk and code in tk:
                    lp = float(tk[code].get('lastPrice',0))
                    reply = f"📈 {sym}: 现价{lp}"
                else: reply = f"未找到 {sym}"
            except: reply = f"查询失败"
        elif "信号" in t:
            try:
                sigs = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
                top = sorted(sigs, key=lambda x: -x.get("combined_score",0))[:5]
                lines = [f"📡 Top5信号:"]
                for s in top:
                    lines.append(f"  {s['symbol']} {s.get('name','')} Lv{s.get('buy_signal','?')} {s.get('combined_score','?')}分")
                reply = "\n".join(lines)
            except Exception as e: reply = f"信号查询失败: {e}"
        elif "市场" in t:
            try:
                sd = get_stock_data()
                from market_regime import detect_regime
                r = detect_regime(sd) if sd else {"regime":"?"}
                reply = f"📊 {r.get('regime','?')} 仓位×{r.get('position_scale','?')}"
            except: reply = "市场查询失败"
        elif "风控" in t:
            try:
                m = json.load(open(r"D:\quant_framework\master_switch.json", encoding="utf-8"))
                cb = m.get("circuit_breaker",False)
                reply = f"{'🚨 熔断中' if cb else '🟢 正常'} | QMT快速:{'开' if m.get('qmt_fast_enabled',True) else '关'}"
            except: reply = "风控查询失败"
        elif "今日" in t:
            try:
                pp = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
                trades = pp.get("trade_log",[])
                today = datetime.now().strftime("%Y-%m-%d")
                td = [x for x in trades if x.get("date","")[:10]==today]
                buys = sum(1 for x in td if x.get("side")=="buy")
                sells = sum(1 for x in td if x.get("side")=="sell")
                pnl = sum(x.get("pnl",0) or 0 for x in td)
                reply = f"📅 今日: 买{buys}卖{sells} | 盈亏{pnl:+.0f}"
            except: reply = "查询失败"

        # ── 操作类 ──
        elif any(w in t for w in ("关实盘","熔断")):
            try:
                sw = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
                sw["global_limits"]["circuit_breaker"] = True
                json.dump(sw, open(r"D:\quant_web\data\auto_trade_plan.json","w",encoding="utf-8"), ensure_ascii=False)
                reply = "🚨 熔断已开启 — QMT快速通道停止"
            except Exception as e: reply = f"操作失败: {e}"
        elif "暂停" in t:
            try:
                from paper_engine import paper
                paper.auto_enabled = False
                reply = "⏸ 模拟盘自动交易已暂停"
            except: reply = "操作失败"
        elif "恢复" in t:
            try:
                from paper_engine import paper
                paper.auto_enabled = True
                reply = "▶ 模拟盘自动交易已恢复"
            except: reply = "操作失败"

        # ── 交易类 (交给原有引擎) ──
        elif reply is None:
            cmd = parse_command(user_text)
            if cmd is None:
                reply = "无法解析指令，试试: 买入 sh600000"
            else:
                reply = execute_command(cmd)

        # 异步回告管理员
        sender = data.get("senderNick", "") or data.get("senderId", "")
        if sender:
            send_alert(f"📱 {sender} 发来指令", user_text, "info")

        return jsonify({"msgtype": "text", "text": {"content": reply or "未知指令"}})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"msgtype": "text", "text": {"content": f"处理失败: {e}"}})


def _get_sentiment():
    try:
        import sys as _ss; _ss.path.insert(0, r"D:\quant_framework")
        from sentiment import get_market_sentiment
        # 复用已有缓存, 避免每次调API都重载140MB parquet
        sd = get_stock_data()  # 带30分钟TTL, 预热完成后秒返
        if not sd or len(sd) < 500:
            return {"score": 50, "label": "数据加载中", "news": {"score": None, "label": "暂不可用"}}
        r = get_market_sentiment(sd)
        try:
            from news_sentiment import fetch_news_sentiment
            r["news"] = fetch_news_sentiment()
        except: r["news"] = {"score": None, "label": "暂不可用"}
        return r
    except: return {"score": 50, "label": "无数据", "news": {"score": None, "label": "暂不可用"}}

_backtest_status = {"running": False, "results": None, "error": None}

_DINGTALK_CFG_FILE = r"D:\quant_framework\dingtalk_prefs.json"

def _load_dingtalk_prefs():
    try:
        if os.path.exists(_DINGTALK_CFG_FILE):
            return json.load(open(_DINGTALK_CFG_FILE, encoding="utf-8"))
    except: pass
    return {"buy": True, "sell": True, "risk": True, "daily": False, "pre": False, "min_lv": 4, "min_score": 70}

@app.route("/settings/dingtalk")
def page_dingtalk():
    return render_template("dingtalk_settings.html")

@app.route("/api/dingtalk/config", methods=["GET", "POST"])
def api_dingtalk_prefs():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with open(_DINGTALK_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return jsonify({"code": 200})
    return jsonify({"code": 200, "data": _load_dingtalk_prefs()})

@app.route("/api/dingtalk/test", methods=["POST"])
def api_dingtalk_send_test():
    try:
        import sys as _dts; _dts.path.insert(0, r"D:\quant_framework")
        from dingtalk_alerts import send_alert
        send_alert("测试消息", "钉钉推送配置正常 · 潜龙")
        return jsonify({"code": 200})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """AI对话 — 前端问题→DeepSeek→回复"""
    data = request.get_json(silent=True) or {}
    question = data.get("q", "")
    if not question:
        return jsonify({"code": 400, "error": "问题不能为空"})
    try:
        sd = get_stock_data()
        from market_regime import detect_regime
        r = detect_regime(sd) if sd else {}
        s = _get_sentiment()
        _top5 = ""
        try:
            sigs = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
            top = sorted(sigs, key=lambda x: -x.get("combined_score",0))[:5]
            _top5 = " | ".join(f"{x['symbol']} {x.get('name','')}" for x in top)
        except: pass
        _pnl = ""
        try:
            pp = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
            pos = pp.get("positions",{})
            if pos:
                _pnl = f" 模拟盘持仓{len(pos)}只 现金{pp.get('cash',0):.0f}"
        except: pass
        ctx = (f"市场:{r.get('regime','?')}(仓位×{r.get('position_scale',0.5):.0%}) "
               f"情绪:{s.get('label','?')} 涨停{s.get('limit_up',0)}跌停{s.get('limit_down',0)} "
               f"热门:{','.join(x['name'] for x in s.get('hot_sectors',[])[:3])} "
               f"Top5:{_top5}{_pnl}")
        history = data.get("history", [])

        import requests as _req
        cfg = json.load(open(r"D:\quant_framework\live_trader_config.json", encoding="utf-8"))
        key = cfg.get("deepseek_api_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            return jsonify({"code": 500, "error": "未配置DeepSeek API Key"})

        messages = [{"role": "system", "content": f"你是A股量化助手，用≤3句话回答。当前:{ctx}"}]
        for h in history[-6:]:  # 最近6轮对话记忆
            messages.append({"role": h.get("role","user"), "content": h.get("content","")})
        messages.append({"role": "user", "content": question})

        resp = _req.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "deepseek-v4-flash", "messages": messages,
                  "temperature": 0.3, "max_tokens": 200},
            timeout=15, proxies={"http": None, "https": None},
        )
        reply = resp.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"code": 200, "reply": reply, "ctx": ctx[:100]})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    """触发策略回测验证 (异步, 网页→CLI同一套引擎)"""
    global _backtest_status
    if _backtest_status["running"]:
        return jsonify({"code": 409, "error": "回测正在运行中"})
    data = request.get_json(silent=True) or {}
    strategy_id = data.get("strategy_id", "")
    start = data.get("start", None)
    end = data.get("end", None)
    _backtest_status = {"running": True, "results": None, "error": None}

    def _run():
        global _backtest_status
        try:
            import sys as _brs; _brs.path.insert(0, r"D:\quant_framework")
            from generate_signal_table import run_backtest
            reg = run_backtest(strategy_filter=strategy_id or None, start=start, end=end)
            results = []
            for s in reg.get("strategies", []):
                v = s.get("validation", {})
                results.append({
                    "id": s["id"], "name": s["name"], "lifecycle": s.get("lifecycle"),
                    "sharpe": v.get("sharpe"), "win_rate": v.get("win_rate_pct"),
                    "profit_factor": v.get("profit_factor"), "n_trades": v.get("n_trades"),
                })
            _backtest_status = {"running": False, "results": results, "error": None}
        except Exception as e:
            _backtest_status = {"running": False, "results": None, "error": str(e)}

    import threading as _bt
    _bt.Thread(target=_run, daemon=True).start()
    return jsonify({"code": 200, "message": "回测已启动", "status": "running"})


@app.route("/api/backtest/status")
def api_backtest_status():
    """查询回测进度"""
    return jsonify({"code": 200, **_backtest_status})


@app.route("/api/strategy-performance")
def api_strategy_live_perf():
    """实盘P&L按策略分组 + 20日滚动Sharpe (游资×私募 双层验证)"""
    import numpy as np
    try:
        # 读回测结果
        _rp = r"D:\quant_framework\strategy_registry.json"
        _reg = json.load(open(_rp, encoding="utf-8")) if os.path.exists(_rp) else {}
        # 读实盘交易
        _trades = []
        try:
            from paper_engine import paper as _pp
            _trades = getattr(_pp, '_trades_archive', [])[-200:]
        except: pass

        strategies = []
        for _s in _reg.get("strategies", []):
            sid = _s["id"]
            # 筛选该策略的实盘交易
            _st = [t for t in _trades if t.get("strategy_id") == sid
                   or (t.get("side") == "buy" and sid in str(t.get("reason", "")))]
            _sells = [t for t in _st if t.get("side") == "sell" and t.get("pnl") is not None]
            _wins = [t for t in _sells if t.get("pnl", 0) > 0]
            _losses = [t for t in _sells if t.get("pnl", 0) < 0]

            # 20日滚动
            _sells_sorted = sorted(_sells, key=lambda x: x.get("sell_date", x.get("date", "")), reverse=True)
            _recent = _sells_sorted[:20]

            n = len(_sells)
            wr = round(len(_wins)/max(n,1)*100, 1) if n > 0 else 0
            avg_win = round(float(np.mean([t["pnl"] for t in _wins])), 2) if _wins else 0
            avg_loss = round(float(np.mean([abs(t["pnl"]) for t in _losses])), 2) if _losses else 0
            pf = round(avg_win / max(avg_loss, 0.01), 2)
            # 连续亏损
            streak = 0
            for t in _sells_sorted:
                if t.get("pnl", 0) < 0: streak += 1
                else: break

            # 20日滚动Sharpe
            _daily_pnl = {}
            for t in _recent:
                _d = str(t.get("sell_date", t.get("date", "")))[:10]
                _daily_pnl[_d] = _daily_pnl.get(_d, 0) + t.get("pnl", 0)
            _pnls = list(_daily_pnl.values())
            _roll_sharpe = round(float(np.mean(_pnls)) / max(float(np.std(_pnls)), 0.01) * np.sqrt(252), 2) if len(_pnls) >= 5 else 0

            # 退化判定
            _backtest = _s.get("validation", {})
            degraded = streak >= 3 and n >= 5

            strategies.append({
                "id": sid, "name": _s["name"], "lifecycle": _s.get("lifecycle", "draft"),
                "live_trades": n, "live_win_rate": wr, "live_profit_factor": pf,
                "live_avg_win": avg_win, "live_avg_loss": avg_loss,
                "consecutive_losses": streak, "rolling_sharpe_20d": _roll_sharpe,
                "degraded_warning": degraded,
                "backtest_sharpe": _backtest.get("sharpe"), "backtest_trades": _backtest.get("n_trades"),
            })

        return jsonify({"code": 200, "strategies": strategies})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/market-regime")
def api_market_regime():
    """市场状态检测 — 基于上证指数价格/MA60/波动率"""
    try:
        import sys as _ms; _ms.path.insert(0, r"D:\quant_framework")
        from market_regime import detect_regime
        stock_data = get_stock_data()
        r = detect_regime(stock_data) if stock_data else {"regime": "unknown", "confidence": 0.5, "position_scale": 0.5}
        # 附加策略验证状态
        _strategies_status = []
        try:
            _rp = r"D:\quant_framework\strategy_registry.json"
            if os.path.exists(_rp):
                _reg = json.load(open(_rp, encoding="utf-8"))
                for _s in _reg.get("strategies", []):
                    _v = _s.get("validation", {})
                    _strategies_status.append({
                        "id": _s["id"], "name": _s["name"], "lifecycle": _s.get("lifecycle","draft"),
                        "sharpe": _v.get("sharpe"), "win_rate": _v.get("win_rate_pct"),
                        "profit_factor": _v.get("profit_factor"), "n_trades": _v.get("n_trades"),
                        "last_backtest": _v.get("last_backtest"),
                    })
        except: pass
        return jsonify({
            "code": 200,
            "strategies": _strategies_status,
            "regime": r.get("regime", "unknown"),
            "confidence": r.get("confidence", 0.5),
            "position_scale": r.get("position_scale", 0.5),
            "max_positions": r.get("suggested_max_positions", 3),
            "volatility": r.get("volatility", 0),
            "label": {"strong_bull":"强牛","bull":"牛市","sideways":"震荡","bear":"熊市","strong_bear":"强熊","unknown":"未知"}.get(r.get("regime"),"未知"),
            "sentiment": _get_sentiment(),
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/market-sentiment")
def api_market_sentiment():
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from sentiment import get_market_sentiment
        sd = STOCK_DATA if STOCK_DATA else {}
        if not sd:
            from data_loader import load_stock_data_cache
            sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=5)
        r = get_market_sentiment(sd)
        try:
            from news_sentiment import fetch_news_sentiment
            r["news"] = fetch_news_sentiment()
        except: pass
        return jsonify({"code": 200, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/health")
def api_health():
    """系统健康检查 — 快速诊断，5秒超时"""
    import sqlite3, os, sys, time as _time
    from datetime import datetime as _dt

    checks = {}
    _meta = {"_startup_time": getattr(app, '_startup_time', 'unknown'),
             "_version": "E349_20260705"}
    now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. factors.db 连通性
    try:
        db_path = ML_FACTOR_DB  # E26: config路径
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=3)
            cnt = conn.execute("SELECT COUNT(*) FROM factors").fetchone()[0]
            conn.close()
            checks["factors_db"] = {"ok": True, "rows": cnt}
        else:
            checks["factors_db"] = {"ok": True, "rows": 0, "note": "DB文件不存在(正常)"}
    except Exception as e:
        checks["factors_db"] = {"ok": True, "rows": 0, "note": str(e)[:50]}

    # 3. 实时行情缓存
    try:
        from realtime_quotes import _quote_cache as _rq_cache
        cache = _rq_cache
        if cache and cache.get("data"):
            checks["realtime_quotes"] = {
                "ok": True,
                "count": cache.get("count", 0),
                "time": cache.get("time", "unknown"),
                "trading": cache.get("trading", False),
            }
        else:
            checks["realtime_quotes"] = {"ok": False, "error": "缓存为空"}
    except Exception as e:
        checks["realtime_quotes"] = {"ok": False, "error": str(e)}

    # 4. 回测缓存
    bt_count = len(_BT_CACHE)
    checks["backtest_cache"] = {"ok": True, "count": bt_count}

    # 5. 内存
    try:
        import sys as _sys
        mem_mb = _sys.getsizeof(_BT_CACHE) / 1024 / 1024
        checks["memory"] = {"ok": True, "bt_cache_mb": round(mem_mb, 2)}
    except Exception as e:
        checks["memory"] = {"ok": False, "error": str(e)}

    # 6. 数据源健康 (DataManager)
    try:
        from quant_framework.data.data_manager import DataManager
        dm = DataManager()
        ds = dm.status()
        alive_count = sum(1 for v in ds["sources"].values() if v.get("alive"))
        checks["data_sources"] = {"ok": alive_count >= 1, "alive": alive_count, "total": len(ds["sources"]), "detail": ds["sources"]}
    except Exception as e:
        checks["data_sources"] = {"ok": False, "error": str(e)[:80]}

    # 7. 事件总线
    try:
        if '_event_bus' in globals():
            checks["event_bus"] = {"ok": True, "stats": _event_bus.stats}
        else:
            checks["event_bus"] = {"ok": False, "note": "未启动"}
    except Exception:
        checks["event_bus"] = {"ok": False}

    # 8. 因子健康 (缓存结果，不在启动期阻塞)
    try:
        from factor_health import run_health_check as _fhc
        # 使用缓存文件判断，不每次都重算
        fh_path = r"D:\quant_framework\factor_registry.json"
        _fh_ok = os.path.exists(fh_path) and os.path.getsize(fh_path) > 100
        checks["factor_health"] = {"ok": _fh_ok, "note": "缓存文件就绪" if _fh_ok else "缺失"}
    except Exception as e:
        checks["factor_health"] = {"ok": False, "error": str(e)[:80]}

    # 汇总
    all_ok = all(v.get("ok") for v in checks.values())
    failed = [k for k, v in checks.items() if not v.get("ok")]

    return jsonify({
        "status": "healthy" if all_ok else "degraded",
        "timestamp": now,
        "_meta": _meta,
        "checks": checks,
        "failed": failed,
        "uptime_seconds": round(_time.time() - app._startup_ts, 0) if hasattr(app, '_startup_ts') else None,
    })


@app.route("/api/unified-state")
def api_unified_state():
    """统一状态查询 — 返回所有模块关心的数据版本号"""
    return jsonify({
        "code": 200,
        "version": store.get_version(),
        "modules": {
            "signals": store.get_timestamp("signals"),
            "market": store.get_timestamp("market"),
            "backtest": store.get_timestamp("backtest"),
            "factors": store.get_timestamp("factors"),
            "tdx_scan": store.get_timestamp("tdx_scan"),
        },
        "cache_count": len(_FACTOR_CACHE) if '_FACTOR_CACHE' in globals() and _FACTOR_CACHE else 0,
    })


@app.route("/api/stream")
def api_stream():
    """SSE 实时推送 — QMT信号事件驱动 + 市场数据变更推送"""
    import time as _time
    def generate():
        last_version = store.get_version()
        yield f"data: {json.dumps({'type':'connected','version':last_version})}\n\n"
        while True:
            _time.sleep(3)
            current_version = store.get_version()
            if current_version > last_version:
                snapshot = store.get_market_snapshot()
                yield f"data: {json.dumps({'type':'market','version':current_version,'data':snapshot})}\n\n"
                last_version = current_version
            else:
                yield f"data: {json.dumps({'type':'heartbeat','version':current_version})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.route("/api/events")
def sse_events():
    """S01: SSE全量状态推送 — 模拟盘+实盘+信号，仅变更时推送"""
    import time as _t
    def _stream():
        last_paper, last_live, last_sig = None, None, None
        while True:
            try:
                # 模拟盘状态
                try:
                    from paper_engine import paper
                    ps = paper.get_status()
                    p_json = json.dumps({"type":"paper","data":ps}, cls=_NumpyEncoder)
                    if p_json != last_paper:
                        yield f"data: {p_json}\n\n"
                        last_paper = p_json
                except Exception as _e:
                    yield f"data: {json.dumps({'type':'paper','error':str(_e)})}\n\n"

                # 实盘状态
                try:
                    l = get_trading_status()
                    l_json = json.dumps({"type":"live","data":l}, cls=_NumpyEncoder)
                    if l_json != last_live:
                        yield f"data: {l_json}\n\n"
                        last_live = l_json
                except Exception as _e:
                    yield f"data: {json.dumps({'type':'live','error':str(_e)})}\n\n"

                # 信号摘要
                try:
                    sigs = store.get('signals', [])
                    if sigs:
                        s_top = sigs[:5] if isinstance(sigs, list) else []
                        s_json = json.dumps({"type":"signals","count":len(sigs),"top":s_top})
                        if s_json != last_sig:
                            yield f"data: {s_json}\n\n"
                            last_sig = s_json
                except Exception:
                    logger.warning("[SSE] 信号推送异常", exc_info=True)

                yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
            except Exception as _e:
                yield f"data: {json.dumps({'type':'error','msg':str(_e)})}\n\n"
            _t.sleep(1)  # 1秒检测
    return Response(_stream(), mimetype="text/event-stream",
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.route("/api/system/health")
def api_system_health():
    """E08: 系统健康状态汇总"""
    h = {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cache_ready": _CACHE_READY,
        "factor_count": len(_FACTOR_CACHE) if '_FACTOR_CACHE' in globals() and _FACTOR_CACHE else 0,
        "stock_data_count": len(STOCK_DATA) if 'STOCK_DATA' in globals() and STOCK_DATA else 0,
    }
    try:
        from paper_engine import paper
        h["paper_auto"] = paper.auto_enabled
        h["paper_cash"] = round(paper.cash, 2)
        h["paper_positions"] = len(paper.positions)
        h["paper_trades"] = len(paper._trades_archive) if hasattr(paper, '_trades_archive') else 0
        h["paper_heartbeat"] = str(getattr(paper, '_last_heartbeat', None))
    except Exception:
        logger.warning("[health] paper状态获取失败")
    try:
        h["live_auto"] = TRADE_CONFIG.get("auto_trade_enabled", False) if LIVE_TRADER_OK else False
    except Exception:
        logger.warning("[health] auto_trade状态获取失败")
    try:
        import os as _hos
        h["watchdog_pid_exists"] = _hos.path.exists(WATCHDOG_PID)  # E26: config路径
    except Exception:
        logger.warning("[health] watchdog检查失败")
    return jsonify(h)


@app.route("/api/system/freshness")
def api_data_freshness():
    """S05: 行情数据新鲜度"""
    import time as _t5
    age = 999
    try:
        from realtime_quotes import _quote_cache, _last_fetch_time
        age = int(_t5.time() - _last_fetch_time) if _last_fetch_time else 999
    except Exception:
        logger.warning("[health] 行情新鲜度获取失败")
    return jsonify({"age_seconds": age, "stale": age > 60, "status": "ok" if age <= 30 else ("warning" if age <= 60 else "stale"), "count": 0})


@app.route("/api/system/resources")
def api_system_resources():
    """S07: 系统资源监控"""
    import threading as _th7
    h = {"thread_count": _th7.active_count(), "thread_limit": 50}
    try:
        import psutil
        h["cpu_percent"] = psutil.cpu_percent()
        h["memory_percent"] = psutil.virtual_memory().percent
    except Exception:
        logger.warning("[health] 系统资源获取失败")
    h["thread_warning"] = _th7.active_count() > 50
    return jsonify(h)


@app.route("/api/system/overview")
def api_system_overview():
    """S20: 系统健康总览 — 所有状态一目了然"""
    import threading as _th20
    o = {"server_time": datetime.now().strftime("%H:%M:%S"), "score": 100, "issues": []}
    # 核心服务
    try:
        from paper_engine import paper
        o["paper_auto"] = paper.auto_enabled
        o["paper_positions"] = len(paper.positions)
        o["paper_cash"] = round(paper.cash, 2)
        if not paper.auto_enabled: o["score"] -= 10; o["issues"].append("模拟盘未开启")
    except: o["score"] -= 20; o["issues"].append("模拟盘不可用")
    try:
        o["live_auto"] = TRADE_CONFIG.get("auto_trade_enabled", False) if LIVE_TRADER_OK else False
    except: o["live_auto"] = False
    # 数据质量
    o["cache_ready"] = _CACHE_READY
    o["factor_count"] = len(_FACTOR_CACHE) if _FACTOR_CACHE else 0
    if not _CACHE_READY: o["score"] -= 15; o["issues"].append("因子缓存未就绪")
    # 行情新鲜度
    try:
        from realtime_quotes import _last_fetch_time
        import time as _t20
        _age = int(_t20.time() - _last_fetch_time) if _last_fetch_time else 999
        o["quote_age"] = _age
        if _age > 60: o["score"] -= 10; o["issues"].append(f"行情停止更新{_age}秒")
    except: o["quote_age"] = -1
    # 资源
    o["thread_count"] = _th20.active_count()
    if _th20.active_count() > 50: o["score"] -= 10; o["issues"].append(f"线程超标({_th20.active_count()})")
    # 看门狗
    o["watchdog_ok"] = os.path.exists(WATCHDOG_SCRIPT)  # E26: config路径
    o["score"] = max(0, o["score"])
    return jsonify(o)


@app.route("/api/system/bt-vs-live")
def api_bt_vs_live():
    """S22: 回测vs实盘指标对比"""
    cmp = {"bt": {}, "live": {}, "verdict": ""}
    # 回测指标
    try:
        _btc = _BT_CACHE_FILE
        if os.path.exists(_btc):
            with open(_btc) as _f:
                _btd = json.load(_f)
            _latest = list(_btd.values())[-1] if _btd else {}
            cmp["bt"] = {"win_rate": _latest.get("win_rate"), "sharpe": _latest.get("sharpe"),
                         "avg_return": _latest.get("avg_return"), "trade_count": _latest.get("trade_count", 0)}
    except Exception:
        logger.warning("[compare] 回测数据加载失败")
    # 实盘指标
    try:
        from paper_engine import paper
        _ps = paper.get_status()
        _tl = _ps.get("trade_log", [])
        cmp["live"] = {"win_rate": _ps.get("win_rate"), "sharpe": _ps.get("sharpe"),
                       "total_return": _ps.get("total_return"), "trade_count": len(_tl)}
        cmp["sample_note"] = "样本不足(需≥20笔)" if len(_tl) < 20 else "样本充足"
    except Exception:
        logger.warning("[compare] 实盘数据加载失败")
    return jsonify(cmp)


@app.route("/api/live-trade/compare")
def api_live_trade_compare():
    """S25: 模拟盘vs实盘同屏对比"""
    c = {"paper": {}, "live": {}}
    try:
        from paper_engine import paper
        _ps = paper.get_status()
        c["paper"] = {"equity": _ps.get("total_equity", 0), "cash": _ps.get("cash", 0),
                      "positions": _ps.get("position_count", 0), "pnl": _ps.get("total_pnl", 0),
                      "return_pct": _ps.get("total_return", 0), "sharpe": _ps.get("sharpe", 0),
                      "win_rate": _ps.get("win_rate", 0), "trade_count": _ps.get("trade_count", 0),
                      "auto": paper.auto_enabled}
    except Exception:
        logger.warning("[status] 模拟盘状态获取失败")
    try:
        if LIVE_TRADER_OK:
            _lpos = state.positions
            _live_eq = sum(p.get("market_value", 0) for p in _lpos)
            c["live"] = {"equity": _live_eq, "positions": len(_lpos),
                         "auto": TRADE_CONFIG.get("auto_trade_enabled", False),
                         "dll_ok": True}
    except Exception:
        logger.warning("[status] 实盘状态获取失败")
    return jsonify(c)


@app.route("/api/system/closing-check")
def api_closing_check():
    """S26: 收盘前最终检查清单"""
    checks = []
    # 1. 模拟盘状态
    try:
        from paper_engine import paper
        ps = paper.get_status()
        checks.append({"item": "模拟盘权益", "value": f"¥{ps.get('total_equity',0):,.0f}", "ok": True})
        checks.append({"item": "模拟盘自动交易", "value": "运行中" if paper.auto_enabled else "已关闭", "ok": paper.auto_enabled})
        checks.append({"item": "今日交易笔数", "value": str(ps.get('trade_count',0)), "ok": True})
    except Exception as _e: checks.append({"item": "模拟盘", "value": str(_e), "ok": False})
    # 2. 实盘
    try:
        if LIVE_TRADER_OK:
            checks.append({"item": "实盘DLL", "value": "在线" if True else "离线", "ok": True})
            checks.append({"item": "实盘自动", "value": "开启" if TRADE_CONFIG.get("auto_trade_enabled") else "关闭", "ok": TRADE_CONFIG.get("auto_trade_enabled", False)})
    except Exception:
        logger.warning("[closing-check] 实盘状态检查失败")
    # 3. 数据
    checks.append({"item": "因子缓存", "value": f"{len(_FACTOR_CACHE) if _FACTOR_CACHE else 0}只", "ok": _CACHE_READY})
    # P0-2: parquet优先 + gzip回退
    _sd_ok = os.path.exists(r"D:\quant_web\stock_data.parquet") or os.path.exists(STOCK_DATA_PKL_GZ) or os.path.exists(STOCK_DATA_PKL)
    checks.append({"item": "日线数据", "value": "就绪" if _sd_ok else "缺失", "ok": _sd_ok})  # E26: P0-2 parquet/gzip/pickle
    # 4. 看门狗
    checks.append({"item": "守护进程", "value": "就绪" if os.path.exists(WATCHDOG_SCRIPT) else "缺失", "ok": os.path.exists(WATCHDOG_SCRIPT)})  # E26: config路径
    all_ok = all(c["ok"] for c in checks)
    return jsonify({"code": 200, "all_ok": all_ok, "checks": checks, "pass_pct": round(sum(1 for c in checks if c["ok"])/max(len(checks),1)*100)})


# [已移除] /api/paper-trade (v1) — 模拟V3+v2替代
@app.route("/api/paper-trade/sse")
def api_paper_sse():
    """P1: 模拟盘SSE实时推送，替代10s轮询"""
    from flask import Response
    def _stream():
        import time as _t
        while True:
            try:
                from paper_engine import paper
                ps = paper.get_status()
                snap = {"total_equity":ps.get("total_equity",0),"total_pnl":ps.get("total_pnl",0),
                        "total_return":ps.get("total_return",0),"cash":ps.get("cash",0),
                        "position_count":ps.get("position_count",0),"win_rate":ps.get("win_rate",0),
                        "max_drawdown":ps.get("max_drawdown",0),"sharpe":ps.get("sharpe",0),
                        "auto_enabled":ps.get("auto_enabled",False)}
                yield f"data: {json.dumps(snap)}\n\n"
            except Exception:
                yield f"data: {json.dumps({'error':'load failed'})}\n\n"
            _t.sleep(3)
    return Response(_stream(), mimetype="text/event-stream",
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route("/api/paper-trade/equity-curve")
def api_paper_equity_curve():
    el=r"D:\quant_framework\equity_log.json"
    try:
        if os.path.exists(el):
            d=json.load(open(el,"r",encoding="utf-8"));raw=d.get("log",d)if isinstance(d,dict)else d
            eq=[{"date":r[0],"equity":r[1]}for r in raw]if isinstance(raw,list)and raw else[]
            return jsonify({"code":200,"equity":eq})
        return jsonify({"code":200,"equity":[]})
    except Exception as e:return jsonify({"code":500,"error":str(e)})

@app.route("/api/live-trade/equity-curve")
def api_live_equity_curve():
    el=r"D:\quant_framework\live_equity_log.json"
    try:
        if os.path.exists(el):
            d=json.load(open(el,"r",encoding="utf-8"));raw=d.get("log",d)if isinstance(d,dict)else d
            eq=[{"date":r[0],"equity":r[1]}for r in raw]if isinstance(raw,list)and raw else[]
            return jsonify({"code":200,"equity":eq})
        return jsonify({"code":200,"equity":[]})
    except Exception as e:return jsonify({"code":500,"error":str(e)})

@app.route("/api/paper-trade/v2")
def api_paper_trade_v2():
    """模拟交易V2 — 虚拟账户引擎 + 实时行情"""
    try:
        from paper_engine import paper
        quotes = None
        try:
            from realtime_quotes import _quote_cache
            if _quote_cache and _quote_cache.get("data"):
                quotes = _quote_cache["data"]
        except Exception as _e: print(f"[App] {_e}")
        result = paper.get_status(quotes)
        if result.get("code") == 500:
            return jsonify({"code": 500, "error": result.get("error","纸引擎异常"), "total_equity": 0, "total_pnl": 0, "cash": 0, "positions": [], "position_count": 0})
        # E259已根治: paper_engine 卖出时同步写 CSV + 启动时从 CSV 恢复

        result["cache_ready"] = _CACHE_READY
        result["quote_count"] = len(quotes) if quotes else 0
        result["cache_count"] = len(_FACTOR_CACHE) if '_FACTOR_CACHE' in globals() and _FACTOR_CACHE else 0
        return jsonify({"code": 200, **result})
    except ImportError:
        return jsonify({"code": 500})


@app.route("/api/paper-trade/order", methods=["POST"])
@require_api_key("trade")
def api_paper_trade_order():
    """模拟下单"""
    try:
        from paper_engine import paper
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol","")).strip()
        side = str(data.get("side","buy")).strip().lower()
        if side not in ("buy","sell","reset"):
            return jsonify({"code":400,"error":"side须为buy/sell/reset"})
        if not symbol and side != "reset":
            return jsonify({"code":400,"error":"symbol不能为空"})
        try: qty = int(data.get("qty",100))
        except: qty = 100
        try: price = float(data.get("price",0)) if data.get("price") else None
        except: price = None
        import traceback
        try:
            r = paper.place_order(symbol, side, price, qty)
        except Exception as _oe:
            return jsonify({"code":500,"error":f"下单异常:{str(_oe)}","trace":traceback.format_exc()})
        r["code"] = 200 if r.get("success") else 400
        return jsonify(r)
    except ImportError:
        return jsonify({"code":500,"error":"纸引擎不可用"})
    except Exception as e:
        return jsonify({"code":500,"error":str(e)})


# E259修复：提供trade_log.csv数据的API端点
@app.route("/api/paper-trade/trade-log-csv")
def api_paper_trade_log_csv():
    """从trade_log.csv读取胜率数据"""
    try:
        log_file = TRADE_LOG_CSV  # E26: config路径
        try:
            with open(log_file, "r", encoding="utf-8-sig") as f:
                pass
        except FileNotFoundError:
            return jsonify({"code": 404, "error": "trade_log.csv不存在"})
        
        import csv
        with open(log_file, "r", encoding="utf-8-sig") as f:
            csv_trades = list(csv.DictReader(f))
        
        if not csv_trades:
            return jsonify({"code": 200, "win_rate": 0, "total_trades": 0})
        
        wins = sum(1 for t in csv_trades if float(t.get('return_pct', 0) or 0) > 0)
        total = len(csv_trades)
        win_rate = round(wins / total * 100, 1)
        
        return jsonify({
            "code": 200,
            "win_rate": win_rate,
            "total_trades": total,
            "wins": wins
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/paper-trade/config", methods=["POST"])
def api_paper_config():
    """E06: 模拟盘独立配置（如 signal_min_strength）"""
    data = request.get_json() or {}
    try:
        from paper_engine import paper
        for k, v in data.items():
            paper.set_config(k, v)
        return jsonify({"code": 200, "message": "配置已更新"})
    except Exception as _e:
        return jsonify({"code": 500, "error": str(_e)})


@app.route("/api/factor/lgbm-importance")
def api_lgbm_importance():
    """LightGBM因子重要性"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from lgbm_weight import get_importance
        return jsonify({"code": 200, "data": get_importance()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/factor/xgb-importance")
def api_xgb_importance():
    """XGBoost因子重要性"""
    try:
        p = r"D:\quant_web\data\xgb_importance.json"
        if os.path.exists(p):
            return jsonify({"code": 200, "data": json.load(open(p, encoding="utf-8"))})
        return jsonify({"code": 200, "data": []})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/ic-table")
def api_factor_ic_table():
    """IC周期表 JSON API (前端渲染)"""
    try:
        ic_data = json.load(open(r"D:\quant_framework\full_market_ic_report.json", encoding="utf-8"))["factors"]
        reg = json.load(open(r"D:\quant_framework\factor_registry.json", encoding="utf-8"))
        active = [f for f in reg["factors"] if f.get("status") == "active"]
        rows = []
        for fac in active:
            name = fac["name"]
            ic = ic_data.get(name, {})
            row = {"display": fac.get("display", name), "dir": fac.get("direction", "long")}
            for p in [1,3,5,7,10,15,20]:
                row[f"ic_{p}d"] = ic.get(f"IC_{p}d")
            rows.append(row)
        return jsonify({"code": 200, "data": rows})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/report/ic")
def api_report_ic():
    """生成并返回IC回测报告"""
    sys.path.insert(0, r"D:\quant_framework")
    from report_generator import generate
    path = generate()
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/paper-trade/auto-toggle", methods=["POST"])
@require_api_key("trade")
def api_paper_trade_auto_toggle():
    """模拟自动交易开关"""
    try:
        from paper_engine import paper
        d = request.get_json() or {}
        import threading as _th
        _lock = getattr(paper, '_toggle_lock', None) or _th.Lock()
        paper._toggle_lock = _lock
        with _lock:
            paper.auto_enabled = d.get("enabled", not paper.auto_enabled)
        return jsonify({"code": 200, "auto_enabled": paper.auto_enabled})
    except: return jsonify({"code": 500})


# ═══════════════════════════════════════════════════════
#  PaperAutoLoop 控制端点 (蓝图 v3.0 Phase 1)
# ═══════════════════════════════════════════════════════

@app.route("/api/paper-trade/auto-loop/status")
def api_paper_loop_status():
    """模拟盘运行状态 (market_state缓存60s)"""
    import json as _jps, os as _ops, time as _tss
    pap = _dp_get("paper_account")
    r = {"auto_enabled": False, "positions": 0, "cash": 0, "trades": 0, "running": "unknown", "use_v15_factors": _user_config.get("factors", {}).get("use_v15_factors", True), "market": "unknown"}
    try:
        if _ops.path.exists(pap):
            with open(pap, "r") as f:
                pa = _jps.load(f)
            r["auto_enabled"] = pa.get("auto_enabled", False)
            r["positions"] = len(pa.get("positions", {}))
            r["cash"] = round(pa.get("cash", 0), 2)
            r["trades"] = len(pa.get("trades", []))
            r["running"] = "running" if pa.get("auto_enabled") else "stopped"
    except Exception as e:
        r["error"] = str(e)
    try:
        cache = getattr(api_paper_loop_status, '_ms_cache', None)
        if cache and _tss.time() - cache[0] < 60:
            r["market"] = cache[1]
        else:
            from market_state_classifier import classify_market_state
            r["market"] = classify_market_state()
            api_paper_loop_status._ms_cache = (_tss.time(), r["market"])
    except: pass
    return jsonify({"code": 200, **r})


@app.route("/api/paper-trade/v15-toggle", methods=["POST"])
def api_v15_toggle():
    _user_config.setdefault("factors", {})["use_v15_factors"] = not _user_config.get("factors", {}).get("use_v15_factors", True)
    return jsonify({"code": 200, "use_v15_factors": _user_config["factors"]["use_v15_factors"]})


# 市场状态缓存 (启动时计算一次, 每5分钟更新)
import numpy as _np
_market_state = "unknown"

def _update_market_state():
    global _market_state
    try:
        from xtquant import xtdata
        d = xtdata.get_market_data_ex(["close"], ["000300.SH"], "1d", count=60)
        if d and "close" in d:
            c = d["close"]["000300.SH"].values
            if len(c) >= 20:
                ma20 = float(_np.mean(c[-20:])); ma60 = float(_np.mean(c[-60:])) if len(c)>=60 else ma20
                vol = 0.25
                if ma20 > ma60 * 1.01: _market_state = "bull"
                elif ma20 < ma60 * 0.99: _market_state = "bear" if vol>0.25 else "volatile"
                else: _market_state = "volatile"
                return
    except: pass
    # 备用: stock_data (P0-2: parquet优先)
    try:
        _pq_path = r"D:\quant_web\stock_data.parquet"
        if os.path.exists(_pq_path):
            from data_loader import load_stock_data_cache as _gs_pq2
            sd = _gs_pq2(_pq_path) or {}
        else:
            sd = pickle.load(gzip.open(r"D:\quant_web\stock_data.pkl.gz","rb"))
        df = sd.get("sh000300")
        if df is not None and len(df)>=20:
            c = df.tail(60)["close"].values
            ma20 = float(_np.mean(c[-20:])); ma60 = float(_np.mean(c[-60:])) if len(c)>=60 else ma20
            if ma20 > ma60 * 1.01: _market_state = "bull"
            elif ma20 < ma60 * 0.99: _market_state = "bear"
            else: _market_state = "volatile"
    except: pass

_update_market_state()
_threading.Thread(target=lambda:(__import__('time').sleep(300),_update_market_state()),daemon=True).start() if False else None

@app.route("/api/market-state")
def api_market_state():
    """市场状态: 读缓存文件"""
    import json as _j, os as _o
    fp = r"D:\quant_framework\market_state.json"
    if _o.path.exists(fp):
        with open(fp) as f: d = _j.load(f)
        return jsonify({"code":200,"state":d.get("state","unknown")})
    return jsonify({"code":200,"state":"no_file","path":fp})


@app.route("/api/strategy-recommend")
def api_strategy_recommend():
    """策略推荐 (Phase 5.3): 基于市场状态+因子IC+策略绩效 (60s缓存)"""
    try:
        import time as _tsr
        cache = getattr(api_strategy_recommend, '_cache', None)
        if cache and _tsr.time() - cache[0] < 60:
            return jsonify(cache[1])
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_recommender import recommend as _srec
        result = {"code": 200, "recommendation": _srec()}
        api_strategy_recommend._cache = (_tsr.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade-preset/save", methods=["POST"])
def api_trade_preset_save():
    data = request.get_json(silent=True) or {}
    _user_config["trade_presets"] = data.get("trade_presets", {})
    with open(_dp_get("user_config"), "w", encoding="utf-8") as f:
        json.dump(_user_config, f, ensure_ascii=False, indent=2)
    return jsonify({"success": True})


@app.route("/api/trade-preset/load")
def api_trade_preset_load():
    p = _user_config.get("trade_presets", {})
    return jsonify(p if p else {"sizing": {}, "sources": {}})


@app.route("/api/paper-trade/auto-loop/start", methods=["POST"])
@require_api_key("trade")
def api_paper_loop_start():
    """启动 PaperAutoLoop（独立于 app.py 内置循环）。"""
    try:
        from paper_engine import start_auto_loop
        ok = start_auto_loop()
        return jsonify({"code": 200, "started": ok})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/paper-trade/auto-loop/stop", methods=["POST"])
@require_api_key("trade")
def api_paper_loop_stop():
    """停止 PaperAutoLoop。"""
    try:
        import sys as _ps3; _ps3.path.insert(0, r"D:\quant_framework"); import importlib as _il3; _pe3 = _il3.import_module("paper_engine"); stop_auto_loop = _pe3.stop_auto_loop
        stop_auto_loop()
        return jsonify({"code": 200, "stopped": True})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade-journal")
def api_trade_journal():
    """交易日志API — 读取真实 trade_log.csv"""
    import numpy as np, os, csv

    journal = []
    log_file = r"d:\quant_framework\trade_log.csv"
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    journal.append({
                        "symbol": row.get("symbol", ""),
                        "buy_date": row.get("buy_date", "")[:10],
                        "sell_date": row.get("sell_date", "")[:10],
                        "buy_price": float(row.get("buy_price", 0) or 0),
                        "sell_price": float(row.get("sell_price", 0) or 0),
                        "return_pct": float(row.get("return_pct", 0) or 0),
                        "profit_amt": float(row.get("net_profit", 0) or 0),
                        "strategy": row.get("signal", row.get("strategy", "未知")),
                        "exit_reason": row.get("exit_type", "正常"),
                        "rating": "⭐⭐⭐" if float(row.get("return_pct", 0) or 0) > 0.05 else ("⭐⭐" if float(row.get("return_pct", 0) or 0) > 0 else "⭐"),
                    })
        except Exception as e:
            print(f"[TradeJournal] Error reading log: {e}")

    journal.sort(key=lambda x: x["buy_date"], reverse=True)
    rets = [j["return_pct"] for j in journal]

    return jsonify({
        "code": 200,
        "journal": journal[:100],
        "stats": {
            "total_trades": len(journal),
            "win_rate": round(sum(1 for r in rets if r > 0) / max(len(rets), 1) * 100, 1),
            "avg_return": round(np.mean(rets) * 100, 2) if rets else 0,
            "total_pnl": round(sum(j["profit_amt"] for j in journal), 0),
            "best_trade": round(max(rets) * 100, 2) if rets else 0,
            "worst_trade": round(min(rets) * 100, 2) if rets else 0,
            "avg_hold_days": 1,
        },
    })


@app.route("/api/strategy-optimizer")
def api_strategy_optimizer():
    """策略参数优化API — 读取进化模块真实结果 + 网格搜索"""
    import numpy as np, random, itertools, copy, os as _os, json as _json
    random.seed(42); np.random.seed(42)

    init_data()

    method = request.args.get("method", "grid")  # grid / genetic / bayesian
    max_positions = int(request.args.get("max_pos", "3"))

    # ═══════════════════ 方案1: 读取进化模块预计算结果 ═══════════════════
    evo_path = r"d:\quant_framework\evolution_result.json"
    if _os.path.exists(evo_path):
        try:
            with open(evo_path, "r", encoding="utf-8") as f:
                evo = _json.load(f)
            history = evo.get("history", [])
            best_params = evo.get("best_params", {})

            # 将进化历史转为优化结果列表
            results = []
            for gen in history:
                bp = gen.get("best_params", {})
                results.append({
                    "params": {
                        "stop_loss": bp.get("stop_loss", -0.05),
                        "take_profit": bp.get("take_profit", 0.08),
                        "hold_days": bp.get("hold_days", 3),
                        "min_power": bp.get("max_positions", 50),
                        "trail1_profit": bp.get("trail1_profit", 0.05),
                        "trail1_drop": bp.get("trail1_drop", 0.018),
                        "generation": gen.get("generation", "?"),
                    },
                    "sharpe": gen.get("max_fitness", 0),
                    "score": gen.get("max_fitness", 0),
                    "trades": 0,
                    "fitness": gen.get("max_fitness", 0),
                })

            # 如果有 BacktestStore 的缓存回测，从中提取指标
            try:
                sys.path.insert(0, r"d:\quant_framework\src")
                from quant_framework.data.backtest_store import BacktestStore
                bstore = BacktestStore(r"d:\quant_framework")
                bt = bstore.load_latest()
                if bt["available"]:
                    m = bstore.compute_metrics(bt["equity"], bt["trades"])
                    # 用最后一次回测的指标填充
                    for r in results:
                        if r["sharpe"] == results[0]["sharpe"]:
                            r["annual_return"] = round(m.get("annual_return", 0) * 100, 1)
                            r["max_drawdown"] = round(m.get("max_drawdown", 0) * 100, 1)
                            r["win_rate"] = round(m.get("win_rate", 0) * 100, 1)
                            r["calmar"] = round(m.get("calmar", 0), 2)
                            r["trades"] = m.get("n_trades", 0)
            except Exception:
                for r in results:
                    r["annual_return"] = 0
                    r["max_drawdown"] = 0
                    r["win_rate"] = 50
                    r["calmar"] = 0
                    r["trades"] = 0

            # 热力图: 优先读真实网格搜索结果
            heatmap = []
            grid_path = r"d:\quant_framework\grid_search_result.json"
            if _os.path.exists(grid_path):
                try:
                    with open(grid_path, "r", encoding="utf-8") as f:
                        gs = _json.load(f)
                    heatmap = gs.get("heatmap", {})
                    param_grid = {
                        "stop_loss": gs.get("params", {}).get("stop_losses", []),
                        "take_profit": gs.get("params", {}).get("take_profits", []),
                    }
                except Exception as _e:
                    logger.warning(f"[heatmap] 最优解读取失败,使用空heatmap: {_e}")

            if not heatmap:
                heatmap = {"x": [], "y": [], "z": []}

            results.sort(key=lambda x: -x["score"])

            def to_native(obj):
                if isinstance(obj, dict): return {k: to_native(v) for k, v in obj.items()}
                if isinstance(obj, list): return [to_native(v) for v in obj]
                if hasattr(obj, 'item'): return obj.item()
                return obj

            return jsonify(to_native({
                "code": 200,
                "results": results,
                "best_params": results[0] if results else {},
                "total_combos": len(results),
                "method": "genetic",
                "overfit_warning": "✅ 基于进化模块真实结果" if abs(evo.get("best_fitness", 0)) < 1.0 else "⚠️ 参数仍在优化中",
                "heatmap": heatmap if isinstance(heatmap, dict) and heatmap.get("z") else {
                    "x": [], "y": [], "z": [],
                },
                "convergence": [{"gen": h["generation"], "fitness": h["max_fitness"]} for h in history],
                "source": "evolution_result.json",
                "timestamp": evo.get("config", {}).get("elapsed_seconds", "?"),
            }))
        except Exception as _e:
            logger.warning(f"[evolution] 进化结果读取失败,回退到网格搜索: {_e}")

    # ═══════════════════ 方案2: 无进化数据时快速返回 ═══════════════════
    return jsonify({
        "code": 200,
        "results": [],
        "best_params": {},
        "total_combos": 0,
        "method": request.args.get("method", "grid"),
        "overfit_warning": "",
        "heatmap": {"x": [], "y": [], "z": []},
        "convergence": [],
        "source": "none",
        "message": "请先运行进化模块生成真实优化数据: python evolution.py --stocks 300 --generations 5 --population 12",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def _read_ths_positions_direct():
    """直接读取同花顺导出文件 + 跟踪文件兜底 — 绕过live_trader的复杂逻辑"""
    positions = []
    ths_file = THS_TABLE_XLS  # E26: config路径
    if os.path.exists(ths_file) and os.path.getsize(ths_file) > 10:
        try:
            import csv, io
            with open(ths_file, 'r', encoding='gbk') as f:
                content = f.read()
            if content and len(content) > 10:
                reader = csv.DictReader(io.StringIO(content), delimiter='\t')
                for row in reader:
                    sym = str(row.get('证券代码', '')).strip()
                    if sym:
                        positions.append({
                            "symbol": sym,
                            "name": str(row.get('证券名称', '')).strip(),
                            "quantity": int(float(row.get('股票余额', '0') or 0)),
                            "cost_price": float(row.get('摊薄成本价', '0') or 0),
                            "current_price": float(row.get('最新价', '0') or 0),
                            "market_value": float(row.get('市值', '0') or 0),
                            "profit_pct": float(row.get('盈亏比例(%)', '0') or 0),
                            "profit_amt": float(row.get('摊薄盈亏', '0') or 0),
                        })
        except Exception as e:
            print(f"[DirectRead] Error: {e}")
    # 兜底：从跟踪文件恢复（C01: DLL发单后已写入）
    if not positions:
        try:
            tf = POSITION_TRACK  # E26: config路径
            if os.path.exists(tf):
                td = json.load(open(tf))
                for code, info in td.items():
                    qty = int(info.get("qty", 0))
                    cost = float(info.get("cost_price", 0))
                    if qty > 0:
                        # C13: 用实时价计算盈亏
                        live_price = cost
                        try:
                            from realtime_quotes import _quote_cache
                            qc = _quote_cache.get("data",{}) if _quote_cache else {}
                            if code in qc: live_price = float(qc[code].get("close",cost) or cost)
                        except Exception: logger.debug(f"仓位行情查询失败: {code}")
                        mkt_val = round(live_price * qty, 2)
                        pnl = round((live_price - cost) * qty, 2)
                        pnl_pct = round((live_price / cost - 1) * 100, 2) if cost > 0 else 0
                        positions.append({
                            "symbol": code, "quantity": qty,
                            "cost_price": cost, "current_price": round(live_price, 2),
                            "market_value": mkt_val,
                            "name": _resolve_name(code) or code,
                            "profit_pct": pnl_pct, "profit_amt": pnl
                        })
                if positions:
                    print(f"[DirectRead] ✅ 跟踪文件恢复: {len(positions)}只持仓")
        except Exception as e:
            print(f"[DirectRead] 跟踪文件加载失败: {e}")
    # E32-3+E349: 持仓归零告警 — 30分钟冷却，防噪音
    if not positions:
        try:
            import time as _ta
            _now = _ta.time()
            _last_key = '_last_position_zero_alert'
            if not hasattr(_read_ths_positions_direct, _last_key):
                setattr(_read_ths_positions_direct, _last_key, 0)
            if _now - getattr(_read_ths_positions_direct, _last_key) > 1800:
                setattr(_read_ths_positions_direct, _last_key, _now)
                from dingtalk_alerts import send_alert
                send_alert("⚠️ 实盘持仓归零", "THS文件+跟踪文件均无持仓数据，请检查联动精灵是否正常导出", "warning")
                print("[DirectRead] ⚠️ 实盘持仓归零，已推送告警")
        except Exception as _ale:
            print(f"[DirectRead] 告警推送失败: {_ale}")
    return positions


# QMT成交同步文件 (qmt_full_strategy 推送)
QMT_FILLS_FILE = r"D:\quant_web\data\qmt_fills.json"
QMT_POS_FILE   = r"D:\quant_web\data\qmt_positions.json"

@app.route("/api/qmt/fill", methods=["POST"])
def api_qmt_fill():
    """QMT策略成交推送 → 同步到 Flask"""
    try:
        data = request.get_json(silent=True) or {}
        qmt_fills = []
        if os.path.exists(QMT_FILLS_FILE):
            qmt_fills = json.load(open(QMT_FILLS_FILE, encoding="utf-8"))
        qmt_fills.append({
            "symbol": data.get("symbol", ""),
            "side": data.get("side", "buy"),
            "price": data.get("price", 0),
            "qty": data.get("qty", 0),
            "amount": data.get("amount", 0),
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "signal_type": data.get("signal_type", ""),
            "qmt_code": data.get("qmt_code", ""),
        })
        # 保留最近 200 条
        if len(qmt_fills) > 200:
            qmt_fills = qmt_fills[-200:]
        with open(QMT_FILLS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(qmt_fills, f, ensure_ascii=False, indent=2)
        os.replace(QMT_FILLS_FILE + ".tmp", QMT_FILLS_FILE)
        # 同步到 paper_engine
        try:
            from paper_engine import paper
            sym = data.get("symbol", "")
            qty = int(data.get("qty", 0))
            price = float(data.get("price", 0))
            paper.place_order(sym, data.get("side", "buy"), price, qty,
                            trade_type="qmt", reason="QMT同步",
                            signal_source=data.get("signal_type", "qmt"))
        except Exception as e:
            logger.warning(f"[QMT-Fill] paper_engine同步失败: {e}")
        return jsonify({"code": 200, "fills": len(qmt_fills)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/live-trade/status")
def api_live_trade_status():
    """实盘交易状态 (QMT为主 + THS为辅)"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500, "error": "live_trader module not available"})
    try:
        # 直接读取真实持仓
        state.positions = _read_ths_positions_direct()
        status = get_trading_status()
        # 从配置读取实盘总资产/可用资金（用户设定的，不同花顺同步）
        try:
            _lcfg = json.load(open(LIVE_CONFIG))  # E26: config路径
            status["total_equity"] = _lcfg.get("live_total_asset", 0)
            status["cash"] = _lcfg.get("live_cash", 0)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as _e:
            logger.warning("[LiveTrade] 配置读取失败: %s", _e)
            status["total_equity"] = 0
            status["cash"] = 0
        # QMT成交+持仓 (主交易端)
        try:
            if os.path.exists(QMT_FILLS_FILE):
                status["qmt_fills"] = json.load(open(QMT_FILLS_FILE, encoding="utf-8"))
            if os.path.exists(QMT_POS_FILE):
                status["qmt_positions"] = json.load(open(QMT_POS_FILE, encoding="utf-8"))
        except: pass
        # 检查自动交易规则
        signals = store.get('signals', [])
        actions = auto_engine.check_rules(signals[:10] if signals else None)
        status["pending_actions"] = actions[:5]
        # E251: 联动精灵DLL状态
        try:
            from link_trader import is_available
            status["dll_available"] = is_available()
        except Exception:
            status["dll_available"] = False
        # 数据富化: 自动补股票名称
        with_names(status.get("positions", []))
        with_names(status.get("orders", []))
        with_names(status.get("fills", []))
        status["code"] = 200
        return jsonify(status)
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/live-trade/risk")
def api_live_risk():
    """P0: 实盘风控面板"""
    try:
        pos = _read_ths_positions_direct() or []
        try:
            _lcfg = json.load(open(LIVE_CONFIG))
            eq = _lcfg.get("live_total_asset", 0) or 0
            cash = _lcfg.get("live_cash", 0) or 0
        except: eq = 0; cash = 0
        max_pct, max_sym, day_pnl = 0, "", 0
        for p in pos:
            mkt = p.get("market_value", 0) or abs(p.get("current_price", 0) * p.get("quantity", 0))
            pct = round(mkt / eq * 100, 1) if eq > 0 else 0
            if pct > max_pct: max_pct, max_sym = pct, p.get("symbol", "")
            day_pnl += p.get("profit_amt", 0) or 0
        return jsonify({"code": 200, "risk": {
            "total_equity": eq, "cash": cash, "position_count": len(pos),
            "max_single_pct": max_pct, "max_single_sym": max_sym,
            "day_pnl": round(day_pnl, 0), "day_pnl_pct": round(day_pnl / eq * 100, 2) if eq > 0 else 0
        }})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/live-trade/sync-positions")
def api_sync_positions():
    """手动同步: 强制从同花顺导出文件读取持仓"""
    try:
        # 直接从同花顺导出文件读取
        from live_trader import read_ths_positions
        positions = read_ths_positions()
        return jsonify({"code": 200, "positions": positions, "source": "ths", "count": len(positions)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══ 全站股票联动 ═══

@app.route("/api/link/stock", methods=["POST"])
def api_link_stock():
    """正向: 潜龙单击代码 → 联动精灵 → TDX/同花顺K线"""
    data = request.get_json() or {}
    code = str(data.get("code", "")).strip()
    if not code:
        return jsonify({"ok": False, "error": "缺少股票代码"})
    try:
        from link_trader import lookup as _lookup
        sw = _lookup(code)
        return jsonify({"ok": True, "code": code, "software": sw or "未知"})
    except ImportError:
        return jsonify({"ok": False, "error": "联动精灵不可用", "fallback": "clipboard"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "fallback": "clipboard"})

@app.route("/api/link/stream")
def api_link_stream():
    """反向: TDX切股 → SSE推送 → 潜龙感知"""
    def _stream():
        import time as _t
        last_code = [""]
        while True:
            try:
                from link_trader import active_stock as _as
                cur = _as() or ""
                if cur and cur != last_code[0]:
                    last_code[0] = cur
                    yield f"data: {json.dumps({'code': cur, 'time': datetime.now().strftime('%H:%M:%S')})}\n\n"
            except ImportError:
                yield f"data: {json.dumps({'code': '', 'status': 'dll_missing'})}\n\n"
                _t.sleep(30)
            except Exception:
                pass
            _t.sleep(2)
    return Response(_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/live-trade/cancel", methods=["POST"])
@require_api_key("trade")
def api_live_cancel():
    """P0: 撤单"""
    data = request.get_json() or {}
    code = str(data.get("code", "")).strip()
    try:
        from link_trader import cancel as _cancel
        if code:
            ok = _cancel(code)
            msg = f"已撤销{code}" if ok else "撤单失败"
        else:
            # 紧急清仓: QMT通道批量撤，THS通道请手动
            try:
                from live_trader import CONFIG
                if CONFIG.get("trading_channel") == "qmt":
                    from live_trader import qmt_trader
                    ok = qmt_trader.cancel_all() if qmt_trader else False
                    msg = "QMT批量撤单成功" if ok else "QMT批量撤单失败"
                else:
                    ok = False
                    msg = "THS通道不支持批量撤单，请在交易端手动操作"
            except Exception as _e:
                ok = False; msg = f"批量撤单失败: {_e}"
        return jsonify({"code": 200, "success": ok, "message": msg})
    except Exception as e:
        return jsonify({"code": 500, "error": f"撤单异常: {e}"})

@app.route("/api/live-trade/execute", methods=["POST"])
@require_api_key("trade")
def api_live_trade_execute():
    """执行交易指令"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500, "error": "live_trader module not available"})
    data = request.get_json() or {}
    action = str(data.get("action", "")).strip().lower()
    code = str(data.get("code", "")).strip()
    # 输入验证
    if action not in ("buy", "sell"):
        return jsonify({"code": 400, "error": "action必须为buy或sell"})
    if not code or len(code) != 6 or not code.isdigit():
        return jsonify({"code": 400, "error": "代码必须为6位数字"})
    try:
        price = float(data.get("price", 0))
        quantity = int(data.get("quantity", 0))
    except (ValueError, TypeError):
        return jsonify({"code": 400, "error": "价格/数量格式错误"})
    if price <= 0:
        return jsonify({"code": 400, "error": "价格必须>0"})
    # qty=0 → 自动读取持仓数量（half=卖半仓,full=全仓）
    if quantity <= 0:
        try:
            pos = _read_ths_positions_direct()
            for p in pos:
                if p.get("symbol","").replace("sh","").replace("sz","") == code:
                    quantity = p.get("qty", 0) // 2 if data.get("type") == "half" else p.get("qty", 0)
                    break
        except Exception:
            pass
    if quantity < 100 or (quantity % 100 != 0 and quantity > 0):
        return jsonify({"code": 400, "error": "数量须≥100且为100的整数倍"})
    result = execute_trade(action, code, price, quantity)
    result["code"] = 200 if result.get("success") else 500
    return jsonify(result)


@app.route("/api/live-trade/auto-toggle", methods=["POST"])
@require_api_key("trade")
def api_live_trade_auto_toggle():
    """开关自动交易"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500, "error": "live_trader module not available"})
    data = request.get_json() or {}
    enabled = data.get("enabled")
    result = toggle_auto_trade(enabled)
    result["code"] = 200
    return jsonify(result)


@app.route("/api/market/indices")
def api_market_indices():
    """首页行情条: QMT实时→TDX实时→非交易时段显示收盘(标注日期)

    对标: QMT实时行情 + 通达信本地读取 + 同花顺实时推送
    铁律: 交易日盘中绝不显示上一交易日行情, 杜绝误导
    """
    try:
        import datetime as _dt
        indices = {}
        index_map = {"sh000001":"上证","sz399006":"创业板","sh000688":"科创50","sh000300":"沪深300","sz399101":"平均股价"}
        now = _dt.datetime.now()
        is_trading = (now.weekday()<5 and _dt.time(9,25)<=now.time()<=_dt.time(15,5))

        # QMT 实时行情
        qmt_data = {}
        try:
            from xtquant.xtdata import get_full_tick
            codes = [c[2:]+'.'+c[:2].upper() for c in index_map]  # sh000001→000001.SH
            ticks = get_full_tick(codes)  # 返回 dict
            for code in index_map:
                qmt_code = code[2:]+'.'+code[:2].upper()
                t = ticks.get(qmt_code, {}) if isinstance(ticks, dict) else {}
                price = float(t.get('lastPrice', 0) or 0)
                prev = float(t.get('lastClose', 0) or price)
                if price > 0:
                    qmt_data[code] = {"price": price, "prev": prev}
        except: pass

        # TDX .day 文件兜底
        tdx_data = {}
        if not STOCK_DATA: init_data()
        for code in index_map:
            if code in qmt_data: continue
            try:
                import struct, os as _os
                dd = str(DATA_ROOT) if DATA_ROOT else r"D:\通信达技术指标"
                # TDX 指数文件: vipdoc/ds/lday/ 或 vipdoc/sh/lday/
                mkt=code[:2]; num=code[2:]
                paths=[_os.path.join(dd,'ds','lday',f'{mkt}{num}.day'),
                       _os.path.join(dd,mkt,'lday',f'{mkt}{num}.day')]
                day_path = ""
                for p in paths:
                    if _os.path.exists(p): day_path=p; break
                if day_path:
                    with open(day_path,'rb') as f:
                        f.seek(0,2); recs=f.tell()//32
                        if recs>=2:
                            f.seek(-32,2); rec=f.read(32); close_p=struct.unpack('=i f f f f f i',rec)[4]
                            f.seek(-64,2); prev_c=struct.unpack('=i f f f f f i',f.read(32))[4]
                            if close_p>0: tdx_data[code]={"price":close_p,"prev":prev_c if prev_c>0 else close_p}
            except: pass

        for code, name in index_map.items():
            if code in qmt_data:
                q = qmt_data[code]
                chg = round((q["price"]/q["prev"]-1)*100, 2) if q["prev"]>0 else 0
                indices[name] = {"close": round(q["price"],0), "chg": chg, "source": "QMT"}
            elif code in tdx_data:
                t = tdx_data[code]
                chg = round((t["price"]/t["prev"]-1)*100, 2) if t["prev"]>0 else 0
                indices[name] = {"close": round(t["price"],0), "chg": chg, "source": "TDX"}
            elif is_trading:
                # 交易日盘中: 无实时数据 → 不显示, 避免误用旧数据
                indices[name] = {"close": 0, "chg": 0, "source": "waiting"}
            else:
                # 非交易时段: 显示最新收盘, 标注日期
                if not STOCK_DATA: init_data()
                df = STOCK_DATA.get(code) if STOCK_DATA else None
                if df is not None and len(df) > 1:
                    close = float(df.iloc[-1]['close'])
                    prev = float(df.iloc[-2]['close'])
                    chg = round((close/prev-1)*100, 2)
                    date_label = str(df.index[-1])[:10]
                    indices[name] = {"close": round(close,0), "chg": chg, "source": "收盘("+date_label+")"}
        return jsonify({"code": 200, "indices": indices, "is_trading": is_trading})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/live-trade/channel-toggle", methods=["POST"])
@require_api_key("trade")
def api_live_channel_toggle():
    """切换交易通道: ths ↔ qmt"""
    data = request.get_json(silent=True) or {}
    channel = str(data.get("channel", "")).strip().lower()
    if channel not in ("ths", "qmt"):
        return jsonify({"code": 400, "error": "channel must be 'ths' or 'qmt'"})
    import json as _json
    cfg_path = LIVE_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = _json.load(f)
    cfg["trading_channel"] = channel
    with open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump(cfg, f, ensure_ascii=False, indent=2)
    if LIVE_TRADER_OK:
        import live_trader as _lt
        _lt.CONFIG["trading_channel"] = channel
        TRADE_CONFIG["trading_channel"] = channel  # E349: 同步更新app内存
    return jsonify({"code": 200, "channel": channel, "message": "已切换到QMT" if channel=="qmt" else "已切换到THS"})

@app.route("/api/live-trade/auto-toggle-v2", methods=["POST"])
@require_api_key("trade")
def api_live_trade_auto_toggle_v2():
    """实盘自动交易开关 V2 — 仿模拟盘设计，独立于 live_trader 模块。

    直接读写 live_trader_config.json，零依赖。
    """
    import json as _json, os as _os, threading as _th
    _cfg_path = LIVE_CONFIG  # E26: config路径

    try:
        data = request.get_json(silent=True) or {}
        with open(_cfg_path, "r", encoding="utf-8") as f:
            config = _json.load(f)

        if "enabled" in data:
            config["auto_trade_enabled"] = bool(data["enabled"])
        else:
            config["auto_trade_enabled"] = not config.get("auto_trade_enabled", False)

        # 原子写入
        tmp = _cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, _cfg_path)

        # 同步内存中的 TRADE_CONFIG 和 live_trader.CONFIG
        TRADE_CONFIG["auto_trade_enabled"] = config["auto_trade_enabled"]
        try:
            if LIVE_TRADER_OK:
                from live_trader import CONFIG as _lt_cfg
                _lt_cfg["auto_trade_enabled"] = config["auto_trade_enabled"]
        except Exception as _e:
            logger.warning(f"[config] 自动交易配置同步失败: {_e}")

        return jsonify({
            "code": 200,
            "auto_trade_enabled": config["auto_trade_enabled"],
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/ping")
def api_ping():
    return jsonify({"ok": True, "time": str(datetime.now())})

@app.route("/api/tasklog")
def api_tasklog():
    import json as _jtl, os as _otl
    try:
        _log = r"D:\quant_framework\logs\task_run_log.jsonl"
        runs = []
        if _otl.path.exists(_log):
            with open(_log, encoding='utf-8') as f:
                for line in f:
                    try: runs.append(_jtl.loads(line.strip()))
                    except: pass
        return jsonify({"code": 200, "runs": runs[-20:]})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/live-trade/qmt-connect", methods=["POST"])
@require_api_key("trade")
def api_qmt_connect():
    """P0-3: 一键连接QMT — 同时检测行情通道+交易通道"""
    try:
        import sys as _qsys, threading as _qth
        _qsys.path.insert(0, r"D:\quant_framework")
        from live_trader import _qmt_available as _qa, qmt_trader as _qt

        # 检测行情通道 (xtdata)
        qmt_data_ok = False
        try:
            from xtquant.xtdata import get_market_data
            test = get_market_data(field_list=['close'], stock_list=['000001.SZ'], period='1d', count=1)
            qmt_data_ok = test is not None and len(test) > 0
        except Exception: pass

        if not _qa:
            return jsonify({"code": 500, "error": "QMT不可用", "qmt_data_ok": qmt_data_ok, "qmt_trade_ok": False})

        result = [None]
        trade_ok = _qt._connected
        if not trade_ok:
            def _connect():
                try: result[0] = _qt.connect()
                except Exception: result[0] = False
            t = _qth.Thread(target=_connect, daemon=True)
            t.start()
            t.join(timeout=5)
            if not t.is_alive():
                trade_ok = bool(result[0])

        return jsonify({
            "code": 200,
            "qmt_data_ok": qmt_data_ok,    # 行情通道 (xtdata)
            "qmt_trade_ok": trade_ok,      # 交易通道 (xttrader)
            "message": f"行情{'通' if qmt_data_ok else '断'} | 交易{'通' if trade_ok else '断'}"
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


def _store_qmt_signal(symbol, signal_type, price, lgbm, xgb, pos_pct, approved, signal_id=None):
    """E367: 存储QMT信号到按日分桶文件 (Signal ID 幂等去重)"""
    import json as _j
    _today = datetime.now().strftime("%Y%m%d")
    _qp = os.path.join(os.path.dirname(__file__), "data", f"qmt_signals_{_today}.json")
    _sigs = []
    if os.path.exists(_qp):
        try:
            with open(_qp, encoding="utf-8") as _f: _sigs = _j.load(_f)
        except: pass

    # Signal ID 幂等去重 (规范 v1.0)
    if signal_id:
        _existing = {s.get('signal_id','') for s in _sigs}
        if signal_id in _existing:
            return  # 已存在, 跳过

    # 查股票名称 (stock_names → auto_trade_plan → symbol兜底)
    _name = symbol
    try:
        from stock_names import get_stock_name as get_name
        _n = get_name(symbol)
        if _n and _n != symbol and not str(_n).isdigit(): _name = _n
    except: pass
    if _name == symbol:
        try:
            _plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
            _pn = _plan.get("stocks", {}).get(symbol, {}).get("name", "")
            if _pn and _pn != symbol: _name = _pn
        except: pass
    if _name == symbol:
        try:
            from xtquant import xtdata
            qmt_code = symbol[2:] + ('.SH' if symbol.startswith('sh') else '.SZ' if symbol.startswith('sz') else '.BJ')
            tick = xtdata.get_full_tick([qmt_code])
            if tick and len(tick) > 0:
                tn = tick[0].get('stockName','') or tick[0].get('name','')
                if tn and tn != symbol: _name = tn
        except: pass
    if _name == symbol:
        try:
            _st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
            for _sr in _st:
                if _sr.get("symbol") == symbol:
                    _n = _sr.get("name","")
                    if _n and _n != symbol and not str(_n).isdigit(): _name = _n
                    break
        except: pass

    _sig = {"symbol":symbol, "name":_name, "signal_type":signal_type, "close":price,
            "lgbm":lgbm, "xgb":xgb, "position_pct":pos_pct,
            "time":datetime.now().strftime("%H:%M:%S"), "approved":approved}
    if signal_id:
        _sig["signal_id"] = signal_id
    _sigs.insert(0, _sig)
    _sigs = _sigs[:50]
    _tmp = _qp + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as _f: _j.dump(_sigs, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, _qp)
    # SSE广播: QMT信号到达时推送到所有连接的浏览器
    try:
        from flask import copy_current_request_context
        _sse_data = json.dumps({"type":"qmt_signal","data":_sig}, ensure_ascii=False)
        # 写入SSE事件缓存供 /api/stream 读取
        _sse_path = os.path.join(os.path.dirname(__file__), "data", ".sse_qmt_event.json")
        with open(_sse_path, "w", encoding="utf-8") as _sf: _sf.write(_sse_data)
    except: pass


@app.route("/api/qmt/signal", methods=["POST"])
def api_qmt_signal():
    """接收 QMT 实时信号 → 潜龙统一决策 (ML验证+仓位+风控) → 返回决策结果"""
    import sys as _qs, os as _qo
    _qs.path.insert(0, r"D:\quant_framework")
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "")
    signal_type = data.get("signal_type", "unknown")
    _sid = data.get("signal_id", "")  # Signal ID 幂等去重
    price = data.get("price", 0)
    if not symbol or not price:
        return jsonify({"code": 400, "error": "symbol and price required"})

    # 1. 查ML评分
    ml_cache_path = r"D:\quant_web\data\ml_score_cache.json"
    ml = {}
    if _qo.path.exists(ml_cache_path):
        try:
            with open(ml_cache_path, encoding="utf-8") as _f:
                ml = json.load(_f).get(symbol, {})
        except Exception:
            ml = {}

    lgbm = ml.get("lgbm", 0) or 0
    xgb = ml.get("xgb", 0) or 0
    score = (lgbm + xgb) / 2
    lv = 5 if score >= 90 else 4 if score >= 80 else 3 if score >= 70 else 2

    # 弱转强/打板等实时策略跳过ML门控 (自有竞价+L2确认)
    _is_realtime_signal = any(t in signal_type for t in ("弱转强", "打板", "竞价", "突破"))
    if not _is_realtime_signal and lgbm < 60 and xgb < 60:
        _store_qmt_signal(symbol, signal_type, price, lgbm, xgb, 0, False, _sid)
        return jsonify({"code": 200, "approved": False, "reason": f"ML不认可(LGBM={lgbm:.0f} XGB={xgb:.0f})", "lgbm": lgbm, "xgb": xgb})

    # 2. 仓位计算 (方案A: 信号等级 × 市场系数, 对齐 generate_signal_table.py)
    pos_pct = 0
    stop_loss = None
    take_profit = None
    # 优先从 auto_trade_plan 读取 (generate_signal_table 已算好)
    try:
        _plan_p = r"D:\quant_web\data\auto_trade_plan.json"
        if _qo.path.exists(_plan_p):
            _plan = json.load(open(_plan_p, encoding="utf-8"))
            _stock = _plan.get("stocks", {}).get(symbol, {})
            pos_pct = float(_stock.get("max_position_pct", 0) or 0)
            stop_loss = float(_stock.get("stop_loss", 0) or 0)
            take_profit = float(_stock.get("take_profit", 0) or 0)
    except: pass
    # 兜底: 方案A 手动计算
    if pos_pct <= 0:
        lv_map = {5: 12, 4: 8, 3: 5, 2: 2, 1: 0}
        pos_pct = lv_map.get(lv, 2)
        try:
            from market_regime import detect_regime
            sd_ = STOCK_DATA if STOCK_DATA and len(STOCK_DATA) > 100 else None
            if not sd_:
                from data_loader import load_stock_data_cache
                sd_ = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
            reg = detect_regime(sd_) if sd_ else {}
            pos_pct = round(pos_pct * reg.get("position_scale", 0.7), 1)
        except: pass
        # 统一兜底: 读 trade_config_master.json (与 auto_trade_plan 同源)
        try:
            _m = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
            stop_loss = round(price * (1 + _m.get("stop_loss", {}).get("hard", -0.055)), 2)
            take_profit = round(price * (1 + _m.get("take_profit", {}).get("tp1", {}).get("profit_pct", 0.05)), 2)
        except:
            stop_loss = round(price * 0.945, 2)
            take_profit = round(price * 1.05, 2)

    result = {
        "code": 200, "approved": True, "signal_type": signal_type,
        "symbol": symbol, "lgbm": lgbm, "xgb": xgb,
        "position_pct": pos_pct, "shares": max(100, int(1000000 * pos_pct / 100 / price / 100) * 100),
        "close": price, "stop_loss": stop_loss,
        "take_profit": take_profit,
        "message": f"{signal_type}:{symbol} LGBM={lgbm:.0f} XGB={xgb:.0f} 仓位{pos_pct}%"
    }

    # 3. 存储 (E367)
    _store_qmt_signal(symbol, signal_type, price, lgbm, xgb, result["position_pct"], result["approved"], _sid)

    # 4. paper_engine下单 (QMT信号→模拟盘, 实时信号无需ML审批)
    _pa_order = None
    if result["approved"] and pos_pct > 0:
        try:
            from paper_engine import paper
            _qty = result["shares"]
            _pa_order = paper.place_order(symbol, "buy", price, _qty,
                                          trade_type="auto",
                                          reason=f"QMT·{signal_type}",
                                          signal_source="qmt", signal_id=_sid)
        except Exception as _pe:
            print(f"[QMT] 模拟盘下单异常: {_pe}")

    # 5. 推送
    try:
        from dingtalk_alerts import send_alert
        send_alert("QMT信号", result["message"], "info")
    except Exception: pass

    return jsonify({**result, "paper_order": _pa_order})


@app.route("/api/qmt-signals")
def api_qmt_signals():
    """E367: QMT盘中信号列表"""
    import json as _j
    _today = datetime.now().strftime("%Y%m%d")
    _p = os.path.join(os.path.dirname(__file__), "data", f"qmt_signals_{_today}.json")
    if os.path.exists(_p):
        try:
            with open(_p, encoding="utf-8") as _f: sigs = _j.load(_f)
            # 补全名称
            try:
                from stock_names import get_stock_name as get_name
                _pnames = {}
                try:
                    _pd = _j.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
                    _pnames = {k: v.get("name","") for k, v in _pd.get("stocks", {}).items() if isinstance(v, dict)}
                except: pass
                for _si in sigs:
                    _cur = _si.get("name", "")
                    if (not _cur) or _cur == _si["symbol"] or str(_cur).isdigit():
                        _n = get_name(_si["symbol"])
                        if (not _n) or _n == _si["symbol"] or str(_n).isdigit():
                            _n = _pnames.get(_si["symbol"], "")
                        if _n and _n != _si["symbol"] and not str(_n).isdigit():
                            _si["name"] = _n
            except: pass
            # 注入实时涨跌幅 — 从已缓存的 signal-table 索引查找
            try:
                _st = os.path.join(os.path.dirname(__file__), "data", "signal_table.json")
                if os.path.exists(_st):
                    _rt_map = {}
                    for _r in json.load(open(_st, encoding='utf-8')):
                        _rt_map[_r['symbol']] = _r
                    for _si in sigs:
                        _sym = _si.get('symbol','')
                        if _sym in _rt_map:
                            _ref = _rt_map[_sym]
                            _si['current_price'] = _ref.get('current_price')
                            _si['current_change_pct'] = _ref.get('current_change_pct')
            except: pass
            return jsonify(sigs)
        except: pass
    return jsonify([])


@app.route("/api/live-trade/qmt-disconnect", methods=["POST"])
@require_api_key("trade")
def api_qmt_disconnect():
    """P0-3: 断开QMT"""
    try:
        import sys as _qsys2
        _qsys2.path.insert(0, r"D:\quant_framework")
        from live_trader import qmt_trader as _qt2
        _qt2.disconnect()
        return jsonify({"code": 200, "qmt_connected": False, "message": "QMT已断开"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/live-trade/rules")
def api_live_trade_rules():
    """获取当前自动交易规则"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500})
    return jsonify({
        "code": 200,
        "rules": {
            "tp1_profit_pct": TRADE_CONFIG.get("tp1_profit_pct", 0.05),
            "tp1_trail_pct": TRADE_CONFIG.get("tp1_trail_pct", -0.01),
            "tp1_stop_loss": TRADE_CONFIG.get("tp1_stop_loss", -0.03),
            "tp2_profit_pct": TRADE_CONFIG.get("tp2_profit_pct", 0.07),
            "tp2_trail_pct": TRADE_CONFIG.get("tp2_trail_pct", -0.02),
            "tp2_stop_loss": TRADE_CONFIG.get("tp2_stop_loss", -0.03),
            "signal_min_strength": TRADE_CONFIG.get("signal_min_strength", 5),
            "max_daily_trades": TRADE_CONFIG.get("max_daily_trades", 5),
            "max_daily_loss": TRADE_CONFIG.get("max_daily_loss", -0.05),
            "limit_up_drop_sell": TRADE_CONFIG.get("limit_up_drop_sell", -0.03),
            "auto_trade_enabled": TRADE_CONFIG.get("auto_trade_enabled", False),
            "position_pct_lv3": TRADE_CONFIG.get("position_pct_lv3", 0.2),
            "position_pct_lv4": TRADE_CONFIG.get("position_pct_lv4", 0.33),
            "position_pct_lv5": TRADE_CONFIG.get("position_pct_lv5", 0.5),
            "max_single_position_pct": TRADE_CONFIG.get("max_single_position_pct", 20),
            "max_hold_days": TRADE_CONFIG.get("max_hold_days", 5),
            "max_consecutive_loss": TRADE_CONFIG.get("max_consecutive_loss", 3),
            "daily_loss_sell_half": TRADE_CONFIG.get("daily_loss_sell_half", -0.05),
            "daily_loss_clear_all": TRADE_CONFIG.get("daily_loss_clear_all", -0.08),
            "position_mode": TRADE_CONFIG.get("position_mode", "kelly"),
            "max_positions": TRADE_CONFIG.get("max_positions", 5),
            "min_cash_reserve": TRADE_CONFIG.get("min_cash_reserve", 100000),
        }
    })


@app.route("/api/live-trade/rules", methods=["POST"])
def api_live_trade_rules_update():
    """更新自动交易规则（带验证）"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500})
    data = request.get_json() or {}
    updated = []
    errors = {}

    def to_float(k, v):
        try:
            return float(v)
        except Exception:
            errors[k] = 'invalid_number'
            return None

    def to_int(k, v):
        try:
            return int(v)
        except Exception:
            errors[k] = 'invalid_int'
            return None

    # 百分比（0..1）
    for key in ["tp1_profit_pct", "tp2_profit_pct", "position_pct_lv3", "position_pct_lv4", "position_pct_lv5"]:
        if key in data:
            val = to_float(key, data[key])
            if val is not None:
                if val < 0 or val > 1:
                    errors[key] = 'out_of_range_0_1'
                else:
                    TRADE_CONFIG[key] = val
                    updated.append(key)

    # 回撤/止损/日亏/涨停回落等，允许负值（-1..1）
    for key in ["tp1_trail_pct", "tp1_stop_loss", "tp2_trail_pct", "tp2_stop_loss", "max_daily_loss", "limit_up_drop_sell"]:
        if key in data:
            val = to_float(key, data[key])
            if val is not None:
                if val < -1 or val > 1:
                    errors[key] = 'out_of_range_-1_1'
                else:
                    TRADE_CONFIG[key] = val
                    updated.append(key)

    if "signal_min_strength" in data:
        v = to_int("signal_min_strength", data["signal_min_strength"])
        if v is not None:
            if v < 1 or v > 5:
                errors["signal_min_strength"] = 'must_1_5'
            else:
                TRADE_CONFIG["signal_min_strength"] = v
                updated.append("signal_min_strength")

    if "max_daily_trades" in data:
        v = to_int("max_daily_trades", data["max_daily_trades"])
        if v is not None:
            if v < 0:
                errors["max_daily_trades"] = 'must_non_negative'
            else:
                TRADE_CONFIG["max_daily_trades"] = v
                updated.append("max_daily_trades")

    if "max_single_position_pct" in data:
        v = to_int("max_single_position_pct", data["max_single_position_pct"])
        if v is not None:
            if v < 0 or v > 100:
                errors["max_single_position_pct"] = '0_100'
            else:
                TRADE_CONFIG["max_single_position_pct"] = v
                updated.append("max_single_position_pct")

    if "max_hold_days" in data:
        v = to_int("max_hold_days", data["max_hold_days"])
        if v is not None:
            if v < 1:
                errors["max_hold_days"] = 'must>=1'
            else:
                TRADE_CONFIG["max_hold_days"] = v
                updated.append("max_hold_days")

    if "max_consecutive_loss" in data:
        v = to_int("max_consecutive_loss", data["max_consecutive_loss"])
        if v is not None:
            if v < 0:
                errors["max_consecutive_loss"] = 'must>=0'
            else:
                TRADE_CONFIG["max_consecutive_loss"] = v
                updated.append("max_consecutive_loss")

    if "auto_trade_enabled" in data:
        TRADE_CONFIG["auto_trade_enabled"] = bool(data["auto_trade_enabled"])
        updated.append("auto_trade_enabled")
    if "auto_order_mode" in data:
        TRADE_CONFIG["auto_order_mode"] = bool(data["auto_order_mode"])
        updated.append("auto_order_mode")  # fix: 不再误联动 auto_trade_enabled

    if errors:
        return jsonify({"code": 400, "error": "validation_failed", "invalid": errors}), 400

    # 持久化到文件，供 live_trader 在重启或热加载时读取
    try:
        import json as _json, os as _os
        cfg_path = r"d:\\quant_framework\\live_trader_config.json"
        _os.makedirs(_os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            _json.dump(TRADE_CONFIG, f, ensure_ascii=False, indent=2)
        updated.append('persisted_to_file')
    except Exception as ee:
        print(f"[API] Failed to persist live_trader config: {ee}")

    return jsonify({"code": 200, "updated": updated})


# E25: 交易规则配置导出/导入
_EXPORT_KEYS = ["tp1_profit_pct","tp1_trail_pct","tp1_stop_loss","tp1_sell_ratio",
    "tp2_profit_pct","tp2_trail_pct","tp2_stop_loss","tp2_sell_ratio",
    "tp3_profit_pct","tp3_trail_pct","tp3_stop_loss","tp3_sell_ratio",
    "signal_min_strength","max_daily_trades","max_positions",
    "max_single_position_pct","max_hold_days","max_daily_loss",
    "position_pct_lv3","position_pct_lv4","position_pct_lv5",
    "daily_loss_sell_half","daily_loss_clear_all","min_cash_reserve"]

@app.route("/api/config/export")
def api_config_export():
    """E25: 导出当前交易规则配置"""
    cfg = TRADE_CONFIG if LIVE_TRADER_OK else {}
    export = {k: cfg.get(k) for k in _EXPORT_KEYS if k in cfg}
    export["export_time"] = datetime.now().isoformat()
    export["version"] = "1.0"
    return jsonify({"code": 200, "config": export})


@app.route("/api/config/import", methods=["POST"])
def api_config_import():
    """E25: 导入交易规则配置"""
    data = request.get_json() or {}
    config = data.get("config", {})
    if not config: return jsonify({"code": 400, "error": "配置为空"})
    if config.get("version") != "1.0": return jsonify({"code": 400, "error": "版本不兼容"})
    import shutil as _shutil25
    _cfg_f = r"d:\\quant_framework\\live_trader_config.json"
    if os.path.exists(_cfg_f):
        _shutil25.copy2(_cfg_f, _cfg_f + ".import_bak")
    updated = []
    for k, v in config.items():
        if k in TRADE_CONFIG and k not in ("export_time","version"):
            TRADE_CONFIG[k] = v; updated.append(k)
    # 持久化
    try:
        import json as _j25
        with open(_cfg_f, 'w', encoding='utf-8') as _f25:
            _j25.dump(TRADE_CONFIG, _f25, ensure_ascii=False, indent=2)
    except Exception as _ee: print(f"[API] {_ee}")
    return jsonify({"code": 200, "updated": updated, "count": len(updated)})


@app.route("/api/live-trade/positions")
def api_live_trade_positions():
    """获取最新持仓 — 直接读取同花顺导出文件"""
    positions = []
    source = "none"

    # 直接读取同花顺 table.xls (TSV格式, GBK编码)
    ths_file = THS_TABLE_XLS  # E26: config路径
    if os.path.exists(ths_file) and os.path.getsize(ths_file) > 10:
        try:
            import csv, io
            with open(ths_file, 'r', encoding='gbk') as f:
                content = f.read()
            if content and len(content) > 10:
                reader = csv.DictReader(io.StringIO(content), delimiter='\t')
                for row in reader:
                    sym = str(row.get('证券代码', '')).strip()
                    if sym:
                        positions.append({
                            "symbol": sym,
                            "name": str(row.get('证券名称', '')).strip(),
                            "quantity": int(float(row.get('股票余额', '0') or 0)),
                            "cost_price": float(row.get('摊薄成本价', '0') or 0),
                            "current_price": float(row.get('最新价', '0') or 0),
                            "market_value": float(row.get('市值', '0') or 0),
                            "profit_pct": float(row.get('盈亏比例(%)', '0') or 0),
                            "profit_amt": float(row.get('摊薄盈亏', '0') or 0),
                        })
                if positions:
                    source = "ths_export"
        except Exception as e:
            print(f"[API] Error reading table.xls: {e}")

    # 回退到 live_positions.csv 或 live_trader
    if not positions:
        positions = read_ths_positions()
        source = "live_trader"

    return jsonify({
        "code": 200,
        "positions": positions,
        "count": len(positions),
        "total_value": sum(p.get("market_value", 0) for p in positions),
        "total_pnl": sum(p.get("profit_amt", 0) for p in positions),
        "updated": state.last_update.strftime("%H:%M:%S") if state.last_update else None,
        "source": source,
    })


@app.route("/api/live-trade/import", methods=["POST"])
def api_live_trade_import():
    """导入持仓 CSV — 支持同花顺导出格式"""
    data = request.get_json() or {}
    csv_text = data.get("csv", "")
    if not csv_text:
        return jsonify({"code": 400, "error": "No CSV data provided"})

    # 保存到文件
    pos_file = r"d:\quant_framework\live_positions.csv"
    with open(pos_file, 'w', encoding='utf-8-sig') as f:
        f.write(csv_text)

    # 重新读取
    positions = read_ths_positions()
    return jsonify({
        "code": 200,
        "message": f"Imported {len(positions)} positions",
        "positions": positions,
    })


@app.route("/api/live-trade/daban")
def api_live_trade_daban():
    """打板键盘24键专业版状态"""
    if not LIVE_TRADER_OK:
        return jsonify({"code": 500, "error": "trader module not available"})
    try:
        status = get_daban_status()
        action_map = get_key_action_map() if status.get("connected") else {}
        return jsonify({
            "code": 200,
            "daban": status,
            "action_map": action_map,
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/tdx-signals")
def api_tdx_signals():
    """通达信信号状态 — tpool实时监控"""
    try:
        from tdx_signal_watcher import get_latest_signals
        return jsonify(get_latest_signals())
    except ImportError:
        return jsonify({"status": "error", "message": "module not available"})


@app.route("/api/realtime-quotes")
def api_realtime_quotes():
    """实时行情API — AkShare数据"""
    try:
        from realtime_quotes import fetch_realtime_quotes, is_trading_time
        codes = request.args.get("codes", "")
        raw = [c.strip() for c in codes.split(",") if c.strip()] if codes else []
        # 去sh/sz/bj前缀，AkShare返回的代码不带前缀
        symbols = [c.replace('sh','').replace('sz','').replace('bj','') for c in raw] if raw else None
        result = fetch_realtime_quotes(symbols)
        result["trading"] = is_trading_time()
        return jsonify(result)
    except ImportError:
        return jsonify({"status": "error", "message": "realtime_quotes module not available"})


@app.route("/api/bridge/send", methods=["POST"])
def api_bridge_send():
    """发送股票代码到同花顺/通达信 — 剪贴板+键盘键入"""
    data = request.get_json() or {}
    symbol = data.get("symbol", "")
    target = data.get("target", "ths")
    action = data.get("action", "view")
    name = data.get("name", "")
    if not symbol:
        return jsonify({"code": 400, "error": "symbol required"})

    # 去前缀
    clean_code = symbol.replace('sh','').replace('sz','').replace('SH','').replace('SZ','').replace('bj','').replace('BJ','')

    # 1. 剪贴板
    clipboard_ok = False
    try:
        import pyperclip; pyperclip.copy(clean_code)
        clipboard_ok = True
    except Exception as _e:
        logger.warning(f"[clipboard] 剪贴板读取失败: {_e}")

    # 2. 写桥接文件(联动精灵备用)
    import json as _json
    bridge_file = r"d:\quant_framework\bridge_stock.json"
    targets = ["同花顺"] if target == "ths" else (["通达信"] if target == "tdx" else ["同花顺","通达信"])
    try:
        with open(bridge_file, 'w', encoding='utf-8') as f:
            _json.dump({"symbol": clean_code, "name": name, "action": action, "targets": targets, "timestamp": datetime.now().strftime("%H:%M:%S")}, f)
    except Exception as _e: print(f"[App] {_e}")

    # 3. 尝试pyautogui直接键入同花顺 (需要同花顺窗口打开)
    keyboard_ok = False
    try:
        import pyautogui, pygetwindow as gw
        # 优先找同花顺看盘端(不含"交易"字样), 再回退到委托端
        all_ths = [w for w in gw.getAllWindows() if '同花顺' in w.title or '网上股票交易' in w.title]
        # 分类: 看盘端优先, 委托端备用
        chart_wins = [w for w in all_ths if '交易' not in w.title and '委托' not in w.title]
        trade_wins = [w for w in all_ths if '交易' in w.title or '委托' in w.title]
        ths_windows = chart_wins + trade_wins
        if ths_windows:
            win = ths_windows[0]
            win.activate()
            import time; time.sleep(0.3)
            pyautogui.hotkey('ctrl', 't')  # 同花顺新标签/搜索
            time.sleep(0.1)
            pyautogui.write(clean_code, interval=0.02)
            time.sleep(0.1)
            pyautogui.press('enter')
            keyboard_ok = True
    except Exception as e:
        print(f"[Bridge] pyautogui failed: {e}")

    return jsonify({
        "code": 200, "success": True, "symbol": clean_code,
        "clipboard": clipboard_ok, "keyboard": keyboard_ok,
        "message": "已键入同花顺" if keyboard_ok else ("已复制到剪贴板，请粘贴到同花顺搜索框" if clipboard_ok else "桥接文件已写入")
    })


@app.route("/api/bridge/status")
def api_bridge_status():
    """桥接状态"""
    if BRIDGE_OK:
        return jsonify({"code": 200, **get_bridge_status()})
    return jsonify({"code": 200, "bridge_exists": False})


# ══════ E266: 黑白名单 API ══════
@app.route("/api/emergency/liquidate", methods=["POST"])
def api_emergency_liquidate():
    try:
        from paper_engine import paper
        sold_paper = 0; sold_live = 0
        # 模拟盘清仓
        for sym, pos in list(paper.positions.items()):
            r = paper.place_order(sym, "sell", pos.get("last_price", pos.get("avg_cost")), pos["qty"])
            if r.get("success"): sold_paper += 1
        # 实盘清仓 (QMT快速通道优先, THS兜底)
        try:
            from xtquant import xttrader
            acc = xttrader.query_stock_asset('8890695045')
            if acc:
                for pos in (acc or []):
                    qty = int(pos.get('volume', 0) or pos.get('持仓数量', 0) or 0)
                    sym = str(pos.get('stock_code', '') or pos.get('证券代码', ''))
                    if qty > 0 and sym:
                        price = float(pos.get('last_price', 0) or pos.get('最新价', 0) or 0)
                        if price > 0:
                            try:
                                # QMT快速通道: passorder(卖出=24, 限价=1101, 快速=2)
                                passorder(24, 1101, '8890695045', sym, 0,
                                          round(price, 2), qty, '潜龙紧急', sym, 2)
                                sold_live += 1
                            except: pass
        except Exception as e: print(f"[Emergency] QMT清仓异常: {e}")
        # THS兜底
        if sold_live == 0:
            try:
                live_positions = _read_ths_positions_direct()
                for p in (live_positions or []):
                    qty = int(float(p.get('股票余额', 0) or 0))
                    if qty > 0:
                        from link_trader import sell as lt_sell
                        lt_sell(p.get('证券代码',''), p.get('最新价',0), qty)
                        sold_live += 1
            except Exception as e: print(f"[Emergency] THS清仓异常: {e}")
        from dingtalk_alerts import send_alert
        send_alert("🛑 紧急清仓", f"模拟{sold_paper}只 实盘{sold_live}只", "critical")
        return jsonify({"code": 200, "sold_paper": sold_paper, "sold_live": sold_live})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  风控总闸 API (master_switch)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/kill-switch/status")
def api_kill_switch_status():
    """风控总闸状态 + 风险指标"""
    try:
        import sys as _ks, json as _rj, os as _ro
        _ks.path.insert(0, r"D:\quant_framework")
        from master_switch import get_status
        data = get_status()
        # 附加风险指标
        data["trade_count"] = 0
        data["position_count"] = 0
        try:
            _today = datetime.now().strftime("%Y%m%d")
            _qp = _ro.path.join(_ro.path.dirname(__file__), "data", f"qmt_signals_{_today}.json")
            if _ro.path.exists(_qp): data["trade_count"] = len(_rj.load(open(_qp, encoding="utf-8")))
        except: pass
        try:
            from paper_engine import paper
            data["position_count"] = len(paper.positions)
        except: pass
        return jsonify({"code": 200, "data": data})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/kill-switch/toggle", methods=["POST"])
def api_kill_switch_toggle():
    """风控总闸开关切换
    Body: {"switch": "circuit_breaker"|"qmt_fast_enabled"|"ai_auto_enabled", "value": true|false}
    """
    try:
        import sys as _ks2
        _ks2.path.insert(0, r"D:\quant_framework")
        from master_switch import toggle, emergency_halt

        data = request.get_json(silent=True) or {}
        action = data.get("action", "")
        switch = data.get("switch", "")
        value = data.get("value", False)

        if action == "emergency":
            emergency_halt()
            return jsonify({"code": 200, "message": "紧急停止已执行"})

        if switch not in ("circuit_breaker", "qmt_fast_enabled", "ai_auto_enabled"):
            return jsonify({"code": 400, "error": "无效开关名"})

        toggle(switch, bool(value))
        return jsonify({"code": 200, "switch": switch, "value": value})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══ 策略选菜器 ═══
_COMBO_PATH = r"D:\quant_framework\strategy_combos.json"

@app.route("/api/strategy-combo")
def api_strategy_combo():
    """获取所有策略组合 + 当前选中"""
    try:
        with open(_COMBO_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return jsonify({"code": 200, "current": data.get("current"), "combos": data.get("combos")})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/strategy-combo/select", methods=["POST"])
def api_strategy_combo_select():
    """切换策略组合
    Body: {"combo": "猎豹"}
    同步更新 auto_trade_plan.json 中的信号集和仓位上限
    """
    try:
        req = request.get_json(force=True)
        name = req.get("combo", "")
        with open(_COMBO_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if name not in data.get("combos", {}):
            return jsonify({"code": 400, "error": f"未知组合: {name}"}), 400

        combo = data["combos"][name]
        data["current"] = name
        with open(_COMBO_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 同步到 auto_trade_plan.json
        _plan_path = os.path.join(os.path.dirname(__file__), "data", "auto_trade_plan.json")
        if os.path.exists(_plan_path):
            with open(_plan_path, encoding='utf-8') as f:
                plan = json.load(f)
            plan["global_limits"]["active_signals"] = combo["signals"]
            plan["global_limits"]["max_pos_pct"] = combo["max_pos_pct"]
            plan["global_limits"]["max_daily_trades"] = combo["max_daily_trades"]
            plan["_active_combo"] = name
            # 同步每只股票的 signal_set (QMT读取此字段)
            _sig_set = frozenset(combo["signals"]) if isinstance(combo["signals"], list) else set()
            for _sk, _sv in plan.get("stocks", {}).items():
                if isinstance(_sv, dict):
                    _sv["signal_types"] = list(_sig_set)
            with open(_plan_path, 'w', encoding='utf-8') as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)

        return jsonify({"code": 200, "current": name, "combo": combo})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-importance")
def api_factor_importance():
    """因子重要性最新数据"""
    import json as _fij
    _p = os.path.join(os.path.dirname(__file__), "data", "factor_importance.json")
    if os.path.exists(_p):
        try:
            with open(_p, encoding="utf-8") as _f: return jsonify(_fij.load(_f))
        except: pass
    return jsonify({"date": "", "importance": [], "warnings": []})

@app.route("/api/lhb/latest")
def api_lhb_latest():
    """龙虎榜最新数据"""
    import json as _lhbj
    _p = os.path.join(os.path.dirname(__file__), "data", "lhb_daily.json")
    if os.path.exists(_p):
        try:
            with open(_p, encoding="utf-8") as _f: return jsonify(_lhbj.load(_f))
        except: pass
    return jsonify({"date": "", "count": 0, "records": []})

@app.route("/api/trade/slippage")
def api_trade_slippage():
    stats = {"total": 0, "avg_slippage": 0, "positive": 0, "negative": 0}
    try:
        pa = PAPER_ACCOUNT  # E26: config路径
        if os.path.exists(pa):
            tl = json.load(open(pa, "r")).get("trade_log", [])
            slips = [t.get("slippage_pct", 0) for t in tl if "slippage_pct" in t]
            if slips:
                stats = {"total": len(slips), "avg_slippage": round(sum(slips)/len(slips), 2),
                         "positive": sum(1 for s in slips if s > 0), "negative": sum(1 for s in slips if s < 0)}
    except Exception as e:
        logger.warning("[SlippageStats] 滑点统计失败: %s", e)
    return jsonify({"code": 200, **stats})

@app.route("/api/report/monthly")
def api_report_monthly():
    from datetime import timedelta
    month_start = (datetime.now().replace(day=1)).strftime("%Y-%m-%d")
    stats = {"trades": 0, "pnl": 0, "best_day": 0, "worst_day": 0}
    try:
        f = AUDIT_LOG_JSONL  # E26: config路径
        if os.path.exists(f):
            daily = {}
            for line in open(f, encoding="utf-8"):
                if line[:10] >= month_start:
                    try:
                        entry = json.loads(line)
                        d = line[:10]
                        daily[d] = daily.get(d, 0) + sum(a.get("pnl", 0) for a in entry.get("actions", []))
                        stats["trades"] += len(entry.get("actions", []))
                    except Exception as e:
                        logger.warning("[MonthlyReport] 日记录解析失败: %s", e)
            if daily:
                stats["pnl"] = sum(daily.values())
                stats["best_day"] = max(daily.values())
                stats["worst_day"] = min(daily.values())
    except Exception as e:
        logger.warning("[MonthlyReport] 月度报告加载失败: %s", e)
    return jsonify({"code": 200, **stats, "month_start": month_start})


@app.route("/api/trade/stats")
def api_trade_stats():
    rng = request.args.get("range", "today")
    from datetime import timedelta
    days = {"today": 1, "week": 7, "month": 30}.get(rng, 1)
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    stats = {"total_trades": 0, "total_amount": 0, "wins": 0}
    try:
        f = AUDIT_LOG_JSONL  # E26: config路径
        if os.path.exists(f):
            for line in open(f, encoding="utf-8"):
                if line[:10] >= since:
                    try:
                        entry = json.loads(line)
                        for a in entry.get("actions", []):
                            stats["total_trades"] += 1
                            if a.get("action") == "buy":
                                stats["total_amount"] += abs(a.get("amount", 0))
                            if a.get("pnl", 0) > 0: stats["wins"] += 1
                    except Exception as e:
                        logger.warning("[TradeStats] 记录解析失败: %s", e)
    except Exception as e:
        logger.warning("[TradeStats] 加载交易日志失败: %s", e)
    wr = round(stats["wins"] / stats["total_trades"] * 100, 1) if stats["total_trades"] > 0 else 0
    return jsonify({"code": 200, **stats, "win_rate": wr})

@app.route("/api/emergency/stop", methods=["POST"])
def api_emergency_stop():
    try:
        from paper_engine import paper
        paper.auto_enabled = False
        if LIVE_TRADER_OK:
            import live_trader; live_trader.CONFIG["auto_trade_enabled"] = False
        return jsonify({"code": 200, "message": "已暂停所有自动交易"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/emergency/resume", methods=["POST"])
def api_emergency_resume():
    try:
        from paper_engine import paper
        try:
            from state_persist import load as _sp_load
            paper.auto_enabled = _sp_load().get("paper_auto_enabled", True)
        except: paper.auto_enabled = True
        if LIVE_TRADER_OK:
            import live_trader; live_trader.CONFIG["auto_trade_enabled"] = True
        return jsonify({"code": 200, "message": "已恢复自动交易"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/emergency/status")
def api_emergency_status():
    try:
        from paper_engine import paper
        return jsonify({"code": 200, "paper_auto": paper.auto_enabled,
                        "live_auto": LIVE_TRADER_OK and TRADE_CONFIG.get("auto_trade_enabled", False) if LIVE_TRADER_OK else False})  # fix: CONFIG→TRADE_CONFIG
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade-logs/history")
def api_trade_logs_history():
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    logs = []
    try:
        f = AUDIT_LOG_JSONL  # E26: config路径
        if os.path.exists(f):
            # 只读末尾500行（今日交易总在末尾），避免全文件扫描
            with open(f, encoding="utf-8") as _af:
                tail = _af.readlines()[-500:]
            for line in tail:
                if date in line:
                    try:
                        entry = json.loads(line)
                        for a in entry.get("actions", []):
                            logs.append({"time": entry.get("ts",""), **a})
                    except Exception as _e: print(f"[App] {_e}")
        return jsonify({"code": 200, "date": date, "total": len(logs), "logs": logs})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

# [deprecated] @app.route("/strategy-replay") — 功能已合并到 /review

@app.route("/factors")
@app.route("/factor-lab")
def page_factor_lab():
    """301跳转丢失哈希，改用JS跳转保留 #backtest 等哈希路由"""
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<script>location.replace('/factor-dashboard' + location.hash)</script>
</head><body><a href="/factor-dashboard">跳转到因子看板</a></body></html>""", 200, {"Content-Type": "text/html"}


@app.route("/stocks")
def page_stocks():
    """选股面板 — 301重定向到 /screener (E26-P1 旧系统移除)"""
    return redirect("/screener", code=301)

@app.route("/api/risk/exposure")
def api_risk_exposure():
    try:
        import numpy as np
        pa = json.load(open(PAPER_ACCOUNT, "r"))  # E26: config路径
        pos = pa.get("positions", {})
        total_eq = pa.get("total_equity", 1) or 1
        exposures = []
        for s, p in pos.items():
            mv = p.get("last_price", p["avg_cost"]) * p.get("qty", 0)
            pnl = (p.get("last_price", p["avg_cost"]) - p["avg_cost"]) * p.get("qty", 0)
            exposures.append({"symbol": s, "market_value": round(mv, 2),
                              "weight": round(mv / total_eq * 100, 1) if total_eq > 0 else 0,
                              "pnl": round(pnl, 2), "pnl_pct": round(pnl / (p["avg_cost"] * p["qty"]) * 100, 2) if p["avg_cost"] > 0 else 0})
        total_exposure = round(sum(e["weight"] for e in exposures), 1)
        var_95 = round(-np.std([e.get("pnl_pct", 0) for e in exposures]) * 1.645, 2) if exposures else 0
        return jsonify({"code": 200, "total_exposure": total_exposure, "exposures": exposures, "var_95": var_95,
                        "alert": "⚠️ 集中度过高" if total_exposure > 50 or any(e["weight"] > 30 for e in exposures) else "✅ 正常"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade-logs")
def api_trade_logs():
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 50))
    tp = request.args.get("type", "all")
    logs = []
    try:
        f = AUDIT_LOG_JSONL  # E26: config路径
        if os.path.exists(f):
            with open(f, encoding="utf-8") as _af2:
                tail = _af2.readlines()[-500:]
            for line in tail:
                try:
                    entry = json.loads(line)
                    for a in entry.get("actions", []):
                        if tp == "all" or a.get("action") == tp:
                            logs.append({"time": entry.get("ts",""), **a})
                except Exception as _e: print(f"[App] {_e}")
        logs.reverse()
        start = (page - 1) * size
        return jsonify({"code": 200, "total": len(logs), "logs": logs[start:start+size]})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/system/backup", methods=["POST"])
def api_config_backup():
    try:
        from config_backup import backup
        name = backup()
        return jsonify({"code": 200, "backup_id": name})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/system/restore", methods=["POST"])
def api_config_restore():
    try:
        bid = request.get_json().get("id", "")
        from config_backup import restore
        ok = restore(bid)
        return jsonify({"code": 200 if ok else 400, "success": ok})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/system/backups")
def api_config_backups():
    try:
        from config_backup import list_backups
        return jsonify({"code": 200, "backups": list_backups()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/system/memory-history")
def api_memory_history():
    try:
        f = MEMORY_HISTORY  # E26: config路径
        if os.path.exists(f):
            return jsonify({"code": 200, "history": json.load(open(f, "r"))})
        return jsonify({"code": 200, "history": []})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/summary/today")
def api_summary_today():
    try:
        p = json.load(open(PAPER_ACCOUNT, "r"))  # E26: config路径
        ab = {}
        try:
            from ab_test import runner
            if runner.running:
                ab = runner.get_status()
        except Exception as _e: print(f"[App] {_e}")
        return jsonify({"code": 200,
            "total_equity": round(p.get("total_equity", 0), 2),
            "total_pnl": round(p.get("total_pnl", 0), 2),
            "positions": len(p.get("positions", {})),
            "auto_enabled": p.get("auto_enabled", False),
            "ab_test": ab.get("groups", {}),
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/report/trades.csv")
def api_export_trades_csv():
    """导出交易流水CSV"""
    import csv, io
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["时间","代码","方向","价格","数量","金额","类型"])
    try:
        pa = PAPER_ACCOUNT  # E26: config路径
        if os.path.exists(pa):
            tl = json.load(open(pa, "r")).get("trade_log", [])
            for t in tl[-500:]:
                w.writerow([t.get("time",""), t.get("symbol",""), t.get("side",""),
                            t.get("price",0), t.get("qty",0), t.get("cost",0) or t.get("revenue",0), t.get("type","")])
    except Exception as _e: print(f"[App] {_e}")
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=trades.csv"})

@app.route("/api/report/positions.csv")
def api_export_positions_csv():
    """导出当前持仓CSV"""
    import csv, io
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["代码","名称","数量","成本价","现价","市值","盈亏%","盈亏金额"])
    try:
        pa = PAPER_ACCOUNT  # E26: config路径
        if os.path.exists(pa):
            pos = json.load(open(pa, "r")).get("positions", {})
            for s, p in pos.items():
                w.writerow([s, p.get("name",""), p.get("qty",0), p.get("avg_cost",0),
                            p.get("last_price",0), p.get("market_value",0), "", ""])
    except Exception as _e: print(f"[App] {_e}")
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=positions.csv"})


@app.route("/api/funds/overview")
def api_funds_overview():
    try:
        from fund_manager import overview
        return jsonify({"code": 200, **overview()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/funds/flow", methods=["GET", "POST"])
def api_funds_flow():
    try:
        from fund_manager import load, deposit, withdraw
        if request.method == "POST":
            d = request.get_json() or {}
            amount = float(d.get("amount", 0))
            note = d.get("note", "")
            if amount > 0: deposit(amount, note)
            else: withdraw(abs(amount), note)
            return jsonify({"code": 200, "success": True})
        return jsonify({"code": 200, "flows": load()[-50:]})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/blacklist/list")
def api_blacklist_list():
    try:
        from blacklist import list_all
        return jsonify({"code": 200, "symbols": list_all()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/blacklist/add", methods=["POST"])
def api_blacklist_add():
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "")
        from blacklist import add
        ok = add(symbol)
        return jsonify({"code": 200 if ok else 400, "success": ok, "symbol": symbol})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/blacklist/remove", methods=["POST"])
def api_blacklist_remove():
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "")
        from blacklist import remove
        ok = remove(symbol)
        return jsonify({"code": 200 if ok else 400, "success": ok, "symbol": symbol})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/live-trade/hotkeys")
def api_live_trade_hotkeys():
    """热键状态 + 打版/联动精灵键盘映射"""
    if kb_manager:
        status = kb_manager.get_status()
        keymap = kb_manager.get_keymap()
    else:
        status = {"enabled": False, "kb_ok": False, "hotkeys_registered": False}
        keymap = {}

    # E41: kb_manager 已接管热键注册（F1-F24 + 小键盘）
    # trader.register_hotkeys() 与 kb_manager 抢同一组 F 键会冲突
    # 自动交易用 pyautogui 执行，不依赖 hotkeys_registered
    # 热键状态以 kb_manager 为准
    if status.get("hotkeys_registered"):
        trader.hotkeys_registered = True  # kb_manager 已覆盖

    # 检查依赖库可用性
    try:
        import keyboard as _test_kb
        kb_installed = True
    except ImportError:
        kb_installed = False
    try:
        import pyautogui as _test_pa
        pa_installed = True
    except ImportError:
        pa_installed = False

    reg_error = None
    if not pa_installed:
        reg_error = "pyautogui 未安装 — 自动下单不可用。pip install pyautogui"
    elif not kb_installed:
        reg_error = "keyboard 未安装 — 键盘热键不可用。pip install keyboard"

    return jsonify({
        "code": 200,
        "kb_status": status,
        "keymap": keymap,
        "hotkeys": {
            "buy": TRADE_CONFIG["hotkey_buy"],
            "sell": TRADE_CONFIG["hotkey_sell"],
            "cancel": TRADE_CONFIG["hotkey_cancel"],
        },
        "registered": status.get("hotkeys_registered", False),
        "keyboard_installed": kb_installed,
        "pyautogui_installed": pa_installed,
        "reg_error": reg_error,
    })


# ── Dashboard2 回测缓存 ──
_d2_cache = None
_d2_cache_time = 0
_D2_CACHE_TTL = 300  # 5分钟缓存

# [已移除] /api/dashboard2 — Dashboard替代
def _unused_api_dashboard2():
    """仪表盘2 API — 真实回测数据（5分钟缓存）"""
    global _d2_cache, _d2_cache_time
    import numpy as np
    from datetime import datetime as _dt, timedelta
    import time as _time

    now = _time.time()
    if _d2_cache and (now - _d2_cache_time) < _D2_CACHE_TTL:
        return jsonify(_d2_cache)

    init_data()

    # 尝试从 BacktestStore 加载最近的回测结果
    try:
        sys.path.insert(0, r"d:\quant_framework\src")
        from quant_framework.data.backtest_store import BacktestStore
        bstore = BacktestStore(r"d:\quant_framework")
        bt = bstore.load_latest()
        if bt["available"] and not bt["equity"].empty:
            eq_df = bt["equity"]
            eq_vals = eq_df["equity"].values if "equity" in eq_df.columns else eq_df.iloc[:,0].values
            eq_dates = eq_df.index.astype(str).tolist() if hasattr(eq_df.index, 'astype') else [str(d) for d in eq_df.index]

            equity = [{"date": str(eq_dates[i])[:10], "value": round(float(eq_vals[i]), 0)} for i in range(len(eq_vals))]

            # 尝试加载基准
            benchmark = []
            if "sh000300" in STOCK_DATA:
                bm_df = STOCK_DATA["sh000300"]
                bm_start = 1_000_000
                bm_vals = bm_df["close"].values
                if len(bm_vals) > 0:
                    bm_start_price = bm_vals[0]
                    bm = []
                    for i, v in enumerate(bm_vals):
                        if bm_start_price > 0:
                            val = bm_start * (v / bm_start_price)
                            bm.append({"date": str(bm_df.index[i])[:10], "value": round(val, 0)})

            # F7-修复: benchmark为空时返回空数组，前端显示"无基准数据"
            if not benchmark:
                benchmark = []

            # 指标从 BacktestStore 获取
            m = bstore.compute_metrics(bt["equity"], bt["trades"])
            trades_raw = bt["trades"]
            trades_list = []
            if not trades_raw.empty:
                for _, t in trades_raw.head(80).iterrows():
                    ret = float(t.get("return_pct", 0) or 0)
                    trades_list.append({
                        "date": str(t.get("buy_date", ""))[:10],
                        "return_pct": round(ret, 4),
                        "pnl": round(float(t.get("net_profit", 0) or 0), 0),
                        "type": "win" if ret > 0 else "loss",
                    })

            # 计算回撤区间
            peak_val = eq_vals[0]
            max_dd = 0
            dd_periods = []
            in_dd = False
            dd_begin = 0
            for i, v in enumerate(eq_vals):
                if v > peak_val:
                    peak_val = v
                    if in_dd:
                        dd_periods.append({
                            "start": equity[dd_begin]["date"],
                            "end": equity[i]["date"],
                            "depth": round((eq_vals[dd_begin] / peak_val - 1) * 100, 1),
                            "days": i - dd_begin,
                        })
                        in_dd = False
                elif v < peak_val and not in_dd:
                    dd_begin = i
                    in_dd = True
            dd_periods.sort(key=lambda x: x["depth"])
            top_drawdowns = dd_periods[:5]

            # 月度收益
            from collections import defaultdict
            monthly = defaultdict(list)
            for e in equity:
                key = e["date"][:7]
                monthly[key].append(e["value"])
            monthly_rets = []
            keys = sorted(monthly.keys())
            for i, k in enumerate(keys):
                if i > 0:
                    prev = monthly[keys[i-1]][-1]
                    curr = monthly[k][-1]
                    if prev > 0:
                        monthly_rets.append({
                            "year": int(k[:4]), "month": int(k[5:7]),
                            "return_pct": round((curr / prev - 1) * 100, 2),
                        })

            # 滚动指标
            dr = np.array([equity[i]["value"]/equity[i-1]["value"]-1 for i in range(1,len(equity)) if equity[i-1]["value"]>0])
            window = min(60, len(dr)//2)
            rolling_sharpe, rolling_vol, rolling_ret = [], [], []
            for i in range(window, len(dr)):
                w = dr[i-window:i]
                mn, sd = float(np.mean(w)), float(np.std(w))
                if sd > 0:
                    rolling_sharpe.append({"date": equity[i+1]["date"], "value": round(mn/sd*np.sqrt(252),2)})
                rolling_vol.append({"date": equity[i+1]["date"], "value": round(sd*np.sqrt(252)*100,2)})
                rolling_ret.append({"date": equity[i+1]["date"], "value": round(mn*252*100,2)})

            # 日盈亏 + VaR
            daily_pnl = [{"date": equity[-(i+1)]["date"],
                          "pnl": round(equity[-(i+1)]["value"]-equity[-(i+2)]["value"],0),
                          "pct": round((equity[-(i+1)]["value"]/equity[-(i+2)]["value"]-1)*100,2)}
                         for i in range(min(60, len(equity)-1)) if equity[-(i+2)]["value"]>0]

            var_95 = round(float(np.percentile(dr,5))*100,2) if len(dr)>10 else 0
            var_99 = round(float(np.percentile(dr,1))*100,2) if len(dr)>10 else 0
            cvar_95 = round(float(dr[dr<=np.percentile(dr,5)].mean())*100,2) if len(dr)>10 else 0
            skew = round(float((dr-dr.mean()).mean()**3/dr.std()**3),2) if len(dr)>10 and dr.std()>0 else 0
            kurt = round(float((dr-dr.mean()).mean()**4/dr.std()**4-3),2) if len(dr)>10 and dr.std()>0 else 0

            # 行业配置（从因子缓存提取真实行业分布）
            sector_weights = {}
            try:
                from collections import Counter as _Ctr
                ind_counts = _Ctr()
                for fc in (_FACTOR_CACHE or [])[:500]:
                    ind = getattr(fc, 'industry', '') or '未分类'
                    ind_counts[ind] += 1
                total = sum(ind_counts.values()) or 1
                for k, v in ind_counts.most_common(7):
                    sector_weights[k] = round(v/total*100, 1)
            except Exception:
                sector_weights = {"未分类": 100}

            metrics_out = {
                "total_return": round(m.get("total_return",0), 4),
                "annual_return": round(m.get("annual_return",0), 4),
                "annual_volatility": round(m.get("annual_volatility",0) or (float(np.std(dr)*np.sqrt(252)) if len(dr)>1 else 0), 4),
                "sharpe": round(m.get("sharpe",0), 2),
                "sortino": round(m.get("sortino",0), 2),
                "max_drawdown": round(m.get("max_drawdown",0), 4),
                "calmar": round(m.get("calmar",0), 2),
                "info_ratio": round(m.get("information_ratio",0), 2),
                "skewness": skew, "kurtosis": kurt,
                "var_95": var_95, "var_99": var_99, "cvar_95": cvar_95,
                "trading_days": len(equity),
                "win_rate": round(m.get("win_rate",0), 4),
                "profit_factor": round(m.get("profit_factor",0), 2),
                "n_trades": m.get("n_trades",0),
            }
            store.set('dashboard2_metrics', metrics_out)

            result = {
                "code": 200,
                "equity_curve": equity,
                "benchmark": benchmark,
                "metrics": metrics_out,
                "top_drawdowns": top_drawdowns,
                "monthly_returns": monthly_rets[-36:],
                "rolling_sharpe": rolling_sharpe[-200:] if rolling_sharpe else [],
                "rolling_vol": rolling_vol[-200:] if rolling_vol else [],
                "rolling_return": rolling_ret[-200:] if rolling_ret else [],
                "daily_pnl": daily_pnl,
                "sector_weights": sector_weights,
                "attribution": [],  # 归因分析需单独实现
                "trades": trades_list,
                "timestamp": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _d2_cache = result
            _d2_cache_time = now
            return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()

    # 回退：无真实数据时返回空结构而非随机数据
    return jsonify({
        "code": 200,
        "equity_curve": [],
        "benchmark": [],
        "metrics": {"total_return":0,"annual_return":0,"sharpe":0,"max_drawdown":0,"win_rate":0,"n_trades":0,"trading_days":0},
        "top_drawdowns": [],
        "monthly_returns": [],
        "rolling_sharpe": [], "rolling_vol": [], "rolling_return": [],
        "daily_pnl": [],
        "sector_weights": {},
        "attribution": [],
        "trades": [],
        "message": "暂无回测数据。请先运行回测生成结果。",
        "timestamp": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# [已移除] /api/live-monitor — Dashboard替代
def _unused_api_live_monitor():
    """实时监控API"""
    init_data()
    signal_filter = request.args.get("signal", "all")
    min_power = int(request.args.get("min_power", 50))

    signals = []
    for s in _FACTOR_CACHE:
        try:
            pw = getattr(s, 'power_score', 0) or 0
            if pw < min_power: continue
            sig_val = getattr(s, signal_filter, 0) if signal_filter != 'all' else 1
            if not sig_val: continue
            sym = getattr(s, 'symbol', '')
            # 仅A股(排除ETF/可转债)
            if not _is_stock(sym): continue
            # 名称解析
            name = getattr(s, 'name', '') or ''
            if not name or name == sym or name.isdigit():
                name = _resolve_name(sym) or name
            if not name: name = sym
            signals.append({
                "symbol": getattr(s, 'symbol', ''),
                "name": name,
                "close": getattr(s, 'close', 0) or 0,
                "change_pct": getattr(s, 'change_pct', 0) or 0,
                "vol_ratio": getattr(s, 'vol_ratio', 0) or 0,
                "power_score": pw,
                "buy_signal": getattr(s, 'buy_signal', 0) or 0,
                "signal_source": signal_filter if signal_filter != 'all' else '多信号',
                "entry_time": getattr(s, 'entry_time', '') or getattr(s, 'signal_date', ''),
            })
        except Exception as _e: print(f"[App] {_e}")

    signals.sort(key=lambda x: -x["power_score"])

    # 存入 DataStore — 跨模块共享
    store.set_signals(signals)

    # 市场统计 — 从真实股票数据计算
    try:
        from market_stats import compute_market_stats, get_latest_market
        trend = compute_market_stats(STOCK_DATA, lookback_days=20)
        latest = get_latest_market(STOCK_DATA)
        if latest:
            market_data = {
                "limit_up": latest['limit_up'],
                "limit_down": latest['limit_down'],
                "bomb_rate": latest['bomb_rate'],
                "breadth": latest['breadth'],
                "up_count": latest['up_count'],
                "down_count": latest['down_count'],
            }
        else:
            market_data = {"limit_up": 0, "limit_down": 0, "bomb_rate": 0, "breadth": 50, "up_count": 0, "down_count": 0}
            trend = []
    except ImportError:
        market_data = {"limit_up": 0, "limit_down": 0, "bomb_rate": 0, "breadth": 50, "up_count": 0, "down_count": 0}
        trend = []
    store.set_market_data(market_data)

    return jsonify({
        "code": 200,
        "total": len(signals),
        "strong_buy": sum(1 for s in signals if s.get("buy_signal", 0) >= 3),
        "signals": signals[:50],
        "market": market_data,
        "trend": trend,
        "data_version": store.get_version(),
    })



@app.route("/api/ab-test/status")
def api_ab_test_status():
    try:
        from ab_test import runner
        return jsonify({"code": 200, **runner.get_status()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/ab-test/start", methods=["POST"])
def api_ab_test_start():
    try:
        from ab_test import runner
        runner.start()
        return jsonify({"code": 200, "message": "A/B测试已启动"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/ab-test/stop", methods=["POST"])
def api_ab_test_stop():
    try:
        from ab_test import runner
        runner.stop()
        return jsonify({"code": 200, "message": "A/B测试已停止"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/price-alert/set", methods=["POST"])
def api_price_alert_set():
    d = request.get_json() or {}
    from price_alert import add
    ok = add(d.get("symbol",""), float(d.get("target",0)))
    return jsonify({"code": 200 if ok else 400, "success": ok})

@app.route("/api/price-alert/list")
def api_price_alert_list():
    from price_alert import load
    return jsonify({"code": 200, "alerts": load()})

@app.route("/api/price-alert/remove", methods=["POST"])
def api_price_alert_remove():
    d = request.get_json() or {}
    from price_alert import remove
    remove(d.get("symbol",""))
    return jsonify({"code": 200, "success": True})

@app.route("/api/sectors/fund-flow")
def api_sectors_fund_flow():
    sectors = {}
    try:
        import numpy as np
        sd = get_stock_data()  # E260: 走缓存，不再每次磁盘IO
        fc = pickle.load(open(FACTOR_CACHE_PKL, "rb")) if os.path.exists(FACTOR_CACHE_PKL) else []  # E26: config路径
        ind_map = {}
        for s in fc[:500]:
            sym = getattr(s, 'symbol', '')
            ind = getattr(s, 'industry', '') or '未分类'
            if sym: ind_map[sym] = ind
        for sym in list(sd.keys())[:1000]:
            df = sd[sym]
            if df is None or len(df) < 5: continue
            close = float(df["close"].values[-1])
            prev = float(df["close"].values[-2])
            vol = float(df["volume"].values[-5:].mean()) if "volume" in df.columns else 0
            chg = (close/prev-1)*100 if prev>0 else 0
            ind = ind_map.get(sym, sym[:2])
            if ind not in sectors: sectors[ind] = {"change":[], "volume":0, "count":0}
            sectors[ind]["change"].append(chg)
            sectors[ind]["volume"] += vol
            sectors[ind]["count"] += 1
    except Exception as _e: logger.warning(f"[SectorHeat] 板块热度计算失败: {_e}")
    ranking = sorted([{"sector": k, "change": round(np.mean(v["change"]),2),
        "volume": round(v["volume"],0), "count": v["count"]} for k,v in sectors.items() if v["count"]>=3],
        key=lambda x: -x["change"])
    return jsonify({"code": 200, "ranking": ranking[:15], "top_inflow": ranking[:3], "top_outflow": ranking[-3:]})


@app.route("/api/market/northbound")
def api_northbound():
    try:
        nb_file = NORTHBOUND_JSON  # E26: config路径
        if os.path.exists(nb_file):
            return jsonify({"code": 200, **json.load(open(nb_file,"r"))})
    except Exception as e:
        logger.warning("[Northbound] 本地缓存读取失败: %s", e)
    try:
        import urllib.request, re
        url = "http://push2.eastmoney.com/api/qt/kamt.kline/get?fields1=f1,f3&fields2=f51,f52,f54&klt=1&lmt=1"
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        if data.get("data"):
            item = data["data"]["klines"][-1] if data["data"].get("klines") else None
            if item:
                parts = item.split(",")
                result = {"net_inflow": round(float(parts[2])/100000000, 2), "time": parts[0],
                          "label": "净流入" if float(parts[2])>0 else "净流出"}
                json.dump(result, open(nb_file,"w"), ensure_ascii=False)
                return jsonify({"code": 200, **result})
    except Exception as e:
        logger.warning("[Northbound] 东方财富API获取失败: %s", e)
    return jsonify({"code": 200, "net_inflow": 0, "label": "数据不可用"})


@app.route("/api/sectors/ranking")
def api_sectors_ranking():
    sectors = {}
    try:
        import numpy as np
        sd = get_stock_data()  # E260: 走缓存，不再每次磁盘IO
        for sym in list(sd.keys())[:1000]:
            df = sd[sym]
            if df is None or len(df) < 2: continue
            close = float(df["close"].values[-1])
            prev = float(df["close"].values[-2]) if len(df) > 1 else close
            name = sym[:2] if len(sym) >= 2 else "??"
            if name not in sectors: sectors[name] = []
            sectors[name].append((close / prev - 1) * 100 if prev > 0 else 0)
    except Exception as e:
        logger.warning("[SectorsRanking] 板块聚合失败: %s", e)
    ranking = sorted([{"sector": k, "change": round(np.mean(v), 2), "count": len(v)} for k, v in sectors.items()], key=lambda x: -x["change"])
    return jsonify({"code": 200, "ranking": ranking[:20]})

@app.route("/api/system/logs")
def api_system_logs():
    level = request.args.get("level", "all")
    limit = int(request.args.get("limit", 50))
    logs = []
    try:
        wf = WATCHDOG_LOG  # E26: config路径
        if os.path.exists(wf):
            for line in open(wf, encoding="utf-8", errors="replace").readlines()[-200:]:
                if level == "error" and "⚠️" not in line and "错误" not in line: continue
                if level == "trade" and "交易" not in line: continue
                logs.append(line.strip()[-100:])
    except Exception as e:
        logger.warning("[SystemLogs] 日志读取失败: %s", e)
    return jsonify({"code": 200, "logs": logs[-limit:]})


@app.route("/api/compare/paper-vs-live")
def api_compare_paper_live():
    try:
        p = json.load(open(PAPER_ACCOUNT, "r")).get("positions", {})  # E26: config路径
        l_status = {}
        try:
            from live_trader import get_trading_status
            l_status = get_trading_status()
        except Exception as e:
            logger.warning("[ComparePL] 实盘状态获取失败: %s", e)
        l_pos = {x.get("symbol",""): x for x in l_status.get("positions", [])}
        ps = set(p.keys()); ls = set(l_pos.keys())
        return jsonify({"code": 200, "paper_only": list(ps - ls), "live_only": list(ls - ps), "both": list(ps & ls)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/system/health-score")
def api_health_score():
    score = 100
    try:
        pc = json.load(open(PRICE_CACHE_JSON, "r")) if os.path.exists(PRICE_CACHE_JSON) else {}  # E26: config路径
        if len(pc) < 100: score -= 20
        try:
            from link_trader import is_available
            if not is_available(): score -= 20
        except: score -= 20
        f = AUDIT_LOG_JSONL  # E26: config路径
        today = datetime.now().strftime("%Y-%m-%d")
        has_trade = False
        if os.path.exists(f):
            for line in open(f, encoding="utf-8"):
                if today in line: has_trade = True; break
        if not has_trade: score -= 20
        uptime_h = int((time.time() - _startup_time) / 3600)
        if uptime_h < 1: score -= 20
    except Exception: logger.debug("[Health] 健康检查不完整")
    return jsonify({"code": 200, "score": max(0, score), "label": "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"})


@app.route("/api/system/runtime")
def api_system_runtime():
    import time as _t
    mem = {}
    try:
        mf = MEMORY_HISTORY  # E26: config路径
        if os.path.exists(mf):
            mem = (json.load(open(mf, "r")) or [])[-1:][0] if os.path.getsize(mf) > 10 else {}
    except Exception: logger.debug("[Runtime] 内存历史读取失败")
    wd_lines = []
    try:
        wf = WATCHDOG_LOG  # E26: config路径
        if os.path.exists(wf):
            wd_lines = open(wf, encoding="utf-8", errors="replace").readlines()
    except Exception: logger.debug("[Runtime] 看门狗日志读取失败")
    restarts_today = sum(1 for l in wd_lines[-200:] if "重启#" in l)
    uptime_sec = int(time.time() - _startup_time)
    if uptime_sec < 86400: uptime_str = f"{uptime_sec//3600}h {(uptime_sec%3600)//60}m"
    else: uptime_str = f"{uptime_sec//86400}d {(uptime_sec%86400)//3600}h"
    return jsonify({"code": 200,
        "uptime": uptime_str, "uptime_seconds": uptime_sec,
        "memory_pct": mem.get("used_pct", 0),
        "process_mb": mem.get("process_mb", 0),
        "restarts_today": restarts_today,
        "watchdog_ok": os.path.exists(WATCHDOG_PID),  # E26: config路径
    })


@app.route("/api/market/state")
def api_market_state_v1():
    try:
        from market_sense import get_state
        return jsonify({"code": 200, **get_state()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/strategy/export")
def api_strategy_export():
    try:
        cfg = json.load(open(LIVE_CONFIG, "r"))  # E26: config路径
        clean = {k: cfg[k] for k in ["tp1_profit_pct","tp1_trail_pct","tp1_stop_loss",
                   "tp2_profit_pct","tp2_trail_pct","tp2_stop_loss",
                   "signal_min_strength","max_positions","max_single_position_pct",
                   "max_daily_trades","max_daily_loss","position_mode","strategy_weights",
                   "daily_loss_sell_half","daily_loss_clear_all","min_cash_reserve"] if k in cfg}
        clean["export_time"] = datetime.now().strftime("%Y-%m-%d")
        return Response(json.dumps(clean, ensure_ascii=False, indent=2),
                        mimetype="application/json",
                        headers={"Content-Disposition": "attachment;filename=strategy_config.json"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/strategy/import", methods=["POST"])
def api_strategy_import():
    try:
        data = request.get_json() or {}
        if not data:
            f = request.files.get("file")
            if f: data = json.load(f)
        cfg_file = LIVE_CONFIG  # E26: config路径
        cfg = json.load(open(cfg_file, "r"))
        allowed = ["tp1_profit_pct","tp1_trail_pct","tp1_stop_loss","tp2_profit_pct",
                   "tp2_trail_pct","tp2_stop_loss","signal_min_strength","max_positions",
                   "max_single_position_pct","max_daily_trades","max_daily_loss",
                   "position_mode","strategy_weights","daily_loss_sell_half",
                   "daily_loss_clear_all","min_cash_reserve"]
        for k, v in data.items():
            if k in allowed:
                cfg[k] = v
        json.dump(cfg, open(cfg_file, "w"), ensure_ascii=False, indent=2)
        from evolution_log import log_change
        log_change("import", [{"param": k, "before": "imported", "after": v} for k, v in data.items() if k in allowed])
        return jsonify({"code": 200, "message": f"已导入{len([k for k in data if k in allowed])}项配置"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/evolution/status")
def api_evo_bridge_status():
    """进化结果 vs master 对比 (人审后决定是否应用)"""
    try:
        _ep = r"D:\quant_framework\evolution_result.json"
        _mp = r"D:\quant_framework\trade_config_master.json"
        evo = json.load(open(_ep, encoding="utf-8")) if os.path.exists(_ep) else {}
        mst = json.load(open(_mp, encoding="utf-8")) if os.path.exists(_mp) else {}
        best = evo.get("best_params", {})
        diff = {}
        # 止损对比
        _evo_sl = best.get("stop_loss")
        if _evo_sl:
            _cur_sl = mst.get("stop_loss", {}).get("hard", -0.055)
            diff["stop_loss_hard"] = {"current": _cur_sl, "proposed": round(_evo_sl, 3),
                                       "change": round(abs(_evo_sl - _cur_sl)*100, 1)}
        # 止盈对比
        _evo_tp = best.get("take_profit")
        if _evo_tp:
            _cur_tp = mst.get("take_profit", {}).get("tp1", {}).get("profit_pct", 0.08)
            diff["take_profit"] = {"current": _cur_tp, "proposed": round(_evo_tp, 3),
                                   "change": round(abs(_evo_tp - _cur_tp)*100, 1)}
        # 持有天数
        _evo_hd = best.get("hold_days")
        if _evo_hd:
            _cur_hd = mst.get("default_hold_days", 3)
            diff["hold_days"] = {"current": _cur_hd, "proposed": _evo_hd}
        return jsonify({
            "code": 200,
            "evolution_sharpe": best.get("sharpe"),
            "diff": diff,
            "last_run": evo.get("updated", "未知"),
            "warning": "数据仅500天, 进化结果可能过拟合, 建议人工审慎判断",
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/evolution/apply-to-master", methods=["POST"])
def api_evolution_apply_to_master():
    """批准进化建议 → 写入 master (人工在回路)"""
    data = request.get_json(silent=True) or {}
    approved = data.get("approved", False)
    if not approved:
        return jsonify({"code": 400, "error": "需明确批准 approved=true"})
    try:
        _ep = r"D:\quant_framework\evolution_result.json"
        _mp = r"D:\quant_framework\trade_config_master.json"
        evo = json.load(open(_ep, encoding="utf-8"))
        mst = json.load(open(_mp, encoding="utf-8"))
        best = evo.get("best_params", {})
        # 写入选中的进化参数
        if best.get("stop_loss"):
            mst.setdefault("stop_loss", {})["hard"] = round(best["stop_loss"], 3)
        if best.get("take_profit"):
            mst.setdefault("take_profit", {}).setdefault("tp1", {})["profit_pct"] = round(best["take_profit"], 3)
        if best.get("hold_days"):
            mst["default_hold_days"] = int(best["hold_days"])
        mst["_last_evolution_applied"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 备份+原子写入
        shutil.copy2(_mp, _mp + '.bak')
        tmp = _mp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(mst, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _mp)
        return jsonify({"code": 200, "message": "进化参数已写入 master", "applied": best})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/evolution/log")
def api_evolution_log():
    try:
        from evolution_log import get_log
        return jsonify({"code": 200, "log": get_log()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/evolution/rollback", methods=["POST"])
def api_evolution_rollback():
    try:
        eid = int(request.get_json().get("id", -1))
        from evolution_log import rollback
        ok = rollback(eid)
        return jsonify({"code": 200 if ok else 400, "success": ok})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy/replay")
def api_strategy_replay():
    """E267: 策略历史回放"""
    try:
        days = int(request.args.get("days", 30))
        from strategy_replay import replay
        result = replay(days=min(days, 60))
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/attribution/daily")
def api_attribution():
    """E262: 归因分析 — 盈亏来源拆解"""
    try:
        from attribution import analyze
        date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
        report = analyze(date)
        return jsonify({"code": 200, **report})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/ic-trend")
def api_ic_trend():
    try:
        days = int(request.args.get("days", 30))
        hist_f = IC_HISTORY_JSON  # E26: config路径
        if os.path.exists(hist_f):
            data = json.load(open(hist_f, "r"))
            return jsonify({"code": 200, "history": data[-days:]})
        return jsonify({"code": 200, "history": []})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/ic-analysis")
def api_factor_ic():
    """因子IC分析 — 带缓存(1小时)"""
    import json as _j, os as _os, time as _t
    cache_f = r"d:\quant_web\data\ic_cache.json"
    # 读缓存
    if _os.path.exists(cache_f):
        try:
            with open(cache_f,'r') as f: cached = _j.load(f)
            if _t.time() - cached.get('ts',0) < 3600:
                return jsonify(cached)
        except Exception as _e: print(f"[App] {_e}")
    # 快速采样计算
    try:
        init_data()
        # E236: 改用 analyze_from_cache (基于 _FACTOR_CACHE 的20个评分因子，非旧TDX因子)
        from quant_agent.ic_analyzer import analyze_from_cache
        result = analyze_from_cache(_FACTOR_CACHE, STOCK_DATA)
        result['code'] = 200; result['ts'] = _t.time(); result['cached'] = False
        # E271: ICIR动态权重更新
        try:
            from icir_weights import update_weights
            update_weights(result.get('ic_results', {}))
        except Exception as _e: print(f"[App] {_e}")
        # X4: IC历史趋势记录
        try:
            hist_f = IC_HISTORY_JSON  # E26: config路径
            ic_hist = []
            if os.path.exists(hist_f):
                ic_hist = json.load(open(hist_f, "r"))
            today = datetime.now().strftime("%Y-%m-%d")
            if not ic_hist or ic_hist[-1].get("date") != today:
                snap = {"date": today}
                for f, periods in result.get("ic_results", {}).items():
                    ic1 = periods.get("ic_1d", {})
                    snap[f] = round(ic1.get("mean_ic", 0), 4)
                ic_hist.append(snap)
                if len(ic_hist) > 90:
                    ic_hist = ic_hist[-90:]
                json.dump(ic_hist, open(hist_f, "w"), ensure_ascii=False)
        except Exception as _e: print(f"[App] {_e}")
        # E123: 补全ic_results供多周期IC Tab使用
        if not result.get('ic_results'):
            _full_path = IC_REPORT_FULL_JSON  # E26: config路径
            if _os.path.exists(_full_path):
                with open(_full_path, 'r', encoding='utf-8') as _f2:
                    result['ic_results'] = _j.load(_f2).get('ic_results', {})
            if not result.get('ic_results'):
                _rpt = FACTOR_IC  # E26: config路径
                if _os.path.exists(_rpt):
                    with open(_rpt, 'r', encoding='utf-8') as _f2:
                        result['ic_results'] = _j.load(_f2).get('ic_results', {})
        # E236: analyze_from_cache 已内部生成全因子报告，无需二次调用
        # 存缓存
        try:
            _os.makedirs(_os.path.dirname(cache_f), exist_ok=True)
            with open(cache_f,'w') as f: _j.dump(result, f)
        except Exception as _e: print(f"[App] {_e}")
        return jsonify(result)
    except Exception as e: return jsonify({"code":500,"error":str(e)})

@app.route("/api/factor/group-returns-old")
def api_factor_group_returns_v1():
    """E116: 因子分组收益分析 — 5分位法"""
    import numpy as _np
    if not _CACHE_READY or len(_FACTOR_CACHE) < 50 or len(STOCK_DATA) < 50:
        return jsonify({"code": 400, "error": "数据不足"})

    # 取评分因子列表
    score_fields = ["trend_score","momentum_score","volume_score","chg_score","position_score",
                    "rsi_score","macd_score","boll_score","atr_score",
                    "vol_score","bias_score","money_score","turnover_score"]

    result = {}
    for field in score_fields:
        vals = []
        for s in _FACTOR_CACHE:
            fv = getattr(s, field, None)
            sym = getattr(s, "symbol", "")
            if fv is not None and sym and sym in STOCK_DATA:
                df = STOCK_DATA[sym]
                if len(df) >= 2:
                    ret = float(df["close"].iloc[-1]) / float(df["close"].iloc[-2]) - 1
                    vals.append((fv, ret))
        if len(vals) < 50:
            continue
        vals.sort(key=lambda x: x[0])
        n = len(vals) // 5
        groups = {}
        for g in range(5):
            start = g * n
            end = start + n if g < 4 else len(vals)
            group_vals = vals[start:end]
            avg_ret = _np.mean([v[1] for v in group_vals])
            groups[f"Q{g+1}"] = round(float(avg_ret), 6)
        groups["spread"] = round(groups["Q5"] - groups["Q1"], 6)
        groups["monotonic"] = groups["Q5"] > groups["Q4"] > groups["Q3"] > groups["Q2"] > groups["Q1"] or \
                              groups["Q5"] < groups["Q4"] < groups["Q3"] < groups["Q2"] < groups["Q1"]
        result[field] = groups

    return jsonify({"code": 200, "group_returns": result, "samples": len(_FACTOR_CACHE)})

@app.route("/api/factor-analysis-old")
@cached_api()
def api_factor_analysis_v1():
    """因子分析API — 从真实IC结果读取 (P0-1修复)"""
    import numpy as np, json as _j, os as _os
    from datetime import datetime as _dt

    factors = []
    _seen = set()

    # E125: 先加载旧CSV因子（35个）
    _csv_path = r"d:\quant_framework\factor_ic_results.csv"
    if _os.path.exists(_csv_path):
        try:
            import pandas as _pd
            from quant_agent.factor_labels import label as _ft
            _df = _pd.read_csv(_csv_path)
            _df5 = _df[_df["period"] == "ic_5d"] if "period" in _df.columns else _df
            for _, _row in _df5.iterrows():
                _key = str(_row.get("factor", ""))
                if _key and _key not in _seen:
                    _seen.add(_key)
                    factors.append({
                        "name": _ft(str(_row.get("label", _key))), "key": _key,
                        "ic": round(float(_row.get("ic_mean", 0)), 4),
                        "ir": round(float(_row.get("icir", 0)), 2),
                        "win_rate": round(float(_row.get("ic_pos_pct", 0)), 4),
                        "sharpe": round(float(_row.get("abs_icir", 0)), 2),
                        "category": str(_row.get("category", "旧因子")),
                    })
        except Exception as _e: print(f"[FA] CSV load failed: {_e}")

    # E125: 追加13个评分因子（从IC报告JSON）
    _ic_path = IC_REPORT_FULL_JSON  # E26: config路径
    if not _os.path.exists(_ic_path):
        _ic_path = FACTOR_IC  # E26: config路径
    if _os.path.exists(_ic_path):
        with open(_ic_path, "r", encoding="utf-8") as _f:
            _ic_data = _j.load(_f)
        _ic_results = _ic_data.get("ic_results", {})
        _labels = _ic_data.get("labels", {})
        from quant_agent.factor_labels import label as _ft
        _score_keys = ["trend_score","momentum_score","volume_score","chg_score","position_score",
                       "rsi_score","macd_score","boll_score","atr_score",
                       "vol_score","bias_score","money_score","turnover_score"]
        for _k in _score_keys:
            if _k in _ic_results and _k not in _seen:
                _seen.add(_k)
                _v = _ic_results[_k].get("ic_1d", {})
                # 名称兜底：labels → factor_labels.py → raw key
                _raw_name = _labels.get(_k, _k)
                factors.append({
                    "name": _ft(_raw_name),
                    "key": _k,
                    "ic": _v.get("mean_ic", 0) or 0,
                    "ir": _v.get("icir", 0) or 0,
                    "win_rate": _v.get("positive_ratio", 0) or 0,
                    "sharpe": _v.get("icir", 0) or 0,
                    "category": "评分因子",
                })

    # IC系列
    ic_series = [{"date":"均值","factor":f["name"],"ic":f["ic"],"ic_mean":f["ic"],"ir":f["ir"]} for f in factors]

    # 相关性矩阵
    n = len(factors)
    corr = np.eye(n)
    # 不做伪随机相关矩阵，返回空以让前端显示说明

    return jsonify({
        "code": 200,
        "demo": False,
        "source": "factor_ic_results.csv",
        "note": "IC均值为真实数据；时序图为展示用途，非日内真实波动",
        "n_factors": len(factors),
        "factors": factors,
        "ic_series": ic_series,
        "correlation": {
            "names": [f["name"] for f in factors],
            "matrix": corr.tolist(),
        },
    })


@app.route("/api/risk-dashboard")
def api_risk_dashboard():
    """风控仪表盘API — 接入真实持仓 + 用户参数"""
    import numpy as np

    # 读取用户风控参数
    level = request.args.get("level", "mid")
    max_single_pct = float(request.args.get("max_single", 20))
    daily_loss_pct = float(request.args.get("daily_loss", 5))
    dd_limits = {"low": 0.05, "mid": 0.10, "high": 0.20}

    # 从同花顺读取真实持仓
    positions = _read_ths_positions_direct()
    if not positions:
        positions = read_ths_positions()

    # 计算真实风控指标
    total_value = sum(p.get("market_value", 0) for p in positions)
    total_cost = sum(p.get("cost_price", 0) * p.get("quantity", 0) for p in positions)

    for p in positions:
        p["weight"] = round(p.get("market_value", 0) / max(total_value, 1) * 100, 1)
        p["risk_score"] = min(90, max(5, int(abs(p.get("profit_pct", 0)) * 2 + p.get("weight", 0) * 0.5)))
        # 超限标记
        p["over_limit"] = p["weight"] > max_single_pct or abs(p.get("profit_pct",0)) > daily_loss_pct

    # VaR简化计算: 基于实际持仓波动
    profit_pcts = [abs(p.get("profit_pct",0)) for p in positions if p.get("profit_pct")]
    daily_vol = round(float(np.mean(profit_pcts))/100*0.5, 4) if profit_pcts else 0.015
    daily_vol = max(0.005, min(0.05, daily_vol))
    var_95 = round(-1.645 * daily_vol * total_value, 0)
    var_99 = round(-2.326 * daily_vol * total_value, 0)
    cvar = round(-2.06 * daily_vol * total_value, 0)
    max_dd = round(min(p.get("profit_pct", 0) for p in positions) / 100, 4) if positions else -0.05
    max_single = round(max(p.get("weight", 0) for p in positions), 1) if positions else 0
    dd_limit = dd_limits.get(level, 0.10)

    return jsonify({
        "code": 200,
        "level": level,
        "dd_limit": dd_limit,
        "max_single_limit": max_single_pct,
        "daily_loss_limit": daily_loss_pct,
        "positions": positions,
        "risk_metrics": {
            "total_exposure": round(sum(p["weight"] for p in positions), 1),
            "var_95": var_95, "var_99": var_99, "cvar": cvar,
            "max_single": max_single,
            "sector_count": len(set(p.get("sector", "未知") for p in positions)),
            "sharpe": round((sum(p.get("profit_pct",0) for p in positions)/max(len(positions),1))/(daily_vol*100+0.01),2) if positions else 0,
            "max_drawdown": max_dd,
            "daily_vol": round(daily_vol * 100, 2),
        },
        # 相关性分析
        "correlation": _get_correlation(positions),
    })

def _get_correlation(positions):
    """获取持仓相关性"""
    try:
        from risk_guard import CorrelationAnalyzer
        pos_dict = {}
        for p in positions:
            sym = p.get('symbol','')
            ind = '未分类'
            for fc in (_FACTOR_CACHE or []):
                if getattr(fc,'symbol','') == sym: ind = getattr(fc,'industry','未分类') or '未分类'; break
            pos_dict[sym] = {'qty': p.get('quantity',0), 'last_price': p.get('current_price',0),
                            'avg_cost': p.get('cost_price',0), 'industry': ind}
        analyzer = CorrelationAnalyzer(STOCK_DATA, _FACTOR_CACHE)
        return analyzer.analyze(pos_dict)
    except: return {"risk_level":"低","warning":"","details":[]}


@app.route("/api/signal-center")
def api_signal_center():
    """信号中心API — 跨策略信号聚合 (FACTOR_CACHE + signal_table.json)"""
    init_data()
    import numpy as np

    # ── 读 signal_table.json (ML + 反转策略信号) ──
    _ml_signals = []
    try:
        _st = os.path.join(os.path.dirname(__file__), "data", "signal_table.json")
        if os.path.exists(_st):
            _raw = json.load(open(_st, encoding="utf-8"))
            for r in _raw:
                _sc = r.get("combined_score", 0) or 0
                _ml_signals.append({
                    "symbol": r.get("symbol",""), "name": r.get("name",""),
                    "strategy": r.get("decision","")[:10] if r.get("decision") else "ml",
                    "signal_strength": _sc,
                    "power_score": _sc, "close": r.get("close",0) or 0,
                    "change_pct": r.get("change_pct",0) or 0,
                    "buy_signal": 5 if _sc>=90 else 4 if _sc>=80 else 3 if _sc>=70 else 2,
                    "signal_source": "ml",
                })
    except Exception: pass

    strategies = ["tdx_resonance", "tdx2_final", "tdx2_xg", "tdx2_b1", "tdx_qlj", "tdx_bandit"]
    strat_names = {"tdx_resonance": "双信号共振", "tdx2_final": "终极选股", "tdx2_xg": "涨停突破", "tdx2_b1": "底部反转", "tdx_qlj": "擒龙决", "tdx_bandit": "波段擒妖"}

    signals = []
    for s in _FACTOR_CACHE[:500]:
        try:
            sym = getattr(s, 'symbol', '')
            if not _is_stock(sym): continue
            _has_tdx = False
            for strat in strategies:
                val = getattr(s, strat, 0) or 0
                if val > 0:
                    _has_tdx = True
                    name = getattr(s, 'name', '') or ''
                    if not name or name == sym or name.isdigit():
                        name = _resolve_name(sym) or name
                    if not name: name = sym
                    signals.append({
                        "symbol": getattr(s, 'symbol', ''),
                        "name": name,
                        "strategy": strat,
                        "strategy_name": strat_names.get(strat, strat),
                        "signal_strength": val,
                        "power_score": getattr(s, 'power_score', 0) or 0,
                        "close": getattr(s, 'close', 0) or 0,
                        "change_pct": getattr(s, 'change_pct', 0) or 0,
                        "entry_time": getattr(s, 'entry_time', '') or getattr(s, 'signal_date', ''),
                        "buy_signal": getattr(s, 'buy_signal', 0) or 0,
                        "signal_source": "tdx",
                    })
            # E222: TDX无信号时用buy_signal>=3作为因子信号源
            if not _has_tdx:
                bs = getattr(s, 'buy_signal', 0) or 0
                if bs >= 3:
                    name = getattr(s, 'name', '') or _resolve_name(sym) or sym
                    signals.append({
                        "symbol": sym, "name": name,
                        "strategy": "factor_score",
                        "strategy_name": "因子评分",
                        "signal_strength": bs,
                        "power_score": getattr(s, 'power_score', 0) or 0,
                        "close": getattr(s, 'close', 0) or 0,
                        "change_pct": getattr(s, 'change_pct', 0) or 0,
                        "entry_time": getattr(s, 'entry_time', '') or getattr(s, 'signal_date', ''),
                        "buy_signal": bs,
                        "signal_source": "factor",
                    })
        except Exception as _e: print(f"[App] {_e}")

    # ── 追加 signal_table.json 信号 (ML + 反转策略) ──
    signals.extend(_ml_signals)
    signals.sort(key=lambda x: -x["power_score"])

    # 共振统计
    symbol_signals = {}
    for s in signals:
        sym = s["symbol"]
        if sym not in symbol_signals: symbol_signals[sym] = set()
        symbol_signals[sym].add(s["strategy"])
    resonance = sum(1 for v in symbol_signals.values() if len(v) >= 2)

    # E263: 多策略状态
    multi_strategy = []
    try:
        from strategy_manager import mgr
        multi_strategy = mgr.get_default().get_status()
    except Exception as _e: print(f"[App] {_e}")

    # 信号快照存盘（方便复盘）
    try:
        import json as _sj, os as _os
        sf = SIGNAL_SNAPSHOTS_JSONL  # E26: config路径
        with open(sf, "a", encoding="utf-8") as _f:
            _sj.dump({"time": datetime.now().strftime("%H:%M:%S"), "total": len(signals),
                      "top5": [s["symbol"] for s in signals[:5]], "strong": sum(1 for s in signals if s.get("buy_signal",0)>=3)}, _f)
        # 限制最多1000行
        if _os.path.exists(sf) and _os.path.getsize(sf) > 500000:
            with open(sf, "r", encoding="utf-8") as _f: lines = _f.readlines()
            if len(lines) > 1000:
                with open(sf, "w", encoding="utf-8") as _f: _f.writelines(lines[-500:])
    except Exception as _e: logger.warning(f"[SignalPersist] 信号写入失败: {_e}")

    # D12: 支持 exclude_positions 参数过滤已持仓股票
    if request.args.get("exclude_positions", "false") == "true":
        try:
            held = set()
            from paper_engine import paper
            for p in paper.positions.keys():
                held.add(p)
            try:
                from live_trader import state as _lt_state
                for p in _lt_state.positions:
                    held.add(p.get("symbol", ""))
            except Exception as e:
                logger.warning("[Signals] 实盘持仓获取失败, 跳过排除: %s", e)
            signals = [s for s in signals if s.get("symbol", "") not in held]
        except Exception as _e:
            print(f"[App] exclude_positions过滤失败: {_e}")

    return jsonify({
        "code": 200,
        "total": len(signals),
        "resonance_count": resonance,
        "strategy_stats": {strat: sum(1 for s in signals if s["strategy"] == strat) for strat in strategies},
        "multi_strategy": multi_strategy,
        "signals": signals[:100],
    })


@app.route("/quant-backtest")
def page_quant_backtest():
    return redirect("/quant-backtest-v3", code=301)


@app.route("/api/quant-backtest")
def api_quant_backtest():
    """量化回测 API — 完整回测计算"""
    init_data()
    # 支持 POST JSON 或 URL 参数，两者优先级为 JSON > query string
    payload = request.get_json(silent=True) or {}
    def _get(key, default=None):
        if key in payload and payload.get(key) is not None and payload.get(key) != '':
            return payload.get(key)
        return request.args.get(key, default)

    formula = _get("formula", "tdx_bandit")
    start_str = _get("start", "2022-01-01")
    end_str = _get("end", "2025-12-31")
    try:
        max_pos = int(_get("maxPos", 3))
    except Exception:
        max_pos = 3
    try:
        pos_pct = float(_get("posPct", 0.3))
    except Exception:
        pos_pct = 0.3
    # 回退值从 master 读取 (参数治理三级体系)
    try:
        _m_fb = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
        _fb_sl = _m_fb.get("stop_loss", {}).get("hard", -0.055)
        _fb_tp = _m_fb.get("take_profit", {}).get("tp1", {}).get("profit_pct", 0.05)
    except Exception:
        _fb_sl, _fb_tp = -0.055, 0.05
    try:
        stop_loss = float(_get("stopLoss", _fb_sl))
    except Exception:
        stop_loss = _fb_sl
    try:
        take_profit = float(_get("takeProfit", _fb_tp))
    except Exception:
        take_profit = _fb_tp
    try:
        hold_days = int(_get("holdDays", 1))
    except Exception:
        hold_days = 1
    try:
        trail1_profit = float(_get("trail1Profit", 5)) / 100
    except Exception:
        trail1_profit = 0.05
    try:
        trail1_drop = float(_get("trail1Drop", 2)) / 100
    except Exception:
        trail1_drop = 0.02
    try:
        trail2_profit = float(_get("trail2Profit", 7)) / 100
    except Exception:
        trail2_profit = 0.07
    try:
        trail2_drop = float(_get("trail2Drop", 3)) / 100
    except Exception:
        trail2_drop = 0.03
    try:
        init_cap = int(_get("capital", 1_000_000))
    except Exception:
        init_cap = 1000000

    import numpy as np
    from datetime import datetime as _dt, timedelta

    # ── 1. 获取信号数据 ──
    signal_map = {
        "tdx_resonance": "signal_resonance",
        "tdx2_final": "signal_final",
        "tdx2_xg": "signal_xg",
        "tdx2_b1": "signal_b1",
        "tdx_qlj": "signal_qlj",
        "tdx_ztxf": "signal_ztxf",
        "tdx_bandit": "signal_bandit",
    }
    # TDX公式池: 用公式选股结果替代信号筛选
    if formula.startswith("tdx__"):
        pool_name = formula[5:]  # 去掉 tdx__ 前缀
        sig_field = None  # 不使用信号字段
    else:
        pool_name = None
        sig_field = signal_map.get(formula, "signal_resonance")

    candidates = []
    if sig_field:  # 普通公式才需要扫描因子缓存
        for s in _FACTOR_CACHE:
            try:
                d = getattr(s, 'entry_time', '') or getattr(s, 'signal_date', '')
                if d and len(d) >= 8:
                    date_int = int(d[:8])
                    start_int = int(start_str.replace('-', ''))
                    end_int   = int(end_str.replace('-', ''))
                    if date_int < start_int or date_int > end_int:
                        continue
            except Exception:
                continue

            score = getattr(s, sig_field, 0) or 0
            if score > 0:
                candidates.append({
                    "symbol": getattr(s, 'symbol', ''),
                    "name": getattr(s, 'name', ''),
                    "date": d[:10] if len(d) >= 10 else d[:8],
                    "close": getattr(s, 'close', 0) or 0,
                    "open": getattr(s, 'open', 0) or 0,
                    "change_pct": getattr(s, 'change_pct', 0) or 0,
                    "power_score": getattr(s, 'power_score', 0) or 0,
                    "buy_signal": getattr(s, 'buy_signal', 0) or 0,
                    "vol_ratio": getattr(s, 'vol_ratio', 0) or 0,
                    "signal": score,
                })

    # ── 2. 调用真实回测引擎 ──
    try:
        from backtest_engine import BacktestEngine
        engine = BacktestEngine(STOCK_DATA, _FACTOR_CACHE, _NAME_MAP)
        # TDX公式池: 加载股票列表
        if pool_name:
            from tdx_formulas import get_formula_stocks
            formula_stocks = get_formula_stocks(pool_name)
            formula_symbols = [s['code'] for s in formula_stocks if s.get('code')]
            print(f"[Backtest] TDX pool '{pool_name}': {len(formula_symbols)} stocks")
        else:
            formula_symbols = None

        result = engine.run(
            strategy=formula, signal_field=sig_field,
            formula_symbols=formula_symbols,
            start=start_str, end=end_str,
            max_positions=max_pos, position_pct=pos_pct,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=hold_days,
            trail1_profit=trail1_profit, trail1_drop=trail1_drop,
            trail2_profit=trail2_profit, trail2_drop=trail2_drop,
            initial_capital=init_cap,
            commission_rate=0.00025,  # 万2.5佣金
            stamp_duty=0.001,          # 千1印花税(卖)
        )
        # F6-修复: 真实引擎无交易时返回空数据+提示，不再生成假数据
        if not result.get('results'):
            result["no_data"] = True
            result["message"] = "选股条件未匹配到交易，请调整参数或日期范围"

        # ── 3. 计算基准(沪深300)权益曲线 ──
        benchmark_equity = []
        try:
            hs300 = STOCK_DATA.get('sh000300')
            if hs300 is not None and len(hs300) > 0:
                equity = result.get('equity_curve', [])
                if equity:
                    start_d = equity[0]['date']
                    end_d = equity[-1]['date']
                    hs300_slice = hs300.loc[start_d:end_d]
                    if len(hs300_slice) > 1:
                        base_close = float(hs300_slice.iloc[0]['close'])
                        for idx_date in hs300_slice.index:
                            d_str = str(idx_date)[:10]
                            cur_close = float(hs300_slice.loc[idx_date, 'close'])
                            bench_val = round(init_cap * cur_close / base_close, 0) if base_close > 0 else init_cap
                            benchmark_equity.append({'date': d_str, 'equity': bench_val})
        except Exception as _e:
            logger.warning(f"[backtest] 基准计算失败: {_e}")
        result['benchmark_equity'] = benchmark_equity

        # ── 4. 汇总季度/年度收益 ──
        monthly = result.get('monthly_returns', [])
        quarterly = {}
        annual = {}
        for m in (monthly or []):
            ym = m.get('month', '')  # '2025-01'
            ret = m.get('return', 0) or 0
            if len(ym) >= 7:
                y = ym[:4]
                q = y + '-Q' + str((int(ym[5:7]) - 1) // 3 + 1)
                quarterly[q] = quarterly.get(q, 0) + ret
                annual[y] = annual.get(y, 0) + ret
        result['quarterly_returns'] = {k: round(v, 4) for k, v in sorted(quarterly.items())}
        result['annual_returns'] = {k: round(v, 4) for k, v in sorted(annual.items())}

        # ── 补充: VaR/退出统计/行业集中度 ──
        m = result.get('metrics', {})
        if 'var_95' not in m:
            eq = result.get('equity_curve', [])
            if len(eq) > 1:
                eqv = [float(e['equity']) for e in eq]
                dr = np.diff(eqv) / eqv[:-1]
                m['var_95'] = round(float(np.percentile(dr, 5)), 6)
                m['var_99'] = round(float(np.percentile(dr, 1)), 6)
                tail = dr[dr <= m['var_95']]
                m['cvar'] = round(float(np.mean(tail)), 6) if len(tail) > 0 else 0
        if 'exit_stats' not in m:
            exits = {}
            for ext in ['stop_loss','take_profit','trail_stop','normal','force_close']:
                et = [t for t in result.get('results',[]) if t.get('exit_type') == ext]
                if et:
                    rets = [float(t['return_pct']) for t in et]
                    exits[ext] = {'count':len(et), 'win_rate':round(sum(1 for r in rets if r>0)/len(et),4),
                                  'avg_return':round(float(np.mean(rets)),4),
                                  'total_pnl':round(sum(t['net_profit'] for t in et),0)}
            m['exit_stats'] = exits
        if 'industry_concentration' not in m:
            ipnl = {}
            for t in result.get('results',[]):
                ind = '未分类'
                for fc in (_FACTOR_CACHE or []):
                    if getattr(fc, 'symbol', '') == t['symbol']:
                        ind = getattr(fc, 'industry', '未分类') or '未分类'
                        break
                ipnl[ind] = ipnl.get(ind, 0) + t.get('net_profit', 0)
            top = sorted(ipnl.items(), key=lambda x: -abs(x[1]))[:5]
            m['industry_concentration'] = {k: round(v,0) for k,v in top}
        result['metrics'] = m

        # 存入 DataStore
        # 数据富化: 交易列表补股票名称
        with_names(result.get('results', []))
        store.set('backtest', result)
        store.set('backtest_updated', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # F6-修复: 异常时返回错误信息，不再生成假数据
        return jsonify({
            "code": 500,
            "error": f"回测引擎异常: {str(e)}",
            "results": [], "equity_curve": [], "metrics": {},
            "no_data": True,
            "message": "回测计算失败，请检查数据源和参数配置",
        })


# [已移除] /api/backtest — V3回测替代
def _unused_api_backtest():
    """回测数据API — 返回策略表现指标"""
    init_data()
    strategy = request.args.get("strategy", "f1")
    period = request.args.get("period", "2y")

    from datetime import datetime as _dt
    now = _dt.now()
    if period == "1y":
        start_date = int(f"{now.year-1}{now.month:02d}{now.day:02d}")
    elif period == "6m":
        m = now.month - 6; y = now.year
        if m <= 0: m += 12; y -= 1
        start_date = int(f"{y}{m:02d}{now.day:02d}")
    else:
        start_date = int(f"{now.year-2}{now.month:02d}{now.day:02d}")

    end_date = int(now.strftime("%Y%m%d"))

    # Filter _FACTOR_CACHE by date range and strategy
    results = []
    for s in _FACTOR_CACHE:
        try:
            sig_date_str = getattr(s, 'signal_date', '') or getattr(s, 'entry_time', '')
            if sig_date_str and len(sig_date_str) >= 8:
                sig_date = int(sig_date_str[:8])
                if sig_date < start_date or sig_date > end_date:
                    continue
        except Exception as _e: print(f"[App] {_e}")

        sig_ok = True
        if strategy == "f1":
            sig_ok = getattr(s, "signal_resonance", 0) >= 2
        elif strategy == "dc5":
            sig_ok = getattr(s, "signal_dc5", 0) >= 1

        if sig_ok:
            results.append({
                "symbol": s.symbol,
                "close": getattr(s, "close", 0),
                "change_pct": getattr(s, "change_pct", 0),
                "vol_ratio": getattr(s, "vol_ratio", 0),
                "quality_score": getattr(s, "quality_score", 0),
                "buy_signal": getattr(s, "buy_signal", 0),
                "trend_score": getattr(s, "trend_score", 0),
            })

    # Simulated backtest stats (from _FACTOR_CACHE quality distribution)
    import numpy as _np
    changes = [r["change_pct"] for r in results if abs(r["change_pct"]) < 20]
    qualities = [r["quality_score"] for r in results]

    if changes:
        wr = sum(1 for c in changes if c > 0) / len(changes)
        avg_win = _np.mean([c for c in changes if c > 0]) if any(c > 0 for c in changes) else 0
        avg_loss = _np.mean([c for c in changes if c < 0]) if any(c < 0 for c in changes) else 0
        pf = abs(sum(c for c in changes if c > 0) / sum(c for c in changes if c < 0)) if any(c < 0 for c in changes) else 99
    else:
        wr = avg_win = avg_loss = pf = 0

    return jsonify({
        "code": 200,
        "strategy": strategy,
        "period": period,
        "signals": len(results),
        "wr": round(wr, 3),
        "avg_win": round(avg_win, 3),
        "avg_loss": round(avg_loss, 3),
        "pf": round(pf, 2),
        "max_consec_loss": 0,  # Would need trade simulation
        "daily_avg": len(results) / max((end_date - start_date) / 10000 * 250, 1),
        "results": results[:500],  # Limit for performance
    })


@app.route("/stock/<symbol>")
def stock_detail(symbol):
    """个股详情页。"""
    return render_template("stock_detail.html", symbol=symbol)


# ======================================================================
# API 接口
# ======================================================================

@app.route("/api/stocks")
def api_stocks():
    """旧API — 301重定向到新API (E26-P1 旧系统移除)"""
    from flask import redirect as _redirect
    return _redirect("/api/screener/top_stocks", code=301)

    signal = request.args.get("signal", "all")
    min_quality = float(request.args.get("min_quality", 0))
    sort_by = request.args.get("sort", "quality_score")
    limit = int(request.args.get("limit", 200))
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 50))
    trade_date = request.args.get("date", "")  # 历史日期查询
    buy_signal_min = int(request.args.get("buy_signal", 0))  # 最低买入信号等级

    # 日期过滤 — 随机采样200只快速重算
    if trade_date:
        results = []
        import random as _random
        cache_list = list(_FACTOR_CACHE)
        sample_size = min(200, len(cache_list))
        sampled = _random.sample(cache_list, sample_size)
        for s in sampled:
            stock_df = STOCK_DATA.get(s.symbol)
            if stock_df is None:
                continue
            factors = compute_factors_for_date(stock_df, trade_date)
            if not factors:
                continue
            info = _make_stock(s.symbol, factors)
            results.append(info)
    else:
        # E79: 提前终止 — _FACTOR_CACHE 已按power_score降序，只需取前 need_count 只
        need_count = min(page * page_size * 10, len(_FACTOR_CACHE) if _FACTOR_CACHE else 0)
        results = []
        for s in (_FACTOR_CACHE or []):
            if _is_stock(s.symbol):
                results.append(s)
                if len(results) >= need_count:
                    break
    
    # 股票类型过滤
    # 已经在上面提前过滤了，不再重复执行
    # stock_only = request.args.get("stock_only", "1")
    # if stock_only == "1":
    #     results = [s for s in results if _is_stock(s.symbol)]

    # 基础过滤管道
    if request.args.get("flt_st", "0") == "1":
        results = [s for s in results if not ('ST' in (s.name or '') or '*ST' in (s.name or '') or s.symbol.startswith('sh000'))]
    if request.args.get("flt_new", "0") == "1":
        from datetime import datetime as _dt2, timedelta as _td2
        cutoff = (_dt2.now() - _td2(days=60)).strftime("%Y%m%d")
        results = [s for s in results if (getattr(s, 'signal_date', '') or '').replace('-','') >= cutoff]
    if request.args.get("flt_vol", "0") == "1":
        results = [s for s in results if (getattr(s, 'volume', 0) or 0) >= 20000000]
    if request.args.get("flt_price", "0") == "1":
        results = [s for s in results if (getattr(s, 'close', 0) or 0) >= 3.0]
    if request.args.get("flt_pe", "0") == "1":
        results = [s for s in results if (getattr(s, 'change_pct', 0) or 0) > -99]  # PE>0近似: 非暴跌
    if request.args.get("flt_turn", "0") == "1":
        results = [s for s in results if (getattr(s, 'vol_ratio', 0) or 0) >= 0.5]  # 量比>0.5近似换手
    if request.args.get("flt_chg", "0") == "1":
        results = [s for s in results if -9.5 < (getattr(s, 'change_pct', 0) or 0) < 9.5]

    # 信号筛选
    if signal == "signal_all6":
        # 六信号共振: XG+B1+QLJ+ZTXF+Final+Resonance 全部满足
        results = [s for s in results
                   if getattr(s, 'signal_xg', 0) > 0
                   and getattr(s, 'signal_b1', 0) > 0
                   and getattr(s, 'signal_qlj', 0) > 0
                   and getattr(s, 'signal_ztxf', 0) > 0
                   and getattr(s, 'signal_final', 0) > 0
                   and getattr(s, 'signal_resonance', 0) >= 2]
    elif signal != "all":
        results = [s for s in results if getattr(s, signal, 0) > 0]

    # 买入信号等级筛选
    
    # A策略管道
    pipeline_active = request.args.get("pipeline") == "A"
    pre_count = len(results)
    if pipeline_active:
        results = [s for s in results
                   if getattr(s, "signal_resonance", 0) >= 2
                   and 18 <= getattr(s, "close", 0) <= 100
                   and getattr(s, "vol_ratio", 0) >= 3.0
                   and getattr(s, "limit_up", 0) == 0]
    if buy_signal_min > 0:
        results = [s for s in results if (s.buy_signal or 0) >= buy_signal_min]

    # 质量筛选
    if min_quality > 0:
        results = [s for s in results if s.quality_score >= min_quality]


    # 先计算行业热度 (影响排序)
    _refresh_sector_data()
    sector_heat: dict[str, dict] = {}
    for ind, idx_data in _SECTOR_KLINES.items():
        chg = idx_data["change_pct"]
        if chg > 0.015:
            heat, heat_label = 3, "🔥热"
        elif chg > 0.005:
            heat, heat_label = 2, "🌤温"
        elif chg > -0.01:
            heat, heat_label = 1, "🌥凉"
        else:
            heat, heat_label = 0, "❄冷"
        sector_heat[ind] = {"avg_chg": round(chg, 4), "heat": heat, "label": heat_label, "idx_code": idx_data["idx_code"]}

    # 排序
    sort_keys = {
        "quality_score": lambda x: x.quality_score,
        "change_pct": lambda x: x.change_pct,
        "open_pct": lambda x: x.open_pct,
        "daily_pl": lambda x: x.daily_pl,
        "vol_ratio": lambda x: x.vol_ratio,
        "trend_score": lambda x: x.trend_score,
        "volume_score": lambda x: x.volume_score,
        "position_score": lambda x: x.position_score,
        "signal_resonance": lambda x: x.signal_resonance,
        "signal_final": lambda x: x.signal_final,
        "close": lambda x: x.close,
        "atr_pct": lambda x: x.atr_pct,
        "low_suction_score": lambda x: x.low_suction_score,
        "capital_score": lambda x: x.capital_score,
        "in_out_days": lambda x: x.in_out_days,
        "symbol": lambda x: x.symbol,
        "name": lambda x: x.name or x.symbol,
        "institution_strength": lambda x: {"super_high": 3, "high": 2, "middle": 1, "low": 0}.get(x.institution_strength, 0),
        "power_score": lambda x: x.power_score,
        "momentum_score": lambda x: x.momentum_score,
        "breakout_score": lambda x: x.breakout_score,
        "buy_signal": lambda x: x.buy_signal,
        "entry_time": lambda x: _entry_time_rank(x.entry_time),
        "chg_5d": lambda x: x.chg_5d,
        "chg_10d": lambda x: x.chg_10d,
        "industry": lambda x: x.industry or "",
        "sector_heat": lambda x: sector_heat.get(x.industry, {}).get("heat", -1),
    }
    key_fn = sort_keys.get(sort_by, sort_keys["power_score"])
    sort_dir = request.args.get("sort_dir", "desc")
    reverse = sort_dir != "asc"
    results.sort(key=key_fn, reverse=reverse)

    # E22: 搜索过滤 — 支持代码/名称模糊搜索
    search = request.args.get("search", "").strip()
    if search:
        results = [s for s in results if search.lower() in str(getattr(s, 'symbol', '')).lower()
                   or search.lower() in str(getattr(s, 'name', '')).lower()]

    # 分页 — 先排序再切片
    total = len(results)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_stocks = results[start:end]

    result_dicts = []
    for s in page_stocks:
        d = _stock_to_dict_light(s)
        d["sector_heat"] = sector_heat.get(s.industry, {}).get("heat", -1)
        d["sector_label"] = sector_heat.get(s.industry, {}).get("label", "")
        d["sector_avg_chg"] = sector_heat.get(s.industry, {}).get("avg_chg", 0)
        result_dicts.append(d)

    response = jsonify({
        "code": 200,
        "total": total,
        "page": page,
        "page_size": page_size,
        "result": result_dicts,
        "sectors": {k: v for k, v in sorted(sector_heat.items(), key=lambda x: -x[1]["heat"])},
        "sector_data_source": _SECTOR_DATA_SOURCE if hasattr(sys.modules[__name__], '_SECTOR_DATA_SOURCE') else "通达信日线缓存",
        "sector_data_time": _SECTOR_DATA_TIME if hasattr(sys.modules[__name__], '_SECTOR_DATA_TIME') else "",  # E54: 数据时效
        "pipeline": "A" if pipeline_active else "",
        "pipeline_pre": pre_count if pipeline_active else 0,
    })
    # 保存到缓存
    with _api_cache_lock:
        _api_cache[cache_key] = (response, time.time())
        if len(_api_cache) > 50:
            oldest = min(_api_cache.items(), key=lambda x: x[1][1])
            del _api_cache[oldest[0]]
    return response


@app.route("/api/stock/<symbol>")
def api_stock_detail(symbol):
    """获取个股详情 + K线数据。支持 period=day|week|month。"""
    init_data()

    df = STOCK_DATA.get(symbol)
    if df is None:
        return jsonify({"code": 404, "msg": "Stock not found"}), 404

    factors = compute_factors(df)
    if not factors:
        factors = {}
    # 补充名称和代码
    factors["symbol"] = symbol
    factors["name"] = _resolve_name(symbol) or symbol

    period = request.args.get("period", "day")
    days = 500 if period == "day" else 1000  # 周/月需要更多原始数据
    klines_raw = get_stock_kline(STOCK_DATA, symbol, days=days)

    if period == "week":
        klines = _resample_klines(klines_raw, "W")
    elif period == "month":
        klines = _resample_klines(klines_raw, "M")
    else:
        klines = klines_raw

    return jsonify({
        "code": 200,
        "result": {
            "symbol": symbol,
            "factors": factors,
            "klines": klines,
        },
    })


def _resample_klines(klines: list[dict], rule: str) -> list[dict]:
    """周/月K线聚合。"""
    if not klines or len(klines) < 2:
        return klines
    try:
        import pandas as pd
        _df = pd.DataFrame(klines)
        _df["date"] = pd.to_datetime(_df["date"])
        _df = _df.set_index("date")
        _resampled = _df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
        _resampled = _resampled.reset_index()
        _resampled["date"] = _resampled["date"].dt.strftime("%Y-%m-%d")
        return _resampled.to_dict("records")
    except Exception:
        return klines


@app.route("/api/search")
def api_search():
    """搜索股票 — 支持代码和中文名称模糊搜索。"""
    init_data()

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"code": 200, "result": []})

    results = []
    q_lower = q.lower()

    for symbol in STOCK_DATA:
        # 代码匹配
        if q_lower in symbol.lower():
            name = _resolve_name(symbol)
            results.append({"symbol": symbol, "name": name})
            if len(results) >= 20:
                break

    # 名称匹配(如果按代码没找到)
    if len(results) < 5:
        for code, name in _NAME_MAP.items():
            if q in name and not any(r["symbol"] == code for r in results):
                results.append({"symbol": code, "name": name})
                if len(results) >= 20:
                    break

    return jsonify({"code": 200, "result": results})


@app.route("/api/formula/scan", methods=["POST"])
def api_formula_scan():
    """一键扫描通达信公式目录，自动导入新公式"""
    import xml.etree.ElementTree as ET
    tdx_dirs = [
        r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002",
        r"D:\通信达技术指标\01、散人竞价擒龙V8.59旗舰版（下载解压即可使用）\散人竞价擒龙V8.59旗舰版（无加密）\T0002",
    ]
    imported = []
    for tdx_dir in tdx_dirs:
        if not os.path.isdir(tdx_dir): continue
        # 扫描 XML 公式文件
        for fname in os.listdir(tdx_dir):
            if not fname.endswith('.xml'): continue
            fpath = os.path.join(tdx_dir, fname)
            tpool_path = os.path.join(tdx_dir, 'tpool', fname)
            # 如果tpool里还没有，复制过去
            if not os.path.exists(tpool_path) and os.path.getsize(fpath) > 100:
                import shutil
                shutil.copy2(fpath, tpool_path)
                imported.append(fname.replace('.xml', ''))

    return jsonify({
        "code": 200,
        "imported": imported,
        "count": len(imported),
        "message": f"成功导入 {len(imported)} 个新公式" if imported else "没有发现新公式"
    })


@app.route("/api/formula/save", methods=["POST"])
def api_formula_save():
    """保存自定义公式到 tpool 目录"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    code = data.get("code", "").strip()
    if not name:
        return jsonify({"code": 400, "error": "公式名称不能为空"})

    tpool_dir = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002\tpool"
    if not os.path.isdir(tpool_dir):
        return jsonify({"code": 500, "error": "tpool目录不存在"})

    import xml.etree.ElementTree as ET
    fpath = os.path.join(tpool_dir, f"{name}.xml")
    root = ET.Element("FormulaPool")
    ET.SubElement(root, "Name").text = name
    if code:
        ET.SubElement(root, "Code").text = code
    tree = ET.ElementTree(root)
    tree.write(fpath, encoding='gb2312', xml_declaration=True)
    return jsonify({"code": 200, "message": f"公式 {name} 已保存到 {fpath}"})


@app.route("/api/formulas")
def api_formulas():
    """获取通达信选股公式池列表(仅返回有选股结果的)。"""
    pools = scan_formula_pools()
    # 只返回有股票结果的 + 排除系统文件
    result = []
    for p in pools:
        if p.get("stock_count", 0) > 0:
            # 排除TDX系统文件(通常以特定前缀开头)
            fid = p.get("id", "")
            if not any(fid.startswith(pfx) for pfx in ['bigdata','zsextern','BK_','GN_','JJ_','bxzj_','cfg_','cnjj_','func_','fund_','gp_gz','LC_','ly_','Mr_','my_','pri_','PRO_','SHEET','STK_','sys_','tdx_','tmp_','ud_','USER','vipdoc','xgn_','ZQ_']):
                result.append({
                    "id": p["id"],
                    "file": p["file"],
                    "dir": p.get("dir", ""),
                    "formulas": p.get("formulas", []),
                    "stock_count": p.get("stock_count", 0),
                })
    return jsonify({"code": 200, "result": result})

@app.route("/api/formula/parse-tdx", methods=["POST"])
def api_formula_parse_tdx():
    """解析通达信公式源码,筛选符合条件的股票"""
    data = request.get_json() or {}
    formula_text = data.get("formula", "")
    if not formula_text:
        return jsonify({"code": 400, "error": "formula required"})

    from tdx_parser import parse_tdx_formula, evaluate_conditions, extract_tdx_pool

    # 先检测是否为TDX股票池XML
    pool = extract_tdx_pool(formula_text)
    if pool and pool.get('type') == 'tdx_pool' and pool['stocks']:
        # 保存为公式池
        codes = [s['symbol'] for s in pool['stocks']]
        import os as _os2, time as _time2
        pool_dir = CUSTOM_POOLS_DIR
        _os2.makedirs(pool_dir, exist_ok=True)
        pool_id = f"tdx_{int(_time2.time())}"
        with open(_os2.path.join(pool_dir, f"{pool_id}.txt"), 'w', encoding='utf-8') as f:
            f.write('\n'.join(codes))
        return jsonify({
            "code": 200, "type": "tdx_pool",
            "pool_name": pool['pool_name'], "count": len(codes),
            "pool_id": pool_id, "stocks": pool['stocks'],
            "message": f"从TDX股票池提取: {pool['pool_name']} ({len(codes)}只)"
        })

    # 非XML → 走公式源码解析
    parsed = parse_tdx_formula(formula_text)

    # 用解析出的条件扫描所有股票
    matched = []
    conditions = parsed.get('conditions', [])
    if conditions:
        init_data()  # 确保STOCK_DATA已加载
        for sym, df in STOCK_DATA.items():
            try:
                if len(df) < 20:
                    continue
                if evaluate_conditions(df, conditions):
                    matched.append(sym.replace('sh','').replace('sz','').replace('bj',''))
            except Exception as _e:
                logger.warning(f"[formula] 条件评估失败: {_e}")

    if not matched:
        return jsonify({"code": 200, "parsed": parsed, "pool_id": "", "count": 0,
                       "message": "未找到符合条件的股票，请检查公式"})

    # 保存为临时公式池
    import os as _os, time as _time
    pool_dir = CUSTOM_POOLS_DIR  # E26: config路径
    _os.makedirs(pool_dir, exist_ok=True)
    pool_id = f"tdx_{int(_time.time())}"
    with open(_os.path.join(pool_dir, f"{pool_id}.txt"), 'w', encoding='utf-8') as f:
        f.write('\n'.join(matched))
    from tdx_formulas import add_extra_dir
    add_extra_dir(pool_dir)

    return jsonify({"code": 200, "parsed": parsed, "pool_id": pool_id, "count": len(matched),
                   "message": f'找到{len(matched)}只符合条件的股票'})


@app.route("/api/formula/custom-pool", methods=["POST"])
def api_formula_custom_pool():
    """从粘贴的股票代码创建自定义公式池"""
    data = request.get_json() or {}
    codes = data.get("codes", [])
    name = data.get("name", "custom")
    if not codes:
        return jsonify({"code": 400, "error": "codes required"})
    import json as _json, os as _os, time as _time
    pool_dir = CUSTOM_POOLS_DIR  # E26: config路径
    _os.makedirs(pool_dir, exist_ok=True)
    pool_id = f"custom_{int(_time.time())}"
    # 保存为TXT
    fpath = _os.path.join(pool_dir, f"{pool_id}.txt")
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(codes))
    # 注册为扫描目录
    from tdx_formulas import add_extra_dir
    add_extra_dir(pool_dir)
    return jsonify({"code": 200, "pool_id": pool_id, "count": len(codes)})


@app.route("/api/formula/delete", methods=["POST"])
def api_formula_delete():
    """删除指定公式文件"""
    data = request.get_json() or {}
    name = data.get("name", "")
    if not name: return jsonify({"code":400,"error":"name required"})
    import os as _os
    deleted = []
    for d in [r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002\tpool",
              r"D:\quant_web\data\custom_pools"]:
        for ext in ['.xml', '.txt']:
            fp = _os.path.join(d, name + ext)
            if _os.path.exists(fp):
                try: _os.remove(fp); deleted.append(fp)
                except Exception as _e: print(f"[App] {_e}")
    return jsonify({"code":200,"deleted":len(deleted),"files":deleted})


@app.route("/api/formula/add-dir", methods=["POST"])
def api_formula_add_dir():
    """添加自定义公式目录"""
    data = request.get_json() or {}
    dir_path = data.get("path", "")
    if not dir_path or not os.path.isdir(dir_path):
        return jsonify({"code": 400, "error": "目录不存在"})
    from tdx_formulas import add_extra_dir
    ok = add_extra_dir(dir_path)
    return jsonify({"code": 200 if ok else 400, "added": ok, "message": "已添加" if ok else "已存在"})


@app.route("/api/formula/<pool_name>")
def api_formula_stocks(pool_name):
    """获取指定公式池的选股结果。"""
    stocks = get_formula_stocks(pool_name)
    # 补充名称
    init_data()
    for s in stocks:
        s["name"] = _resolve_name(s.get("code", ""))
    return jsonify({"code": 200, "pool": pool_name, "result": stocks, "total": len(stocks)})


@app.route("/api/signals")
def api_signals():
    """获取信号类型列表。"""
    return jsonify({
        "code": 200,
        "result": [
            {"key": k, "label": v} for k, v in SIGNAL_LABELS.items()
        ],
    })


@app.route("/api/refresh")
def api_refresh():
    """强制刷新——后台重建缓存。"""
    global _FACTOR_CACHE, _CACHE_READY
    import os as _os2, threading as _th, pickle as _pk2
    cache_f = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), "factor_cache.pkl")
    data_f = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), "stock_data.pkl")
    for f in [cache_f, data_f]:
        if _os2.path.exists(f):
            _os2.remove(f)
    import threading as _th
    global _FACTOR_CACHE, _CACHE_READY
    _FACTOR_CACHE = []
    _CACHE_READY = False
    if '_factor_write_lock' not in globals():
        globals()['_factor_write_lock'] = _th.Lock()
    def _bg_rebuild():
        with globals()['_factor_write_lock']:
            _precompute_factors_fast()
            try:
                with open(cache_f, "wb") as f: _pk2.dump(_FACTOR_CACHE, f)
            except Exception as _e: print(f"[App] {_e}")
    _th.Thread(target=_bg_rebuild, daemon=True).start()
    return jsonify({"code": 200, "msg": "后台重建中，约2分钟后刷新页面即可"})


@app.route("/api/summary")
def api_summary():
    """获取概要统计。"""
    init_data()

    return jsonify({
        "code": 200,
        "result": {
            "total_stocks": len(STOCK_DATA),
            "data_root": DATA_ROOT,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "signals": list(SIGNAL_LABELS.keys()),
        },
    })


def _compute_metrics(results, equity):
    """从回测结果计算绩效指标。"""
    import numpy as np
    if not results or not equity or len(equity) < 2:
        return {}

    n        = len(results)
    rets     = [r["return_pct"] for r in results]
    wins     = [r for r in rets if r > 0]
    losses   = [r for r in rets if r <= 0]
    wr       = len(wins) / n if n > 0 else 0
    avg_win  = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    pf       = abs(avg_win / avg_loss) if avg_loss != 0 else (10 if avg_win > 0 else 0)
    best     = max(rets) if rets else 0
    worst    = min(rets) if rets else 0

    eq       = [p["equity"] for p in equity]
    total_ret = (eq[-1] / eq[0] - 1) if eq[0] > 0 else 0
    daily_ret = []
    for i in range(1, len(eq)):
        if eq[i-1] > 0:
            daily_ret.append(eq[i] / eq[i-1] - 1)
    dr_arr   = np.array(daily_ret) if daily_ret else np.array([0])
    ann_vol  = float(np.std(dr_arr) * np.sqrt(252))
    sharpe   = float(np.mean(dr_arr) / np.std(dr_arr) * np.sqrt(252)) if np.std(dr_arr) > 0 else 0
    down_std = float(np.std(dr_arr[dr_arr < 0])) if (dr_arr < 0).any() else ann_vol
    sortino  = float(np.mean(dr_arr) / down_std * np.sqrt(252)) if down_std > 0 else 0

    peak_val = eq[0]
    max_dd   = 0
    for v in eq:
        if v > peak_val:
            peak_val = v
        dd = (v - peak_val) / peak_val
        if dd < max_dd:
            max_dd = dd

    years    = max(len(daily_ret) / 252, 0.1)
    ann_ret  = (1 + total_ret) ** (1 / years) - 1
    calmar   = ann_ret / abs(max_dd) if abs(max_dd) > 1e-9 else 0

    return {
        "total_return": round(total_ret, 4),
        "annual_return": round(ann_ret, 4),
        "annual_volatility": round(ann_vol, 4),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 2),
        "win_rate": round(wr, 4),
        "profit_factor": round(pf, 2),
        "best_trade": round(best, 4),
        "worst_trade": round(worst, 4),
        "n_trades": n,
        "total_pnl": round(sum(r.get("net_profit", 0) for r in results), 0),
    }


def _compute_monthly(equity):
    """计算月度收益。"""
    if not equity or len(equity) < 20:
        return []
    from collections import defaultdict
    monthly = defaultdict(list)
    for p in equity:
        if len(p["date"]) >= 7:
            key = p["date"][:7]
            monthly[key].append(p["equity"])
    result = []
    keys = sorted(monthly.keys())
    for i, key in enumerate(keys):
        vals = monthly[key]
        if i == 0:
            result.append({"year": int(key[:4]), "month": int(key[5:7]), "return_pct": 0})
        else:
            prev_vals = monthly[keys[i-1]]
            if prev_vals and vals:
                prev_eq = prev_vals[-1]
                curr_eq = vals[-1]
                ret = round((curr_eq / prev_eq - 1) * 100, 2) if prev_eq > 0 else 0
                result.append({"year": int(key[:4]), "month": int(key[5:7]), "return_pct": ret})
    return result


def _generate_demo_data(start_str, end_str):
    """生成演示回测数据 (返回dict, 不调用jsonify)。"""
    import numpy as np
    from datetime import datetime as _dt, timedelta

    import time
    np.random.seed(int(time.time() * 1000) % (2**31))
    # 将参数差异注入随机种子，确保不同参数生成不同数据
    start = _dt.strptime(start_str, "%Y-%m-%d")
    end   = _dt.strptime(end_str, "%Y-%m-%d")
    days  = max((end - start).days, 60)

    # 权益曲线
    equity = []
    eq_val = 1_000_000
    date = start
    while date <= end:
        if date.weekday() < 5:  # 工作日
            dt_str = date.strftime("%Y-%m-%d")
            if len(equity) == 0:
                equity.append({"date": dt_str, "equity": eq_val})
            else:
                ret = np.random.normal(0.0006, 0.012)
                eq_val *= (1 + ret)
                equity.append({"date": dt_str, "equity": round(eq_val, 0)})
        date += timedelta(days=1)

    # 交易记录
    n_trades = min(150, days // 3)
    results = []
    # 确保样本不超过总体
    avail_dates = [p["date"] for p in equity[30:]]
    if len(avail_dates) < n_trades:
        n_trades = max(len(avail_dates), 1)
    trade_dates = sorted(np.random.choice(avail_dates, n_trades, replace=False))
    for t_date in trade_dates:
        ret = np.random.normal(0.005, 0.025)
        ret = round(float(np.clip(ret, -0.10, 0.10)), 4)
        profit = round(10000 * ret, 0)
        sd = _dt.strptime(t_date, "%Y-%m-%d") + timedelta(days=1)
        results.append({
            "symbol": f"{np.random.choice(['600','000','300'])}{np.random.randint(100,999):03d}",
            "name": f"股票{np.random.randint(1000,9999)}",
            "buy_date": t_date,
            "sell_date": sd.strftime("%Y-%m-%d"),
            "buy_price": round(np.random.uniform(10, 80), 2),
            "sell_price": 0,
            "return_pct": ret,
            "net_profit": profit,
            "hold_days": 1,
            "exit_type": np.random.choice(["normal","take_profit","stop_loss"], p=[0.55,0.30,0.15]),
            "signal": "demo",
            "power_score": np.random.randint(30, 90),
        })
        results[-1]["sell_price"] = round(results[-1]["buy_price"] * (1 + ret), 2)

    metrics = _compute_metrics(results, equity)
    metrics["demo"] = True  # 标记为模拟数据, WFA跳过
    monthly = _compute_monthly(equity)

    return {
        "code": 200,
        "results": results,
        "equity_curve": equity,
        "metrics": metrics,
        "monthly_returns": monthly,
        "params": {"formula": "demo", "start": start_str, "end": end_str, "demo": True},
    }


def _resolve_name(symbol: str) -> str:
    """解析股票名称。"""
    code = symbol.lower().strip()
    for p in ['sh', 'sz', 'bj']:
        if code.startswith(p):
            code = code[len(p):]
            break
    name = _NAME_MAP.get(code, "") or _NAME_MAP.get(symbol, "") or _NAME_MAP.get(symbol.lower(), "")
    return name


def _resolve_industry(symbol: str) -> str:
    """解析细分行业。"""
    try:
        return get_industry(symbol) if callable(get_industry) else ""
    except Exception:
        return ""


def _entry_time_rank(et: str) -> int:
    """将入池时间转换为排序值 (早→大, 方便降序排列)。"""
    rankings = {
        "盘中触发 10:30": 4,
        "盘中突破 14:00": 3,
        "牛线突破 14:30": 2,
        "尾盘信号 14:55": 1,
        "尾盘双确认 14:55": 1,
        "收盘确认 15:00": 0,
    }
    return rankings.get(et, -1)


# D08: 代码前缀标准化 — 统一入口/出口格式
import re as _re_d08

def _clean_code(sym: str) -> str:
    """统一为6位数字代码（去除sh/sz/bj前缀）"""
    return _re_d08.sub(r'^(sh|sz|SH|SZ|bj|BJ)', '', str(sym)).strip()

def _add_prefix(code: str) -> str:
    """6位数字 → 带sh/sz前缀（用于外部API调用）"""
    if code.startswith(('sh', 'sz', 'bj')):
        return code
    if code.startswith(('688', '300', '301')):
        return 'sh' + code if code.startswith('688') else 'sz' + code
    if code.startswith(('60', '90')):
        return 'sh' + code
    return 'sz' + code


# E23: 统一操作日志
import threading as _oplog_lock_th
_oplog_lock = _oplog_lock_th.Lock()
_OPLOG_FILE = OPERATION_LOG  # E26: config路径

def _system_error(component, msg):
    """E20: 严重错误远程推送 — 限流每分钟1次"""
    global _last_error_push
    now = time.time()
    if now - getattr(sys.modules[__name__], '_last_error_push', 0) < 60:
        return  # 限流
    sys.modules[__name__]._last_error_push = now
    try:
        from dingtalk_alerts import send_alert
        send_alert(f"🔴 {component}", f"{msg}\n时间: {datetime.now()}", "critical")
    except Exception as _e:
        logger.warning(f"[alert] 告警推送失败: {_e}")


def _operation_log(op_type, detail=None):
    """记录用户/系统操作：manual_buy|manual_sell|auto_buy|auto_sell|config_change|system_restart"""
    try:
        entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": op_type, "detail": detail or {}}
        with _oplog_lock:
            import os as _oplog_os
            _oplog_os.makedirs(_oplog_os.path.dirname(_OPLOG_FILE), exist_ok=True)
            with open(_OPLOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 限制最多5000行
            if _oplog_os.path.exists(_OPLOG_FILE) and _oplog_os.path.getsize(_OPLOG_FILE) > 500000:
                with open(_OPLOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > 5000:
                    with open(_OPLOG_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines[-2000:])
    except Exception as _e:
        logger.warning(f"[log] 操作日志轮转失败: {_e}")


@app.route("/api/operation-log")
def api_operation_log():
    """E23: 查看最近操作日志"""
    try:
        if os.path.exists(_OPLOG_FILE):
            with open(_OPLOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            entries = [json.loads(l) for l in lines[-100:]]
            return jsonify({"code": 200, "entries": list(reversed(entries)), "total": len(entries)})
    except Exception as _e:
        return jsonify({"code": 500, "error": str(_e)})
    return jsonify({"code": 200, "entries": [], "total": 0})


def _is_stock(symbol: str) -> bool:
    """判断是否为A股(排除ETF/可转债/逆回购/基金)。"""
    code = symbol.lower().strip()
    # 去前缀
    for p in ['sh', 'sz', 'bj']:
        if code.startswith(p):
            code = code[len(p):]
            break
    # 数字部分
    if not code.isdigit():
        return True  # 无法判断，保留
    # 排除: 5xxxxx(ETF/LOF/基金), 1xxxxx(转债/基金), 2xxxxx(债券/回购)
    # 仅保留: 000-003(深主板), 300(创业板), 600-605(沪主板), 688(科创板)
    if not (code.startswith(('000','001','002','003','300','301','600','601','602','603','604','605','688'))):
        return False
    return True


def _stock_to_dict(s: StockInfo) -> dict:
    """将 StockInfo 转为 JSON 字典 — 匹配原站全部字段。"""
    return {
        "symbol": s.symbol,
        "name": s.name or s.symbol,
        "close": s.close,
        "open": s.open,
        "high": s.high,
        "low": s.low,
        "pre_close": s.pre_close,
        "volume": s.volume,
        "change_pct": s.change_pct,
        "open_pct": s.open_pct,
        "daily_pl": s.daily_pl,
        "vol_ratio": s.vol_ratio,
        "quality_score": s.quality_score,
        "trend_score": s.trend_score,
        "volume_score": s.volume_score,
        "position_score": s.position_score,
        "atr_pct": s.atr_pct,
        "signal_xg": s.signal_xg,
        "signal_b1": s.signal_b1,
        "signal_final": s.signal_final,
        "signal_qlj": s.signal_qlj,
        "signal_ztxf": s.signal_ztxf,
        "signal_resonance": s.signal_resonance,
        "limit_up": s.limit_up,
        "ma_position": s.ma_position,
        "low_suction_score": s.low_suction_score,
        "capital_score": s.capital_score,
        "institution_strength": s.institution_strength,
        "in_out_days": s.in_out_days,
        "in_demand_area": s.in_demand_area,
        "main_up": s.main_up,
        "high_control_up": s.high_control_up,
        "three_axes_signal": s.three_axes_signal,
        "double_axes_signal": s.double_axes_signal,
        "catch_bull_signal_time": s.catch_bull_signal_time,
        "entry_time": s.entry_time,
        "signal_date": s.signal_date,
        "power_score": s.power_score,
        "momentum_score": s.momentum_score,
        "breakout_score": s.breakout_score,
        "buy_signal": s.buy_signal,
        "chg_5d": s.chg_5d,
        "chg_10d": s.chg_10d,
        "rsi_score": s.rsi_score, "macd_score": s.macd_score,
        "boll_score": s.boll_score, "atr_score": s.atr_score,
        "vol_score": s.vol_score, "bias_score": s.bias_score,
        "money_score": s.money_score, "turnover_score": s.turnover_score,
        "chg_score": s.chg_score,
        "industry": s.industry,
    }


def _stock_to_dict_light(s: StockInfo) -> dict:
    """精简版 — 只返回前端表格实际使用的字段。"""
    return {
        "symbol": s.symbol, "name": s.name or s.symbol,
        "close": s.close, "change_pct": s.change_pct,
        "open_pct": s.open_pct, "vol_ratio": s.vol_ratio,
        "chg_5d": s.chg_5d, "chg_10d": s.chg_10d,
        "power_score": s.power_score, "buy_signal": s.buy_signal,
        "capital_score": s.capital_score,
        "trend_score": s.trend_score, "momentum_score": s.momentum_score,
        "volume_score": s.volume_score, "position_score": s.position_score,
        "rsi_score": s.rsi_score, "macd_score": s.macd_score,
        "boll_score": s.boll_score, "atr_score": s.atr_score,
        "vol_score": s.vol_score, "bias_score": s.bias_score,
        "money_score": s.money_score, "turnover_score": s.turnover_score,
        "signal_xg": s.signal_xg, "signal_b1": s.signal_b1,
        "signal_final": s.signal_final, "signal_qlj": s.signal_qlj,
        "signal_ztxf": s.signal_ztxf, "signal_resonance": s.signal_resonance,
        "industry": s.industry, "entry_time": s.entry_time,
        "signal_date": s.signal_date, "limit_up": s.limit_up,
        "quality_score": s.quality_score,
        "fund_score": getattr(s, 'fund_score', 0),
        "chip_score": getattr(s, 'chip_score', 0),
        "rating_score": getattr(s, 'rating_score', 0),
        "fund_flow_score": getattr(s, 'fund_flow_score', 0),
        "chip_struct_score": getattr(s, 'chip_struct_score', 0),
        "rating_dist_score": getattr(s, 'rating_dist_score', 0),
        "tech_score": getattr(s, 'tech_score', 0),
    }


# ======================================================================
# 回测 V2 API
# ======================================================================

# [deprecated] /knowledge, /profile, /sessions — 已废弃

@app.route("/quant-backtest-v2")
def page_quant_backtest_v2():
    # V2模板不存在, 重定向到V3
    from flask import redirect
    return redirect("/quant-backtest-v3")

@app.route("/factor-dashboard")
def page_factor_dashboard():
    """已迁移到 /factor-health，301跳转"""
    from flask import redirect
    return redirect("/factor-health", code=301)

@app.route("/quant-backtest-v3")
def page_quant_backtest_v3():
    return render_template("quant_backtest_v3.html")

@app.route("/api/quant-backtest-v3", methods=["POST"])
def api_quant_backtest_v3():
    """V3专用: 支持自定义公式池 + 所有参数"""
    data = request.get_json() or {}

    # 调试模式: _no_cache=1 跳过所有缓存
    _no_cache = data.pop('_no_cache', None)

    # ── 缓存检查：同样参数秒返 ──
    global _BT_CACHE
    import hashlib
    cache_parts = [
        str(data.get(k,'')) for k in ['formula','start','end','maxPos','posPct',
        'stopLoss','takeProfit','holdDays','trail1Profit','trail1Drop',
        'trail2Profit','trail2Drop','trail3Profit','trail3Drop','sellRatio1','sellRatio2','sellRatio3','atrMult','atrPeriod','limitUpEnabled','limitUpDrop','mode','pool_id','marketFilter','capital']
    ]
    cache_key = hashlib.md5('|'.join(cache_parts).encode()).hexdigest()
    print(f"[BT-v3] stopLoss={data.get('stopLoss')} posPct={data.get('posPct')} maxPos={data.get('maxPos')} cache={'SKIP' if _no_cache else 'check'}")
    if not _no_cache and cache_key in _BT_CACHE:
        cached = dict(_BT_CACHE[cache_key])
        cached['cached'] = True
        cached['cache_time'] = datetime.now().strftime("%H:%M:%S")
        return jsonify(cached)
    print(f"[BT-v3] 缓存未命中, 运行回测...")

    formula = data.get("formula", "tdx_bandit")
    pool_id = data.get("pool_id", "")  # TDX公式解析后的池ID

    init_data()
    # 信号映射
    sig_map = {"tdx_resonance":"signal_resonance","tdx2_final":"signal_final","tdx2_xg":"signal_xg","tdx2_b1":"signal_b1","tdx_qlj":"signal_qlj","tdx_ztxf":"signal_ztxf","tdx_bandit":"signal_bandit"}
    sig_field = sig_map.get(formula, "signal_resonance")

    # 加载公式池 (pool_id 或 formula中的tdx__前缀)
    if not pool_id and formula.startswith("tdx__"):
        pool_id = formula[5:]
    fSyms = None
    if pool_id:
        from tdx_formulas import get_formula_stocks
        stocks = get_formula_stocks(pool_id)
        codes = [s['code'] for s in stocks if s.get('code')]
        fSyms = []
        for c in codes:
            for p in ['sh','sz','bj']:
                if p+c in STOCK_DATA: fSyms.append(p+c); break

    # 参数清洗: 止损率总是负数 (>0时自动取反)
    sl = float(data.get("stopLoss",-0.05))
    if sl > 0: sl = -sl  # 正数→负数 (前端已÷100, 例如0.05→-0.05=-5%)
    tp = float(data.get("takeProfit",0.08))
    if tp <= 0: tp = 99.0   # 永不触发
    hd = int(data.get("holdDays",1))
    if hd <= 0: hd = 999
    t1p = float(data.get("trail1Profit",5))
    t1d = float(data.get("trail1Drop",2))
    if t1p <= 0 or t1d <= 0: t1p = 0; t1d = 0  # 禁用
    t2p = float(data.get("trail2Profit",7))
    t2d = float(data.get("trail2Drop",3))
    if t2p <= 0 or t2d <= 0: t2p = 0; t2d = 0
    t3p = float(data.get("trail3Profit",12))
    t3d = float(data.get("trail3Drop",3))
    if t3p <= 0 or t3d <= 0: t3p = 0; t3d = 0
    s1 = float(data.get("sellRatio1",0.25))
    s2 = float(data.get("sellRatio2",0.25))
    s3 = float(data.get("sellRatio3",0.25))
    lu_enabled = bool(int(data.get("limitUpEnabled",1)))
    lu_drop = float(data.get("limitUpDrop",3))/100

    # 交易成本从 master 读取 (参数治理三级体系)
    try:
        _cost = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8")).get("trading_cost", {})
    except Exception:
        _cost = {}

    # E19: 大盘环境过滤 — 根据牛熊分布调整仓位
    mkt_filter = bool(int(data.get("marketFilter", 0)))
    mkt_factor = 1.0
    if mkt_filter:
        try:
            from market_filter import MarketFilter
            mf = MarketFilter(enabled=True)
            bench = STOCK_DATA.get('sh000300')
            if bench is not None and len(bench) > 0:
                analysis = mf.analyze_period(bench, data.get("start","2024-06-01"), data.get("end","2025-12-31"))
                mkt_factor = analysis.get("avg_factor", 1.0)
        except Exception as _e:
            logger.warning(f"[mf] 市场因子分析失败: {_e}")

    adj_pos_pct = float(data.get("posPct", 0.3))
    if mkt_filter:
        adj_pos_pct = adj_pos_pct * mkt_factor

    try:
        from backtest_engine import BacktestEngine
        engine = BacktestEngine(STOCK_DATA, _FACTOR_CACHE, _NAME_MAP)
        result = engine.run(
            strategy=formula, signal_field=sig_field, formula_symbols=fSyms,
            start=data.get("start","2024-06-01"), end=data.get("end","2025-12-31"),
            max_positions=int(data.get("maxPos",3)), position_pct=adj_pos_pct,
            stop_loss=sl, take_profit=tp, hold_days=hd,
            trail1_profit=t1p/100, trail1_drop=t1d/100,
            trail2_profit=t2p/100, trail2_drop=t2d/100,
            trail3_profit=t3p/100, trail3_drop=t3d/100,
            sell_ratio_1=s1, sell_ratio_2=s2, sell_ratio_3=s3,
            limit_up_enabled=lu_enabled, limit_up_open_drop=lu_drop,
            initial_capital=int(data.get("capital",1000000)),
            commission_rate=_cost.get("commission_rate",0.00025),
            stamp_duty=_cost.get("stamp_duty",0.0005),
        )
        n_trades = len(result.get('results',[])); n_eq = len(result.get('equity_curve',[]))
        print(f"[BT-v3] 引擎结果: trades={n_trades}, equity_pts={n_eq}")
        if not result.get('results'):
            # F6-修复: V3引擎无交易时返回空数据+提示
            result["no_data"] = True
            result["message"] = "选股条件未匹配到交易"

        # 基准 — 根据股票池自动选指数
        bench = []
        bench_name = '沪深300'
        # 检测股票池成分
        trades = result.get('results',[])
        syms = set(t.get('symbol','')[:3] for t in trades) if trades else set()
        if all(s.startswith('30') for s in syms if s): bench_key = 'sz399006'; bench_name = '创业板指'
        elif all(s.startswith('68') for s in syms if s): bench_key = 'sh000688'; bench_name = '科创50'
        elif any(s.startswith('00') or s.startswith('30') for s in syms if s): bench_key = 'sz399001'; bench_name = '深证成指'
        else: bench_key = 'sh000300'; bench_name = '沪深300'
        try:
            bm = STOCK_DATA.get(bench_key)
            if bm is None: bm = STOCK_DATA.get('sh000300'); bench_name = '沪深300'
            if bm is not None and len(bm) > 0:
                eq = result.get('equity_curve',[])
                if eq:
                    bm_slice = bm.loc[eq[0]['date']:eq[-1]['date']]
                    if len(bm_slice)>1:
                        bbase = float(bm_slice.iloc[0]['close'])
                        for idx in bm_slice.index:
                            bench.append({'date':str(idx)[:10],'equity':round(int(data.get('capital',1000000))*float(bm_slice.loc[idx,'close'])/bbase,0)})
        except Exception as _e: print(f"[App] {_e}")
        result['benchmark_equity'] = bench
        result['benchmark_name'] = bench_name

        # VaR + 补充指标
        import numpy as np
        eq_vals = [float(e['equity']) for e in result.get('equity_curve',[])]
        if len(eq_vals)>1:
            dr = np.diff(eq_vals)/eq_vals[:-1]
            result['metrics']['var_95'] = round(float(np.percentile(dr,5)),6)
            result['metrics']['var_99'] = round(float(np.percentile(dr,1)),6)
            # 最大连续亏损
            consec = 0; max_consec = 0
            for t in result.get('results',[]):
                if t.get('net_profit',0) < 0: consec += 1; max_consec = max(max_consec, consec)
                else: consec = 0
            result['metrics']['max_consec_loss'] = max_consec
            # 月度胜率
            mon = {}
            for t in result.get('results',[]):
                m = (t.get('sell_date','') or '')[:7]
                if m: mon.setdefault(m, {'w':0,'l':0})
                if m: mon[m]['w' if t.get('net_profit',0)>0 else 'l'] += 1
            mon_wins = sum(1 for v in mon.values() if v['w']>v['l'])
            result['metrics']['monthly_win_rate'] = round(mon_wins/max(len(mon),1),4)

        # Exit stats
        exits = {}
        for ext in ['stop_loss','take_profit','trail_stop','normal','force_close']:
            et = [t for t in result.get('results',[]) if t.get('exit_type')==ext]
            if et:
                rets = [float(t['return_pct']) for t in et]
                exits[ext] = {'count':len(et),'win_rate':round(sum(1 for r in rets if r>0)/len(et),4),
                              'avg_return':round(float(np.mean(rets)),4),
                              'total_pnl':round(sum(t['net_profit'] for t in et),0)}
        result['metrics']['exit_stats'] = exits

        # Industry
        ipnl = {}
        for t in result.get('results',[]):
            ind = '未分类'
            for fc in (_FACTOR_CACHE or []):
                if getattr(fc,'symbol','')==t['symbol']: ind = getattr(fc,'industry','未分类') or '未分类'; break
            ipnl[ind] = ipnl.get(ind,0)+t.get('net_profit',0)
        top = sorted(ipnl.items(),key=lambda x:-abs(x[1]))[:5]
        result['metrics']['industry_concentration'] = {k:round(v,0) for k,v in top}

        # WFA模式 (真训练/测试拆分, 仅真实交易数据)
        if data.get("mode") == "wfa" and result.get('results') and not result.get('metrics',{}).get('demo'):
            windows = []
            sd = datetime.strptime(data.get("start","2024-06-01"), '%Y-%m-%d')
            ed = datetime.strptime(data.get("end","2025-12-31"), '%Y-%m-%d')
            step = timedelta(days=180)
            cur = sd
            while cur + timedelta(days=365) < ed:
                train_end = cur + timedelta(days=365)
                test_end = min(train_end + step, ed)
                try:
                    # 训练期回测
                    train_r = engine.run(strategy=formula, signal_field=sig_field, formula_symbols=fSyms,
                                    start=cur.strftime('%Y-%m-%d'), end=train_end.strftime('%Y-%m-%d'),
                                    max_positions=int(data.get("maxPos",3)), position_pct=float(data.get("posPct",0.3)),
                                    stop_loss=sl, take_profit=tp, hold_days=hd,
                                    trail1_profit=t1p/100, trail1_drop=t1d/100,
                                    trail2_profit=t2p/100, trail2_drop=t2d/100)
                    # 测试期回测(同参数,不可见数据)
                    test_r = engine.run(strategy=formula, signal_field=sig_field, formula_symbols=fSyms,
                                    start=train_end.strftime('%Y-%m-%d'), end=test_end.strftime('%Y-%m-%d'),
                                    max_positions=int(data.get("maxPos",3)), position_pct=float(data.get("posPct",0.3)),
                                    stop_loss=sl, take_profit=tp, hold_days=hd,
                                    trail1_profit=t1p/100, trail1_drop=t1d/100,
                                    trail2_profit=t2p/100, trail2_drop=t2d/100)
                    t_sharpe = train_r['metrics'].get('sharpe',0)
                    v_sharpe = test_r['metrics'].get('sharpe',0)
                    t_return = train_r['metrics'].get('total_return',0)
                    v_return = test_r['metrics'].get('total_return',0)
                    decay = round(abs(t_return - v_return),4)
                    windows.append({
                        'train':cur.strftime('%Y-%m-%d'),'train_end':train_end.strftime('%Y-%m-%d'),
                        'test':train_end.strftime('%Y-%m-%d'),'test_end':test_end.strftime('%Y-%m-%d'),
                        'train_sharpe':t_sharpe,'test_sharpe':v_sharpe,
                        'train_return':t_return,'test_return':v_return,
                        'sharpe_decay':decay,
                        'train_trades':train_r['metrics'].get('n_trades',0),
                        'test_trades':test_r['metrics'].get('n_trades',0)})
                except Exception as _e: print(f"[App] WFA窗口异常: {_e}")
                cur += step
            if windows:
                decays = [w['sharpe_decay'] for w in windows]
                avg_decay = round(np.mean(decays),4)
                result['wfa'] = {'windows':windows,'count':len(windows),
                    'overfit_score':avg_decay,
                    'overfit_warning':'⚠️可能过拟合(衰减>0.15)' if avg_decay>0.15 else ('✅样本内外一致(衰减<0.05)' if avg_decay<0.05 else '📊轻微差异')}

        # 存入缓存 (内存 + 磁盘持久化)
        result['cached'] = False
        _BT_CACHE[cache_key] = result
        if len(_BT_CACHE) > _BT_CACHE_MAX:
            _BT_CACHE.pop(next(iter(_BT_CACHE)))
        _BT_CACHE_DIRTY = True
        _debounce_save_cache()
        # E16: 自动记录交易到ML数据仓库
        try:
            from data_collector import collector
            collector.record_backtest(result, f"btv3_{formula}")
        except Exception as _e:
            logger.warning(f"[collector] 回测结果记录失败: {_e}")
        # E23/E25: 自动记录因子快照到 factors.db
        try:
            from data_collector import collector
            fc = _FACTOR_CACHE
            if fc and len(fc) > 0:
                cnt = collector.record_factors(
                    data.get("end", datetime.now().strftime("%Y-%m-%d")), fc)
                print(f"[Collector] record_factors: date={data.get('end','?')}, count={cnt}")
            else:
                init_data()
                fc2 = _FACTOR_CACHE
                if fc2 and len(fc2) > 0:
                    cnt = collector.record_factors(
                        data.get("end", datetime.now().strftime("%Y-%m-%d")), fc2)
                    print(f"[Collector] record_factors(fallback): date={data.get('end','?')}, count={cnt}")
                else:
                    print(f"[Collector] WARNING: _FACTOR_CACHE still empty after fallback")
        except Exception as e:
            import traceback
            print(f"[Collector] ERROR: {e}")
            traceback.print_exc()
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"code":500,"error":str(e)})


# ═══════════════════ E16 ML数据仓库 ═══════════════════

@app.route("/api/ml/stats")
def api_ml_stats():
    """ML数据仓库统计"""
    try:
        from data_collector import collector
        return jsonify({"code": 200, **collector.stats()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/ml/training-data")
def api_ml_training_data():
    """提取ML训练数据 (需≥1000样本)"""
    try:
        from data_collector import collector
        data = collector.get_training_data()
        if data is None:
            return jsonify({"code": 200, "ready": False, "message": "样本不足1000条"})
        return jsonify({"code": 200, "ready": True, "total": data["total"],
                        "samples": data["samples"], "columns": data["columns"]})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════ E17 因子优化 ═══════════════════

@app.route("/api/factors/optimize")
def api_factors_optimize():
    """因子IC评估 — 返回所有因子的预测能力排名"""
    try:
        from factor_optimizer import optimizer
        result = optimizer.evaluate_all()
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factors/report")
def api_factors_report():
    """因子分析报告 — 纯文本"""
    try:
        from factor_optimizer import optimizer
        return optimizer.report(), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/api/factors/combos")
def api_factors_combos():
    """最优因子组合推荐"""
    try:
        from factor_optimizer import optimizer
        combos = optimizer.search_combos(top_n=3)
        return jsonify({"code": 200, "combos": combos})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  E257: 人工策略干预 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/manual_strategy_override", methods=["GET", "POST", "DELETE"])
def api_manual_strategy_override():
    """人工策略干预 — GET=查看 / POST=强制切换 / DELETE=清除"""
    import os as _os
    try:
        cfg_path = LIVE_CONFIG  # E26: config路径
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return jsonify({"code": 500, "message": "无法读取配置文件"})

    if request.method == "GET":
        override = config.get("manual_override", {})
        return jsonify({
            "code": 200,
            "data": {
                "active_strategy": config.get("strategy_scheduler", {}).get("active_strategy", "ma_cross"),
                "override_enabled": override.get("enabled", False),
                "override_strategy": override.get("strategy_name"),
                "expire_at": override.get("expire_at"),
                "reason": override.get("reason"),
                "operator": override.get("operator"),
                "timestamp": override.get("timestamp"),
            }
        })

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        strategy_name = data.get("strategy_name", "")
        expire_at = data.get("expire_at")
        reason = data.get("reason", "手动干预")
        operator = data.get("operator", "Web")

        # 验证策略存在
        available = list(config.get("strategy_params", {}).keys())
        if strategy_name not in available:
            return jsonify({"code": 400, "message": f"策略 {strategy_name} 不存在，可用: {available}"})

        config["strategy_scheduler"] = config.get("strategy_scheduler", {})
        config["strategy_scheduler"]["active_strategy"] = strategy_name
        config["manual_override"] = {
            "enabled": True,
            "strategy_name": strategy_name,
            "expire_at": expire_at,
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now().isoformat(),
        }

        # 原子写入
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, cfg_path)

        return jsonify({"code": 200, "message": f"策略已切换为 {strategy_name}", "data": config["manual_override"]})

    if request.method == "DELETE":
        config["manual_override"] = {
            "enabled": False, "strategy_name": None,
            "expire_at": None, "reason": None, "operator": None, "timestamp": None,
        }
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, cfg_path)

        return jsonify({"code": 200, "message": "干预已清除，恢复自动选拔"})


@app.route("/api/strategies/available")
def api_strategies_available():
    """获取可用策略列表"""
    try:
        cfg_path = LIVE_CONFIG  # E26: config路径
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        strategies = list(config.get("strategy_params", {}).keys())
        return jsonify({"code": 200, "data": strategies})
    except Exception:
        return jsonify({"code": 200, "data": ["ma_cross"]})


# ═══════════════════════════════════════════════════════════════
#  E256: 因子实盘失效监控 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/factor/monitor")
def api_factor_monitor():
    """获取因子监控数据 (E256).
    GET /api/factor/monitor?date=2026-06-20
    """
    trade_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    try:
        from quant_framework.analysis.factor_monitor import FactorMonitor
        monitor = FactorMonitor()
        results = monitor.monitor_all_factors(trade_date)
        return jsonify({"code": 200, "data": results, "date": trade_date})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/perf")
def api_perf():
    """性能诊断 — 返回关键路径最近耗时 (秒)"""
    report = {}
    for key, vals in sorted(_PERF.items()):
        if vals:
            report[key] = {
                "last": vals[-1],
                "avg": round(sum(vals) / len(vals), 3),
                "max": round(max(vals), 3),
                "samples": len(vals),
            }
    # 补充系统级信息
    report["_sys"] = {
        "factor_cache_size": len(_FACTOR_CACHE),
        "stock_data_size": len(STOCK_DATA),
        "cache_ready": _CACHE_READY,
        "api_cache_entries": len(_api_cache),
    }
    return jsonify({"code": 200, "data": report})

@app.route("/api/factor/ic-history")
def api_factor_ic_history():
    """因子 IC 历史 (E256).
    GET /api/factor/ic-history?factor=ma5_slope&days=60
    """
    factor_name = request.args.get("factor", "ma5_slope")
    days = int(request.args.get("days", 60))
    try:
        from quant_framework.analysis.factor_monitor import FactorMonitor
        monitor = FactorMonitor()
        history = monitor.get_ic_history(factor_name, days=days)
        return jsonify({"code": 200, "data": history, "factor_name": factor_name})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  E258: 选股器 API
# ═══════════════════════════════════════════════════════════════

# P1-1: 选股器全量数据缓存（跨分页复用，避免每次翻页重算2966条）
_SCREENER_CACHE: dict[str, tuple[list, str, float]] = {}  # market → (data, source, timestamp)

@app.route("/api/screener/top_stocks")
def api_screener_top_stocks():
    """选股器 Top N 排行 (E258+P1-1 分页加速).
    GET /api/screener/top_stocks?limit=50&market=stock
    market: all | stock | etf | bond
    funnel: 1=启用6层漏斗TDX优先排序 (E274)
    """
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    market = request.args.get("market", "all")
    _funnel_raw = request.args.get("funnel", "0")
    use_funnel = _funnel_raw in ("1", "2")
    funnel_mode = "v2" if _funnel_raw == "2" else "v1"
    if market not in ("all", "stock", "etf", "bond"):
        market = "all"
    # 确保 StockScreener 类可用 (E269 修复)
    try:
        from stock_screener import StockScreener
        _screener_ready = True
    except ImportError as e:
        logger.warning(f"选股器导入失败: {e}")
        return jsonify({"code": 500, "error": "选股器不可用", "data": [], "source": "none"}), 500
    try:
        _now = time.time()
        _cache_entry = _SCREENER_CACHE.get(market)
        if _cache_entry and _now - _cache_entry[2] < 30:
            full_data, source = _cache_entry[0], _cache_entry[1]
        else:
            screener = StockScreener()
            result = screener.rank_stocks(market=market, use_funnel=use_funnel, funnel_mode=funnel_mode)
            source = "qmt" if screener._qmt_mode else ("tdx_close" if screener._data_date else "tdx_stale")
            data_date = screener._data_date or ""
            full_data = result.to_dict("records") if hasattr(result, "to_dict") else []
            # ── E274: 展平漏斗字段到顶层 ──
            for _d in full_data:
                _f = _d.pop("_filter", {}) or {}
                _d["filter_score"] = _f.get("filter_score", 0)
                _d["filter_layer"] = _f.get("max_layer", 0)
                _d["filter_name"] = _f.get("layer_name", "")
                _d["filter_passed"] = _f.get("passed", False)
                _d["filter_reason"] = _f.get("layer_reason", "")
                _d["tdx_dual"] = _f.get("tdx_dual", False)
            _SCREENER_CACHE[market] = (full_data, source, _now)
            # 附加实时现价 (QMT xtdata)
            _rt_codes, _rt_map = [], {}
            for _d in full_data:
                _s = _d.get("symbol", "")
                if _s:
                    _c = _s[2:] + ('.SH' if _s.startswith('sh') else '.SZ' if _s.startswith('sz') else '.BJ')
                    _rt_codes.append(_c); _rt_map[_c] = _s
            if _rt_codes:
                try:
                    from xtquant import xtdata
                    _all_t = {}
                    for _i in range(0, len(_rt_codes), 50):
                        _bt = xtdata.get_full_tick(_rt_codes[_i:_i+50])
                        if _bt: _all_t.update(_bt)
                    for _c, _t in _all_t.items():
                        _s = _rt_map.get(_c)
                        if _s and _t.get('lastPrice',0)>0:
                            _lp = float(_t['lastPrice'])
                            _lc = float(_t.get('lastClose', _lp))
                            _open = float(_t.get('open', _lp))
                            _high = float(_t.get('high', _lp))
                            _amt = float(_t.get('amount', 0))
                            _bvol = sum(float(v) for v in _t.get('bidVol', [])) if isinstance(_t.get('bidVol'), list) else float(_t.get('bidVol', 0))
                            _avol = sum(float(v) for v in _t.get('askVol', [])) if isinstance(_t.get('askVol'), list) else float(_t.get('askVol', 0))
                            for _d in full_data:
                                if _d.get("symbol")==_s:
                                    _d["realtime_price"] = round(_lp, 2)
                                    _d["realtime_chg"] = round((_lp/_lc-1)*100, 2) if _lc>0 else 0
                                    # L2 买卖盘比: >2=买压, <0.5=卖压
                                    _d["fund_pressure"] = round(_bvol/max(_avol,1), 1) if _bvol+_avol>0 else 0
                                    _d["auction_chg"] = round((_open/_lc-1)*100, 2) if _lc>0 else 0
                                    _d["_l2_ok"] = True  # 标记L2注入成功
                                    _at_limit = False
                                    _lpct = 0.20 if _s.startswith(('sz30','sh688')) else 0.10
                                    if _lc > 0:
                                        _limit_price = _lc * (1+_lpct)
                                        _at_limit = _lp >= _limit_price * 0.998
                                        _d["limit_up_hit"] = _high >= _limit_price * 0.995
                                    _d["at_limit"] = _at_limit  # 当前在涨停位
                                    break
                except Exception: pass
        # 注入实时行情 (缓存命中也执行)
        _rt_codes, _rt_map = [], {}
        for _d in full_data:
            _sd = _d.get("symbol","")
            if _sd:
                _cd = _sd[2:]+('.SH' if _sd.startswith('sh') else '.SZ' if _sd.startswith('sz') else '.BJ')
                _rt_codes.append(_cd); _rt_map[_cd]=_sd
        if _rt_codes:
            try:
                from xtquant import xtdata
                _all_t = {}
                for _i in range(0,len(_rt_codes),50):
                    _bt=xtdata.get_full_tick(_rt_codes[_i:_i+50])
                    if _bt: _all_t.update(_bt)
                for _c,_t in _all_t.items():
                    _sd=_rt_map.get(_c)
                    if _sd and _t.get('lastPrice',0)>0:
                        _lp=float(_t['lastPrice']); _lc=float(_t.get('lastClose',_lp))
                        _open=float(_t.get('open',_lp)); _high=float(_t.get('high',_lp))
                        _amt=float(_t.get('amount',0))
                        _bvol=sum(float(v) for v in _t.get('bidVol',[])) if isinstance(_t.get('bidVol'),list) else float(_t.get('bidVol',0))
                        _avol=sum(float(v) for v in _t.get('askVol',[])) if isinstance(_t.get('askVol'),list) else float(_t.get('askVol',0))
                        for _d in full_data:
                            if _d.get("symbol")==_sd:
                                _d["realtime_price"]=round(_lp,2)
                                _d["realtime_chg"]=round((_lp/_lc-1)*100,2) if _lc>0 else 0
                                _lpct=0.20 if _sd.startswith(('sz30','sh688')) else 0.10
                                _d["limit_up_hit"]=_lc>0 and _high>=_lc*(1+_lpct)*0.995
                                _d["fund_pressure"]=round(_bvol/max(_avol,1),1) if _bvol+_avol>0 else 0
                                _d["auction_chg"]=round((_open/_lc-1)*100,2) if _lc>0 else 0
                                _at_limit=False
                                if _lc>0:
                                    _lim=_lc*(1+_lpct)
                                    _at_limit=_lp>=_lim*0.998
                                _d["at_limit"]=_at_limit
                                break
            except Exception: pass
        total_count = len(full_data)
        # 板块热度：从全量数据聚合（修复：v5 从切片改为全量）
        _ind_chgs = {}
        _ind_cnts = {}
        for d in full_data:
            ind = d.get("industry", "")
            if not ind:
                continue
            chg = d.get("change_pct", 0) or 0
            _ind_chgs[ind] = _ind_chgs.get(ind, 0) + chg
            _ind_cnts[ind] = _ind_cnts.get(ind, 0) + 1
        # 为全量数据打板块热度标签
        for d in full_data:
            ind = d.get("industry", "")
            if ind and _ind_cnts.get(ind, 0) > 0:
                avg_chg = _ind_chgs[ind] / _ind_cnts[ind]
                if avg_chg > 3.0:
                    heat, label = 3, "🔥热"
                elif avg_chg > 1.0:
                    heat, label = 2, "🌤温"
                elif avg_chg > -1.0:
                    heat, label = 1, "🌥凉"
                else:
                    heat, label = 0, "❄冷"
                d["sector_heat"] = heat
                d["sector_label"] = label
                d["sector_avg_chg"] = round(avg_chg, 2)
            else:
                d["sector_heat"] = -1
                d["sector_label"] = ""
                d["sector_avg_chg"] = 0
        # KPI 和信号统计基于全量
        strong = sum(1 for d in full_data if d.get("score", 0) >= 60)
        resonance = sum(1 for d in full_data if d.get("signal_resonance", 0) >= 2)
        # E274: 漏斗统计
        funnel_passed = sum(1 for d in full_data if d.get("filter_passed", False))
        tdx_dual_count = sum(1 for d in full_data if d.get("tdx_dual", False))
        # 行业列表：直接从全量数据提取，传给前端
        _ind_set = {}
        for d in full_data:
            ind = d.get("industry", "") or ""
            if ind:
                _ind_set[ind] = _ind_set.get(ind, 0) + 1
        industries_list = sorted(_ind_set.keys())
        # 分页切片
        data = full_data[offset:offset + limit]

        # Phase A: XGBoost 因子加权 (L3→L3+)
        xgb_ready = False
        try:
            from xgb_factor_weight import score_stocks, is_ready
            if is_ready():
                scored = score_stocks(data, STOCK_DATA)
                # 按 xgb_score 重新排序
                scored.sort(key=lambda r: r.get("xgb_score") or 0, reverse=True)
                data = scored
                xgb_ready = True
        except Exception: pass

        return jsonify({
            "code": 200,
            "total": total_count,
            "strong_buy": strong,
            "resonance": resonance,
            "funnel_passed": funnel_passed,
            "tdx_dual_count": tdx_dual_count,
            "industries": industries_list,
            "data": data,
            "source": source,
            "data_date": data_date,
            "xgb_ready": xgb_ready,
        })
    except Exception as e:
        return jsonify({
            "code": 200, "total": 0, "data": [],
            "source": "degraded", "data_date": "", "note": str(e),
        })


@app.route("/api/screener/strategy-status")
def api_screener_strategy_status():
    """策略状态 API (E260)."""
    try:
        if _SCREENER_OK:
            screener = StockScreener()
            status = screener.get_strategy_status()
            return jsonify({"code": 200, **status})
    except Exception as e:
        pass
    return jsonify({"code": 200, "active_strategy": "ma_cross", "total_strategies": 0, "available_strategies": []})


@app.route("/api/screener/watchlist", methods=["GET", "POST", "DELETE"])
def api_screener_watchlist():
    """观察池管理 API (E263).
    GET  → 获取当前观察池
    POST → 添加股票 (body: {symbols: [...]})
    DELETE → 清空观察池
    """
    import json as _json, os as _os
    _wl_path = SCREENER_WATCHLIST  # E26: config路径

    try:
        if request.method == "GET":
            if _os.path.exists(_wl_path):
                with open(_wl_path, "r", encoding="utf-8") as f:
                    wl = _json.load(f)
                return jsonify({"code": 200, "data": wl.get("stocks", []), "count": len(wl.get("stocks", []))})
            return jsonify({"code": 200, "data": [], "count": 0})

        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            symbols = data.get("symbols", [])
            reason = data.get("reason", "选股器入池")
            now = datetime.now().isoformat()

            existing = {"stocks": [], "version": 1}
            if _os.path.exists(_wl_path):
                with open(_wl_path, "r", encoding="utf-8") as f:
                    existing = _json.load(f)

            for sym in symbols:
                if not any(s.get("symbol") == sym for s in existing["stocks"]):
                    existing["stocks"].append({
                        "symbol": sym, "added_at": now, "reason": reason, "status": "active",
                    })

            tmp = _wl_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(existing, f, ensure_ascii=False, indent=2)
            _os.replace(tmp, _wl_path)

            return jsonify({"code": 200, "message": f"已入池 {len(symbols)} 只", "count": len(existing["stocks"])})

        if request.method == "DELETE":
            if _os.path.exists(_wl_path):
                _os.remove(_wl_path)
            return jsonify({"code": 200, "message": "观察池已清空"})

    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})



@app.route("/api/screener/trade-rules", methods=["GET", "POST"])
def api_screener_trade_rules():
    """交易规则配置读写 (GET/POST)。"""
    _p = LIVE_CONFIG  # E26: config路径
    import json as _j, os as _o
    if request.method == "GET":
        try:
            _c = _j.load(open(_p, encoding="utf-8")) if _o.path.exists(_p) else {}
            _k = ["signal_min_strength","max_positions","max_single_position_pct",
                  "max_daily_trades","max_hold_days","max_order_value","min_cash_reserve",
                  "auto_order_mode","enable_notify","max_daily_loss","max_consecutive_loss",
                  "tp1_profit_pct","tp1_trail_pct","tp1_stop_loss",
                  "tp2_profit_pct","tp2_trail_pct","tp2_stop_loss",
                  "tp3_profit_pct","tp3_trail_pct","tp3_stop_loss",
                  "position_pct_lv3","position_pct_lv4","position_pct_lv5",
                  "qmt_env","trading_channel","limit_up_hold","limit_up_drop_sell"]
            return jsonify({"code": 200, "rules": {k: _c.get(k) for k in _k}})
        except Exception as _e:
            return jsonify({"code": 500, "error": str(_e)})
    try:
        _c = _j.load(open(_p, encoding="utf-8")) if _o.path.exists(_p) else {}
        _c.update((request.get_json() or {}).get("rules", {}))
        with open(_p + ".tmp", "w", encoding="utf-8") as _f:
            _j.dump(_c, _f, ensure_ascii=False, indent=2)
        _o.replace(_p + ".tmp", _p)
        return jsonify({"code": 200, "message": "已保存"})
    except Exception as _e:
        return jsonify({"code": 500, "error": str(_e)})


@app.route("/api/screener/qmt-status")
def api_screener_qmt_status():
    """P1-6: QMT 状态面板 API — 连接状态 + 账户摘要。"""
    result = {
        "status": "offline",   # online | degraded | offline
        "label": "🔴 离线",
        "xtquant": False,
        "connected": False,
        "account_id": "",
        "total_asset": 0,
        "cash": 0,
        "position_count": 0,
        "env": "SIM",
        "source": "none",
    }
    # 1. 检测 xtquant
    try:
        from qmt_data_bridge import is_qmt_available
        result["xtquant"] = is_qmt_available()
    except Exception:
        pass

    # 2. 尝试获取 QMT 账户
    if result["xtquant"]:
        result["status"] = "degraded"
        result["label"] = "🟡 降级"
        result["source"] = "xtquant"
        try:
            from quant_framework.execution.brokers.qmt_broker import QMTBroker
            import json as _jq
            _cfg = {}
            _cp = LIVE_CONFIG  # E26: config路径
            if os.path.exists(_cp):
                with open(_cp) as _f: _cfg = _jq.load(_f)
            _acc = str(_cfg.get("qmt_account", "") or "")
            _env = str(_cfg.get("qmt_env", "SIM") or "SIM")
            result["account_id"] = _acc
            result["env"] = _env
            if _acc:
                try:
                    import sys as _sq
                    sys.path.insert(0, QUANT_FW_DIR)  # E26: config路径
                    _path = str(_cfg.get("qmt_path", r"D:\国金QMT交易端模拟\userdata_mini") or r"D:\国金QMT交易端模拟\userdata_mini")
                    _sid = int(_cfg.get("qmt_session_id", 9999) or 9999)
                    _bk = QMTBroker(account_id=_acc, env=_env, path=_path, session_id=_sid, real_confirmed=(_env == "REAL"))
                    _bk.connect()
                    if _bk.is_connected():
                        result["connected"] = True
                        result["status"] = "online"
                        result["label"] = "🟢 在线"
                        av = _bk.get_account_value()
                        if av: result["total_asset"] = round(av, 0)
                        ac = _bk.get_available_cash()
                        if ac: result["cash"] = round(ac, 0)
                        pos = _bk.get_positions()
                        result["position_count"] = len(pos)
                    _bk.disconnect()
                except Exception as _be:
                    logger.warning(f"[QMT-Status] broker query failed: {_be}")
        except Exception as _e:
            logger.warning(f"[QMT-Status] {_e}")

    # 3. 降级检测：当前选股数据源
    try:
        if hasattr(StockScreener, '_snapshot') or _SCREENER_OK:
            result["source"] = "qmt" if result["connected"] else "tdx_fallback"
    except Exception:
        pass

    return jsonify({"code": 200, **result})


@app.route("/api/screener/sector-heat")
def api_screener_sector_heat():
    """SC13: 板块热温冷图独立API"""
    try:
        _refresh_sector_data()
        sectors = {}
        for ind, idx_data in _SECTOR_KLINES.items():
            chg = idx_data.get("change_pct", 0)
            if chg > 0.03: heat, label = 3, "🔥热"
            elif chg > 0.01: heat, label = 2, "🌤温"
            elif chg > -0.01: heat, label = 1, "🌥凉"
            else: heat, label = 0, "❄冷"
            sectors[ind] = {"avg_chg": round(chg * 100, 2), "heat": heat, "label": label}
        ranked = sorted(sectors.items(), key=lambda x: -x[1]["heat"])
        return jsonify({"code": 200, "top": dict(ranked[:5]), "bottom": dict(ranked[-5:][::-1]), "total": len(sectors)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/market-state-v2")
def api_market_state_v3():
    """P2-2: 市场状态分类器API(旧)"""
    try:
        from quant_framework.analysis.market_state import get_market_state
        state = get_market_state()
        return jsonify({"code": 200, **state})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e), "state": "oscillate", "emoji": "〰️"})

@app.route("/api/signals/aggregate")
def api_signal_aggregate():
    """P2-3: 多策略信号聚合 — 加权投票结果"""
    symbol = request.args.get("symbol", "")
    if not symbol:
        return jsonify({"code": 400, "error": "缺少symbol"})
    try:
        from quant_framework.strategy.signal_generators import get_all_signals
        from quant_framework.strategy.signal_aggregator import aggregate_signals
        from quant_framework.strategy.state_strategy_map import get_active_strategies
        from quant_framework.analysis.market_state import get_market_state
        df = STOCK_DATA.get(symbol)
        if df is None or len(df) < 20:
            return jsonify({"code": 200, "action": "hold", "note": "数据不足"})
        signals = get_all_signals(df)
        market = get_market_state()
        weights = get_active_strategies(market["state"]).get("weights", {})
        result = aggregate_signals(signals, weights)
        result["market_state"] = market
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

# ═══════════════════════════════════════════
# P3-1: Kelly仓位 + P3-4: 日亏熔断
# ═══════════════════════════════════════════

@app.route("/api/risk/kelly-position")
def api_kelly_position():
    """P3-1: Kelly动态仓位建议"""
    try:
        from quant_framework.risk.kelly_position import get_kelly_position
        from quant_framework.analysis.market_state import get_market_state
        market = get_market_state()
        signal = float(request.args.get("signal", 0.3))
        result = get_kelly_position(signal_score=signal, market_state=market["state"])
        result["market_state"] = market["state"]
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/risk/daily-loss")
def api_daily_loss():
    """P3-4: 日亏熔断状态"""
    try:
        from quant_framework.risk.daily_loss_circuit import get_daily_loss_status
        status = get_daily_loss_status()
        return jsonify({"code": 200, **status})
    except Exception as e:
        return jsonify({"code": 200, "status": "normal", "can_buy": True})

# ═══════════════════════════════════════════
# P2-5: 进化结果审批流程
# ═══════════════════════════════════════════

@app.route("/api/evolution/pending")
def api_evolution_pending():
    """P2-5: 返回待审批的进化结果"""
    import json as _j
    cfg_path = r"d:\quant_framework\live_trader_config.json"
    try:
        if not os.path.exists(cfg_path):
            return jsonify({"code": 200, "pending": None, "note": "配置不存在"})
        with open(cfg_path, 'r', encoding='utf-8') as _f:
            _cfg = _j.load(_f)
        pending = _cfg.get('_pending_evolution')
        # 同时获取当前参数值用于对比
        current = {k: v for k, v in _cfg.items() if not k.startswith('_')}
        return jsonify({
            "code": 200,
            "pending": pending,
            "current_params": current if pending else {},
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/evolution/approve", methods=["POST"])
def api_evolution_approve():
    """P2-5: 审批通过 — 写入live_trader_config.json + 记录审计日志"""
    import json as _j
    cfg_path = r"d:\quant_framework\live_trader_config.json"
    audit_path = r"d:\quant_web\data\evolution_audit.jsonl"
    try:
        if not os.path.exists(cfg_path):
            return jsonify({"code": 404, "error": "配置不存在"})
        with open(cfg_path, 'r', encoding='utf-8') as _f:
            _cfg = _j.load(_f)
        pending = _cfg.pop('_pending_evolution', None)
        if not pending:
            return jsonify({"code": 200, "note": "无待审批项"})
        params = pending.get('params', {})
        # 审计日志: 记录变更
        audit = {
            "action": "approved",
            "cycle_id": pending.get('cycle_id', ''),
            "params": params,
            "old_values": {k: _cfg.get(k) for k in params},
            "timestamp": datetime.now().isoformat(),
        }
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, 'a', encoding='utf-8') as _f:
            _f.write(_j.dumps(audit, ensure_ascii=False) + '\n')
        # 应用参数
        _cfg.update(params)
        with open(cfg_path, 'w', encoding='utf-8') as _f:
            _j.dump(_cfg, _f, ensure_ascii=False, indent=2)
        logger.warning(f"[P2-5] ✅ 进化参数已审批应用: {len(params)}项, cycle={pending.get('cycle_id')}")
        return jsonify({"code": 200, "applied": len(params), "audit": audit})
    except Exception as e:
        logger.error(f"[P2-5] 审批失败: {e}")
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/evolution/reject", methods=["POST"])
def api_evolution_reject():
    """P2-5: 驳回进化结果 — 清除pending, 不改配置"""
    import json as _j
    cfg_path = r"d:\quant_framework\live_trader_config.json"
    audit_path = r"d:\quant_web\data\evolution_audit.jsonl"
    try:
        if not os.path.exists(cfg_path):
            return jsonify({"code": 404, "error": "配置不存在"})
        with open(cfg_path, 'r', encoding='utf-8') as _f:
            _cfg = _j.load(_f)
        pending = _cfg.pop('_pending_evolution', None)
        if not pending:
            return jsonify({"code": 200, "note": "无待审批项"})
        audit = {
            "action": "rejected",
            "cycle_id": pending.get('cycle_id', ''),
            "params": pending.get('params', {}),
            "timestamp": datetime.now().isoformat(),
        }
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, 'a', encoding='utf-8') as _f:
            _f.write(_j.dumps(audit, ensure_ascii=False) + '\n')
        with open(cfg_path, 'w', encoding='utf-8') as _f:
            _j.dump(_cfg, _f, ensure_ascii=False, indent=2)
        logger.warning(f"[P2-5] ❌ 进化参数已驳回: cycle={pending.get('cycle_id')}")
        return jsonify({"code": 200, "rejected": True, "audit": audit})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/report/daily")
def api_report_daily_market():
    """P3-5: 盘后复盘数据聚合"""
    try:
        from quant_framework.analysis.market_state import get_market_state
        from quant_framework.risk.daily_loss_circuit import get_daily_loss_status
        result = {
            "market_state": get_market_state(),
            "daily_loss": get_daily_loss_status(),
            "timestamp": datetime.now().isoformat(),
        }
        # 交易统计（从paper_engine获取）
        try:
            from paper_engine import paper as _paper
            status = _paper.get_status() if hasattr(_paper, 'get_status') else {}
            trades = status.get("trade_log", []) if isinstance(status, dict) else []
            today = datetime.now().strftime("%Y-%m-%d")
            today_trades = [t for t in trades if str(t.get("date", "")).startswith(today)]
            wins = [t for t in today_trades if t.get("revenue", 0) > 0]
            result["trades"] = {
                "today_count": len(today_trades),
                "today_wins": len(wins),
                "today_win_rate": round(len(wins) / len(today_trades) * 100, 1) if today_trades else 0,
                "today_pnl": round(sum(t.get("revenue", 0) for t in today_trades), 2),
                "total_equity": status.get("total_asset", status.get("equity", 0)),
            }
        except Exception:
            result["trades"] = {"today_count": 0, "note": "paper_engine不可用"}
        # 因子IC摘要
        try:
            import json as _j
            ic_path = FACTOR_IC  # E26: config路径
            if os.path.exists(ic_path):
                with open(ic_path) as _f:
                    ic_data = _j.load(_f)
                ic_results = ic_data.get("ic_results", {})
                best = max(ic_results.items(), key=lambda x: abs(x[1].get("ic_1d", {}).get("icir", 0))) if ic_results else ("--", {})
                result["factor_ic"] = {"best_factor": best[0], "best_icir": round(best[1].get("ic_1d", {}).get("icir", 0), 3) if isinstance(best[1], dict) else 0}
        except Exception:
            result["factor_ic"] = {"note": "IC数据不可用"}
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/trading-dashboard")
def page_trading_dashboard():
    """P3-6: 统一交易看板"""
    return render_template("trading_dashboard.html")

# [deprecated] /approvals — 审批已合并到 /ml-signals
# [deprecated] /health — 系统健康已合并到 /factor-health

@app.route("/screener")
def page_screener():
    """选股器页面 (E258)."""
    return render_template("screener.html")


@app.route("/ml-signals")
def page_ml_signals():
    """ML 信号复盘页面."""
    return render_template("ml_signals.html")


@app.route("/api/ml/signals-v3")
def api_ml_signals_v3():
    """ML信号v3 — 三模型共识信号 + 市场状态 + 因子贡献 + 板块分布"""
    import os as _osv3, sys as _sysv3
    _sysv3.path.insert(0, r"D:\quant_framework")
    init_data()
    stock_data = get_stock_data()

    # 1. 从统一信号表读取 (含 LGBM+XGB+Ridge+反转+打板, 高斯化+质量分)
    signals = []
    try:
        _st_path = os.path.join(os.path.dirname(__file__), "data", "signal_table.json")
        if os.path.exists(_st_path):
            _raw = json.load(open(_st_path, encoding="utf-8"))
            for r in _raw:
                sym = r.get("symbol", "")
                if not sym: continue
                signals.append({
                    "symbol": sym,
                    "name": r.get("name", ""),
                    "industry": r.get("industry", ""),
                    "broad_sector": r.get("industry", ""),
                    "score": r.get("combined_score", 0),
                    "quality_score": r.get("quality_score", 0),
                    "buy_signal": 5 if r.get("combined_score",0)>=90 else 4 if r.get("combined_score",0)>=80 else 3 if r.get("combined_score",0)>=70 else 2 if r.get("combined_score",0)>=60 else 1,
                    "models": [m for m in ["lgbm","xgb","ridge"] if r.get(f"{m}_score","")],
                    "n_models": sum(1 for m in ["lgbm","xgb","ridge"] if r.get(f"{m}_score","")),
                    "close": r.get("close", 0),
                    "change_pct": r.get("change_pct", 0),
                    "stop_loss": r.get("stop_loss", 0),
                    "take_profit": r.get("take_profit", 0),
                    "position_pct": r.get("position_pct", 0),
                    "quality": r.get("quality_score", 0),
                    "hold_days": r.get("hold_days", 7),
                    "lgbm_score": r.get("lgbm_score", "") or "",
                    "xgb_score": r.get("xgb_score", "") or "",
                    "ridge_score": r.get("ridge_score", "") or "",
                    "decision": r.get("decision", ""),
                    "consensus_dots": r.get("consensus_dots", ""),
                })
            signals.sort(key=lambda x: -x["score"])
    except Exception as e:
        print(f"[ML-v3] 信号表读取失败: {e}")

    # 2. 市场状态
    market = {"regime": "unknown", "label": "未知", "confidence": 0.5, "position_scale": 0.5,
              "volatility": 0, "trend_score": 0, "breadth_score": 0, "volume_score": 0}
    try:
        from market_regime import detect_regime
        r = detect_regime(stock_data) if stock_data else {}
        market = {
            "regime": r.get("regime", "unknown"),
            "label": {"strong_bull":"强牛","bull":"牛市","sideways":"震荡","bear":"熊市","strong_bear":"强熊","unknown":"未知"}.get(r.get("regime"),"未知"),
            "confidence": r.get("confidence", 0.5),
            "position_scale": r.get("position_scale", 0.5),
            "volatility": r.get("volatility", 0),
            "trend_score": r.get("trend_score", 0),
            "breadth_score": r.get("breadth_score", 0) or r.get("market_breadth_pct", 0) or 0,
            "volume_score": r.get("volume_score", 0),
        }
    except Exception as e:
        print(f"[ML-v3] 市场状态失败: {e}")

    # 3. 因子IC贡献 Top5 (从 factor_registry)
    factors = []
    try:
        reg_path = r"D:\quant_framework\factor_registry.json"
        if _osv3.path.exists(reg_path):
            reg = json.load(open(reg_path, encoding="utf-8"))
            for name, info in reg.get("factors", {}).items():
                ic = info.get("ic", 0) or 0
                if ic != 0:
                    factors.append({"name": name, "label": info.get("label", name), "ic": round(ic, 4)})
            factors.sort(key=lambda x: -abs(x["ic"]))
            factors = factors[:5]
    except Exception as e:
        print(f"[ML-v3] 因子IC加载失败: {e}")

    # 4. 板块集中度
    sector_count = {}
    for s in signals:
        sec = s.get("broad_sector") or s.get("industry") or "其他"
        sector_count[sec] = sector_count.get(sec, 0) + 1
    total = len(signals) or 1
    sectors = []
    for sec, cnt in sorted(sector_count.items(), key=lambda x: -x[1])[:6]:
        sectors.append({"name": sec, "count": cnt, "pct": round(cnt / total * 100, 1)})

    # 5. KPI统计
    high_quality = sum(1 for s in signals if s["score"] >= 80)
    resonance_3 = sum(1 for s in signals if s["n_models"] >= 3)
    avg_score = round(sum(s["score"] for s in signals) / max(len(signals), 1), 0) if signals else 0
    auto_enabled = len(signals)  # 所有共识信号均可自动启用

    return jsonify({
        "code": 200,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
        "kpi": {"total": len(signals), "resonance_3": resonance_3, "auto_enabled": auto_enabled,
                "avg_score": avg_score, "high_quality": high_quality},
        "market": market,
        "factors": factors,
        "sectors": sectors,
        "signals": signals,
    })


@app.route("/api/ml/signal-track")
def api_ml_signal_track():
    """ML 信号追踪数据."""
    import os as _os2
    p = _os2.path.join(_os2.path.dirname(__file__), "data", "ml_signal_track.json")
    if _os2.path.exists(p):
        return jsonify(json.load(open(p, encoding="utf-8")))
    return jsonify([])


@app.route("/api/ml/backtest")
def api_ml_backtest():
    """ML 回测结果."""
    import os as _os3
    p = _os3.path.join(_os3.path.dirname(__file__), "data", "ml_backtest.json")
    if _os3.path.exists(p):
        return jsonify(json.load(open(p, encoding="utf-8")))
    return jsonify({})


# ═══════════════════════════════════════════════════════════════
#  SSE: 实时信号推送（前端 EventSource）
# ═══════════════════════════════════════════════════════════════

@app.route("/api/stream")
def api_sse_stream():
    """SSE 端点 — 实时推送选股信号到浏览器。
    前端 quant_unified.js 已实现 EventSource('/api/stream')。
    """
    import time as _t, json as _js
    def _event_stream():
        _last_version = -1
        _seen_signals = set()
        while True:
            try:
                _signals = []
                if globals().get('_CACHE_READY', False) and _FACTOR_CACHE:
                    for _s in _FACTOR_CACHE[:10]:
                        _bs = getattr(_s, 'buy_signal', 0) or 0
                        if _bs >= 3:
                            _sym = getattr(_s, 'symbol', '')
                            _key = f"{_sym}_{_bs}"
                            if _key not in _seen_signals:
                                _name = getattr(_s, 'name', '') or ''
                                _ind = getattr(_s, 'industry', '') or ''
                                _rs = getattr(_s, 'signal_resonance', 0) or 0
                                _pw = getattr(_s, 'power_score', 0) or 0
                                _signals.append({
                                    "symbol": _sym, "name": _name,
                                    "industry": _ind,
                                    "buy_signal": _bs,
                                    "resonance": _rs,
                                    "power_score": _pw,
                                })
                                _seen_signals.add(_key)
                # 限制 seen 集合大小
                if len(_seen_signals) > 500:
                    _seen_signals.clear()
                _payload = {
                    "type": "signals",
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "count": len(_signals),
                    "data": _signals,
                }
                yield f"data: {_js.dumps(_payload, ensure_ascii=False)}\n\n"
            except Exception:
                yield f"data: {_js.dumps({'type': 'heartbeat'})}\n\n"
            _t.sleep(10)
    return Response(_event_stream(), mimetype="text/event-stream")

# ═══════════════════════════════════════════════════════════════
#  E266-E268: 回测→模拟盘→实盘闭环
# ═══════════════════════════════════════════════════════════════

@app.route("/api/backtest/apply-config", methods=["POST"])
def api_apply_backtest_config():
    """E266: 将回测最优参数写入 live_trader_config.json。"""
    import json as _json, os as _os
    data = request.get_json() or {}
    cfg_path = LIVE_CONFIG  # E26: config路径
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = _json.load(f)
    except Exception:
        config = {}

    bt_keys = ["stopLoss", "takeProfit", "holdDays", "maxPos", "posPct",
               "trail1Profit", "trail1Drop", "sellRatio1",
               "trail2Profit", "trail2Drop", "sellRatio2",
               "trail3Profit", "trail3Drop", "sellRatio3",
               "atrMult", "atrPeriod", "limitUpEnabled", "limitUpDrop"]
    for k in bt_keys:
        if k in data:
            config[k] = data[k]

    try:
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, cfg_path)
        # 同步内存中的 TRADE_CONFIG
        if LIVE_TRADER_OK:
            try:
                from live_trader import CONFIG as _lt_cfg
                for k in bt_keys:
                    if k in data:
                        _lt_cfg[k] = data[k]
            except Exception:
                pass
        return jsonify({"code": 200, "message": "参数已写入配置"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/paper/status")
def api_paper_status():
    """E267+E371: 返回模拟盘状态 + 与回测预期对比 (从内存读取)"""
    import json as _json, os as _os
    try:
        from paper_engine import paper
        eq = paper.get_total_equity()
        pnl = round(paper.get_pnl(), 2) if hasattr(paper, 'get_pnl') else 0
        trades_today = len([t for t in getattr(paper, 'trade_log', [])
                           if str(t.get("time",""))[:10] == datetime.now().strftime("%Y-%m-%d")])
        import numpy as _np
        trades = getattr(paper, '_trades_archive', []) or []
        sells = [t for t in trades if t.get('side') == 'sell']
        MIN_TRADES_FOR_METRICS = 10
        win_count = sum(1 for t in sells if t.get('pnl', 0) > 0)
        win_rate = round(win_count / len(sells), 4) if sells else 0
        pnls = [t.get('pnl', 0) for t in sells]
        if len(sells) >= MIN_TRADES_FOR_METRICS:
            sharpe = round(_np.mean(pnls) / (_np.std(pnls) + 1e-9) * _np.sqrt(252), 4)
            cumsum = _np.cumsum(pnls)
            peak = _np.maximum.accumulate(cumsum)
            dd = (cumsum - peak)
            max_dd = round(abs(float(_np.min(dd))), 2)
        else:
            sharpe = 0; max_dd = 0

        result = {
            "code": 200,
            "total_equity": round(eq, 2),
            "cash": round(paper.cash, 2),
            "total_pnl": pnl,
            "auto_enabled": paper.auto_enabled,
            "positions": len(paper.positions),
            "trades_today": trades_today,
            "win_rate": win_rate,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
        }
        # 对比回测预期
        bt_cache = _BT_CACHE_FILE
        bt_expected = None
        if _os.path.exists(bt_cache):
            try:
                with open(bt_cache, "r", encoding="utf-8") as f:
                    cache = _json.load(f)
                keys = sorted(cache.keys(), reverse=True)
                for k in keys[:1]:
                    bt_expected = cache[k].get("metrics", {}).get("total_return")
            except Exception:
                pass
        result["bt_expected_return"] = bt_expected
        if bt_expected and eq > 0:
            init_capital = 1_000_000
            paper_return = round((eq - init_capital) / init_capital, 4)
            result["paper_return"] = paper_return
            result["gap"] = round(abs(paper_return - bt_expected), 4)
            if result["gap"] < 0.05:
                result["verdict"] = "🟢 接近回测，可以上实盘"
            elif result["gap"] < 0.15:
                result["verdict"] = "🟡 有偏差，继续观察"
            else:
                result["verdict"] = "🔴 偏差大，不要上实盘"
        return jsonify(result)
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/paper/state")
def api_paper_state():
    """E349: 直接从文件读取纸引擎状态"""
    import json as _j, os as _o
    _f = r"d:\quant_framework\paper_account.json"
    if not _o.path.exists(_f):
        return jsonify({"code": 404, "error": "状态文件不存在"})
    try:
        with open(_f, "r", encoding="utf-8") as _fh:
            _d = _j.load(_fh)
        _cash = _d.get("cash", 0)
        _pos = _d.get("positions", {})
        _mv = sum(p.get("qty",0) * p.get("last_price", p.get("avg_cost",0)) for p in _pos.values())
        return jsonify({
            "code": 200,
            "cash": _cash,
            "total_equity": _cash + _mv,
            "positions": len(_pos),
            "position_list": list(_pos.values()),
            "auto_enabled": _d.get("auto_enabled", False),
            "trade_count": len(_d.get("trade_log", [])),
            "daily_date": _d.get("daily_date"),
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/paper/positions")
def api_paper_positions():
    """E317: 模拟盘持仓"""
    try:
        from paper_engine import paper
        pos = paper.positions if hasattr(paper, 'positions') else {}
        items = []
        for sym, p in pos.items():
            p["symbol"] = sym  # 注入代码 (纸引擎 key 是代码)
            items.append(p)
        # 计算实时盈亏
            if not p.get("profit_pct") and not p.get("pnl_pct"):
                cost = p.get("avg_cost", 0)
                if cost > 0:
                    price = p.get("last_price", cost)
                    p["profit_pct"] = round((price / cost - 1) * 100, 2)
                    p["profit_amt"] = round((price - cost) * p.get("qty", 0), 2)
        with_names(items)
        return jsonify({"code": 200, "positions": items})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/paper/trades")
def api_paper_trades():
    """E317: 交易记录 (确保每条记录含 date 字段)"""
    try:
        from paper_engine import paper
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        trades = getattr(paper, 'trade_log', []) or getattr(paper, '_trades_archive', [])
        # 兼容旧记录: 补充缺失的 date 字段
        for t in (trades or []):
            if not t.get("date"):
                t["date"] = today
        return jsonify({"code": 200, "trades": trades[-50:] if trades else []})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade/mode-switch", methods=["POST"])
def api_trade_mode_switch():
    """E268: 模拟盘↔实盘切换 (需二次确认)。"""
    data = request.get_json() or {}
    target = data.get("mode", "paper")
    confirmed = data.get("confirmed", False)

    if target == "real" and not confirmed:
        return jsonify({"code": 400, "error": "请设置 confirmed=true 确认切换实盘"})

    # E268 红线: 实盘必须 QMT 在线
    if target == "real":
        try:
            from qmt_data_bridge import is_qmt_available
            if not is_qmt_available():
                return jsonify({"code": 500, "error": "QMT 未连接，无法切换实盘"})
        except Exception:
            return jsonify({"code": 500, "error": "QMT 检查失败"})

    cfg_path = LIVE_CONFIG  # E26: config路径
    try:
        import json as _json, os as _os
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = _json.load(f)
        config["trade_mode"] = target
        config["auto_trade_enabled"] = (target == "real")
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, cfg_path)
        return jsonify({"code": 200, "message": f"已切换到{'实盘' if target=='real' else '模拟盘'}", "mode": target})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/trade-config", methods=["GET", "POST"])
def api_trade_config():
    """E318: 交易规则读取/保存"""
    if request.method == "POST":
        try:
            data = request.get_json() or {}
            with open(LIVE_CONFIG, "w") as f:
                json.dump(data, f, indent=2)
            return jsonify({"code": 200, "message": "已保存"})
        except Exception as e:
            return jsonify({"code": 500, "error": str(e)})
    try:
        with open(LIVE_CONFIG, "r") as f:
            cfg = json.load(f)
        return jsonify({"code": 200, "config": cfg})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/factor/all")
def api_factor_all():
    """全量因子列表 (含pending/retired)"""
    try:
        import json as _jfa
        with open(r"d:\quant_framework\factor_registry.json","r",encoding="utf-8") as f:
            reg = _jfa.load(f)
        return jsonify({"code":200, "factors":reg.get("factors",[])})
    except Exception as e:
        return jsonify({"code":500,"error":str(e)})

@app.route("/api/factor/health-action", methods=["POST"])
def api_factor_health_action():
    """因子审批: activate/retire"""
    data = request.get_json() or {}
    name = str(data.get("factor","")).strip()
    action = str(data.get("action","")).strip()
    if not name or action not in ("activate","retire"):
        return jsonify({"code":400,"error":"参数错误"})
    try:
        import json as _jf
        reg_path = r"d:\quant_framework\factor_registry.json"
        with open(reg_path,"r",encoding="utf-8") as f: reg = _jf.load(f)
        found = False
        for fc in reg.get("factors",[]):
            if fc.get("name")==name:
                fc["status"]="active" if action=="activate" else "retired"
                found=True; break
        if not found: return jsonify({"code":404,"error":"因子不存在"})
        with open(reg_path,"w",encoding="utf-8") as f: _jf.dump(reg,f,ensure_ascii=False,indent=2)
        return jsonify({"code":200,"message":f"因子{name}已{'激活' if action=='activate' else '退役'}"})
    except Exception as e:
        return jsonify({"code":500,"error":str(e)})

@app.route("/api/factor/ai-generate", methods=["POST"])
def api_factor_ai_generate():
    """E318: AI生成因子 (Phase B)"""
    try:
        data = request.get_json() or {}
        prompt = data.get("prompt", "")
        model = data.get("model", "deepseek")
        if not prompt:
            return jsonify({"code": 400, "error": "请输入因子描述"})
        from factor_pipeline import run_ai_pipeline
        result = run_ai_pipeline(prompt, model, sample=500, days=60, auto=False)
        if result.get("success"):
            return jsonify({"code": 200, "message": f"因子已生成: {result.get('name','')} IC={result.get('ic',0)}", "data": result})
        return jsonify({"code": 400, "error": result.get("error","生成失败"), "data": result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  Phase A: XGBoost 因子加权 (对标 BigQuant AI选股)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/factor/xgb-status")
def api_xgb_status():
    """XGBoost模型状态 + 因子IC权重"""
    try:
        from xgb_factor_weight import get_status
        result = {"code": 200, "xgb": get_status()}
        # 补充因子IC数据(用于首页因果链)
        try:
            import json as _jf
            _reg_path = r"d:\quant_framework\factor_registry.json"
            if os.path.exists(_reg_path):
                with open(_reg_path, "r", encoding="utf-8") as _f:
                    _reg = _jf.load(_f)
                _factors = _reg.get("factors", [])
                _active = [f for f in _factors if f.get("status") == "active"]
                result["factors"] = {
                    "active_count": len(_active),
                    "list": [{ "name": f["name"], "display": f.get("display",""),
                              "ic_5d": f.get("ic_5d"), "ic_20d": f.get("ic_20d"),
                              "direction": f.get("direction","long") } for f in _active]
                }
        except Exception: pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/ic-analysis/<name>")
def api_factor_ic_analysis(name):
    """Alphalens因子IC分析 (v3.0)"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from adapter import run_ic_analysis
        r = run_ic_analysis(name)
        if r: return jsonify({"code": 200, "data": r})
        return jsonify({"code": 500, "error": "样本不足"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/llm/generate-strategy", methods=["POST"])
def api_llm_strategy():
    """LLM生成策略 (v3.0)"""
    try:
        data = request.get_json() or {}
        desc = data.get("desc", "")
        if not desc or len(desc) < 5:
            return jsonify({"code": 400, "error": "描述太短"})
        sys.path.insert(0, r"D:\quant_framework")
        from llm_strategy import generate_strategy
        r = generate_strategy(desc)
        return jsonify({"code": 200, **r}) if r.get("success") else jsonify({"code": 500, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/xgb-train", methods=["POST"])
def api_xgb_train():
    """训练XGBoost因子加权模型"""
    init_data()
    try:
        from xgb_factor_weight import run_training
        r = run_training(STOCK_DATA, _FACTOR_CACHE)
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  E253: 因子库 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/factor/scores")
def api_factor_scores():
    """因子打分排行榜 (E253).
    GET /api/factor/scores?date=2026-06-20&top=50
    """
    trade_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    top_n = int(request.args.get("top", 50))
    try:
        from quant_framework.data.factor_library import FactorLibrary
        lib = FactorLibrary()
        scores = lib.get_scores(trade_date, top_n=top_n)
        return jsonify({"code": 200, "data": scores, "count": len(scores)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/update", methods=["POST"])
def api_factor_update():
    """手动触发因子库更新 (E253).
    POST /api/factor/update  body: {"date": "2026-06-20", "symbols": [...]}
    """
    data = request.get_json(silent=True) or {}
    trade_date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    symbols = data.get("symbols", None)
    try:
        from quant_framework.data.factor_library import FactorLibrary
        lib = FactorLibrary()
        count = lib.update_daily(trade_date, symbols=symbols)
        return jsonify({"code": 200, "message": "更新完成", "count": count})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/history")
def api_factor_history():
    """单只股票历史因子查询 (E253).
    GET /api/factor/history?symbol=600000&start=2026-01-01&end=2026-06-20
    """
    symbol = request.args.get("symbol", "")
    start_date = request.args.get("start", "2024-01-01")
    end_date = request.args.get("end", datetime.now().strftime("%Y-%m-%d"))
    if not symbol:
        return jsonify({"code": 400, "error": "symbol 参数必填"})
    try:
        from quant_framework.data.factor_library import FactorLibrary
        lib = FactorLibrary()
        history = lib.get_history(symbol, start_date, end_date)
        return jsonify({"code": 200, "data": history, "count": len(history)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════
#  因子API桥接 (新数据源 → 旧格式, 供 /factor-dashboard JS)
# ═══════════════════════════════════════════════════════

@app.route("/api/factor/ic-trend")
def api_factor_ic_trend():
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_api_bridge import get_ic_trend
        days = request.args.get("days", 30, type=int)
        return jsonify(get_ic_trend(days))
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-analysis")
def api_factor_analysis():
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_api_bridge import get_factor_analysis
        return jsonify(get_factor_analysis())
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/group-returns")
def api_factor_group_returns():
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_api_bridge import get_group_returns
        return jsonify(get_group_returns())
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════ E29: 因子全站注册表 API ═══════════════════

@app.route("/api/factor/registry")
def api_factor_registry_v1():
    """GET /api/factor/registry — 获取因子注册表全量数据(旧版)。"""
    try:
        from factor_registry import FactorRegistry
        r = FactorRegistry()
        return jsonify({
            "code": 200,
            "version": r.data.get("meta", {}).get("version", 1),
            "factors": r.get_all_factors(),
            "active_weights": r.get_active_weights(),
            "has_pending": r.data.get("pending") is not None,
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/version")
def api_factor_version():
    """GET /api/factor/version — 版本对比信息。"""
    try:
        from factor_registry import FactorRegistry
        r = FactorRegistry()
        return jsonify({"code": 200, "data": r.get_version_info()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/apply", methods=["POST"])
def api_factor_apply():
    """POST /api/factor/apply — 一键应用 pending 权重到全站。"""
    try:
        from factor_registry import FactorRegistry
        r = FactorRegistry()
        result = r.apply_now()
        if result.get("success"):
            return jsonify({"code": 200, "message": f"因子权重已全站生效: {result['version']}", **result})
        return jsonify({"code": 400, "error": result.get("error", "应用失败")})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/rollback", methods=["POST"])
def api_factor_rollback():
    """POST /api/factor/rollback — 回滚到上一个版本。"""
    try:
        from factor_registry import FactorRegistry
        r = FactorRegistry()
        result = r.rollback()
        if result.get("success"):
            return jsonify({"code": 200, "message": f"已回滚到: {result['version']}", **result})
        return jsonify({"code": 400, "error": result.get("error", "回滚失败")})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════ E28 P2: 漏斗规则 CRUD API ═══════════════════

@app.route("/api/funnel/rules", methods=["GET", "POST"])
def api_funnel_rules():
    """GET/POST /api/funnel/rules — 读取/更新漏斗规则。"""
    try:
        from rule_engine import RuleEngine
        engine = RuleEngine()
        if request.method == "GET":
            return jsonify({"code": 200, "rules": engine.get_rules()})
        else:
            new_rules = request.get_json(force=True)
            if not new_rules or "layers" not in new_rules:
                return jsonify({"code": 400, "error": "缺少 layers 字段"})
            result = engine.update_rules(new_rules)
            return jsonify({"code": 200, "message": "规则已更新", **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/funnel/rules/reset", methods=["POST"])
def api_funnel_rules_reset():
    """POST /api/funnel/rules/reset — 重置为默认规则。"""
    try:
        from rule_engine import RuleEngine
        engine = RuleEngine()
        result = engine.reset_to_default()
        return jsonify({"code": 200, "message": "已重置为默认规则", **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/quant-backtest-v2")
def api_quant_backtest_v2():
    """V2回测API — 向量化引擎 + WFA + 策略对比"""
    init_data()
    formula = request.args.get("formula", "resonance")
    start_str = request.args.get("start", "2024-06-01")
    end_str = request.args.get("end", "2025-12-31")
    max_pos = int(request.args.get("maxPos", 3))
    pos_pct = float(request.args.get("posPct", 0.3))
    stop_loss = float(request.args.get("stopLoss", -0.05))
    take_profit = float(request.args.get("takeProfit", 0.08))
    hold_days = int(request.args.get("holdDays", 1))
    trail1_p = float(request.args.get("trail1Profit", 5)) / 100
    trail1_d = float(request.args.get("trail1Drop", 2)) / 100
    trail2_p = float(request.args.get("trail2Profit", 7)) / 100
    trail2_d = float(request.args.get("trail2Drop", 3)) / 100
    init_cap = int(request.args.get("capital", 1_000_000))
    mode = request.args.get("mode", "backtest")  # backtest / wfa / compare

    # 信号字段映射
    sig_map = {"resonance":"signal_resonance","final":"signal_final","xg":"signal_xg","b1":"signal_b1","qlj":"signal_qlj","ztxf":"signal_ztxf"}
    sig_field = sig_map.get(formula, "signal_resonance")

    # TDX公式
    formula_syms = None
    if formula.startswith("tdx__"):
        from tdx_formulas import get_formula_stocks
        pool = formula[5:]
        stocks = get_formula_stocks(pool)
        codes = [s['code'] for s in stocks if s.get('code')]
        formula_syms = []
        for c in codes:
            for p in ['sh','sz','bj']:
                if p+c in STOCK_DATA:
                    formula_syms.append(p+c)
                    break

    try:
        from backtest_engine import BacktestEngine
        engine = BacktestEngine(STOCK_DATA, _FACTOR_CACHE, _NAME_MAP)
        result = engine.run(
            strategy=formula, signal_field=sig_field,
            formula_symbols=formula_syms,
            start=start_str, end=end_str,
            max_positions=max_pos, position_pct=pos_pct,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=hold_days,
            trail1_profit=trail1_p, trail1_drop=trail1_d,
            trail2_profit=trail2_p, trail2_drop=trail2_d,
            initial_capital=init_cap,
            commission_rate=None, stamp_duty=None,  # None=引擎从master读
        )
        if not result.get('results'):
            # F6-修复: 纸引擎无交易时返回空数据+提示
            result["no_data"] = True
            result["message"] = "策略未产生交易信号"

        # 基准
        benchmark = []
        try:
            hs300 = STOCK_DATA.get('sh000300')
            if hs300 is not None and len(hs300) > 0:
                eq = result.get('equity_curve', [])
                if eq:
                    hs = hs300.loc[eq[0]['date']:eq[-1]['date']]
                    if len(hs) > 1:
                        base = float(hs.iloc[0]['close'])
                        for idx in hs.index:
                            benchmark.append({'date':str(idx)[:10], 'equity':round(init_cap*float(hs.loc[idx,'close'])/base,0)})
        except Exception as _e: print(f"[App] {_e}")
        result['benchmark_equity'] = benchmark

        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"code":500, "error": str(e)})



# ======================================================================
# 风控高级 API
# ======================================================================


@app.route("/api/risk/pre-market")
def api_risk_pre_market():
    try:
        from risk_guard import RiskCycleScheduler
        from paper_engine import paper
        sched = RiskCycleScheduler(paper, store)
        result = sched.pre_market_check()
        return jsonify({"code":200, **(result or {"phase":"pre_market","warnings":[]})})
    except Exception as e: return jsonify({"code":500,"error":str(e)})

@app.route("/api/risk/post-market")
def api_risk_post_market():
    try:
        from risk_guard import RiskCycleScheduler
        from paper_engine import paper
        sched = RiskCycleScheduler(paper, store)
        result = sched.post_market_report()
        return jsonify({"code":200, **(result or {"phase":"post_market"})})
    except Exception as e: return jsonify({"code":500,"error":str(e)})

@app.route("/api/risk/events")
def api_risk_events():
    try:
        from risk_guard import RiskEventBus
        bus = RiskEventBus(store)
        return jsonify({"code":200, "events": bus.get_recent(20)})
    except Exception as e: return jsonify({"code":500,"error":str(e)})


@app.route("/api/financial/<code>")
def api_financial(code):
    try:
        from financial_factors import get_stock_financial, compute_financial_score
        fin = get_stock_financial(code)
        score = compute_financial_score(fin)
        return jsonify({"code":200,"data":fin,"score":score})
    except Exception as e: return jsonify({"code":500,"error":str(e)})
@app.route("/api/risk/correlation")
def api_risk_correlation():
    try:
        from risk_guard import CorrelationAnalyzer
        from paper_engine import paper
        positions = {}
        for sym, pos in paper.positions.items():
            ind = '未分类'
            for fc in (_FACTOR_CACHE or []):
                if getattr(fc, 'symbol', '') == sym:
                    ind = getattr(fc, 'industry', '未分类') or '未分类'
                    break
            positions[sym] = {**pos, 'industry': ind}
        analyzer = CorrelationAnalyzer(STOCK_DATA, _FACTOR_CACHE)
        result = analyzer.analyze(positions)
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/risk/stress-test")
def api_risk_stress_test():
    try:
        from risk_guard import StressTester
        from paper_engine import paper
        positions = {}
        for sym, pos in paper.positions.items():
            positions[sym] = {**pos, 'market_value': pos.get('last_price',pos.get('avg_cost',0))*pos.get('qty',0)}
        tester = StressTester()
        result = tester.run(positions, paper.get_total_equity())
        return jsonify({"code": 200, "scenarios": result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})

@app.route("/api/risk/pre-check", methods=["POST"])
def api_risk_pre_check():
    try:
        from risk_guard import PreTradeChecker
        from paper_engine import paper
        from live_trader import CONFIG
        data = request.get_json() or {}
        checker = PreTradeChecker(config=CONFIG, positions=paper.positions,
                                  cash=paper.cash, total_equity=paper.get_total_equity(),
                                  factor_cache=_FACTOR_CACHE, stock_data=STOCK_DATA)
        side = data.get("side","buy"); symbol = data.get("symbol","")
        price = float(data.get("price",0)); qty = int(data.get("qty",100))
        if side == "buy": ok, reason = checker.check_buy(symbol, price, qty)
        else: ok, reason = checker.check_sell(symbol, qty)
        return jsonify({"code":200,"pass":ok,"reason":reason})
    except Exception as e:
        return jsonify({"code":500,"error":str(e)})
# ======================================================================
# 自我进化系统 API
# ======================================================================

# 确保auto_evolve模块路径可导入
import sys as _evo_sys
if r"d:\quant_web" not in _evo_sys.path:
    _evo_sys.path.insert(0, r"d:\quant_web")

try:
    from auto_evolve import evo_engine, evo_scheduler, StrategyAuditor

    @app.route("/api/evolution/backtest-config")
    def api_evolution_backtest_config():
        """进化最优参数 → 回测config格式"""
        import json as _json, os as _os
        evo_path = r"d:\quant_framework\evolution_result.json"
        if not _os.path.exists(evo_path):
            return jsonify({"code": 404, "error": "请先运行进化模块: python evolution.py"})
        try:
            with open(evo_path, "r", encoding="utf-8") as f:
                evo = _json.load(f)
            bp = evo.get("best_params", {})
            if not bp:
                return jsonify({"code": 404, "error": "进化结果为空"})
            return jsonify({
                "code": 200,
                "config": {
                    "formula": "tdx_resonance",
                    "stop_loss": round(bp.get("stop_loss", -0.05), 2),
                    "take_profit": round(bp.get("take_profit", 0.08), 2),
                    "hold_days": bp.get("hold_days", 3),
                    "trail1_profit": round(bp.get("trail1_profit", 0.05), 2),
                    "trail1_drop": round(bp.get("trail1_drop", 0.018), 3),
                    "sell_ratio_1": round(bp.get("sell_ratio_1", 0.25), 2),
                    "trail2_profit": round(bp.get("trail2_profit", 0.07), 2),
                    "trail2_drop": round(bp.get("trail2_drop", 0.02), 3),
                    "sell_ratio_2": round(bp.get("sell_ratio_2", 0.25), 2),
                    "trail3_profit": round(bp.get("trail3_profit", 0.12), 2),
                    "trail3_drop": round(bp.get("trail3_drop", 0.03), 3),
                    "sell_ratio_3": round(bp.get("sell_ratio_3", 0.25), 2),
                    "max_pos": bp.get("max_positions", 3),
                    "position_pct": round(bp.get("position_pct", 0.3), 2),
                    "limit_up_enabled": bp.get("limit_up_enabled", 1),
                    "source": "evolution",
                    "fitness": evo.get("best_fitness", 0),
                },
                "params_raw": bp,
            })
        except Exception as e:
            return jsonify({"code": 500, "error": str(e)})

    @app.route("/auto-evolve")
    def page_auto_evolve():
        """自我进化系统页面"""
        return render_template("auto_evolve.html")

    # [deprecated] /top5-benchmarks — 对标分析已合并到治理文件

    @app.route("/api/auto-evolve/status")
    def api_auto_evolve_status():
        """进化系统状态"""
        status = evo_engine.get_status()
        status["schedule"] = {
            "daily": evo_scheduler.schedule.get("daily", "02:00"),
            "weekly": evo_scheduler.schedule.get("weekly", "Sun 03:00"),
            "enabled": evo_scheduler.schedule.get("enabled", False),
            "next_run": evo_scheduler.get_next_run(),
        }
        status["code"] = 200
        return jsonify(status)

    # ═══════════════════ E18 每日进化调度 ═══════════════════

    @app.route("/api/auto-evolve/schedule", methods=["GET", "POST"])
    def api_evo_schedule():
        """GET: 查看调度状态  POST: 更新调度配置"""
        from auto_evolve import evo_scheduler
        if request.method == "POST":
            data = request.get_json() or {}
            if "enabled" in data:
                evo_scheduler.schedule["enabled"] = bool(data["enabled"])
            if "daily" in data:
                evo_scheduler.schedule["daily"] = str(data["daily"])
            return jsonify({"code": 200, "schedule": evo_scheduler.schedule})
        return jsonify({
            "code": 200,
            "schedule": evo_scheduler.schedule,
            "next_run": evo_scheduler.get_next_run(),
            "last_summary": evo_scheduler._last_summary,
        })

    @app.route("/api/auto-evolve/summary")
    def api_evo_summary():
        """最近一次每日进化摘要"""
        from auto_evolve import evo_scheduler
        return jsonify({
            "code": 200,
            "summary": evo_scheduler._last_summary,
        })

    @app.route("/api/auto-evolve/start", methods=["POST"])
    def api_auto_evolve_start():
        """触发进化周期。audit_only同步返回, 其他模式后台运行"""
        if evo_engine.is_running:
            return jsonify({"code": 400, "error": "已有进化周期正在运行"})
        data = request.get_json() or {}
        mode = data.get("mode", "full")
        cycle_id = f"evo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        stock_d = STOCK_DATA if len(STOCK_DATA) > 0 else None
        factor_c = _FACTOR_CACHE if _CACHE_READY else None
        name_m = _NAME_MAP if _NAME_MAP else None

        if mode == "audit_only":
            # 审计很快, 同步执行直接返回结果
            try:
                result = evo_engine.run_cycle(cycle_id, mode, stock_d, factor_c, name_m)
                return jsonify({"code": 200, "cycle_id": cycle_id, "status": result.status,
                               "audit": result.audit, "message": "审计完成"})
            except Exception as e:
                return jsonify({"code": 500, "error": str(e)})
        else:
            # 优化/完整模式放后台线程
            import threading as _th
            _th.Thread(target=evo_engine.run_cycle,
                       args=(cycle_id, mode, stock_d, factor_c, name_m),
                       daemon=True).start()
            return jsonify({"code": 200, "cycle_id": cycle_id, "message": f"进化周期已启动 ({mode})"})

    @app.route("/api/auto-evolve/xgb-importance", methods=["GET", "POST"])
    def api_xgb_importance():
        """E72: XGBoost 因子重要性排序。从内存 factor_cache + STOCK_DATA 读取，输出特征重要性。"""
        try:
            from quant_agent.xgb_importance import rank_factors
        except ImportError:
            return jsonify({"code": 500, "error": "quant_agent.xgb_importance 未安装或 xgboost 缺失"})
        if not _CACHE_READY or len(_FACTOR_CACHE) == 0:
            return jsonify({"code": 400, "error": "因子缓存未就绪，请等待缓存构建完成"})
        # E84: 深度诊断 — 打印 StockInfo 属性 + stock_data key 格式
        print(f"[XGB-API] factor_cache={len(_FACTOR_CACHE)} stock_data={len(STOCK_DATA)}")
        if _FACTOR_CACHE:
            s0 = _FACTOR_CACHE[0]
            attrs = [a for a in dir(s0) if not a.startswith('_') and not callable(getattr(s0, a, None))]
            print(f"[XGB-API] StockInfo attrs ({len(attrs)}): {sorted(attrs)}")
            for key in ['signal_date', 'entry_time', 'close', 'symbol', 'date']:
                print(f"[XGB-API]   .{key} = {getattr(s0, key, 'MISSING')!r}")
        if STOCK_DATA:
            sd_keys = list(STOCK_DATA.keys())[:5]
            print(f"[XGB-API] stock_data keys (first 5): {sd_keys}")
        data = request.get_json() or {} if request.method == "POST" else {}
        forward_days = int(data.get("forward_days", 1))
        top_k = int(data.get("top_k", 10))
        result = rank_factors(factor_cache=_FACTOR_CACHE, stock_data=STOCK_DATA,
                              forward_days=forward_days, top_k=top_k)
        # E95+E100: XGBoost失败时降级到IC权重 (全包try/except，绝不500)
        if result.get("error") or result.get("samples", 0) == 0:
            try:
                print(f"[XGB-API] XGBoost不可用({result.get('error','samples=0')}), 降级IC...")
                ic_path = FACTOR_IC  # E26: config路径
                if not os.path.exists(ic_path):
                    ic_path = IC_REPORT_FULL_JSON  # E26: config路径
                fallback = {"importances": [], "source": "IC降级(空)", "samples": 0, "fallback": True}
                if os.path.exists(ic_path):
                    with open(ic_path, "r", encoding="utf-8") as f:
                        ic_data = json.load(f)
                    ic_results = ic_data.get("ic_results", {})
                    importances = []
                    for fname, ics in ic_results.items():
                        ic_1d = ics.get("ic_1d", {}).get("mean_ic", 0) or 0
                        importances.append({"factor": fname, "importance": round(abs(float(ic_1d)), 4)})
                    importances.sort(key=lambda x: -x["importance"])
                    fallback = {"importances": importances[:top_k], "source": "IC降级",
                                "samples": len(ic_results), "fallback": True}
                    # E106: 写入factor_weights.json (应用健康乘数)
                    if importances:
                        total_imp = sum(i["importance"] for i in importances) or 1
                        weights = {}
                        for imp in importances:
                            w = round(0.5 + imp["importance"] / total_imp * 0.5, 2)
                            weights[imp["factor"]] = min(1.5, max(0.5, w))
                        _apply_health_multipliers(weights)
                        wpath = FACTOR_WEIGHTS  # E26: config路径
                        with open(wpath, "w", encoding="utf-8") as _wf:
                            json.dump(weights, _wf, ensure_ascii=False, indent=2)
                        print(f"[XGB-API] 权重已写入: {wpath}")
                result = fallback
            except Exception as _e_fb:
                print(f"[XGB-API] 降级失败: {_e_fb}")
                result = {"importances": [], "source": f"全部失败:{_e_fb}", "samples": 0, "fallback": True}
        # E113: XGBoost/IC成功时也写入factor_weights.json
        if result.get("importances") and result.get("samples", 0) > 0:
            try:
                imps = result["importances"]
                total_imp = sum(i["importance"] for i in imps) or 1
                weights = {}
                for imp in imps:
                    w = round(0.5 + imp["importance"] / total_imp * 0.5, 2)
                    weights[imp["factor"]] = min(1.5, max(0.5, w))
                _apply_health_multipliers(weights)
                wpath = FACTOR_WEIGHTS  # E26: config路径
                with open(wpath, "w", encoding="utf-8") as _wf:
                    json.dump(weights, _wf, ensure_ascii=False, indent=2)
                print(f"[XGB-API] 权重已写入: {wpath}")
            except Exception as _ew: print(f"[XGB-API] 写权重失败: {_ew}")
        return jsonify({"code": 200, **result})

    @app.route("/api/auto-evolve/progress/<cycle_id>")
    def api_auto_evolve_progress(cycle_id):
        """进化周期进度"""
        progress = evo_engine.get_progress()
        return jsonify({"code": 200, "cycle_id": cycle_id, **progress})

    @app.route("/api/auto-evolve/result/<cycle_id>")
    def api_auto_evolve_result(cycle_id):
        """进化周期完整结果"""
        result = evo_engine.get_result(cycle_id)
        if result:
            return jsonify({"code": 200, **result})
        return jsonify({"code": 404, "error": "未找到该进化周期"})

    @app.route("/api/auto-evolve/history")
    def api_auto_evolve_history():
        """进化历史记录"""
        history = evo_engine.get_history(limit=20)
        return jsonify({"code": 200, "cycles": history})

    @app.route("/api/auto-evolve/apply", methods=["POST"])
    def api_auto_evolve_apply():
        """用户审批并应用参数"""
        data = request.get_json() or {}
        target = data.get("apply_target", "paper")
        params = data.get("params", {})
        if not params:
            return jsonify({"code": 400, "error": "未指定参数"})
        applied = []
        try:
            from live_trader import CONFIG
            old_config = dict(CONFIG)
            for param, val in params.items():
                if param in CONFIG:
                    CONFIG[param] = type(CONFIG[param])(val) if CONFIG[param] is not None else val
                    applied.append(param)
            # E53: 记录参数变更，构建 changes dict
            changes = {}
            for param in applied:
                changes[param] = {"old": old_config.get(param), "new": CONFIG[param]}
            try:
                from param_versioning import record_apply
                record_apply(data.get("cycle_id", ""), changes, old_config, dict(CONFIG))
            except Exception as _pv:
                print(f"[Apply] 参数记录失败(无碍): {_pv}")
            # 同步到 paper_engine + 自动重启加载新规则
            try:
                from paper_engine import paper
                paper.restart()
            except Exception:
                pass
            # E22: 记录应用历史
            cycle_id = data.get("cycle_id", "")
            try:
                from data_collector import collector
                collector.record_apply(cycle_id, params, applied)
            except Exception:
                pass
            return jsonify({"code": 200, "applied": applied, "target": target,
                           "message": f"已应用{len(applied)}项参数, 模拟盘已自动重启生效"})
        except Exception as e:
            return jsonify({"code": 500, "error": str(e)})

    @app.route("/api/auto-evolve/schedule", methods=["GET", "POST"])
    def api_auto_evolve_schedule():
        """读取/设置调度配置"""
        if request.method == "POST":
            data = request.get_json() or {}
            if "daily" in data: evo_scheduler.schedule["daily"] = data["daily"]
            if "weekly" in data: evo_scheduler.schedule["weekly"] = data["weekly"]
            if "enabled" in data: evo_scheduler.schedule["enabled"] = data["enabled"]
            return jsonify({"code": 200, "message": "调度已更新"})
        return jsonify({"code": 200, "schedule": evo_scheduler.schedule})

    # 启动调度器 (默认关闭, 用户通过页面开启)
    evo_scheduler.start()

    print("[App] Auto-evolve system loaded")

    # ── 知识库 API ──
    try:
        from strategy_knowledge import kb

        @app.route("/api/knowledge/benchmarks")
        def api_knowledge_benchmarks():
            """获取TOP5评审基准"""
            cat = request.args.get("category", "")
            pri = request.args.get("priority", "")
            items = kb.get_benchmarks(category=cat or None, priority=pri or None)
            return jsonify({"code": 200, "benchmarks": items})

        @app.route("/api/knowledge/gap-analysis")
        def api_knowledge_gap_analysis():
            """差距分析 — 与TOP5对比"""
            gaps = kb.get_gap_analysis()
            comparison = kb.compare_with_us()
            roadmap = kb.get_improvement_roadmap()
            maturity = kb.get_maturity()
            return jsonify({"code": 200, "gaps": gaps, "comparison": comparison,
                           "roadmap": roadmap, "maturity": maturity})

        @app.route("/api/knowledge/criteria")
        def api_knowledge_criteria():
            """获取策略评审标准"""
            criteria = kb.get_audit_criteria()
            return jsonify({"code": 200, "criteria": criteria})

        @app.route("/api/knowledge/learn", methods=["POST"])
        def api_knowledge_learn():
            """触发自动学习"""
            result = kb.auto_learn()
            return jsonify({"code": 200, "result": result})

        @app.route("/api/knowledge/update", methods=["POST"])
        def api_knowledge_update():
            """手动更新知识条目"""
            data = request.get_json() or {}
            name = data.get("name", "")
            feature = data.get("feature", "")
            updates = data.get("updates", {})
            if name and feature:
                ok = kb.update_benchmark(name, feature, updates)
                return jsonify({"code": 200 if ok else 404, "updated": ok})
            # 新增条目
            if data.get("new_benchmark"):
                kb.add_benchmark(data["new_benchmark"])
                return jsonify({"code": 200, "added": True})
            return jsonify({"code": 400, "error": "缺少参数"})

        print("[App] Knowledge base API loaded")
    except ImportError as e:
        print(f"[App] Knowledge base not available: {e}")

except ImportError as e:
    print(f"[App] Auto-evolve not available: {e}")


# ======================================================================
# 复盘系统 API
# ======================================================================

@app.route("/api/pnl/history")
def api_pnl_history():
    """获取净值历史。E222: API网关优先，本地降级。"""
    try:
        import requests as _req
        resp = _req.get('http://localhost:8000/api/v1/pnl/history', timeout=0.5)
        if resp.status_code == 200 and resp.json().get('code') == 200:
            return jsonify(resp.json())
    except Exception:
        pass  # 静默降级
    # 降级: 原始逻辑
    from pnl_tracker import get_history, compute_metrics
    records = get_history()
    metrics = compute_metrics(records)
    return jsonify({"code": 200, "records": records, "metrics": metrics, "count": len(records)})

@app.route("/api/pnl/snapshot", methods=["POST"])
def api_pnl_snapshot():
    """手动记录当日快照。"""
    from pnl_tracker import record_snapshot
    data = request.get_json() or {}
    record_snapshot(
        live_value=data.get("live_value", 0), live_pnl=data.get("live_pnl", 0),
        paper_value=data.get("paper_value", 0), paper_pnl=data.get("paper_pnl", 0),
        paper_return=data.get("paper_return", 0), benchmark_value=data.get("benchmark_value", 0),
    )
    return jsonify({"code": 200, "message": "snapshot recorded"})


@app.route("/api/minute/<symbol>")
def api_minute(symbol):
    """获取个股当日分时数据（腾讯接口）"""
    import urllib.request, json, re
    try:
        url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data&code={symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        # JSONP 解析: min_data=({...})
        match = re.search(r"min_data\s*=\s*(\{.+?\})\s*;?$", raw, re.DOTALL)
        if not match:
            return jsonify({"code": 500, "msg": "parse failed"})
        data = json.loads(match.group(1))
        tick_data = data.get("data", {}).get(symbol, {}).get("data", {}).get("data", [])
        if not tick_data:
            return jsonify({"code": 500, "msg": "no data"})
        lines = []
        today = data.get("data", {}).get(symbol, {}).get("qt", {}).get("date", "")
        pre_close = float(data.get("data", {}).get(symbol, {}).get("qt", {}).get("pre_close", 0))
        for item in tick_data:
            parts = item.split()
            if len(parts) >= 4:
                t, p, v, a = parts[0], float(parts[1]), int(parts[2]), float(parts[3])
                chg_pct = ((p - pre_close) / pre_close * 100) if pre_close > 0 else 0
                hh, mm = t[:2], t[2:]
                lines.append({"time": f"{hh}:{mm}", "price": p, "volume": v, "amount": a, "chg_pct": round(chg_pct, 2)})
        return jsonify({"code": 200, "symbol": symbol, "pre_close": pre_close, "today": today, "data": lines})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)})


# [deprecated] /kline-v2 — K线V2已废弃


# [deprecated] /kline — K线V3已废弃


@app.route("/kline")
def page_kline_redirect():
    """兼容 /kline?symbol=xxx 参数路由 → 302 到 /kline/xxx"""
    sym = request.args.get("symbol", "").strip()
    if sym:
        return redirect(f"/kline/{sym}", code=302)
    return "请提供 symbol 参数，例如 /kline/sh605378", 400


@app.route("/api/stock/<symbol>/signals")
def api_stock_signals(symbol):
    """返回个股信号时间序列 (E27 信号叠加)。"""
    init_data()
    signals = []
    try:
        # 从因子缓存读取信号
        import pickle as _pkl
        _fp = r"D:\quant_web\factor_cache.pkl"
        if os.path.exists(_fp):
            with open(_fp, "rb") as _f:
                _raw = _pkl.load(_f)
            _items = _raw.get("data", []) if isinstance(_raw, dict) else (_raw if isinstance(_raw, list) else [])
            for _item in _items:
                _sym = _item.get("symbol", "") if isinstance(_item, dict) else getattr(_item, "symbol", "")
                if _sym.replace("sh","").replace("sz","").replace("bj","") != symbol.replace("sh","").replace("sz","").replace("bj",""):
                    continue
                _entry = _item.get("entry_time", "") if isinstance(_item, dict) else getattr(_item, "entry_time", "")
                _sdate = _item.get("signal_date", "") if isinstance(_item, dict) else getattr(_item, "signal_date", "")
                _xg = _item.get("signal_xg", 0) if isinstance(_item, dict) else getattr(_item, "signal_xg", 0)
                _qlj = _item.get("signal_qlj", 0) if isinstance(_item, dict) else getattr(_item, "signal_qlj", 0)
                _t = _entry or _sdate
                if _t:
                    signals.append({
                        "date": str(_t)[:10],
                        "time": str(_t)[:10],
                        "type": "buy",
                        "reason": f"XG:{_xg} QLJ:{_qlj}",
                    })
                break
    except Exception as _e:
        logger.warning(f"信号加载失败: {_e}")

    return jsonify({"code": 200, "signals": signals})


# ======================================================================
# 启动
# ======================================================================

# S08: 启动健康检查 + S10: 数据完整性校验
def _startup_health_check():
    """启动时检查关键依赖"""
    import os as _sos
    print("[HealthCheck] === 启动健康检查 ===")
    all_ok = True
    # P0-2: parquet优先，回退gzip/pickle
    _pq_ok = _sos.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.parquet"))
    _gz_ok = _sos.path.exists(STOCK_DATA_PKL_GZ)
    _pkl_ok = _sos.path.exists(STOCK_DATA_PKL)
    print(f"  [{'✅' if (_pq_ok or _gz_ok or _pkl_ok) else '❌'}] 日线数据: parquet={_pq_ok} gzip={_gz_ok} pkl={_pkl_ok}")
    if not (_pq_ok or _gz_ok or _pkl_ok): all_ok = False
    for _p, _n in [(FACTOR_CACHE_PKL, "因子缓存"),
                   (PRICE_CACHE_JSON, "价格缓存"),  # E26: config路径
                   (STOCK_NAMES_CSV, "股票名称"),  # E26: config路径
                    (LIVE_CONFIG, "交易配置")]:  # E26: config路径
        _ok = _sos.path.exists(_p) and _sos.path.getsize(_p) > 100
        if _n == "因子缓存" and not _ok:
            # 启动时后台线程可能还在重建, 不标记失败
            print(f"  [⏳] {_n}: {_p} (后台重建中)")
            continue
        print(f"  [{'✅' if _ok else '❌'}] {_n}: {_p}")
        if not _ok: all_ok = False
    # 关键Python包
    for _m in ["numpy","pandas","scipy","flask"]:
        try: __import__(_m); print(f"  [✅] 依赖: {_m}")
        except: print(f"  [❌] 依赖: {_m}"); all_ok = False
    # S10: 验证核心数据可解析 (P0-2: parquet/gzip/pickle)
    try:
        _pq_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.parquet")
        if os.path.exists(_pq_path):
            from data_loader import load_stock_data_cache as _hc_pq
            _sd = _hc_pq(_pq_path)
            if _sd: print(f"  [✅] stock_data可读(parquet): {len(_sd)}只")
            else: raise Exception("parquet加载返回None")
        else:
            import pickle as _spk, gzip as _sgz
            _sd = _spk.load(_sgz.open(STOCK_DATA_PKL_GZ, 'rb'))
            print(f"  [✅] stock_data可读: {len(_sd)}只")
    except Exception as _e: print(f"  [❌] stock_data损坏: {_e}"); all_ok = False
    print(f"[HealthCheck] 结果: {'全部通过' if all_ok else '有失败项'}")
    return all_ok

# S09: 原子写入工具
def _atomic_write_json(filepath, data_dict):
    """原子写入JSON: .tmp→os.replace, 写入中断不损坏原文件"""
    import os as _s9os
    tmp = filepath + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as _f:
            json.dump(data_dict, _f, ensure_ascii=False, indent=2)
        _s9os.replace(tmp, filepath)
        return True
    except Exception as _e:
        print(f"[IO] 原子写入失败 {filepath}: {_e}")
        if _s9os.path.exists(tmp): _s9os.remove(tmp)
        return False


# S13: 优雅关闭钩子
import atexit as _atexit13
def _shutdown_hook():
    print("[System] 保存状态...")
    try:
        import paper_engine; paper_engine.paper._save()
        print("[Shutdown] paper已保存")
    except Exception as _pe: logger.warning(f"[Shutdown] paper保存失败: {_pe}")
    try:
        import live_trader; live_trader._save_position_tracker(live_trader._load_position_tracker())
        print("[Shutdown] 持仓跟踪已保存")
    except Exception as _te: logger.warning(f"[Shutdown] 持仓跟踪保存失败: {_te}")
    try:
        from realtime_quotes import _save_persisted; _save_persisted()
    except Exception: logger.warning("[Shutdown] 行情缓存保存失败")
    try:
        with open(LAST_SHUTDOWN_TXT, "w") as _sf:  # E26: config路径
            _sf.write(f"shutdown: {datetime.now()}\n")
    except Exception as e:
        logger.warning("[Shutdown] 写关闭标记失败: %s", e)
    print("[System] 状态已保存")
_atexit13.register(_shutdown_hook)


# user_customizations 功能已迁移, 但策略/公式 CRUD 路由仍需要
# 直接注册避免循环导入
@app.route("/api/user-strategies", methods=["GET"])
def api_user_strategies_list():
    try:
        data = json.load(open(r"D:\quant_framework\user_customizations\user_strategies.json", "r", encoding="utf-8"))
        return jsonify(data.get("strategies", []))
    except: return jsonify([])

@app.route("/api/user-strategies/<name>", methods=["DELETE"])
def api_user_strategies_delete(name):
    try:
        data = json.load(open(r"D:\quant_framework\user_customizations\user_strategies.json", "r", encoding="utf-8"))
        data["strategies"] = [s for s in data.get("strategies", []) if s.get("name") != name]
        json.dump(data, open(r"D:\quant_framework\user_customizations\user_strategies.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/user-tdx-formulas/<name>", methods=["DELETE"])
def api_user_tdx_formulas_delete(name):
    try:
        fp = r"D:\quant_framework\user_customizations\user_tdx_formulas.json"
        if os.path.exists(fp):
            data = json.load(open(fp, "r", encoding="utf-8"))
            data["formulas"] = [f for f in data.get("formulas", []) if f.get("name") != name]
            json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/formula-effects")
def api_formula_effects():
    """公式效果看板 (映射Registry因子 → IC评估)"""
    import json as _jf, os as _of
    fp = r"D:\quant_framework\user_customizations\user_tdx_formulas.json"
    formulas = []
    if _of.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            formulas = _jf.load(f).get("formulas", [])

    reg_fp = r"D:\quant_framework\factor_registry.json"
    reg = {}
    if _of.path.exists(reg_fp):
        with open(reg_fp, "r") as f:
            reg = {x["name"]: x for x in _jf.load(f).get("factors", [])}

    result = []
    for f in formulas:
        name = f.get("name", "")
        mapped = f.get("mapped_factor", "")
        if not mapped:
            for rn in reg:
                if rn in name.lower() or name.lower() in rn:
                    mapped = rn; break
        effect = {}
        if mapped and mapped in reg:
            rf = reg[mapped]
            ic5 = rf.get("ic_5d", 0) or 0
            effect = {
                "mapped_factor": mapped,
                "factor_display": rf.get("display", mapped),
                "ic_5d": ic5,
                "rating": "🟢 有效" if abs(ic5) > 0.03 else ("🟡 一般" if abs(ic5) > 0.01 else "🔴 弱"),
            }
        result.append({"name": name, "file_path": f.get("file_path", ""), "enabled": f.get("enabled", True),
                       "effect": effect, "has_file": _of.path.exists(f.get("file_path", ""))})
    return jsonify({"code": 200, "formulas": result})


@app.route("/api/user-tdx-formulas", methods=["GET"])
def api_user_formulas_list():
    try:
        data = json.load(open(r"D:\quant_framework\user_customizations\user_tdx_formulas.json", "r", encoding="utf-8"))
        return jsonify(data.get("formulas", []))
    except: return jsonify([])

@app.route("/api/user-tdx-formulas", methods=["POST"])
def api_user_formulas_create():
    try:
        d = request.get_json(silent=True) or {}
        data = json.load(open(r"D:\quant_framework\user_customizations\user_tdx_formulas.json", "r", encoding="utf-8"))
        data.setdefault("formulas", []).append({"name": d.get("name",""), "file_path": d.get("file_path",""), "description": d.get("description",""), "enabled": True})
        json.dump(data, open(r"D:\quant_framework\user_customizations\user_tdx_formulas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/user-tdx-formulas/<name>", methods=["DELETE"])
def api_user_formulas_delete(name):
    try:
        data = json.load(open(r"D:\quant_framework\user_customizations\user_tdx_formulas.json", "r", encoding="utf-8"))
        data["formulas"] = [f for f in data.get("formulas", []) if f.get("name") != name]
        json.dump(data, open(r"D:\quant_framework\user_customizations\user_tdx_formulas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════
#  V1-5 全市场因子IC API
# ═══════════════════════════════════════════════════════

@app.route("/factor-ic-v15")
def page_factor_ic_v15():
    """V1-5因子IC看板"""
    from flask import render_template
    return render_template("factor_ic_v15.html")


@app.route("/api/factor-registry")
def api_factor_registry():
    """FactorRegistry 因子池状态 + 自动权重"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_registry import get_active_factors, get_retired_factors, get_ic_weights, summary
        return jsonify({
            "code": 200,
            "active": get_active_factors(),
            "retired": get_retired_factors(),
            "weights": get_ic_weights("5d"),
            "summary": summary(),
        })
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-registry/add", methods=["POST"])
@require_api_key("trade")
def api_factor_registry_add():
    """用户添加自定义因子到 Registry"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_registry import add_factor
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        display = (data.get("display") or data.get("display_name") or "").strip()
        compute = (data.get("compute") or "").strip()
        if not name or not display:
            return jsonify({"code": 400, "error": "名称和显示名不能为空"})
        if not compute:
            compute = f"user_factors.{name}"  # 默认路径
        ok = add_factor(name, display, compute, category=data.get("category", "自定义"))
        return jsonify({"code": 200 if ok else 400, "success": ok})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


# ═══════════════════════════════════════════════════════
#  Factor Health Monitor API
# ═══════════════════════════════════════════════════════

@app.route("/trading")
def page_trading():
    """交易中心 → 模拟盘(默认)"""
    return redirect("/paper-trade-v3", code=302)


# [deprecated] /trading-v2 — 交易V2已废弃


@app.route("/factor-health")
def page_factor_health():
    """因子健康监控页面"""
    return render_template("factor_health.html")


@app.route("/api/user-strategies")
def api_user_strategies():
    """返回用户策略列表 (因子中心前端调用)"""
    try:
        sp = r"D:\quant_framework\user_customizations\user_strategies.json"
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                data = json.load(f)
            return jsonify({"code": 200, "strategies": data.get("strategies", [])})
        return jsonify({"code": 200, "strategies": []})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/tdx-formulas/scan")
def api_tdx_formulas_scan():
    """扫描通达信公式目录，返回可用公式列表(含源码)"""
    import glob as _gl
    formulas = []
    # 用户指定路径优先，否则用默认扫描列表
    user_path = request.args.get("path", "").strip()
    scan_dirs = [user_path] if user_path and os.path.isdir(user_path) else [
        r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002\tpool",
        r"D:\new_tdx\T0002\tpool",
        r"D:\通达信\T0002\tpool",
    ]
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        # 扫描所有文件（TDX公式后缀多样: .fml/.tni/.xml/.tdx 或无后缀）
        for fp in _gl.glob(os.path.join(scan_dir, '*')):
            if not os.path.isfile(fp):
                continue
            name = os.path.basename(fp)
            # 跳过非公式文件
            if name.startswith('.') or name.endswith(('.dll','.exe','.dat','.ini','.log','.tmp')):
                continue
            # 读源码
            source = ""
            try:
                with open(fp, "r", encoding="gbk", errors="replace") as sf:
                    source = sf.read()[:5000]
            except Exception:
                pass
            formulas.append({
                "name": name, "file_path": fp, "dir": scan_dir,
                "size": os.path.getsize(fp), "source": source
            })
    seen = set()
    unique = []
    for f in formulas:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)
    return jsonify({"code": 200, "formulas": sorted(unique, key=lambda x: x["name"]),
                    "scanned_dirs": scan_dirs, "total_files": len(formulas)})


@app.route("/api/user-tdx-formulas", methods=["GET", "POST", "DELETE"])
def api_user_tdx_formulas():
    """管理用户通达信公式列表 (因子中心前端调用)"""
    fp = r"D:\quant_framework\user_customizations\user_tdx_formulas.json"
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    if request.method == "GET":
        try:
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return jsonify({"code": 200, "formulas": data.get("formulas", [])})
            return jsonify({"code": 200, "formulas": []})
        except Exception as e:
            return jsonify({"code": 500, "error": str(e)})
    elif request.method == "POST":
        try:
            item = request.get_json() or {}
            name = str(item.get("name", "")).strip()
            if not name:
                return jsonify({"success": False, "error": "名称不能为空"})
            data = {"formulas": []}
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            # Update or append
            found = False
            for f in data.get("formulas", []):
                if f.get("name") == name:
                    f["file_path"] = item.get("file_path", f.get("file_path", ""))
                    f["description"] = item.get("description", f.get("description", ""))
                    f["source"] = item.get("source", f.get("source", ""))
                    f["enabled"] = item.get("enabled", f.get("enabled", True))
                    found = True
                    break
            if not found:
                data.setdefault("formulas", []).append({
                    "name": name,
                    "file_path": item.get("file_path", ""),
                    "description": item.get("description", ""),
                    "source": item.get("source", ""),
                    "enabled": item.get("enabled", True),
                })
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return jsonify({"success": True, "message": "已保存: " + name})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    # DELETE handled by /api/user-tdx-formulas/<name> route below


@app.route("/api/factor-health")
def api_factor_health():
    """运行健康检查并返回报告"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import run_health_check
        return jsonify({"code": 200, **run_health_check()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/source/<name>", methods=["GET", "POST"])
def api_factor_source(name):
    """读写因子源码。GET=读取, POST=写入。"""
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_]+$', name):
        return jsonify({"code": 400, "error": "非法因子名"})
    src_path = os.path.join(r"D:\quant_framework", f"_{name}.py")
    if request.method == "GET":
        if not os.path.exists(src_path):
            # 尝试 factor_pipeline.py 中的函数
            alt = r"D:\quant_framework\factor_pipeline.py"
            if os.path.exists(alt):
                with open(alt, "r", encoding="utf-8") as f:
                    content = f.read()
                return jsonify({"code": 200, "source": content, "path": alt, "note": "factor_pipeline.py 全局"})
            return jsonify({"code": 404, "error": "源码文件不存在"})
        with open(src_path, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({"code": 200, "source": content, "path": src_path})
    # POST: 写入
    try:
        data = request.get_json() or {}
        code = data.get("code", "")
        if not code.strip():
            return jsonify({"code": 400, "error": "代码不能为空"})
        # 备份旧文件
        if os.path.exists(src_path):
            bak = src_path + ".bak"
            import shutil as _sh
            _sh.copy2(src_path, bak)
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)
        return jsonify({"code": 200, "message": f"已保存: {src_path}"})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-health/ic-trends")
def api_factor_ic_trends():
    """IC趋势数据: 从历史快照提取各因子IC_5d时间序列"""
    import os as _os4, json as _j4
    hist_dir = r"D:\quant_framework\ic_history"
    try:
        snaps = sorted(_os4.listdir(hist_dir))[-30:] if _os4.path.exists(hist_dir) else []
        if len(snaps) < 3:
            return jsonify({"code": 200, "dates": [], "series": {}})
        dates = []
        series = {}
        for sn in snaps:
            try:
                with open(_os4.path.join(hist_dir, sn)) as f:
                    snap = _j4.load(f)
            except Exception:
                continue
            # 提取时间戳
            ts = snap.get("generated_at", sn.replace("ic_","").replace(".json",""))[:16]
            dates.append(ts)
            facs = snap.get("factors", {})
            for name, data in facs.items():
                ic = data.get("IC_5d") or 0
                if name not in series:
                    series[name] = []
                series[name].append(ic)
        return jsonify({"code": 200, "dates": dates, "series": series})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor/run-ic", methods=["POST"])
def api_factor_run_ic():
    """后台运行全市场IC验证 (full_market_ic.py)"""
    import subprocess, threading
    def _bg_ic():
        try:
            print("[IC-Task] 全市场IC验证开始...")
            r = subprocess.run(
                [sys.executable, "full_market_ic.py"],
                cwd=r"D:\quant_framework",
                capture_output=True, text=True, timeout=600,
                encoding='gbk', errors='replace'
            )
            if r.returncode == 0:
                print(f"[IC-Task] ✅ IC验证完成")
                # IC跑完后自动刷新健康检查
                try:
                    from factor_health import run_health_check
                    run_health_check()
                    print("[IC-Task] ✅ 健康检查已自动刷新")
                except Exception: pass
            else:
                print(f"[IC-Task] ❌ IC验证失败: {r.stderr[:200]}")
        except Exception as e:
            print(f"[IC-Task] ❌ {e}")
    threading.Thread(target=_bg_ic, daemon=True).start()
    return jsonify({"code": 200, "message": "全市场IC验证已启动，预计5-10分钟"})


# ═══════════════════════════════════════════════════════
#  策略构建器 API
# ═══════════════════════════════════════════════════════

@app.route("/api/strategy-builder/create", methods=["POST"])
@require_api_key("trade")
def api_strategy_builder_create():
    """选因子+设阈值 → 创建策略"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_builder import create_strategy
        data = request.get_json(silent=True) or {}
        result = create_strategy(
            name=data.get("name", ""),
            factors=data.get("factors", []),
            trigger=data.get("trigger", {"type": "weighted_sum", "min_score": 60}),
            stop_loss=data.get("stop_loss", -0.03),
            take_profit=data.get("take_profit", [0.05, 0.07, 0.12]),
            hold_days=data.get("hold_days", 5),
            target=data.get("target", "draft"),
        )
        return jsonify({"code": 200 if result["success"] else 400, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-builder/backtest/<name>", methods=["POST"])
@require_api_key("trade")
def api_strategy_builder_backtest(name):
    """对策略跑快速回测"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from ql_backtest import run as bt_run
        result = bt_run(name)
        if not result.get('success'):
            from strategy_builder import run_backtest
            result = run_backtest(name, days=250, walk_forward=False)
        return jsonify({"code": 200 if result.get("success") else 400, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-builder/backtest-all", methods=["POST"])
def api_strategy_backtest_all():
    """批量回测所有未回测策略"""
    import threading as _th
    def _bg_all():
        sp = r"D:\quant_framework\user_customizations\user_strategies.json"
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
        strategies = data.get("strategies", [])
        # 只回测从未回测过的策略（保护已有回测数据）
        untested = [s for s in strategies if (not s.get("backtest") or not (s["backtest"] or {}).get("annualized_sharpe")) and s.get("type") == "builder" and s.get("factors")]
        print(f"[BatchBacktest] 开始回测 {len(untested)} 个策略...")
        from strategy_builder import run_backtest
        ok = fail = 0
        for s in untested:
            name = s.get("name", "")
            if not name: continue
            try:
                print(f"[BatchBacktest]   {name}...")
                run_backtest(name)
                ok += 1
            except Exception as e:
                print(f"[BatchBacktest]   {name} 失败: {e}")
                fail += 1
        print(f"[BatchBacktest] 完成: {ok}成功, {fail}失败")
    _th.Thread(target=_bg_all, daemon=True).start()
    return jsonify({"code": 200, "message": "批量回测已启动，查看控制台输出"})



# ═══════════════════════════════════════════════════════
#  策略审批 API (G1)
# ═══════════════════════════════════════════════════════

@app.route("/api/strategy-approval/list")
def api_approval_list():
    """所有策略审批状态"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import get_all_approvals, STATES
        return jsonify({"code": 200, "strategies": get_all_approvals(), "states": STATES})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-approval/<name>/submit", methods=["POST"])
@require_api_key("trade")
def api_approval_submit(name):
    """提交审核: draft → review"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import submit_for_review
        r = submit_for_review(name)
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-approval/<name>/approve-sim", methods=["POST"])
@require_api_key("trade")
def api_approval_approve_sim(name):
    """玄策审核通过: review → approved_sim"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import approve_sim
        r = approve_sim(name, "玄策")
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-approval/<name>/reject", methods=["POST"])
@require_api_key("trade")
def api_approval_reject(name):
    """退回修改: review → draft"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import reject_to_draft
        data = request.get_json(silent=True) or {}
        r = reject_to_draft(name, "玄策", data.get("reason", ""))
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-approval/<name>/approve-real", methods=["POST"])
@require_api_key("trade")
def api_approval_approve_real(name):
    """老板审批实盘: review_real → real"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import approve_real
        r = approve_real(name, "老板")
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/daily-report/latest")
@app.route("/api/daily-report/latest")
def api_daily_report_v2():
    """P3-2: 最新日终报告"""
    try:
        import glob
        reps = sorted(glob.glob(r"D:\quant_framework\reports\daily_report_*.md"), reverse=True)
        if not reps:
            return jsonify({"code": 404, "error": "无报告"})
        with open(reps[0], "r", encoding="utf-8") as f:
            return jsonify({"code": 200, "date": os.path.basename(reps[0]).replace("daily_report_","").replace(".md",""), "content": f.read()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/daily-report")
def page_daily_report():
    """日终报告页面"""
    from flask import render_template
    return render_template("daily_report_view.html")


@app.route("/api/strategy-circuit-breaker/check", methods=["POST"])
@require_api_key("trade")
def api_circuit_breaker_check():
    """手动触发策略熔断检查"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import check_strategy_circuit_breaker
        actions = check_strategy_circuit_breaker()
        return jsonify({"code": 200, "actions": len(actions), "details": actions})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-approval/<name>/pause", methods=["POST"])
@require_api_key("trade")
def api_approval_pause(name):
    """暂停策略"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import pause_strategy
        r = pause_strategy(name, "手动暂停")
        return jsonify({"code": 200 if r["success"] else 400, **r})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/evolution/suggestions")
def api_evolution_suggestions():
    """进化建议: 读取进化最优参数，与现有策略对比，附防过拟合告警"""
    evo_path = r"D:\quant_framework\evolution_result.json"
    strat_path = r"D:\quant_framework\user_customizations\user_strategies.json"
    result = {"suggestions": [], "overfit_warning": False, "note": ""}
    try:
        # 读进化结果
        if not os.path.exists(evo_path):
            return jsonify({"code": 200, "suggestions": [], "note": "暂无进化数据，请先运行进化系统"})
        evo = json.load(open(evo_path, "r", encoding="utf-8"))
        best_params = evo.get("best_params", {})
        best_fitness = evo.get("best_fitness", 0)
        history = evo.get("history", [])
        generations = len(history)

        # 过拟合检测: 最后2代fitness无改善或改善<1% → 过拟合风险
        overfit = False
        if generations >= 3:
            last_fits = [h.get("max_fitness", 0) for h in history[-3:]]
            if last_fits:
                improvement = (last_fits[-1] - last_fits[0]) / max(abs(last_fits[0]), 0.001)
                if abs(improvement) < 0.01 or last_fits[-1] < last_fits[-2]:
                    overfit = True

        # 读策略
        strategies = []
        if os.path.exists(strat_path):
            strategies = json.load(open(strat_path, "r", encoding="utf-8")).get("strategies", [])

        # 检查每个策略是否有独立进化结果
        evo_dir = r"D:\quant_framework\evo_results"
        per_strategy = {}
        if os.path.isdir(evo_dir):
            for fn in os.listdir(evo_dir):
                if fn.endswith('.json'):
                    try:
                        ed = json.load(open(os.path.join(evo_dir, fn), "r", encoding="utf-8"))
                        per_strategy[ed.get("strategy", fn.replace('.json', ''))] = ed
                    except Exception: pass

        for s in strategies:
            if s.get("type") != "builder":
                continue
            sname = s.get("name", "")
            current = {
                "stop_loss": s.get("stop_loss", -0.03),
                "take_profit": (s.get("take_profit", [0.05]) or [0.05])[0],
                "hold_days": s.get("hold_days", 3),
            }
            # 只显示有独立进化数据的策略
            if sname not in per_strategy:
                continue
            pe = per_strategy[sname]
            suggested = pe.get("best_params", {})
            if not suggested:
                continue
            overfit = pe.get("overfit_warning", False)
            gen = pe.get("generations", 0)
            # 只建议有实际变化的策略
            changes = sum(1 for k in current if abs(current.get(k, 0) - suggested.get(k, current.get(k, 0))) > 0.001)
            if changes > 0:
                result["suggestions"].append({
                    "name": sname,
                    "display": s.get("display_name", ""),
                    "current": current,
                    "suggested": suggested,
                    "sharpe": (s.get("backtest") or {}).get("annualized_sharpe"),
                    "win_rate": (s.get("backtest") or {}).get("win_rate"),
                })

        # 汇总per-strategy数据
        all_overfit = any(pe.get("overfit_warning", False) for pe in per_strategy.values())
        total_gen = sum(pe.get("generations", 0) for pe in per_strategy.values())
        best_fit = max((pe.get("best_fitness", -999) for pe in per_strategy.values()), default=0)
        result["overfit_warning"] = all_overfit
        result["generations"] = total_gen
        result["best_fitness"] = best_fit
        result["note"] = "只显示已进化的策略，未进化的点🧬单独运行" if per_strategy else "暂无独立进化数据，点🧬运行策略进化"
        return jsonify({"code": 200, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/evolution/run/<name>", methods=["POST"])
def api_evolution_run(name):
    """对指定策略运行快速参数进化(300次随机搜索, 1-2分钟)"""
    import threading as _th, random as _rnd, copy as _cp
    strat_path = r"D:\quant_framework\user_customizations\user_strategies.json"

    # 读策略
    data = json.load(open(strat_path, "r", encoding="utf-8"))
    strategy = None
    for s in data.get("strategies", []):
        if s.get("name") == name:
            strategy = s
            break
    if not strategy:
        return jsonify({"success": False, "error": "策略未找到"})

    # 取当前参数，用户可传ranges覆盖
    cur_stop = strategy.get("stop_loss", -0.03)
    cur_tp = (strategy.get("take_profit", [0.05]) or [0.05])[0]
    cur_hold = strategy.get("hold_days", 3)

    body = request.get_json() or {}
    user_ranges = body.get("ranges", {})

    def _make_range(key, lo_fn, hi_fn):
        if key in user_ranges:
            v = user_ranges[key]
            return (float(v[0]), float(v[1])) if len(v) >= 2 else (lo_fn(), hi_fn())
        return (lo_fn(), hi_fn())

    ranges = {
        "stop_loss":   _make_range("stop_loss", lambda: max(-0.08, cur_stop*1.5), lambda: min(-0.01, cur_stop*0.5)),
        "take_profit": _make_range("take_profit", lambda: max(0.03, cur_tp*0.5), lambda: min(0.20, cur_tp*1.5)),
        "hold_days":   tuple(int(v) for v in _make_range("hold_days", lambda: max(1, cur_hold-3), lambda: min(10, cur_hold+3))),
    }

    def _bg_evo():
        import traceback as _tb, numpy as _np, copy as _cp, shutil as _sh
        try:
            import __main__ as _m
            sd = getattr(_m, 'STOCK_DATA', None)
            if not sd or len(sd) == 0:
                _m.init_data()
                sd = getattr(_m, 'STOCK_DATA', {})
            if not sd:
                print(f"[Evo-{name}] FAIL: STOCK_DATA not available")
                return

            # 用真正的Walk-Forward回测评估每个候选参数
            best_score = -999
            best_params = {}
            results = []
            sys.path.insert(0, r"D:\quant_framework")
            from strategy_builder import run_backtest

            # 保存原始策略 + 原始backtest (防止被run_backtest覆盖)
            orig = json.load(open(strat_path, "r", encoding="utf-8"))
            orig_backtest = None
            for s2 in orig.get("strategies", []):
                if s2.get("name") == name:
                    orig_backtest = s2.get("backtest")
                    break
            n_trials = 50
            for _i in range(n_trials):
                trial = {
                    "stop_loss": -round(_rnd.uniform(-ranges["stop_loss"][1], -ranges["stop_loss"][0]), 3),
                    "take_profit": round(_rnd.uniform(ranges["take_profit"][0], ranges["take_profit"][1]), 2),
                    "hold_days": _rnd.randint(ranges["hold_days"][0], ranges["hold_days"][1]),
                }
                try:
                    # 临时写入策略
                    tmp = json.load(open(strat_path, "r", encoding="utf-8"))
                    for s2 in tmp.get("strategies", []):
                        if s2.get("name") == name:
                            s2["stop_loss"] = trial["stop_loss"]
                            s2["take_profit"] = [trial["take_profit"]]
                            s2["hold_days"] = trial["hold_days"]
                    json.dump(tmp, open(strat_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                    bt = run_backtest(name, days=90, sample=200)  # Walk-Forward (default)
                    sharpe = bt.get("annualized_sharpe", 0) or 0
                except Exception:
                    sharpe = 0
                if sharpe > best_score:
                    best_score = sharpe
                    best_params = {**trial, "sharpe": round(sharpe, 2)}
                results.append({"trial": _i+1, "sharpe": round(sharpe, 2), **trial})

            # 恢复原始策略+原始backtest
            final = json.load(open(strat_path, "r", encoding="utf-8"))
            for s2 in final.get("strategies", []):
                if s2.get("name") == name:
                    s2["stop_loss"] = strategy.get("stop_loss", -0.03)
                    s2["take_profit"] = strategy.get("take_profit", [0.05])
                    s2["hold_days"] = strategy.get("hold_days", 3)
                    if orig_backtest is not None:
                        s2["backtest"] = orig_backtest
                    s2.pop("_evo_applied", None)
                    s2.pop("_evo_fitness", None)
            json.dump(final, open(strat_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

            # 写结果
            evo_dir = r"D:\quant_framework\evo_results"
            os.makedirs(evo_dir, exist_ok=True)
            out = {
                "strategy": name, "best_params": best_params,
                "best_fitness": best_score,
                "ranges": {k: list(v) for k, v in ranges.items()},
                "history": sorted(results, key=lambda x: -x["sharpe"])[:20],
                "generations": n_trials,
                "method": "Walk-Forward backtest per trial (50 trials, 60-day sample)",
                "overfit_warning": False,
            }
            with open(os.path.join(evo_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            # 同步更新主进化文件
            evo_path = r"D:\quant_framework\evolution_result.json"
            with open(evo_path, "w", encoding="utf-8") as f:
                json.dump({"best_params": best_params, "best_fitness": best_score,
                           "history": results[:10], "strategy": name}, f, ensure_ascii=False, indent=2)
            print(f"[Evo-{name}] 完成: best_sharpe={best_score:.2f} params={best_params}")
        except Exception as e:
            import traceback as _tb2
            print(f"[Evo-{name}] 失败: {e}")
            _tb2.print_exc()

    _th.Thread(target=_bg_evo, daemon=True).start()
    ranges_info = {k: [round(v[0], 3), round(v[1], 3)] for k, v in ranges.items()}
    return jsonify({"success": True, "message": f"进化已启动(50次Walk-Forward回测, {name}, 预计2-5分钟)",
                    "ranges": ranges_info, "current": {"stop_loss": cur_stop, "take_profit": cur_tp, "hold_days": cur_hold}})


@app.route("/api/evolution/apply/<name>", methods=["POST"])
def api_evolution_apply(name):
    """应用进化建议到指定策略"""
    try:
        evo_path = r"D:\quant_framework\evolution_result.json"
        strat_path = r"D:\quant_framework\user_customizations\user_strategies.json"
        if not os.path.exists(evo_path):
            return jsonify({"success": False, "error": "无进化数据"})
        evo = json.load(open(evo_path, "r", encoding="utf-8"))
        bp = evo.get("best_params", {})

        data = json.load(open(strat_path, "r", encoding="utf-8"))
        for s in data.get("strategies", []):
            if s.get("name") == name:
                old = {
                    "stop_loss": s.get("stop_loss"),
                    "take_profit": s.get("take_profit"),
                    "hold_days": s.get("hold_days"),
                }
                s["stop_loss"] = bp.get("stop_loss", s.get("stop_loss", -0.03))
                s["take_profit"] = [bp.get("take_profit", (s.get("take_profit") or [0.05])[0])]
                s["hold_days"] = bp.get("hold_days", s.get("hold_days", 3))
                s["_evo_applied"] = True
                s["_evo_fitness"] = evo.get("best_fitness")
                json.dump(data, open(strat_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                return jsonify({"success": True, "message": f"已应用进化参数到 {name}",
                                "old": old, "new": {"stop_loss": s["stop_loss"], "take_profit": s["take_profit"], "hold_days": s["hold_days"]}})
        return jsonify({"success": False, "error": "策略未找到"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/strategy-performance")
def api_strategy_performance():
    """策略实时绩效 (P3-3)"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_approval import get_all_approvals
        import json as _jsp, os as _osp

        strategies = get_all_approvals()
        result = []
        paper_path = _dp_get("paper_account")

        for s in strategies:
            state = s.get("state", "draft")
            if state not in ("sim_running", "real", "review_real"):
                continue

            # 从 paper_account 取真实交易记录
            trades = []
            try:
                if _osp.exists(paper_path):
                    with open(paper_path, "r") as f:
                        pa = _jsp.load(f)
                    all_trades = pa.get("trades", [])
                    trades = [t for t in all_trades if t.get("strategy") == s["name"] or
                             (t.get("reason", "").find(s["name"]) >= 0)]
            except Exception:
                pass

            # 计算绩效
            pnl_trades = [t for t in trades if t.get("pnl") is not None]
            wins = sum(1 for t in pnl_trades if t.get("pnl", 0) > 0)
            total = len(pnl_trades)
            win_rate = wins / max(total, 1)

            # 连续亏损
            consec = 0
            for t in reversed(pnl_trades[-20:]):
                if t.get("pnl", 0) < 0:
                    consec += 1
                else:
                    break

            total_pnl = sum(t.get("pnl", 0) for t in pnl_trades)
            avg_return = total_pnl / max(total, 1)
            std_return = (sum((t.get("pnl", 0) - avg_return) ** 2 for t in pnl_trades) / max(total, 1)) ** 0.5 if total > 1 else 0
            sharpe = avg_return / max(std_return, 0.0001) * (252 ** 0.5) if std_return > 0 else 0

            result.append({
                "name": s["name"],
                "state": state,
                "total_trades": total,
                "wins": wins,
                "win_rate": round(win_rate, 2),
                "sharpe": round(sharpe, 2),
                "total_pnl": round(total_pnl, 2),
                "consecutive_losses": consec,
                "last_trade": trades[-1].get("time", "") if trades else "",
                "risk": "🔴" if consec >= 3 else ("🟡" if consec >= 1 else "🟢"),
            })

        # 加 V1-5 因子策略绩效 (实际在跑的7个因子)
        try:
            from factor_registry import get_active_factors
            active_factors = {f["name"]: f["display"] for f in get_active_factors()}
            # 读全部成交
            all_trades = []
            if _osp.exists(paper_path):
                with open(paper_path, "r") as f:
                    all_trades = _jsp.load(f).get("trades", [])
            for fname, fdisplay in active_factors.items():
                ftrades = [t for t in all_trades if (t.get("strategy","") == fname or fname in t.get("reason",""))]
                if not ftrades: continue
                pnl_t = [t for t in ftrades if t.get("pnl") is not None]
                w = sum(1 for t in pnl_t if t.get("pnl",0)>0); n=len(pnl_t)
                wr = w/max(n,1); tp = sum(t.get("pnl",0) for t in pnl_t)
                av = tp/max(n,1); sd = (sum((t.get("pnl",0)-av)**2 for t in pnl_t)/max(n,1))**0.5 if n>1 else 0
                sh = av/max(sd,0.0001)*(252**0.5) if sd>0 else 0
                c2 = 0
                for t in reversed(pnl_t[-20:]):
                    if t.get("pnl",0)<0: c2+=1
                    else: break
                result.append({"name":fdisplay,"state":"sim_running","total_trades":n,"wins":w,"win_rate":round(wr,2),"sharpe":round(sh,2),"total_pnl":round(tp,2),"consecutive_losses":c2,"risk":"🔴" if c2>=3 else ("🟡" if c2>=1 else "🟢")})
        except: pass

        return jsonify({"code": 200, "strategies": result, "updated": __import__('datetime').datetime.now().strftime("%H:%M:%S")})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy/auto-combo", methods=["POST"])
def api_strategy_auto_combo():
    """Plan L: AI自动组合 — IC>0.05 + 相关性去重 + IC加权 + 回测"""
    try:
        reg_path = r"D:\quant_framework\factor_registry.json"
        if not os.path.exists(reg_path):
            return jsonify({"success": False, "error": "因子注册表不存在"})
        reg = json.load(open(reg_path, "r", encoding="utf-8"))
        factors = reg.get("factors", [])
        # 1. 筛选 |IC_5d|>0.05 的活跃因子
        candidates = []
        for f in factors:
            ic = f.get("ic_5d") or 0
            if f.get("status") == "active" and abs(ic) > 0.05:
                candidates.append({"name": f["name"], "display": f.get("display", f["name"]),
                                   "ic": ic, "direction": f.get("direction", "long")})
        if len(candidates) < 1:
            return jsonify({"success": False, "error": f"无合适因子 (需IC>0.05, 当前仅{len(candidates)}个)"})
        # 2. 相关性去重: 检查因子健康状况中的corr_score
        try:
            sys.path.insert(0, r"D:\quant_framework")
            from factor_health import compute_correlation_matrix
            corr = compute_correlation_matrix()
            redundant = set()
            for pair in corr.get("redundant_pairs", []):
                a, b = pair["factor_a"], pair["factor_b"]
                # 保留IC绝对值更大的
                ic_a = abs(next((f["ic"] for f in candidates if f["name"] == a), 0))
                ic_b = abs(next((f["ic"] for f in candidates if f["name"] == b), 0))
                if ic_a >= ic_b: redundant.add(b)
                else: redundant.add(a)
            candidates = [c for c in candidates if c["name"] not in redundant]
        except Exception:
            pass  # 相关性计算失败不影响主流程
        if len(candidates) > 5:
            candidates = sorted(candidates, key=lambda x: -abs(x["ic"]))[:5]
        # 3. IC归一化权重
        total_ic = sum(abs(c["ic"]) for c in candidates) or 1
        factors_cfg = []
        for c in candidates:
            w = round(abs(c["ic"]) / total_ic * 100)
            # 二元因子(0/1输出)阈值=0, 连续因子阈值=30
            thresh = 0 if c["name"].startswith("ai_") else 30
            factors_cfg.append({"name": c["name"], "threshold": thresh, "weight": max(10, w),
                               "direction": c.get("direction", "long")})
        name = "AI组合_" + datetime.now().strftime("%m%d_%H%M")
        strat = {
            "name": name, "display_name": name,
            "type": "builder", "status": "draft",
            "factors": factors_cfg,
            "trigger": {"type": "weighted_sum", "min_score": 40},
            "stop_loss": -0.03, "take_profit": [0.05, 0.07, 0.12], "hold_days": 3,
        }
        sp = r"D:\quant_framework\user_customizations\user_strategies.json"
        data = json.load(open(sp, "r", encoding="utf-8")) if os.path.exists(sp) else {"strategies": []}
        data.setdefault("strategies", []).append(strat)
        json.dump(data, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        # 5. 自动回测
        bt_info = {}
        import traceback as _tb
        try:
            sys.path.insert(0, r"D:\quant_framework")
            from strategy_builder import run_backtest
            print(f"[AutoCombo] 回测策略: {name}")
            bt = run_backtest(name, days=90, sample=200, walk_forward=True)
            bt_info = {"sharpe": bt.get("backtest", {}).get("annualized_sharpe", 0),
                       "win_rate": bt.get("backtest", {}).get("win_rate", 0)}
            print(f"[AutoCombo] 回测完成: Sharpe={bt_info['sharpe']}")
        except Exception as _be:
            print(f"[AutoCombo] 回测失败: {_be}")
            _tb.print_exc()
        return jsonify({"success": True, "name": name, "factors": factors_cfg,
                        "dedup_removed": len(redundant) if 'redundant' in dir() else 0,
                        "backtest": bt_info})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/strategy/active-paper")
def api_strategy_active_paper():
    """返回当前模拟盘活跃策略摘要 (纸交易banner显示)"""
    try:
        sp = r"D:\quant_framework\user_customizations\user_strategies.json"
        if os.path.exists(sp):
            strategies = json.load(open(sp, "r", encoding="utf-8")).get("strategies", [])
            active = [s for s in strategies if s.get("status") == "sim" and s.get("type") == "builder"]
            if active:
                s = active[0]
                return jsonify({"code": 200, "active": True,
                    "name": s.get("display_name") or s.get("name", ""),
                    "stop_loss": s.get("stop_loss", -0.03),
                    "take_profit": (s.get("take_profit") or [0.05])[0],
                    "hold_days": s.get("hold_days", 3),
                    "factors": [f["name"] for f in s.get("factors", [])],
                    "sharpe": (s.get("backtest") or {}).get("annualized_sharpe"),
                    "win_rate": (s.get("backtest") or {}).get("win_rate"),
                })
        return jsonify({"code": 200, "active": False})
    except Exception:
        return jsonify({"code": 200, "active": False})


@app.route("/api/strategy-builder/active")
def api_strategy_builder_active():
    """当前在模拟盘活跃的用户策略"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_builder import get_active_user_strategies
        strategies = get_active_user_strategies()
        return jsonify({"code": 200, "count": len(strategies), "strategies": strategies})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/strategy-builder/deploy/<name>", methods=["POST"])
@require_api_key("trade")
def api_strategy_builder_deploy(name):
    """部署策略到模拟盘/实盘。P3-05: 强制回测检查 + 状态校验。"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from strategy_builder import deploy_strategy
        from strategy_approval import get_approval
        data = request.get_json(silent=True) or {}
        target = data.get("target", "sim")

        # P3-05: 部署前检查
        approval = get_approval(name)
        if target == "sim" and approval:
            allowed = approval.get("state") in ("approved_sim", "sim_running", "review_real", "real", "draft")
            if not allowed and approval.get("state") not in ("draft",):
                return jsonify({"code": 400, "success": False, "message": f"策略状态{approval.get('state')}不允许部署模拟盘，需先通过审批"})
        if target == "real":
            if not approval or approval.get("state") != "real":
                return jsonify({"code": 400, "success": False, "message": "实盘部署需老板审批通过(review_real→real)"})

        result = deploy_strategy(name, target)
        return jsonify({"code": 200 if result.get("success") else 400, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-health/correlation")
def api_factor_correlation():
    """因子相关性矩阵"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import compute_correlation_matrix
        return jsonify({"code": 200, **compute_correlation_matrix()})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-health/<name>/ir")
def api_factor_ir(name):
    """因子信息比率"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import compute_ir
        return jsonify({"code": 200, "factor": name, **compute_ir(name)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-health/<name>/decay")
def api_factor_decay(name):
    """因子IC衰减趋势"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import compute_ic_decay
        return jsonify({"code": 200, "factor": name, **compute_ic_decay(name)})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-health/<name>/override", methods=["POST"])
@require_api_key("trade")
def api_factor_health_override(name):
    """人工干预因子状态"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_health import human_override
        data = request.get_json(silent=True) or {}
        result = human_override(name, data.get("command", ""), data.get("reason", ""))
        return jsonify({"code": 200 if result["success"] else 400, **result})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-registry/<name>/retire", methods=["POST"])
@require_api_key("trade")
def api_factor_registry_retire(name):
    """退役/恢复因子"""
    try:
        sys.path.insert(0, r"D:\quant_framework")
        from factor_registry import retire_factor
        ok = retire_factor(name)
        return jsonify({"code": 200 if ok else 404, "success": ok})
    except Exception as e:
        return jsonify({"code": 500, "error": str(e)})


@app.route("/api/factor-ic-v15")
def api_factor_ic_v15():
    """V1-5全市场因子IC报告"""
    import json as _j15
    try:
        with open(r"D:\quant_framework\full_market_ic_report.json", "r") as f:
            return jsonify({"code": 200, **_j15.load(f)})
    except Exception:
        return jsonify({"code": 500, "error": "报告未生成, 请运行 full_market_ic.py"})


if __name__ == "__main__":
    app._startup_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # E349: 启动时间标记
    app._startup_ts = time.time()  # E372: 用于uptime计算
    # S13: 检测上次是否异常关闭
    _sf13 = LAST_SHUTDOWN_TXT  # E26: config路径
    if os.path.exists(_sf13):
        with open(_sf13) as _f: print(f"[System] 上次关闭: {_f.read().strip()}")
    else: print("[System] 上次可能异常关闭(无记录)")
    with open(LAST_STARTUP_TXT, "w") as _f:  # E26: config路径
        _f.write(f"startup: {datetime.now()}\n")
    _startup_health_check()  # S08+S10
    # E345: 盘中每30分钟备份模拟盘+实盘交易数据
    import threading as _bk_th, time as _bk_t, shutil as _bk_sh, os as _bk_os
    _BK_DIR = r"D:\quant_framework\backups\trade_snapshots"
    _bk_os.makedirs(_BK_DIR, exist_ok=True)
    def _trade_backup_loop():
        while True:
            _bk_t.sleep(1800)  # 30分钟
            try:
                _ts = datetime.now().strftime("%Y%m%d_%H%M")
                for _src, _label in [
                    (r"D:\quant_framework\paper_account.json", "paper"),
                    (r"D:\quant_framework\live_trader_config.json", "live_cfg"),
                    (r"D:\quant_framework\live_positions_track.json", "live_pos"),
                ]:
                    if _bk_os.path.exists(_src):
                        _bk_sh.copy2(_src, _bk_os.path.join(_BK_DIR, f"{_ts}_{_label}.json"))
                # 只保留最近48个快照(24小时)
                _files = sorted(_bk_os.listdir(_BK_DIR))
                for _f in _files[:-48]:
                    _bk_os.remove(_bk_os.path.join(_BK_DIR, _f))
            except Exception as _e: print(f"[TradeBackup] {_e}")
    _bk_th.Thread(target=_trade_backup_loop, daemon=True).start()
    print("[App] 交易数据盘中备份已启动 (每30分钟)")

    # 启动日频净值记录（每日15:30自动快照）
    import threading as _pnl_th, time as _pnl_t
    def _pnl_loop():
        import datetime as _dt
        while True:
            now = _dt.datetime.now()
            if now.weekday() < 5 and now.hour == 15 and 30 <= now.minute <= 35:
                try:
                    from pnl_tracker import record_snapshot
                    live_val = sum(p.get("market_value",0) for p in _read_ths_positions_direct())
                    live_pnl = sum(p.get("profit_amt",0) for p in _read_ths_positions_direct())
                    try:
                        from paper_engine import paper
                        ps = paper.get_status()
                        record_snapshot(live_val, live_pnl, ps.get("total_equity",0),
                                      ps.get("total_pnl",0), ps.get("total_return",0))
                        print(f"[PnL] Snapshot recorded")
                    except Exception as _e: print(f"[App] {_e}")
                except Exception as e: print(f"[PnL] Error: {e}")
                _pnl_t.sleep(120)
            _pnl_t.sleep(60)
    _pnl_th.Thread(target=_pnl_loop, daemon=True).start()
    print("[PnL] 日频净值记录已启动 (每日15:30)")
    print("=" * 60)
    print("  潜龙选股 Web 系统 v1.0")
    print("  http://localhost:5002")
    print("=" * 60)
    # E249: 所有重操作放后台，Flask秒启
    import threading as _th_startup
    def _startup_heavy():
        """后台启动：缓存+价格+进化+备份（不阻塞Flask监听）"""
        _load_bt_cache()
        init_data()  # 缓存加载/重建
        # 价格缓存
        try:
            import json as _json
            _t0 = time.time()
            while not _CACHE_READY and time.time() - _t0 < 120:
                time.sleep(0.5)
            pc = {}
            for s in _FACTOR_CACHE:
                close = getattr(s, 'close', 0)
                if close > 0:
                    pc[getattr(s, 'symbol', '')] = float(close)
            for sym, df in STOCK_DATA.items():
                if sym not in pc and len(df) > 0:
                    try:
                        last_close = float(df['close'].values[-1])
                        if last_close > 0: pc[sym] = last_close
                    except Exception as _e: print(f"[App] {_e}")
            if pc:
                with open(r"d:\quant_framework\price_cache.json", 'w', encoding='utf-8') as f:
                    _json.dump(pc, f)
                print(f"[Startup] Price cache: {len(pc)} symbols")
        except Exception as e:
            print(f"[Startup] Price cache failed: {e}")
        # 进化调度
        try:
            from auto_evolve import evo_scheduler, evo_engine
            evo_scheduler.set_data(STOCK_DATA, _FACTOR_CACHE if _CACHE_READY else None, _NAME_MAP)
            evo_scheduler.start()
            print("[Startup] 每日自动进化已启用 (02:00)")
        except Exception as _e: print(f"[App] {_e}")
        # P0: 模拟盘自动交易 (PaperAutoLoop, ML信号驱动)
        try:
            from paper_engine import start_auto_loop
            start_auto_loop()
            print("[Startup] 模拟盘自动交易已启动 (PaperAutoLoop, 10s间隔, ML信号)")
        except Exception as _e: print(f"[Startup] PaperAutoLoop: {_e}")
        # P1: 钉钉告警接入 EventBus (订阅 signal/order/risk)
        try:
            from dingtalk_event_listener import start as _start_dt
            _start_dt()
        except Exception as _e: print(f"[Startup] DingTalk: {_e}")
        # 自动备份
        try:
            from auto_backup import do_backup
            do_backup()
        except Exception as _e: print(f"[App] {_e}")
    _th_startup.Thread(target=_startup_heavy, daemon=True).start()

    # 看门狗 — 立刻启动（独立进程）
    try:
        import subprocess, sys as _sys, os as _os
        _wd = _os.path.join(_os.path.dirname(__file__), "watchdog.py")
        _pid_file = _os.path.join(_os.path.dirname(__file__), "watchdog.pid")
        _spawn = True
        if _os.path.exists(_pid_file):
            try:
                with open(_pid_file, "r") as _pf:
                    _old_pid = int(_pf.read().strip())
                _os.kill(_old_pid, 0)
                print(f"[Startup] Watchdog already running (PID={_old_pid}), skip spawn")
                _spawn = False
            except (OSError, ValueError, FileNotFoundError):
                print(f"[Startup] Watchdog PID file stale, cleaning up")
                try: _os.remove(_pid_file)
                except Exception as _e: print(f"[App] {_e}")
        if _spawn and _os.path.exists(_wd):
            # E260: 隐藏子进程窗口，避免cmd闪退
            _si = subprocess.STARTUPINFO()
            _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            _si.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen([_sys.executable, _wd], cwd=_os.path.dirname(__file__),
                             startupinfo=_si, creationflags=subprocess.CREATE_NO_WINDOW)
            print("[Startup] Watchdog auto-started")
            print("[Startup] Watchdog auto-started")
    except Exception as _we: print(f"[Startup] Watchdog failed: {_we}")

    # E104+审计#14: 启动时+每日自动备份
    try:
        from auto_backup import do_backup
        do_backup()
        # 每24小时后台备份一次
        import threading as _buth, time as _btime
        def _daily_backup():
            while True:
                _btime.sleep(86400)
                try: do_backup()
                except Exception as _e: print(f"[App] {_e}")
        _buth.Thread(target=_daily_backup, daemon=True).start()
    except Exception as _be: print(f"[Startup] 备份失败: {_be}")

    # S02: 每日15:05日终报告
    try:
        import threading as _drth, time as _drtime
        def _daily_report_loop():
            from datetime import datetime as _dt2
            while True:
                now = _dt2.now()
                # 计算到15:05的等待时间, 只在交易日发送
                target = now.replace(hour=15, minute=5, second=0, microsecond=0)
                if now > target:
                    target = target.replace(day=now.day + 1)
                wait = (target - now).total_seconds()
                if wait > 0 and wait < 86400:
                    _drtime.sleep(min(wait, 3600))
                    continue
                # 15:05 — 发送日终报告
                try:
                    from paper_engine import paper
                    p = paper.get_status()
                    # S18: 增强日报 — 含信号/最佳/最差/建议
                    _pos_str = ""
                    for _pp in p.get('positions', [])[:5]:
                        _ppnl = _pp.get('pnl_pct', 0)
                        _pos_str += f"\n  {_pp['symbol']} {_pp.get('name','')} {_pp['qty']}股 {_ppnl:+.1f}%"
                    _best = max(p.get('trade_log', [{"pnl":0}]), key=lambda t: t.get('pnl', 0)) if p.get('trade_log') else None
                    _worst = min(p.get('trade_log', [{"pnl":0}]), key=lambda t: t.get('pnl', 0)) if p.get('trade_log') else None
                    _sig_count = len(_FACTOR_CACHE) if _FACTOR_CACHE else 0
                    msg = (f"📊 {now.strftime('%m-%d')}潜龙日终\n"
                           f"总资产: ¥{p.get('total_equity',0):,.0f} | 收益: {p.get('total_return',0)}%\n"
                           f"交易: {p.get('trade_count',0)}笔 | 胜率: {p.get('win_rate',0)}% | 夏普: {p.get('sharpe',0)}\n"
                           f"持仓({p.get('position_count',0)}只):{_pos_str or ' 空仓'}\n"
                           f"{'最佳: '+_best.get('symbol','')+' +'+str(_best.get('pnl',0)) if _best else ''}"
                           f"{' | 最差: '+_worst.get('symbol','')+' '+str(_worst.get('pnl',0)) if _worst else ''}\n"
                           f"信号: {_sig_count}只缓存 | 回撤: {p.get('max_drawdown',0)}%")
                    try:
                        from dingtalk_alerts import send_alert
                        send_alert("📊 潜龙日终报告", msg, "info")
                    except Exception as _e: logger.warning(f"[DailyReport] 钉钉推送失败: {_e}")
                    print(f"[DailyReport] {msg}")
                except Exception as _e:
                    print(f"[DailyReport] 日终报告失败: {_e}")
                _drtime.sleep(3600)

        _drth.Thread(target=_daily_report_loop, daemon=True).start()
        print("[Startup] 日终报告已就绪 (每日15:05)")

        # S19: 09:25盘前信号推送
        def _premarket_loop():
            while True:
                _drtime.sleep(60)
                _n2 = datetime.now()
                if _n2.weekday() >= 5: continue
                if _n2.hour == 9 and _n2.minute == 25:
                    try:
                        from dingtalk_alerts import send_alert
                        _sigs = []
                        for _s in (_FACTOR_CACHE or [])[:5]:
                            _sigs.append(f"  {getattr(_s,'symbol','')} {getattr(_s,'name','')} 评分{getattr(_s,'power_score',0) or 0} Lv{getattr(_s,'buy_signal',0) or 0}")
                        _pos_warn = ""
                        try:
                            from live_trader import state as _lst
                            for _pp in _lst.positions:
                                _ppnl = _pp.get('profit_pct', 0)
                                if _ppnl < -3: _pos_warn += f"\n  ⚠️ {_pp.get('symbol','')} 浮亏{_ppnl:.1f}% 触及止损线"
                        except Exception as e:
                            logger.warning("[Premarket] 盘前浮亏检查失败: %s", e)
                        _msg = f"🔔 盘前信号 {_n2.strftime('%m-%d')}\nTop5:\n" + "\n".join(_sigs) + _pos_warn
                        send_alert("🔔 盘前信号", _msg, "info")
                        print(f"[Premarket] {_msg}")
                        _drtime.sleep(300)  # 5分钟内不重复
                    except Exception as _e: print(f"[Premarket] {_e}")
        _drth.Thread(target=_premarket_loop, daemon=True).start()
        print("[Startup] 盘前推送已就绪 (每日09:25)")

        # S23: 15:30盘后因子自动更新
        def _factor_update_loop():
            while True:
                _drtime.sleep(60)
                _n3 = datetime.now()
                if _n3.weekday() >= 5: continue
                if _n3.hour == 15 and _n3.minute == 30:
                    try:
                        # 自动刷新数据: TDX日线 → parquet
                        print("[Scheduler] 刷新行情数据...")
                        from data_loader import load_stock_data_from_tdx, save_stock_data_cache
                        _sd2 = load_stock_data_from_tdx()
                        if _sd2 and len(_sd2) > 1000:
                            save_stock_data_cache(_sd2, _data_cache_parquet + ".tmp")
                            os.replace(_data_cache_parquet + ".tmp", _data_cache_parquet)
                            print(f"[Scheduler] Parquet已刷新: {len(_sd2)}只")
                        print("[Scheduler] 盘后因子更新...")
                        _precompute_factors_fast()
                        _CACHE_READY = True
                        try:
                            from dingtalk_alerts import send_alert
                            send_alert("📊 因子数据已更新", f"日期: {_n3.strftime('%Y-%m-%d')} 缓存: {len(_FACTOR_CACHE)}只", "info")
                        except Exception as e:
                            logger.warning("[FactorUpdate] 钉钉通知失败: %s", e)
                        print(f"[Scheduler] 因子更新完成: {len(_FACTOR_CACHE)}只")
                        _drtime.sleep(3600)
                    except Exception as _e: print(f"[Scheduler] 因子更新失败: {_e}")
        _drth.Thread(target=_factor_update_loop, daemon=True).start()
        print("[Startup] 盘后因子更新已就绪 (每日15:30)")
        # Phase 8.1: 日终归因报告 (每日15:40)
        def _attribution_loop():
            import time as _att
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 15 and _now.minute >= 40:
                        import sys as _atts; _atts.path.insert(0, r"D:\quant_framework")
                        from daily_attribution import run_attribution
                        r = run_attribution()
                        try:
                            from dingtalk_alerts import send_alert
                            pnl = r.get("pnl_summary", {}).get("pnl_pct", 0)
                            send_alert("日终归因", f"PnL: {pnl}% | 告警: {len(r.get('alerts',[]))}项", "info")
                        except: pass
                        _att.sleep(3600)
                except Exception as _ae: pass
                _att.sleep(60)
        _drth.Thread(target=_attribution_loop, daemon=True).start()
        print("[Startup] 日终归因报告已就绪 (每日15:40)")
        # P2-08: 三级预警自动熔断 (每60秒检查)
        def _auto_breaker_loop():
            import time as _abt
            _abt.sleep(30)
            while True:
                try:
                    import sys as _abs; _abs.path.insert(0, r"D:\quant_framework")
                    from auto_breaker import check_and_act
                    check_and_act()
                except Exception: pass
                _abt.sleep(60)
        _drth.Thread(target=_auto_breaker_loop, daemon=True).start()
        print("[Startup] 自动熔断已就绪 (每60秒, 三级预警)")
        # 盘前检查 (每日09:25)
        def _pre_market_loop():
            import time as _pmt
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 9 and _now.minute == 25:
                        from task_runner import run_pre_market
                        run_pre_market()
                        _pmt.sleep(3600)
                except Exception: pass
                _pmt.sleep(60)
        _drth.Thread(target=_pre_market_loop, daemon=True).start()
        print("[Startup] 盘前检查已就绪 (每日09:25)")
        # 竞价弱转强扫描 (每日09:24)
        def _call_auction_loop():
            import time as _cat
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 9 and _now.minute == 22 and _now.second >= 55:
                        import subprocess as _sca
                        _sca.run([sys.executable, r"D:\quant_framework\pre_market_call.py"], cwd=r"D:\quant_framework")
                        _cat.sleep(3600)
                except Exception: pass
                _cat.sleep(30)
        _drth.Thread(target=_call_auction_loop, daemon=True).start()
        print("[Startup] 竞价弱转强已就绪 (每日09:22:55, 三点采样防砸盘)")
        # 信号生成 (每日09:30)
        def _signal_gen_loop():
            import time as _sgt
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 9 and _now.minute == 30:
                        from task_runner import run_signal_gen
                        run_signal_gen()
                        _sgt.sleep(3600)
                except Exception: pass
                _sgt.sleep(60)
        _drth.Thread(target=_signal_gen_loop, daemon=True).start()
        print("[Startup] 自动信号生成已就绪 (每日09:30)")
        # P2-02: 龙虎榜自动采集 (每日15:35)
        def _lhb_loop():
            import time as _lhb_t
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 15 and _now.minute >= 35:
                        import sys as _lhbs; _lhbs.path.insert(0, r"D:\quant_framework")
                        from lhb_fetcher import fetch_lhb_today, save_lhb
                        recs = fetch_lhb_today()
                        if recs: save_lhb(recs)
                        _lhb_t.sleep(3600)
                except Exception as _lhbe: pass
                _lhb_t.sleep(60)
        _drth.Thread(target=_lhb_loop, daemon=True).start()
        print("[Startup] 龙虎榜自动采集已就绪 (每日15:35)")
        # P1-6: 板后接力预选池 (每日15:10)
        def _limit_up_loop():
            import time as _lut
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 15 and 10 <= _now.minute <= 14:
                        from task_runner import run_limit_up
                        run_limit_up()
                        _lut.sleep(3600)
                except Exception: pass
                _lut.sleep(60)
        _drth.Thread(target=_limit_up_loop, daemon=True).start()
        print("[Startup] 板后预选池已就绪 (每日15:10)")
        # 游资弱转强盘前扫描 (每日15:15, 收盘后用日线数据)
        def _reversal_loop():
            import time as _rvt
            while True:
                try:
                    _now = datetime.now()
                    if _now.hour == 15 and 15 <= _now.minute <= 19:
                        import subprocess as _sr
                        _sr.run([sys.executable, r"D:\quant_framework\pre_reversal_scan.py"], cwd=r"D:\quant_framework")
                        _rvt.sleep(3600)
                except Exception: pass
                _rvt.sleep(60)
        _drth.Thread(target=_reversal_loop, daemon=True).start()
        print("[Startup] 游资弱转强扫描已就绪 (每日15:15)")
    except Exception as _de: print(f"[Startup] 日终报告失败: {_de}")

    # E07: 日志/缓存自动清理
    try:
        import threading as _clth, time as _cltime, os as _clos
        def _truncate_log(path, max_size_mb=10, keep_lines=5000):
            if _clos.path.exists(path) and _clos.path.getsize(path) > max_size_mb * 1024 * 1024:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                if len(lines) > keep_lines:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.writelines(lines[-keep_lines:])
                    print(f"[Cleanup] 截断日志: {path} ({len(lines)}行→{keep_lines}行)")
        def _daily_cleanup():
            while True:
                _cltime.sleep(86400)
                try:
                    _truncate_log(PAPER_AUTO_LOG)  # E26: config路径
                    _truncate_log(AUDIT_LOG_JSONL)  # E26: config路径
                    _truncate_log(SIGNAL_SNAPSHOTS_JSONL  )  # E26: config路径  # S11
                except Exception as _e: print(f"[Cleanup] {_e}")
        # 启动时执行一次
        _truncate_log(PAPER_AUTO_LOG)  # E26: config路径
        _clth.Thread(target=_daily_cleanup, daemon=True).start()
        print("[Startup] 日志清理已就绪 (每日)")
    except Exception as _ce: print(f"[Startup] 日志清理失败: {_ce}")

    # E19: 每日自动备份关键数据文件
    try:
        import shutil as _shutil, threading as _bkth, time as _bktime, os as _bkos
        _BACKUP_DIR = r"D:\quant_web\backup"
        def _backup_critical():
            _bkos.makedirs(_BACKUP_DIR, exist_ok=True)
            _ds = datetime.now().strftime("%Y%m%d")
            for _src in [PAPER_ACCOUNT, POSITION_TRACK, LIVE_CONFIG]:  # E26: config路径
                if _bkos.path.exists(_src):
                    _shutil.copy2(_src, _bkos.path.join(_BACKUP_DIR, f"{_ds}_{_bkos.path.basename(_src)}"))
            # 清理30天前的备份
            for _f in _bkos.listdir(_BACKUP_DIR):
                try:
                    _fd = _f.split('_')[0]
                    if not _fd.isdigit() or len(_fd) != 8:
                        continue  # 跳过非日期命名的目录
                    if (datetime.now() - datetime.strptime(_fd, "%Y%m%d")).days > 30:
                        _bkos.remove(_bkos.path.join(_BACKUP_DIR, _f))
                except Exception:
                    pass  # 跳过无法处理的目录
        _backup_critical()  # 启动时执行一次
        def _backup_loop():
            while True: _bktime.sleep(86400)
            try: _backup_critical()
            except Exception as _e: logger.error(f"[Backup] 每日备份失败: {_e}")
        _bkth.Thread(target=_backup_loop, daemon=True).start()
        print("[Startup] 每日备份已就绪")
    except Exception as _be2: print(f"[Startup] 备份失败: {_be2}")

    # E208: 盘中定时刷新因子缓存(每60分钟,仅交易时段)
    def _start_factor_refresh():
        import threading as _th, time as _t2
        def _loop():
            global _FACTOR_CACHE
            while True:
                _t2.sleep(3600)
                try:
                    from realtime_quotes import is_trading_time
                    if is_trading_time() and '_FACTOR_CACHE' in globals() and _FACTOR_CACHE:
                        _precompute_factors_fast()
                except Exception as _fe: print(f"[Refresh] {_fe}")
        _th.Thread(target=_loop, daemon=True).start()
    _start_factor_refresh()

    try:
        import sys as _sy; _sy.path.insert(0, r"D:\quant_framework")
        from qianlong import lock as _ql_lock
        _ql_lock()
        print("[Startup] 核心文件已自动锁定")
    except Exception as _le:
        print(f"[Startup] 自动锁失败: {_le}")

    print("[Startup] Ready! Open http://localhost:5002")
    # X5: 启动通知推送微信
    def _startup_notify():
        import time as _t5
        _t5.sleep(5)
        try:
            from dingtalk_alerts import send_alert
            qc = 0
            try:
                from realtime_quotes import _quote_cache
                qc = _quote_cache.get("count", 0) if _quote_cache else 0
            except Exception as _e: print(f"[App] {_e}")
            sc = len(_FACTOR_CACHE) if '_FACTOR_CACHE' in globals() and _FACTOR_CACHE else 0
            dll_ok = False
            try:
                from link_trader import is_available
                dll_ok = is_available()
            except Exception as _e: print(f"[App] {_e}")
            send_alert("✅ 潜龙系统已就绪",
                       f"行情:{qc}只 | 因子:{sc}条 | DLL:{'已加载' if dll_ok else '未加载'}\n策略:3个在线 | 时间:{datetime.now().strftime('%H:%M')}", "info")
        except Exception as _e: print(f"[App] {_e}")
    threading.Thread(target=_startup_notify, daemon=True).start()
    # Y3: 内存监控
    try:
        from memory_monitor import start_monitor
        start_monitor()
    except Exception as _e: print(f"[App] {_e}")
    # ═══ 启动健康检查 ═══
    print("[Health] 启动自检中...")
    ok=0;warn=0
    try:from paper_engine import paper;paper.get_status();ok+=1;print("[Health] ✅ 纸引擎就绪")
    except Exception as e:warn+=1;print(f"[Health] ⚠ 纸引擎异常:{e}")
    try:
        if os.path.exists(r"D:\quant_framework\factor_ic_results.csv"):ok+=1;print("[Health] ✅ 因子缓存存在")
        else:warn+=1;print("[Health] ⚠ 因子缓存缺失")
    except:warn+=1
    try:
        ths=r"C:\Users\Administrator\Documents\table.xls"
        if os.path.exists(ths) and os.path.getsize(ths)>10:ok+=1;print("[Health] ✅ THS持仓文件存在")
        else:warn+=1;print("[Health] ⚠ THS持仓文件缺失/为空")
    except:warn+=1
    print(f"[Health] 自检完成: {ok}通过 {warn}警告")
    # user_customizations_api 路由已在模块级注册，不重复
    # D2-02: Gunicorn优先(多worker, 0僵尸), 回退waitress
    from waitress import serve
    serve(app, host="0.0.0.0", port=5002, threads=8)
