#encoding:gbk
'''
潜龙快速通道策略 v2.0 — 双道并行
====================================
审核通道: 所有信号 POST 到潜龙 Flask (记录/通知)
快速通道: enabled=true → passorder() 直接下单 (<20ms)

用法:
  1. QMT客户端 → 策略编辑器 → 新建策略
  2. 粘贴本文件全部内容
  3. 设置主图K线为任意股票 (1分钟周期)
  4. 运行

信号:
  竞价抢筹 — 高开>2% + 量比>3
  盘中突破 — 涨>3% + 放量>2x
  尾盘急拉 — 14:30后涨>3%
  打板追封 — 触及涨停
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
_fast_cache = {}
_bar_history = {}
_fake_check = {}  # 假突破延时验证缓冲区
_tdx_formulas = None


def _load_plan():
    global _plan, _plan_mtime, _fast_cache
    if not os.path.exists(PLAN_PATH):
        return {}
    try:
        mtime = os.path.getmtime(PLAN_PATH)
        if mtime != _plan_mtime:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                _plan = json.load(f)
            _plan_mtime = mtime
            _build_fast_cache()
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
        _limits = {}
        for _tick in range(60):
            _now = _atw.strftime('%H%M%S')
            if _now > '092500': break
            try:
                _plan2 = json.load(open(PLAN_PATH, encoding='utf-8'))
                _limits = _plan2.get("global_limits", {})
                _is_sim = not _limits.get("qmt_fast_enabled", False)
                _ac = _limits.get("_call_auction_confirmed", 0)
                if _ac > 0:
                    for _sym, _cfg in _plan2.get("stocks", {}).items():
                        if isinstance(_cfg, dict) and _cfg.get("enabled") and "竞价弱转强" in _cfg.get("signal_types", []):
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
                    # 竞价卖出
                    for _sym, _cfg in _plan2.get("stocks", {}).items():
                        if isinstance(_cfg, dict) and _cfg.get("sell_signal"):
                            _code = _ql_to_qmt(_sym) if not _is_sim else _sym
                            _qty = _cfg.get("sell_qty", 0)
                            _price = _cfg.get("auction_price", 0)
                            if _qty > 0 and _price > 0:
                                if _is_sim:
                                    # 模拟盘: POST到Flask
                                    _sim_order(_sym, "sell", _price, _qty, _cfg.get("sell_reason", ""))
                                else:
                                    passorder(24, 1101, ContextInfo.accID, _code, 0,
                                              round(_price * 0.98, 2), _qty, "潜龙竞价卖", _sym, 2)
                                print(f"[竞价] 🔴 {_sym} 卖出{_qty}股 ({_cfg.get('sell_reason','')})")
                    break
            except Exception: pass
            _atw.sleep(1)
    import threading as _th2
    _th2.Thread(target=_auction_watch, daemon=True).start()
    print("[潜龙] 竞价守护已启动 (买入+卖出)")


def handlebar(ContextInfo):
    try:
        _handlebar_impl(ContextInfo)
    except Exception as e:
        print(f"[潜龙] handlebar异常: {e}")
        print(traceback.format_exc())


def _handlebar_impl(ContextInfo):
    global _daily

    plan = _load_plan()
    limits = plan.get("global_limits", {})

    _breaker_on = limits.get("circuit_breaker", False)
    if _breaker_on:
        _cancel_all_pending(ContextInfo)

    if _daily["trades"] >= limits.get("max_daily_trades", 5):
        return

    for qmt_code in ContextInfo.trade_code_list:
        ql_sym = _qmt_to_ql(qmt_code)
        cache = _fast_cache.get(ql_sym)
        if not cache:
            continue
        enabled, pos_pct, stop_loss, take_profit, signal_set, best_ml, min_ml, industry, time_window, yesterday_volume = cache

        try:
            mk = ContextInfo.get_market_data(
                ['close', 'open', 'volume'],
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

        # 竞价抢筹: 高开>2% + 量比>3 (+ 反转候选=累积分时量占昨>3%)
        if open_price > prev_close * 1.02 and prev_vol > 0 and volume > prev_vol * 3:
            if _is_reversal and not _vol_ok: pass  # 反转候选需额外验证
            else: signal_name = "竞价抢筹"

        # 盘中突破: 涨>3% + 放量>2x (+ 反转候选=价格>VWAP)
        elif price > prev_close * 1.03 and prev_vol > 0 and volume > prev_vol * 2:
            if _is_reversal and not _above_vwap: pass  # 反转候选需价格>VWAP
            else: signal_name = "盘中突破"

        # 尾盘急拉: 14:30后 + 涨>3%
        elif time.strftime('%H%M') > '1430' and (price / max(prev_close, 0.01) - 1) > 0.03:
            signal_name = "尾盘急拉"

        # 打板追封: 触及涨停 + 放量 (只打10%主板, 不做20/30%板块)
        elif _lp == 0.10 and price >= round(prev_close * (1 + _lp), 2) and prev_vol > 0 and volume > prev_vol * 1.5:
            signal_name = "打板追封"

        # 板后接力: 昨涨停+今低开回升翻红+放量 (只做10%主板)
        if not signal_name and _lp == 0.10:
            try:
                mk3 = ContextInfo.get_market_data(
                    ['close','open','volume'], stock_code=qmt_code, count=3)
                if mk3 and len(mk3.get('close',[])) >= 3:
                    yest_close = float(mk3['close'][-2])
                    yest_open  = float(mk3['open'][-2])
                    today_open  = float(mk3['open'][-1])
                    if yest_close >= round(yest_open * 1.095, 2):
                        if today_open < yest_close and price > today_open and price > yest_close * 0.98:
                            vv = list(mk3.get('volume', [volume]))
                            avg_vol = sum(vv) / len(vv) if vv else volume
                            if volume > avg_vol * 1.3:
                                signal_name = "板后接力"
            except Exception:
                pass

        # 撬板战法: 跌停+巨量开板 (只做10%主板)
        if not signal_name and _lp == 0.10:
            limit_down = round(prev_close * (1 - _lp), 2)
            if price <= limit_down * 1.01 and prev_vol > 0 and volume > prev_vol * 5:
                if open_price <= limit_down * 1.005 and price > limit_down * 1.005:
                    signal_name = "撬板战法"

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
            "盘中突破", "竞价抢筹", "尾盘急拉", "打板追封",
            "板后接力", "撬板战法", "竞价弱转强",
        }
        _is_pattern = signal_name in _PATTERN_SIGNALS
        _pattern_pos = min(pos_pct * 0.3, 3.0)

        _qmt_ok = limits.get("qmt_fast_enabled", False)
        _pass_check = enabled and signal_name in signal_set
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
                if signal_name == '打板追封':
                    _sig_confirmed = _sig_daban(qmt_code, _tick, _prev_close, _limit_up)
                elif signal_name in ('盘中突破','竞价抢筹'):
                    _sig_confirmed = _sig_oversold(qmt_code, _tick, _prev_close)
                if not _sig_confirmed:
                    print(f"[评分] {qmt_code} {signal_name} 实时评分不通过, 拦截")
            except Exception:
                _sig_confirmed = True  # 评分失败不阻止, 保守放行

        if _qmt_ok and not _breaker_on and _pass_check and _sig_confirmed:
            try:
                total_asset = 100000
                try:
                    acc = ContextInfo.get_account_info(ContextInfo.accID)
                    if acc: total_asset = acc.get('total_asset', total_asset)
                except: pass
                _use_pct = _pattern_pos if _is_pattern else pos_pct
                qty = _calc_shares(_use_pct, price, total_asset)
                passorder(23, 1101, ContextInfo.accID, qmt_code, 0,
                          round(price, 2), qty, "潜龙快速", ql_sym, 2)
                _daily["trades"] += 1
                _daily["pct"] += _use_pct
                _tag = "[形态]" if _is_pattern else "[ML]"
                print(f"[快速] {_tag} {qmt_code} {signal_name} {qty}股@{price:.2f} ({_daily['trades']}/{limits.get('max_daily_trades',5)}笔)")
            except Exception as e:
                print(f"[快速] {qmt_code} passorder: {e}")

        def _audit_async():
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
            except Exception as _e:
                print(f"[快速] 推送Flask失败: {_e}")
        import threading
        threading.Thread(target=_audit_async, daemon=True).start()


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
