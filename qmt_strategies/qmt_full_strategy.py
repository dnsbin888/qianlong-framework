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
            s.get("soft_stop_loss_pct", 0),  # ATR自适应软止损%
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

"""打板·一封 check(ctx) — 注册表模式"""
import time

def chase_check(ctx):
    """触板检测: bar.high预筛 + 实时价确认 + 时间/情绪/宽度过滤"""
    _lim = round(ctx['prev_close'] * (1 + ctx['_lp']), 2)
    if ctx['bar_high'] >= _lim * 0.995 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 1.5:
        try:
            _ft = ctx['ContextInfo'].get_full_tick(ctx['qmt_code'])
            _rt = float(_ft.lastPrice) if hasattr(_ft, 'lastPrice') else ctx['price']
        except:
            _rt = ctx['price']
        if _rt >= _lim * 0.995:
            if time.strftime('%H%M') < '1400':
                if ctx.get('_sentiment_stage','ferment') != "retreat":
                    if ctx.get('_advance_ratio',0.5) >= 0.5 and ctx.get('_limit_down_count',0) <= 50:
                        return "打板·一封"
    return None

"""盘中突破 check(ctx) — 注册表模式"""

def breakthrough_check(ctx):
    """涨>3% + 放量>2x, 反转候选需价格>VWAP"""
    if ctx['price'] > ctx['prev_close'] * 1.03 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 2:
        if ctx.get('_is_reversal') and not ctx.get('_above_vwap', True):
            return None
        return "盘中突破"
    return None

"""竞价抢筹 check(ctx)"""

def auction_check(ctx):
    if ctx['open_price'] > ctx['prev_close'] * 1.02 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 3:
        if ctx.get('_is_reversal') and not ctx.get('_vol_ok'):
            return None
        return "竞价抢筹"
    return None

"""尾盘急拉 check(ctx)"""
import time

def tail_rush_check(ctx):
    if time.strftime('%H%M') > '1430' and (ctx['price'] / max(ctx['prev_close'], 0.01) - 1) > 0.03:
        return "尾盘急拉"
    return None

"""板后接力 check(ctx)"""

def continuation_check(ctx):
    if ctx['_lp'] != 0.10: return None
    try:
        # QMT get_market_data(fields, stock_list, start, end, skip, period, dividend, count)
        mk3 = ctx['ContextInfo'].get_market_data(
            ['close','open','volume'], [ctx['qmt_code']],
            '', '', True, '', '', 3)
        if mk3 and ctx['qmt_code'] in mk3:
            d = mk3[ctx['qmt_code']]
            closes = list(d.get('close', [])) if hasattr(d,'get') else []
            opens = list(d.get('open', [])) if hasattr(d,'get') else []
            vols = list(d.get('volume', [])) if hasattr(d,'get') else []
            if len(closes) >= 2:
                yest_close = float(closes[-1])
                yest_open = float(opens[-1])
                if yest_close >= round(yest_open * 1.095, 2):
                    if float(opens[0]) < yest_close and ctx['price'] > float(opens[0]) and ctx['price'] > yest_close * 0.98:
                        avg_vol = sum(vols) / len(vols) if vols else ctx['volume']
                        if ctx['volume'] > avg_vol * 1.3:
                            return "板后接力"
    except: pass
    return None

"""撬板战法 check(ctx)"""

def floor_check(ctx):
    if ctx['_lp'] != 0.10: return None
    limit_down = round(ctx['prev_close'] * (1 - ctx['_lp']), 2)
    if ctx['price'] <= limit_down * 1.01 and ctx['prev_vol'] > 0 and ctx['volume'] > ctx['prev_vol'] * 5:
        if ctx['open_price'] <= limit_down * 1.005 and ctx['price'] > limit_down * 1.005:
            return "撬板战法"
    return None


def init(ContextInfo):
    print("[潜龙] init() 开始...")
    ContextInfo.accID = '8890695045'

    plan = _load_plan()
    limits = plan.get("global_limits", {})

    # 兼容两种结构: {"stocks":{...}} 或 标的直接在顶层
    if "stocks" in plan and isinstance(plan.get("stocks"), dict) and len(plan["stocks"]) > 0:
        stock_dict = plan["stocks"]
    else:
        stock_dict = {k: v for k, v in plan.items()
                      if not str(k).startswith('_')
                      and not str(k).startswith('global')
                      and isinstance(v, dict)}

    trade_list = []
    for sym, cfg in stock_dict.items():
        code = _ql_to_qmt(sym)
        if code:
            trade_list.append(code)

    ContextInfo.trade_code_list = trade_list
    ContextInfo.set_universe(trade_list)
    _enabled_n = sum(1 for k,v in stock_dict.items() if v.get('enabled'))
    print(f"[潜龙] 监控{len(trade_list)}只标的 (启用{_enabled_n}只)")
    print("=" * 50)
    print(f"  潜龙快速通道 v2.0")
    print(f"  监控: {len(trade_list)} 只, 自动: {_enabled_n} 只")
    print(f"  日限额: {limits.get('max_daily_trades', 5)} 笔")
    print(f"  熔断: {'是' if limits.get('circuit_breaker') else '否'}")
    print("=" * 50)
    print("[潜龙] init() 完成, 等待K线...")

    # 竞价守护: 9:22:56-9:25:00 轮询 → 实盘=passorder, 模拟=POST到Flask
    def _sim_order(sym, side, price, qty, reason=""):
        try:
            import urllib.request as _ur
            d = json.dumps({"symbol": sym, "side": side, "price": price,
                           "qty": qty, "reason": reason}).encode()
            r = _ur.Request("http://127.0.0.1:5002/api/paper-trade/order",
                           data=d, headers={"Content-Type": "application/json"})
            _ur.urlopen(r, timeout=5)
        except Exception: pass

    def _auction_watch():
        import time as _atw
        _plan2 = {}
        _refreshed_920 = _refreshed_2256 = False
        _prev_frame = {}  # 缺口检测: {sym: {price, vol}} 记录上一帧
        _five_frame = {}  # 五帧记录: {A:9:15, B:9:18, C:9:19:30, D:9:19:57, E:9:20:01}
        _cancel_risk = False  # 撤单标记
        while True:
            _now = _atw.strftime('%H%M%S')
            if _now > '092500': break
            # ══ 私募标准: 三点刷新法 (9:20笼子收网→9:22:56监控窗口→9:25确认) ══
            if _now >= '092000' and not _refreshed_920:
                _load_plan(); _plan2 = _plan; _refreshed_920 = True
                print(f"[竞价] 9:20刷新, {len(_plan2.get('stocks',{}))}只标的")
            elif _now >= '092256' and not _refreshed_2256:
                _load_plan(); _plan2 = _plan; _refreshed_2256 = True
                print(f"[竞价] 9:22:56刷新, {len(_plan2.get('stocks',{}))}只标的")
            # ══ 五帧快照 (撤单检测: 9:15/18/19:30/57→9:20:01) ══
            try:
                if _now >= '091500' and 'A' not in _five_frame:
                    _five_frame['A'] = ContextInfo.get_full_tick([])
                elif _now >= '091800' and 'B' not in _five_frame:
                    _five_frame['B'] = ContextInfo.get_full_tick([])
                elif _now >= '091930' and 'C' not in _five_frame:
                    _five_frame['C'] = ContextInfo.get_full_tick([])
                elif _now >= '091957' and 'D' not in _five_frame:
                    _five_frame['D'] = ContextInfo.get_full_tick([])
                elif _now >= '092001' and 'E' not in _five_frame:
                    _five_frame['E'] = ContextInfo.get_full_tick([])
                if _five_frame.get('D') and _five_frame.get('E'):
                    _five_d = _five_frame['D']
                    _five_e = _five_frame['E']
                    if isinstance(_five_d, dict) and isinstance(_five_e, dict):
                        _five_pd = float(_five_d.get('lastPrice',0) or 0)
                        _five_pe = float(_five_e.get('lastPrice',0) or 0)
                        _five_vd = float(_five_d.get('volume',0) or 0)
                        _five_ve = float(_five_e.get('volume',0) or 0)
                        if _five_pd > 0 and _five_vd > 0:
                            _five_delta_p = (_five_pe / _five_pd - 1)
                            _five_delta_v = (_five_ve / _five_vd - 1)
                            _cancel_risk = bool(_five_delta_p < -0.01 and _five_delta_v < -0.3)
                            print(f"[竞价] 五帧: D9:19:57→E9:20:01 价变{_five_delta_p*100:.1f}% 量变{_five_delta_v*100:.0f}% {'🔴撤单!' if _cancel_risk else '✅正常'}")
            except Exception as _fe: pass
            try:
                if not _plan2:
                    _plan2 = _plan or json.load(open(PLAN_PATH, encoding='utf-8'))
                _limits = _plan2.get("global_limits", {})
                _is_sim = not _limits.get("qmt_fast_enabled", False)
                _ac = _limits.get("_call_auction_confirmed", 0)
                if _ac > 0:
                    # 板块热度(游资分层晋级率): 1进2率>0才算主线
                    _all_mk = ContextInfo.get_market_data([])
                    _sc_1st, _sc_2nd, _sc_3rd = {}, {}, {}
                    _sector_max, _sector_sum, _sector_n = {}, {}, {}
                    global _SECTOR_HOT
                    _sector_total, _sc_hot = {}, set()
                    # _bd_map已全局加载
                    for _s2, _c2 in _plan2.get("stocks",{}).items():
                        if isinstance(_c2,dict) and _c2.get("enabled"):
                            _ind = _c2.get("industry","")
                            if not _ind: continue
                            _sector_total[_ind] = _sector_total.get(_ind,0) + 1
                            _t = _all_mk.get(_ql_to_qmt(_s2),{})
                            _lp = float(_t.get('lastPrice',0)); _lc = float(_t.get('lastClose',_lp))
                            _chg = (_lp/_lc - 1) if _lc > 0 else 0
                            _sector_max[_ind] = max(_sector_max.get(_ind,-99), _chg)
                            _sector_sum[_ind] = _sector_sum.get(_ind,0) + _chg
                            _sector_n[_ind] = _sector_n.get(_ind,0) + 1
                            _lim = _lc * (1 + _limit_pct(_s2))
                            if _lc > 0 and _lp >= _lim * 0.99:
                                _bd = _bd_map.get(_s2, 0)
                                if _bd >= 3:     _sc_3rd[_ind] = _sc_3rd.get(_ind,0) + 1
                                elif _bd >= 2:   _sc_2nd[_ind] = _sc_2nd.get(_ind,0) + 1
                                else:            _sc_1st[_ind] = _sc_1st.get(_ind,0) + 1
                    _sector_strength = _plan2.get("global_limits",{}).get("_sector_strength",{})
                    for _ind in _sector_total:
                        _n2, _n3 = _sc_2nd.get(_ind,0), _sc_3rd.get(_ind,0)
                        _avg = _sector_sum[_ind]/_sector_n[_ind] if _sector_n.get(_ind,0) > 0 else 0
                        _lpr = _sector_max.get(_ind,0) - _avg  # 龙头溢价
                        _mom = _sector_strength.get(_ind, 0)  # 行业动量(已从Python传入)
                        if _n2 + _n3 > 0 and _lpr > 0.02 and _mom > 0:
                            _sc_hot.add(_ind)
                    _SECTOR_HOT = _sc_hot  # 存全局, handlebar读
                    # 板块涨停计数 (一封/二封共用): {行业: 涨停股数}
                    global _sector_bd_count
                    _sector_bd_count = {}
                    for _ind in _sector_total:
                        _sector_bd_count[_ind] = _sc_1st.get(_ind,0) + _sc_2nd.get(_ind,0) + _sc_3rd.get(_ind,0)
                    for _sym, _cfg in _plan2.get("stocks", {}).items():
                        if isinstance(_cfg, dict) and _cfg.get("enabled") and "竞价弱转强" in _cfg.get("signal_types", []):
                            _ind = _cfg.get("industry","")
                            if _ind and _ind not in _sc_hot:  # 无连板=一日游
                                continue
                            _code = _ql_to_qmt(_sym) if not _is_sim else _sym
                            _pos = _cfg.get("max_position_pct", 2)
                            _yest_close = _cfg.get("close", 0)
                            _auction_price = _cfg.get("auction_price", 0)
                            if _pos > 0 and _yest_close > 0:
                                if _auction_price > 0 and _auction_price < _yest_close * 0.95:
                                    print(f"[竞价] ⚠️ {_sym} 砸盘放弃")
                                    continue
                                _qty = _calc_shares(_pos, _yest_close, 100000)
                                if _is_sim:
                                    _sim_order(_sym, "buy", _yest_close, _qty, "竞价弱转强(模拟)")
                                else:
                                    _limit = round(max(_auction_price, _yest_close) * 1.02, 2)
                                    passorder(23, 1101, ContextInfo.accID, _code, 0,
                                              _limit, _qty, "潜龙竞价", _sym, 2)
                                print(f"[竞价] {'📝模拟' if _is_sim else '✅实盘'} {_sym} 买入{_qty}股")
                                try:  # 推送到前端
                                    import urllib.request as _au; _au.urlopen(_au.Request(FLASK_URL, data=json.dumps({"symbol":_sym,"signal_type":"竞价弱转强·买入","price":round(_yest_close,2),"channel":"fast","enabled":True,"qmt_code":_code,"signal_id":f"ac_{_sym}_{_atw.strftime('%Y%m%d%H%M')}"}).encode(),headers={"Content-Type":"application/json"}),timeout=2)
                                except: pass
                    # 竞价卖出
                    for _sym, _cfg in _plan2.get("stocks", {}).items():
                        if isinstance(_cfg, dict) and _cfg.get("sell_signal"):
                            _code = _ql_to_qmt(_sym) if not _is_sim else _sym
                            _qty = _cfg.get("sell_qty", 0)
                            _price = _cfg.get("auction_price", 0)
                            if _qty > 0 and _price > 0:
                                if _is_sim:
                                    _sim_order(_sym, "sell", _price, _qty, _cfg.get("sell_reason", ""))
                                else:
                                    passorder(24, 1101, ContextInfo.accID, _code, 0,
                                              round(_price * 0.98, 2), _qty, "潜龙竞价卖", _sym, 2)
                                print(f"[竞价] 🔴 {_sym} 卖出{_qty}股 ({_cfg.get('sell_reason','')})")
                                try:  # 推送到前端
                                    import urllib.request as _au2; _au2.urlopen(_au2.Request(FLASK_URL, data=json.dumps({"symbol":_sym,"signal_type":"竞价弱转强·卖出","price":round(_price,2),"channel":"review","enabled":False,"qmt_code":_code,"signal_id":f"acs_{_sym}_{_atw.strftime('%Y%m%d%H%M')}"}).encode(),headers={"Content-Type":"application/json"}),timeout=2)
                                except: pass
                    # ══ 弱转强竞价买点 (9:25, 陈小群: 半仓试错) ══
                    if _SIG_READY and _sig_wts and _wts_candidates:
                        _wts_mk = ContextInfo.get_market_data([])
                        for _wts in sorted(_wts_candidates, key=lambda x: -x.get("score", 0))[:3]:
                            _wts_sym = _wts.get("symbol", "")
                            if _wts_sym in _wts_confirmed: continue
                            _wts_qmt = _ql_to_qmt(_wts_sym)
                            _wts_tick = _wts_mk.get(_wts_qmt, {})
                            _wts_price = float(_wts_tick.get('lastPrice', 0))
                            _wts_prev = float(_wts_tick.get('lastClose', _wts_price))
                            _wts_open = float(_wts_tick.get('open', _wts_price))
                            if _wts_price <= 0: continue
                            # 缺口检测: 最后一秒价量变化
                            _gap_delta = 0  # 价变
                            _gap_vol_delta = 0  # 量变
                            if _wts_sym in _prev_frame:
                                _gap_delta = (_wts_price - _prev_frame[_wts_sym]["price"]) / max(_prev_frame[_wts_sym]["price"], 0.01)
                                _gap_vol_delta = (float(_wts_tick.get('volume',0)) - _prev_frame[_wts_sym]["vol"]) / max(_prev_frame[_wts_sym]["vol"], 1)
                            _prev_frame[_wts_sym] = {"price":_wts_price, "vol":float(_wts_tick.get('volume',0))}
                            _wts_tick_pass = {'lastPrice':_wts_price,'open':_wts_open,
                                'bidVol':float(_wts_tick.get('bid1',_wts_tick.get('bidVol',0))),
                                'gap_delta':_gap_delta, 'gap_vol_delta':_gap_vol_delta,
                                'cancel_risk': _cancel_risk}
                            _wts_score = _sig_wts(_wts_qmt, _wts_tick_pass, _wts_prev, _wts_open)
                            if _wts_score >= 50:
                                _wts_confirmed.add(_wts_sym)
                                _wts_pct = min(4, _wts.get("max_pct", 8) // 2)  # 半仓
                                _wts_qty = _calc_shares(_wts_pct, _wts_price, 100000)
                                if _is_sim:
                                    _sim_order(_wts_sym, "buy", _wts_price, _wts_qty, "弱转强·竞价(半仓)")
                                else:
                                    passorder(23, 1101, ContextInfo.accID, _wts_qmt, 0,
                                              round(_wts_price, 2), _wts_qty, "弱转强竞价", _wts_sym, 2)
                                print(f"[竞价] 🟢 弱转强 {_wts_sym} 半仓{_wts_qty}股 @{_wts_price:.2f}")
                                # POST到Flask
                                try:
                                    import urllib.request as _ur2
                                    from datetime import datetime as _dt2
                                    _wd = json.dumps({"symbol":_wts_sym,"signal_type":"弱转强·竞价确认",
                                        "price":round(_wts_price,2),"channel":"review","enabled":True,
                                        "qmt_code":_wts_qmt,"signal_id":f"wts_{_wts_sym}_{_dt2.now().strftime('%Y%m%d')}"}).encode()
                                    _ur2.urlopen(_ur2.Request(FLASK_URL,data=_wd,headers={"Content-Type":"application/json"}),timeout=3)
                                except Exception as _pe: print(f"[竞价] 推Flask失败: {_pe}")
                                break
                    # ══ 打板竞价抢筹 (9:25, 首笔0.3成) ══
                    if _daban_candidates:
                        _db_mk = ContextInfo.get_market_data([])
                        _db_bought_auction = 0  # 竞价打板日限1笔
                        for _db in sorted(_daban_candidates, key=lambda x: -x.get("score", 0))[:5]:
                            _db_sym = _db.get("symbol", "")
                            if _db_sym in _daban_bought: continue
                            if _db_bought_auction >= 1: break  # 日限1笔
                            _db_qmt = _ql_to_qmt(_db_sym)
                            _db_tick = _db_mk.get(_db_qmt, {})
                            _db_price = float(_db_tick.get('lastPrice', 0))
                            _db_prev = float(_db_tick.get('lastClose', _db_price))
                            _db_open = float(_db_tick.get('open', _db_price))
                            if _db_price <= 0 or _db_prev <= 0: continue
                            # 竞价强度: 高开1%~5% (太低无强度, 太高追高)
                            _db_gap = (_db_price / _db_prev - 1)
                            if _db_gap < 0.01 or _db_gap > 0.05: continue
                            # 板块检查: 无板块热度的跳过
                            _db_ind = _db.get("industry", "")
                            if _db_ind and _SECTOR_HOT and _db_ind not in _SECTOR_HOT: continue
                            # 撤单风险: 有撤单现象的跳过
                            if _cancel_risk: continue
                            # 确认通过 → 0.3成
                            _db_pct = 3.0
                            _db_qty = _calc_shares(_db_pct, _db_price, 100000)
                            if _is_sim:
                                _sim_order(_db_sym, "buy", _db_price, _db_qty, "打板·竞价抢筹")
                            else:
                                passorder(23, 1101, ContextInfo.accID, _db_qmt, 0,
                                          round(_db_price, 2), _db_qty, "打板竞价", _db_sym, 2)
                            _daban_bought.add(_db_sym)
                            _db_bought_auction += 1
                            print(f"[竞价] 🎯 打板抢筹 {_db_sym} 0.3成{_db_qty}股 @{_db_price:.2f} (gap={_db_gap*100:.1f}%)")
                            # POST到Flask
                            try:
                                import urllib.request as _ur3
                                from datetime import datetime as _dt3
                                _dd = json.dumps({"symbol":_db_sym,"signal_type":"打板·竞价抢筹",
                                    "price":round(_db_price,2),"channel":"review","enabled":True,
                                    "qmt_code":_db_qmt,"signal_id":f"db_auction_{_db_sym}_{_dt3.now().strftime('%Y%m%d')}"}).encode()
                                _ur3.urlopen(_ur3.Request(FLASK_URL,data=_dd,headers={"Content-Type":"application/json"}),timeout=3)
                            except Exception as _de: print(f"[竞价] 推Flask失败: {_de}")
                # ── 弱转强竞价 (独立于_ac, 9:25触发) ──
                if _now >= '092500' and _SIG_READY and _sig_wts and _wts_candidates:
                    _wts_mk = ContextInfo.get_market_data([])
                    for _wts in sorted(_wts_candidates, key=lambda x:-x.get("score",0))[:3]:
                        _ws = _wts.get("symbol","")
                        if _ws in _wts_confirmed: continue
                        _wq, _wt = _ql_to_qmt(_ws), _wts_mk.get(_ql_to_qmt(_ws),{})
                        _wp = float(_wt.get('lastPrice',0)); _wpr = float(_wt.get('lastClose',_wp)); _wo = float(_wt.get('open',_wp))
                        if _wp <= 0: continue
                        _gd = (_wp-_prev_frame[_ws]["price"])/max(_prev_frame[_ws]["price"],0.01) if _ws in _prev_frame else 0
                        _gvd = (float(_wt.get('volume',0))-_prev_frame[_ws]["vol"])/max(_prev_frame[_ws]["vol"],1) if _ws in _prev_frame else 0
                        _prev_frame[_ws] = {"price":_wp, "vol":float(_wt.get('volume',0))}
                        _sc = _sig_wts(_wq, {'lastPrice':_wp,'open':_wo,'bidVol':float(_wt.get('bid1',_wt.get('bidVol',0))),'volume':float(_wt.get('volume',0)),'gap_delta':_gd,'gap_vol_delta':_gvd,'cancel_risk':_cancel_risk}, _wpr, _wo)
                        if _sc >= 50:
                            _wts_confirmed.add(_ws)
                            _qty = _calc_shares(min(4,_wts.get("max_pct",8)//2), _wp, 100000)
                            if _is_sim: _sim_order(_ws, "buy", _wp, _qty, "弱转强·竞价(半仓)")
                            else: passorder(23,1101,ContextInfo.accID,_wq,0,round(_wp,2),_qty,"弱转强竞价",_ws,2)
                            print(f"[竞价] 🟢 弱转强={_ws} score={_sc} {_qty}股@{_wp:.2f}")
                            try:
                                import urllib.request as _ur2; from datetime import datetime as _dt2
                                _ur2.urlopen(_ur2.Request(FLASK_URL,data=json.dumps({"symbol":_ws,"signal_type":"弱转强·竞价确认","price":round(_wp,2),"channel":"review","enabled":True,"qmt_code":_wq,"signal_id":f"wts_{_ws}_{_dt2.now().strftime('%Y%m%d')}"}).encode(),headers={"Content-Type":"application/json"}),timeout=3)
                            except Exception as _pe: print(f"[竞价] 推Flask失败: {_pe}")
                            break
                break
            except Exception as _ae: print(f"[竞价] 异常: {_ae}")
            _atw.sleep(1)
    import threading as _th2
    _th2.Thread(target=_auction_watch, daemon=True).start()
    print("[潜龙] 竞价守护已启动 (买入+卖出)")

    # ══ TDX快速通道: 2s轮询 → 跳过handlebar → 极速下单 ══
    global _tdx_fast_seen, _tdx_fast_lock
    _tdx_fast_seen = set()
    _tdx_fast_lock = _th2.Lock()

    def _tdx_fast_loop():
        import time as _tft
        _fast_sig_path = r"D:\quant_web\data\tdx_live_signals.json"
        _fast_cfg_path = r"D:\quant_web\data\tdx_pools_config.json"
        print("[TDX-Fast] 快速通道启动 (2s轮询, target=qmt直接执行)")
        while True:
            _tft.sleep(2)
            try:
                if not os.path.exists(_fast_sig_path): continue
                # 读信号+配置
                _fast_data = json.load(open(_fast_sig_path, encoding="utf-8"))
                _fast_cfg = {}
                if os.path.exists(_fast_cfg_path):
                    _fast_cfg = json.load(open(_fast_cfg_path, encoding="utf-8")).get("pools", {})
                _breaker = limits.get("circuit_breaker", False)
                if _breaker: continue
                if _daily["trades"] >= limits.get("max_daily_trades", 5): continue

                for _pool_name, _pool_info in _fast_data.items():
                    if not isinstance(_pool_info, dict): continue
                    _pool_target = _fast_cfg.get(_pool_name, {}).get("target", "qmt")
                    if _pool_target != "qmt": continue  # 只处理qmt直通
                    _pool_cfg = _fast_cfg.get(_pool_name, {})
                    if not _pool_cfg.get("auto_trade", True): continue
                    _min_ml = _pool_cfg.get("min_ml_score", 60)

                    for _ts in _pool_info.get("_signals", []):
                        _fsym = _ts.get("symbol", "")
                        _fdate = _ts.get("date", "")
                        if _fdate != time.strftime("%Y%m%d"): continue
                        _fkey = f"{_fsym}|{_pool_name}|{_fdate}"
                        with _tdx_fast_lock:
                            if _fkey in _tdx_fast_seen: continue
                            if _fsym in _daban_bought: continue
                            if _daily["trades"] >= limits.get("max_daily_trades", 5): break
                            _tdx_fast_seen.add(_fkey)

                        _fqmt = _ql_to_qmt(_fsym)
                        _fprice = _ts.get("price", 0) or 0
                        try:
                            _fmk = ContextInfo.get_market_data([])
                            _ftk = _fmk.get(_fqmt, {}) if _fmk else {}
                            _flp = float(_ftk.get('lastPrice', 0))
                            if _flp > 0: _fprice = _flp
                        except: pass
                        if _fprice <= 0: continue
                        _fqty = _calc_shares(2, _fprice, 100000)
                        passorder(23, 1101, ContextInfo.accID, _fqmt, 0,
                                  round(_fprice, 2), _fqty, f"TDX-Fast·{_pool_name}", _fsym, 2)
                        _daban_bought.add(_fsym)
                        _daily["trades"] += 1
                        print(f"[TDX-Fast] ⚡ {_fsym} {_pool_name} {_fqty}股@{_fprice:.2f} ({_daily['trades']}/{limits.get('max_daily_trades',5)})")
                    if _daily["trades"] >= limits.get("max_daily_trades", 5): break
            except Exception as _fe:
                pass  # 静默, 不刷屏

    _th2.Thread(target=_tdx_fast_loop, daemon=True).start()
    print("[潜龙] TDX快速通道已启动 (2s轮询, target=qmt直接执行)")


def handlebar(ContextInfo):
    try:
        _handlebar_impl(ContextInfo)
    except Exception as e:
        print(f"[潜龙] handlebar异常: {e}")
        print(traceback.format_exc())


def _handlebar_impl(ContextInfo):
    global _daily, _first_bar, _tdx_last_mtime
    if _first_bar:
        print(f"[潜龙] [OK] handlebar触发! 标的={len(ContextInfo.trade_code_list)}只")
        _first_bar = False

    plan = _load_plan()
    limits = plan.get("global_limits", {})

    _breaker_on = limits.get("circuit_breaker", False)
    if _breaker_on:
        _cancel_all_pending(ContextInfo)

    if _daily["trades"] >= limits.get("max_daily_trades", 5):
        return

    # ══ 弱转强竞价确认 (日线候选→QMT实时验证) ══
    for _wts in _wts_candidates:
        _wts_sym = _wts.get("symbol", "")
        _wts_qmt = _ql_to_qmt(_wts_sym)
        if _wts_qmt not in ContextInfo.trade_code_list:
            continue
        if _wts_sym in _wts_confirmed:
            continue  # 今日已确认
        try:
            mk = ContextInfo.get_market_data([])
            _tick_data = mk.get(_wts_qmt, {})
            _price = float(_tick_data.get('lastPrice', 0))
            _prev_close = float(_tick_data.get('lastClose', _tick_data.get('preClose', _price)))
            _open = float(_tick_data.get('open', _price))
            if _price <= 0: continue
            if _SIG_READY and _sig_wts:
                _tick = {'lastPrice': _price, 'open': _open, 'volume': 0,
                         'bidVol': float(_tick_data.get('bid1', _tick_data.get('bidVol', 0)))}
                _wts_score = _sig_wts(_wts_qmt, _tick, _prev_close, _open)
                if _wts_score >= 50:
                    _wts_confirmed.add(_wts_sym)
                    _use_pct = min(plan.get("weak_to_strong", {}).get("position_pct", 8), 8)
                    qty = _calc_shares(_use_pct, _price, 1_000_000)
                    passorder(23, 1101, ContextInfo.accID, _wts_qmt, 0, round(_price, 2), qty, "弱转强·竞价确认", _wts_sym, 2)
                    _daily["trades"] += 1; _daily["pct"] += _use_pct
                    print(f"[弱转强] {_wts_qmt} 竞价确认, {qty}股@{_price:.2f}")
                    # 推信号到Flask终端显示
                    try:
                        import urllib.request as _ur2
                        from datetime import datetime as _dt2
                        _wdata = json.dumps({"symbol": _wts_sym, "signal_type": "弱转强·竞价确认",
                            "price": round(_price, 2), "channel": "review",
                            "enabled": True, "qmt_code": _wts_qmt,
                            "signal_id": f"wts_{_wts_sym}_{_dt2.now().strftime('%Y%m%d')}"}).encode()
                        _wreq = _ur2.Request(FLASK_URL, data=_wdata, headers={"Content-Type":"application/json"})
                        _ur2.urlopen(_wreq, timeout=3)
                    except Exception as _pe: print(f"[弱转强] 推Flask失败: {_pe}")
        except Exception as _wts_e:
            print(f"[弱转强] {_wts_sym} 异常: {_wts_e}")

    # ══ 弱转强开盘加仓 (9:30-9:35, 陈小群: 竞价半仓→确认→加满) ══
    import time as _wts_tm
    _now_t = _wts_tm.strftime('%H%M%S')
    if not _breaker_on and '093000' <= _now_t <= '093500' and _wts_confirmed:
        _wts_mk = ContextInfo.get_market_data([])
        for _wts_sym in list(_wts_confirmed):
            _wts_qmt = _ql_to_qmt(_wts_sym)
            _wts_tick = _wts_mk.get(_wts_qmt, {})
            _wts_price = float(_wts_tick.get('lastPrice', 0))
            _wts_open = float(_wts_tick.get('open', _wts_price))
            _wts_prev = float(_wts_tick.get('lastClose', _wts_price))
            if _wts_price <= 0: continue
            _wts_gap = (_wts_price / _wts_open - 1) if _wts_open > 0 else 0
            # 开口强度: 分时站稳均价 (行业前沿) + 现价>昨收
            _vwap = float(_wts_tick.get('avgPrice', _wts_price))  # QMT分时均价
            if _wts_gap > -0.005 and _wts_price >= _vwap and _wts_price > _wts_prev:
                _wts_pct = min(4, plan.get("weak_to_strong",{}).get("position_pct",8)//2)
                _wts_qty = _calc_shares(_wts_pct, _wts_price, 100000)
                passorder(23, 1101, ContextInfo.accID, _wts_qmt, 0,
                          round(_wts_price,2), _wts_qty, "弱转强加仓", _wts_sym, 2)
                print(f"[弱转强] 📈 {_wts_sym} 开盘加仓 {_wts_qty}股 @{_wts_price:.2f}")
                _wts_confirmed.add(_wts_sym + "_added")  # 标记已加仓
                _daily["trades"] += 1

    # ══ 打板·二封 (深度刀: 日线预选+次日确认+板块热度+动态阈值) ══
    if _daban_candidates and _SECTOR_HOT:
        # 全局日笔上限 + 二封日笔上限
        _max_daily = limits.get('max_daily_trades', 5)
        if _daily["trades"] >= _max_daily or _daban_daily["reseal"] >= 2:
            pass  # 超限, 二封静默
        else:
            _daban_mk = ContextInfo.get_market_data([])
            for _db in sorted(_daban_candidates, key=lambda x:-x.get("score",0))[:3]:
                _ds = _db.get("symbol","")
                _ind = _db.get("industry","")
                if _ind not in _SECTOR_HOT:  # 非主线板块不扫
                    continue
                # 同票互锁: 一封已买 → 二封不重复
                if _ds in _daban_bought:
                    continue
                # 熊市板块保护: 无≥3只涨停 → 一日游
                _sector_bd = _sector_bd_count.get(_ind, 0)
                _regime = _market_regime
                if _regime == "bear" and _sector_bd < 3:
                    continue
                _dq = _ql_to_qmt(_ds)
                _dt = _daban_mk.get(_dq, {})
                _dp = float(_dt.get('lastPrice',0)); _dpc = float(_dt.get('lastClose',_dp))
                if _dp <= 0: continue
                _dlim = _dpc * (1 + _limit_pct(_ds))
                # 冷却检查
                _bar_time = getattr(ContextInfo, 'bar_time', time.strftime('%Y-%m-%dT%H:%M:%S'))
                _db_ck = f"{_ds}|打板·二封|{_bar_time}"
                if _db_ck in _cooldown: continue
                _cooldown[_db_ck] = time.time()
                if _SIG_READY and _sig_daban:
                    # 流通盘获取
                    _outstanding = 1e9
                    try:
                        from xtquant import xtdata as _xt
                        _instr = _xt.get_instrument_detail(_dq)
                        _outstanding = float(_instr.get('FloatVol', 1e9)) if _instr else 1e9
                    except: pass
                    # 动态阈值 (市场自适应: 牛市跟势/熊市守质)
                    try:
                        from signals.daban.weights import THRESHOLD
                        _th = THRESHOLD.get(_regime, 0.70)
                    except: _th = 0.70
                    if _sig_daban(_dq, {'lastPrice':_dp,'open':float(_dt.get('open',_dp)),'bidVol':float(_dt.get('bid1',_dt.get('bidVol',0))),'outstanding':_outstanding}, _dpc, _dlim, sector_bd_count=_sector_bd, threshold=_th):
                        # 情绪仓位联动
                        _sm = {"startup": 0.5, "ferment": 1.0, "climax": 0.7}.get(_sentiment_stage, 1.0)
                        _db_pct = min(min(_db.get("score",50)/10, 6.0) * _sm, 6.0)
                        _total_asset = 100000
                        try:
                            _acc = ContextInfo.get_account_info(ContextInfo.accID)
                            if _acc: _total_asset = _acc.get('total_asset', 100000)
                        except: pass
                        _db_qty = _calc_shares(_db_pct, _dp, _total_asset)
                        passorder(23, 1101, ContextInfo.accID, _dq, 0, round(_dp,2), _db_qty, "打板·二封", _ds, 2)
                        print(f"[打板] 🎯 {_ds} 二封确认 板块={_ind} bd={_sector_bd} 情绪={_sentiment_stage} {_db_qty}股@{_dp:.2f}")
                        _daily["trades"] += 1
                        _daban_daily["reseal"] += 1
                        _daban_bought.add(_ds)
                        try:
                            import urllib.request as _ur2; from datetime import datetime as _dt2
                            _ur2.urlopen(_ur2.Request(FLASK_URL,data=json.dumps({"symbol":_ds,"signal_type":"打板·二封确认","price":round(_dp,2),"channel":"review","enabled":True,"qmt_code":_dq,"signal_id":f"db_{_ds}_{_dt2.now().strftime('%Y%m%d')}"}).encode(),headers={"Content-Type":"application/json"}),timeout=3)
                        except Exception as _pe: print(f"[打板] 推Flask失败: {_pe}")
                        break  # 只做最优1只

    # ══ TDX原生池信号 (策略E: 通达信公式引擎→自动执行) ══
    _tdx_sig_path = r"D:\quant_web\data\tdx_live_signals.json"
    if os.path.exists(_tdx_sig_path):
        try:
            _tdx_sig_mtime = os.path.getmtime(_tdx_sig_path)
            if _tdx_sig_mtime != _tdx_last_mtime:
                _tdx_last_mtime = _tdx_sig_mtime
                with open(_tdx_sig_path, "r", encoding="utf-8") as _tf:
                    _tdx_data = json.load(_tf)
                # 读TDX池配置 (取target字段)
                _tdx_cfg = {}
                try:
                    _tdx_cfg_path = r"D:\quant_web\data\tdx_pools_config.json"
                    if os.path.exists(_tdx_cfg_path):
                        _tdx_cfg = json.load(open(_tdx_cfg_path, encoding="utf-8")).get("pools", {})
                except: pass
                for _pool_name, _pool_info in _tdx_data.items():
                    if not isinstance(_pool_info, dict): continue
                    _pool_label = _pool_info.get("label", _pool_name)
                    _pool_signals = _pool_info.get("_signals", [])
                    _pool_target = _tdx_cfg.get(_pool_name, {}).get("target", "qmt")  # 默认QMT直接执行
                    for _ts in _pool_signals:
                        _tdx_sym = _ts.get("symbol", "")
                        _tdx_price = _ts.get("price", 0)
                        _tdx_date = _ts.get("date", "")
                        if _tdx_date != time.strftime("%Y%m%d"): continue
                        _tdx_qmt = _ql_to_qmt(_tdx_sym)
                        _tdx_ck = f"{_tdx_sym}|TDX·{_pool_label}|{_tdx_date}"
                        if _tdx_ck in _cooldown: continue
                        _cooldown[_tdx_ck] = time.time()
                        # 补实时价格 (blk信号无价格 → Flask会400拒绝)
                        if _tdx_price <= 0:
                            try:
                                _tdx_tick = ContextInfo.get_market_data([]).get(_tdx_qmt, {})
                                _tdx_lp = float(_tdx_tick.get('lastPrice', 0))
                                if _tdx_lp > 0: _tdx_price = _tdx_lp
                            except: pass
                        # 🆕 按target路由
                        if _pool_target == "daban":
                            # → 打板候选池 → confirm_board确认
                            _daban_candidates.append({"symbol": _tdx_sym, "score": 60, "industry": "", "source": f"TDX·{_pool_label}"})
                            print(f"[TDX] 🎯 {_tdx_sym} → 打板候选池 ({_pool_label})")
                            # ══ 竞价抢筹确认 (TDX 9:26-27出信号, 9:26-9:29竞价窗口) ══
                            _now_dt = time.strftime('%H%M%S')
                            if '092600' <= _now_dt <= '092900' and _tdx_sym not in _daban_bought:
                                _db_tk = ContextInfo.get_market_data([]).get(_tdx_qmt, {})
                                _db_px = float(_db_tk.get('lastPrice', 0))
                                _db_pr = float(_db_tk.get('lastClose', _db_px))
                                if _db_px > 0 and _db_pr > 0:
                                    _db_gap2 = (_db_px / _db_pr - 1)
                                    if 0.01 <= _db_gap2 <= 0.05 and not _breaker_on and _daily["trades"] < limits.get("max_daily_trades", 5):
                                        _db_pct2 = 3.0
                                        _db_qty2 = _calc_shares(_db_pct2, _db_px, 100000)
                                        passorder(23, 1101, ContextInfo.accID, _tdx_qmt, 0,
                                                  round(_db_px, 2), _db_qty2, "打板竞价·TDX", _tdx_sym, 2)
                                        _daban_bought.add(_tdx_sym)
                                        _daily["trades"] += 1
                                        print(f"[TDX] 🎯 竞价抢筹 {_tdx_sym} 0.3成{_db_qty2}股 @{_db_px:.2f} (gap={_db_gap2*100:.1f}%)")
                        elif _pool_target == "wts":
                            # → 弱转强候选池 → confirm_wts确认
                            _wts_candidates.append({"symbol": _tdx_sym, "score": 50, "source": f"TDX·{_pool_label}"})
                            print(f"[TDX] 🔄 {_tdx_sym} → 弱转强候选池 ({_pool_label})")
                        elif _pool_target == "signal":
                            # → 信号表 → 不自动执行
                            pass
                        else:
                            # qmt: TDX快速通道已执行, handlebar不重复
                            pass
                        # 推Flask通知 (所有target)
                        try:
                            import urllib.request as _tdx_ur
                            _tdx_payload = json.dumps({
                                "symbol": _tdx_sym, "signal_type": f"TDX·{_pool_label}",
                                "price": round(_tdx_price, 2), "channel": "review",
                                "enabled": False, "qmt_code": _tdx_qmt,
                                "signal_id": _tdx_ck
                            }).encode()
                            _tdx_ur.urlopen(_tdx_ur.Request(FLASK_URL, data=_tdx_payload,
                                headers={"Content-Type": "application/json"}), timeout=3)
                        except Exception: pass
        except Exception as _tdx_e:
            pass  # TDX信号处理失败不影响其他策略

    for qmt_code in ContextInfo.trade_code_list:
        ql_sym = _qmt_to_ql(qmt_code)
        cache = _fast_cache.get(ql_sym)
        if not cache:
            continue
        enabled, pos_pct, stop_loss, take_profit, soft_stop_pct, signal_set, best_ml, min_ml, industry, time_window, yesterday_volume = cache

        try:
            mk = ContextInfo.get_market_data(
                ['close', 'open', 'volume', 'high'],
                stock_code=qmt_code,
                count=2
            )
            if mk is None or len(mk.get('close', [])) < 2:
                continue
            prev_close = float(mk['close'][-2])
            prev_vol   = float(mk['volume'][-2])
            price      = float(mk['close'][-1])
            volume     = float(mk['volume'][-1])
            open_price = float(mk['open'][-1])
            bar_high   = float(mk.get('high', [[price, price]])[-1])
        except Exception:
            continue

        if volume <= 0 or prev_close <= 0:
            continue

        signal_name = None
        _lp = _limit_pct(qmt_code)  # 该票涨跌停幅度(板块)

        # 弱转强确认增强: 累积分时量+VWAP
        _is_reversal = bool(time_window)  # 有time_window=反转候选
        if _is_reversal:
            # 累积分时量(从开盘到现在) / 昨日全天量 > 3% (游资标准)
            _hist = _bar_history.get(qmt_code)
            _acc_vol = sum(_hist["v"]) if _hist else volume
            _vol_ok = yesterday_volume > 0 and _acc_vol > yesterday_volume * 0.03
            # VWAP: 成交量加权均价
            if qmt_code in _bar_history and len(_bar_history[qmt_code]["c"]) >= 2:
                _bh = _bar_history[qmt_code]
                _prices = _bh["c"]; _vols = _bh["v"]
                _vwap = sum(p*v for p,v in zip(_prices, _vols)) / max(sum(_vols), 1)
                _above_vwap = price > _vwap
            else:
                _above_vwap = True
        else:
            _vol_ok = True; _above_vwap = True

        # 注册表: 统一 ctx → 遍历策略
        _ctx = {
            'ContextInfo': ContextInfo, 'qmt_code': qmt_code, 'ql_sym': ql_sym,
            'price': price, 'open_price': open_price, 'prev_close': prev_close,
            'bar_high': bar_high, 'bar_low': bar_low, 'prev_vol': prev_vol, 'volume': volume,
            '_lp': _lp, '_is_reversal': _is_reversal, '_above_vwap': _above_vwap,
            '_sentiment_stage': _sentiment_stage, '_advance_ratio': _advance_ratio,
            '_limit_down_count': _limit_down_count, 'time_window': time_window,
            '_vol_ok': _vol_ok,
        }
        STRATEGIES = [auction_check, chase_check, breakthrough_check, tail_rush_check, continuation_check, floor_check]
        for _fn in STRATEGIES:
            signal_name = _fn(_ctx)
            if signal_name: break

        # 反转候选时间窗+假突破过滤+午后退场
        if time_window and signal_name in ("竞价抢筹", "盘中突破"):
            _now_t = time.strftime('%H%M')
            # 13:00后强制退场
            if _now_t > '1300': continue
            _start, _end = time_window.split('-') if '-' in time_window else ('0930','1030')
            if not (_start <= _now_t <= _end):
                continue
            # 假突破过滤: 延时3分钟验证
            _fake_key = f"fake_{ql_sym}_{signal_name}"
            _fake_data = _fake_check.get(_fake_key, None)
            if _fake_data is None:
                # 第一次触发: 记录参考价, 3分钟后确认
                _fake_check[_fake_key] = {
                    'ref_price': price, 'ref_time': time.time(),
                    'open_price': open_price,
                }
                continue  # 延时, 这次不下单
            else:
                # 第二次触发: 验证
                _elapsed = time.time() - _fake_data['ref_time']
                if _elapsed < 180: continue  # 还没到3分钟
                # 验证: 当前价>参考价 (不是冲高回落)
                if price <= _fake_data['ref_price'] * 0.995:
                    continue  # 回落 >0.5%, 假突破
                # 验证通过, 清除记录
                del _fake_check[_fake_key]
        # 清理过期缓存 (>10分钟未验证)
        _now_ts = time.time()
        for _fk in list(_fake_check.keys()):
            if _now_ts - _fake_check[_fk].get('ref_time', 0) > 600:
                del _fake_check[_fk]

        if not signal_name:
            _bar_key = qmt_code
            if _bar_key not in _bar_history:
                _bar_history[_bar_key] = {"c":[], "h":[], "l":[], "v":[], "o":[]}
            _bh = _bar_history[_bar_key]
            _bh["c"].append(price)
            _bh["h"].append(float(mk.get("high",[price,price])[-1]))
            _bh["l"].append(float(mk.get("low",[price,price])[-1]))
            _bh["v"].append(volume)
            _bh["o"].append(open_price)
            if len(_bh["c"]) > 100:
                for _k in _bh: _bh[_k] = _bh[_k][-90:]
            if len(_bh["c"]) >= 60:
                _pdf = _bd_to_df(_bh)
                if _check_tdx(_pdf):
                    # TDX公式命中 → 只推审核通道通知, 绝不下单
                    _sig = f"{ql_sym}|TDX公式|{getattr(ContextInfo,'bar_time','')}"
                    if _sig not in _cooldown:
                        _cooldown[_sig] = time.time()
                        _tdx_notice(ql_sym, qmt_code, price, _sig)
            continue

        _bar_time = getattr(ContextInfo, 'bar_time', time.strftime('%Y-%m-%dT%H:%M:%S'))
        _ck = f"{ql_sym}|{signal_name}|{_bar_time}"
        if _ck in _cooldown:
            continue
        _cooldown[_ck] = time.time()

        _PATTERN_SIGNALS = {
            "盘中突破", "竞价抢筹", "尾盘急拉", "打板·一封",
            "板后接力", "撬板战法", "竞价弱转强",
        }
        _is_pattern = signal_name in _PATTERN_SIGNALS
        _pattern_pos = min(pos_pct * 0.3, 3.0)

        _qmt_ok = limits.get("qmt_fast_enabled", False)
        # 打板·一封: 全市场实时扫板, 不依赖 signal_set (候选非日线预选)
        _pass_check = enabled and (signal_name in signal_set or signal_name == "打板·一封")
        if _is_pattern:
            _pass_check = _pass_check
        else:
            _pass_check = _pass_check and best_ml >= min_ml

        # 实时评分确认 (2026-07-13 独立评分方案)
        _sig_confirmed = True
        if _SIG_READY and _pass_check:
            try:
                _prev_close = float(mk.get('lastClose', mk.get('preClose', price)))
                _open = float(mk.get('open', price))
                _limit_up = _prev_close * (1.20 if qmt_code.startswith('688') or qmt_code.startswith('300') else 0.10)
                _tick = {'lastPrice': price, 'open': _open, 'volume': volume,
                         'bidVol': mk.get('bid1', mk.get('bidVol', 0))}
                if signal_name == '打板·一封':
                    _ind = cache[7]
                    if _sector_bd_count.get(_ind, 0) >= 2:
                        # L2 多层盘口分析 (白线: 封单深度+价格墙+多级失衡)
                        try:
                            _ft = ContextInfo.get_full_tick(qmt_code)
                            _get = lambda f, d=0: float(getattr(_ft, f, d) if hasattr(_ft, f) else (_ft.get(f, d) if hasattr(_ft, 'get') else d))
                            _bid1=_get('bid1_vol'); _bid2=_get('bid2_vol'); _bid3=_get('bid3_vol')
                            _ask1=_get('ask1_vol'); _ask2=_get('ask2_vol'); _ask3=_get('ask3_vol')
                            _bp1=_get('bidPrice1'); _ap1=_get('askPrice1')
                        except Exception:
                            _bid1=_bid2=_bid3=_ask1=_ask2=_ask3=_bp1=_ap1=0
                        _oseal = 1e9
                        try:
                            from xtquant import xtdata as _xt2
                            _instr2 = _xt2.get_instrument_detail(qmt_code)
                            _oseal = float(_instr2.get('FloatVol', 1e9)) if _instr2 else 1e9
                        except: pass
                        # 封单深度: 前3档买单 vs 流通盘
                        _depth = (_bid1 + _bid2 + _bid3) / max(_oseal, 1)
                        _seal_ok = _depth >= 0.008  # 三档封单>0.8%流通
                        # 多级失衡: 买方总量 vs 卖方总量
                        _bid_total = _bid1 + _bid2 + _bid3
                        _ask_total = _ask1 + _ask2 + _ask3
                        _imb = _bid_total / (_bid_total + _ask_total) if (_bid_total + _ask_total) > 0 else 0.5
                        # 价格墙: ask1远于bid1→卖方高位→阻力弱→封板更有利
                        _wall_weak = _ap1 > 0 and _bp1 > 0 and (_ap1 / max(_bp1, 0.01) - 1) > 0.005
                        _sig_confirmed = _seal_ok and _imb >= 0.55
                    else:
                        _sig_confirmed = False  # 无板块联动=不追
                elif signal_name == '打板追封':
                    _sig_confirmed = _sig_daban(qmt_code, _tick, _prev_close, _limit_up)
                elif signal_name in ('盘中突破','竞价抢筹'):
                    _sig_confirmed = _sig_oversold(qmt_code, _tick, _prev_close)
                elif signal_name == '弱转强':
                    _sig_confirmed = _sig_wts(qmt_code, _tick, _prev_close, _open) if _sig_wts else False
                elif signal_name == '尾盘急拉':
                    _sig_confirmed = False  # 退审核通道: 尾盘诱多概率高, 只推Flask不下单
                else:
                    _sig_confirmed = True  # 无特殊确认 → 放行
                if not _sig_confirmed:
                    print(f"[评分] {qmt_code} {signal_name} 实时评分不通过, 拦截")
            except Exception:
                if signal_name == "打板·一封":
                    _sig_confirmed = False  # 一封L2核心防线, 异常=不放行
                else:
                    _sig_confirmed = True  # 评分失败不阻止, 保守放行

        if _qmt_ok and not _breaker_on and _pass_check and _sig_confirmed:
            try:
                total_asset = 100000
                try:
                    acc = ContextInfo.get_account_info(ContextInfo.accID)
                    if acc: total_asset = acc.get('total_asset', total_asset)
                except: pass
                # 一封专属: 情绪仓位联动 + 同票锁 + 分刀计数
                if signal_name == "打板·一封":
                    if _daban_daily["chase"] >= 3:
                        continue  # 日笔上限
                    if ql_sym in _daban_bought:
                        continue  # 同票互锁: 当日已买过
                    _sm = {"startup": 0.75, "ferment": 1.25, "climax": 1.0}.get(_sentiment_stage, 1.0)
                    _use_pct = min(2.0 * _sm, 3.0)  # 2%×情绪系数, 上限3%
                    _daban_daily["chase"] += 1
                    _daban_bought.add(ql_sym)
                    # O1: 实时板块涨停计数 (盘中增量, 反哺后续检测)
                    _ind = cache[7]
                    _sector_bd_count[_ind] = _sector_bd_count.get(_ind, 0) + 1
                else:
                    _use_pct = _pattern_pos if _is_pattern else pos_pct
                qty = _calc_shares(_use_pct, price, total_asset)
                passorder(23, 1101, ContextInfo.accID, qmt_code, 0,
                          round(price, 2), qty, "潜龙快速", ql_sym, 2)
                _daily["trades"] += 1
                _daily["pct"] += _use_pct
                _tag = "打板" if signal_name == "打板·一封" else ("形态" if _is_pattern else "ML")
                print(f"[快速·{_tag}] {qmt_code} {signal_name} {qty}股@{price:.2f} ({_daily['trades']}/{limits.get('max_daily_trades',5)}笔)")
            except Exception as e:
                print(f"[快速] {qmt_code} passorder: {e}")

    # 信号推送(不依赖passorder: 所有命中信号都推前端到Flask)
    def _push_flask():
        try:
            import urllib.request, urllib.error
            _tag = "弱转强·" + signal_name if time_window else signal_name
            _data = json.dumps({
                "symbol": ql_sym, "signal_type": _tag,
                "price": round(price, 2),
                "channel": "fast" if enabled else "review",
                "enabled": enabled, "qmt_code": qmt_code,
                "signal_id": _ck,
            }).encode()
            _req = urllib.request.Request(FLASK_URL, data=_data,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(_req, timeout=3)
            print(f"[推送] ✅ {ql_sym} {signal_name} → Flask")
        except Exception as _e:
            print(f"[推送] ❌ {ql_sym} {signal_name}: {_e}")
    import threading
    threading.Thread(target=_push_flask, daemon=True).start()


def _bd_to_df(bh):
    import pandas as pd
    return pd.DataFrame({
        "close": bh["c"], "high": bh["h"], "low": bh["l"],
        "volume": bh["v"], "open": bh["o"],
    })


def _tdx_notice(ql_sym, qmt_code, price, sig_id):
    """TDX公式命中 → 推审核通道(前端可见), 绝不下单"""
    def _post():
        try:
            import urllib.request
            data = json.dumps({"symbol": ql_sym, "signal_type": "TDX公式",
                "price": round(price, 2), "channel": "review", "enabled": False,
                "qmt_code": qmt_code, "signal_id": sig_id}).encode()
            urllib.request.urlopen(urllib.request.Request(FLASK_URL, data=data,
                headers={"Content-Type": "application/json"}), timeout=3)
        except Exception as _e:
            print(f"[QMT] TDX推送Flask失败: {_e}")
    import threading
    threading.Thread(target=_post, daemon=True).start()


def _check_tdx(df):
    global _tdx_formulas
    try:
        if _tdx_formulas is None:
            _tdx_formulas = json.load(open(r"d:\quant_framework\signal_config.json", encoding="utf-8")).get("tdx_formulas", {})
        from qmt_strategies.tdx_formulas import check_tdx_formula
        for _fn, _on in _tdx_formulas.items():
            if _on and check_tdx_formula(_fn, df):
                return True
    except Exception:
        pass
    return False


def on_stock_trade(ContextInfo, trade):
    pass


def on_account_status(ContextInfo, account):
    pass
