#encoding:gbk
'''
潜龙快速通道策略 v3.0 — 双刀打板 + 双道并行
============================================
审核通道: 所有信号 POST 到潜龙 Flask (记录/通知)
快速通道: enabled=true → passorder() 直接下单 (<20ms)

用法:
  1. QMT客户端 → 策略编辑器 → 新建策略
  2. 粘贴本文件全部内容 (gbk编码)
  3. 设置主图K线为任意股票 (1分钟周期)
  4. 运行

信号体系 (2026-07-18 v3.0):
  竞价抢筹 — 高开>2% + 量比>3
  盘中突破 — 涨>3% + 放量>2x
  尾盘急拉 — 14:30后涨>3%
  打板·一封 — 全市场实时扫板(广度刀): 触板+放量+板块联动+L2盘口确认
  打板·二封 — 日线预选+次日确认(深度刀): 炸板回封+6因子+动态阈值
  板后接力 — 昨涨停+今低开回升
  撬板战法 — 跌停开板+巨量
'''
import json, os, time, traceback, sys
sys.path.insert(0, r"D:\quant_framework")  # 加载signals/评分库

# QMT实时评分函数 (2026-07-13 独立评分方案)
try:
    from signals.daban.realtime import confirm_board as _sig_daban
    from signals.reversal.realtime import confirm_oversold as _sig_oversold
    from signals.reversal.realtime import confirm_weak_to_strong as _sig_wts
    _SIG_READY = True
except Exception:
    _SIG_READY = False
    _sig_daban = _sig_oversold = _sig_wts = None

try:
    import requests
except Exception:
    requests = None

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
CFG_PATH  = r"D:\quant_web\data\qmt_trade_config.json"
FLASK_URL = "http://127.0.0.1:5002/api/qmt/signal"

_daily = {"trades": 0, "pct": 0}
_plan = {}
_plan_mtime = 0
_cooldown = {}
_wts_candidates = []  # 弱转强候选
_wts_confirmed = set()  # 已确认
_first_bar = True  # 诊断
_bd_map = {}  # 连板映射
_daban_candidates = []  # 打板候选
_daban_bought = set()  # 当日已买(同票互锁)
_daban_daily = {"chase": 0, "reseal": 0}  # 分刀日计数
_SECTOR_HOT = set()  # 热点板块(竞价守护→handlebar共享)
_sector_bd_count = {}  # 板块涨停计数(竞价守护→handlebar共享)
_sentiment_stage = "ferment"  # 情绪周期(从Python传入, 默认发酵期)
_advance_ratio = 0.5  # 涨跌比(从Python传入)
_limit_down_count = 0  # 跌停数(从Python传入)
_market_regime = "sideways"  # 市场状态(从Python传入)
_tdx_last_mtime = 0  # TDX信号JSON缓存mtime
_prev_frame = {}  # 弱转强竞价缺口检测
_fast_cache = {}
_bar_history = {}
_fake_check = {}  # 假突破延时验证缓冲区
_tdx_formulas = None
_tdx_fast_seen = set()  # TDX快速通道去重
_tdx_fast_lock = None   # 线程锁


def _load_plan():
    global _plan, _plan_mtime, _fast_cache, _wts_candidates, _bd_map, _daban_candidates
    if not os.path.exists(PLAN_PATH):
        return {}
    try:
        mtime = os.path.getmtime(PLAN_PATH)
        if mtime != _plan_mtime:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                _plan = json.load(f)
            _plan_mtime = mtime
            _build_fast_cache()
            # 加载弱转强候选
            try:
                if os.path.exists(CFG_PATH):
                    _cfg = json.load(open(CFG_PATH, "r", encoding="utf-8"))
                    _wts_candidates = _cfg.get("weak_to_strong_candidates", [])
                    _daban_candidates = _cfg.get("daban_candidates", [])
                    _bd_map = _cfg.get("board_days_map", {})
                    # 情绪数据透传 (generate_signal_table → QMT)
                    global _sentiment_stage, _advance_ratio, _limit_down_count, _market_regime
                    _sentiment_stage = _cfg.get("sentiment_stage", "ferment")
                    _advance_ratio = _cfg.get("advance_ratio", 0.5)
                    _limit_down_count = _cfg.get("limit_down_count", 0)
                    _market_regime = _cfg.get("market_regime", "sideways")
                    if _wts_candidates or _daban_candidates:
                        print(f"[潜龙] 弱转强:{len(_wts_candidates)} 打板:{len(_daban_candidates)} 连板:{len(_bd_map)} 情绪:{_sentiment_stage}")
            except: pass
    except:
        pass
    return _plan


def _build_fast_cache():
    global _fast_cache
    _fast_cache.clear()
    ml_cfg = {}
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                ml_cfg = json.load(f)
        except:
            pass
    # 兼容两种计划结构
    if "stocks" in _plan and isinstance(_plan.get("stocks"), dict) and len(_plan["stocks"]) > 0:
        _stock_src = _plan["stocks"]
    else:
        _stock_src = {k: v for k, v in _plan.items()
                      if not str(k).startswith('_')
                      and not str(k).startswith('global')
                      and isinstance(v, dict)}
    for sym, s in _stock_src.items():
        ml = ml_cfg.get(sym, {})
        best_ml = max(ml.get("lgbm", 0), ml.get("xgb", 0), ml.get("cb", 0))
        _fast_cache[sym] = (
            s.get("enabled", False),
            s.get("max_position_pct", 3),
            s.get("stop_loss", 0),
            s.get("take_profit", 0),
            frozenset(s.get("signal_types", [])),
            best_ml,
            s.get("min_ml_score", 80),
            s.get("industry", ""),
            s.get("time_window", ""),
            s.get("yesterday_volume", 0),
        )


def _ql_to_qmt(sym):
    s = sym.lower()
    if s.startswith('sh'): return s[2:] + '.SH'
    if s.startswith('sz'): return s[2:] + '.SZ'
    if s.startswith('bj'): return s[2:] + '.BJ'
    return sym


def _qmt_to_ql(sym):
    s = sym.upper()
    if '.SH' in s: return 'sh' + s.split('.')[0]
    if '.SZ' in s: return 'sz' + s.split('.')[0]
    if '.BJ' in s: return 'bj' + s.split('.')[0]
    return sym


def _cancel_all_pending(ContextInfo):
    try:
        orders = ContextInfo.get_orders()
        if orders:
            for o in orders:
                try:
                    passorder(24, 1101, ContextInfo.accID, o.stock_code, 0, 0, 0, "潜龙紧急", "", 2)
                except: pass
            print(f"[潜龙] 紧急撤单: {len(orders)}笔")
    except Exception:
        pass


def _calc_shares(pos_pct, price, total_asset):
    if price <= 0:
        return 100
    amount = total_asset * pos_pct / 100.0
    return max(100, int(amount / price / 100) * 100)


def _limit_pct(code):
    """A股涨跌停幅度(板块决定): 主板10% / 科创创业20% / 北交所30%"""
    d = ''.join(c for c in str(code) if c.isdigit())
    if len(d) < 6:
        return 0.10
    if d[:3] == '688' or d[:3] == '300':
        return 0.20
    if d[0] in ('8', '4'):
        return 0.30
    return 0.10
