"""统一交易循环 (一次性架构清理)

替代 app.py 内嵌的 V1-5注入 + 纸引擎循环。
所有信号来源 → 统一入口 → 纸引擎。

用法: app.py 中调用 trading_loop.start(paper_acc)
"""

import time, threading, logging
from datetime import datetime

logger = logging.getLogger(__name__)

INTERVAL = 10  # 扫描间隔
_running = False


def start(paper_acc):
    """启动统一交易循环"""
    global _running
    if _running: return
    _running = True
    t = threading.Thread(target=_loop, args=(paper_acc,), daemon=True, name="TradingLoop")
    t.start()
    logger.info("统一交易循环已启动")
    # Phase 5: 灰度事件驱动并行 (对标vnpy EventEngine)
    try:
        t2 = threading.Thread(target=_event_loop, args=(paper_acc,), daemon=True, name="EventLoop")
        t2.start()
        logger.info("事件驱动交易循环已启动(灰度)")
    except Exception as e:
        logger.warning(f"事件循环启动失败: {e}")
    return t


def stop():
    global _running
    _running = False


def _can_trade() -> bool:
    """交易时间检查"""
    now = datetime.now()
    if now.weekday() >= 5: return False
    t = now.time()
    if t < datetime.strptime("09:25","%H:%M").time(): return False
    if datetime.strptime("11:30","%H:%M").time() <= t <= datetime.strptime("13:00","%H:%M").time(): return False
    if t >= datetime.strptime("15:05","%H:%M").time(): return False
    return True


def _get_market_state():
    try:
        from market_state_classifier import classify_market_state
        return classify_market_state()
    except: return "unknown"

def _save_market_state():
    """写入缓存文件供API读取 (E349: 统一路径到 data/market_state.json)"""
    import json as _j, os as _os
    try:
        _p = r"D:\quant_web\data\market_state.json"
        _existing = {}
        if _os.path.exists(_p):
            try:
                with open(_p, "r") as _f:
                    _existing = _j.load(_f)
            except Exception:
                _existing = {}
        _existing["state"] = _get_market_state()
        _tmp = _p + ".tmp"
        with open(_tmp, "w") as _f:
            _j.dump(_existing, _f)
        _os.replace(_tmp, _p)
    except: pass


def _inject_v15_signals(paper_acc, signals):
    """V1-5因子信号注入"""
    try:
        from factor_registry import get_active_factors
        active = get_active_factors()
        if not active: return

        ms = _get_market_state()
        pos_rule = {"bull":{"max":5,"lv3":.25,"lv4":.15,"lv5":.20},"volatile":{"max":3,"lv3":0,"lv4":.15,"lv5":.15},"bear":{"max":2,"lv3":0,"lv4":0,"lv5":.10},"unknown":{"max":3,"lv3":.10,"lv4":.15,"lv5":.15}}.get(ms,{"max":3,"lv3":.10,"lv4":.15,"lv5":.15})
        try:
            import live_trader as _lt
            _lt.CONFIG.update({"max_positions":pos_rule["max"],"position_pct_lv3":pos_rule["lv3"],"position_pct_lv4":pos_rule["lv4"],"position_pct_lv5":pos_rule["lv5"]})
        except: pass

        from realtime_quotes import _quote_cache
        raw = _quote_cache.get("data",{}) if _quote_cache else {}
        cnt = 0
        for code, q in list(raw.items())[:60]:
            if cnt >= 5: break
            chg = float(q.get("change_pct",0) or 0)
            price = float(q.get("close",q.get("price",0)) or 0)
            if abs(chg)>9.5 or price<=0 or price>100 or code.startswith("12"): continue
            sym = "sh"+code if code.startswith("6") else "sz"+code
            try:
                from quant_framework.execution.rules.engine import RuleEngine
                bs = RuleEngine().check_buy_signal(sym,{},market_state=ms)
                if bs:
                    sname = bs.get("strategy","v15")
                    signals.append({"symbol":sym,"name":sname,"buy_signal":4,"close":bs.get("entry_price",price),"change_pct":chg,"vol_ratio":1,"power_score":bs.get("score",0),"strategy":sname})
                    cnt += 1
            except: pass
        if cnt:
            try: paper_acc._paper_log(f"V1-5注入: {cnt}个信号")
            except: pass
    except Exception as e:
        try: paper_acc._paper_log(f"V1-5异常: {e}")
        except: pass


def _event_loop(paper_acc):
    """事件驱动交易循环 (Phase 5, 对标vnpy EventEngine)

    订阅EventBus的quote事件, 行情到达时触发交易检查。
    与轮询循环并行运行(灰度), 结果写入对比日志。
    """
    import time as _t2
    _t2.sleep(10)  # 等轮询循环先启动
    try:
        from quant_framework.core.event_bus import EventBus
        bus = EventBus._instance
        if not bus:
            print("[EventLoop] EventBus未启动, 退出灰度")
            return
        import sys; print("[EventLoop] 灰度事件循环已启动", flush=True); logger.info("[EventLoop] 灰度事件循环已启动")
        _cnt = [0]
        _last_ts = [0]
        def on_quote(data):
            _cnt[0] += 1
            now = _t2.time()
            if now - _last_ts[0] < 10:  # 防止事件风暴, 最少间隔10秒
                return
            _last_ts[0] = now
            if not _can_trade() or not paper_acc.auto_enabled:
                return
            try:
                signals = []
                import app as _app3
                cache = getattr(_app3, '_FACTOR_CACHE', None)
                if cache and getattr(_app3, '_CACHE_READY', False):
                    for s in cache[:200]:
                        sym = getattr(s, 'symbol', '')
                        if not sym: continue
                        signals.append({"symbol": sym, "name": getattr(s, 'name', ''), "buy_signal": getattr(s, 'buy_signal', 0) or 0, "close": getattr(s, 'close', 0) or 0, "change_pct": getattr(s, 'change_pct', 0) or 0, "vol_ratio": getattr(s, 'vol_ratio', 1) or 1, "industry": getattr(s, 'industry', '') or ''})
                _inject_v15_signals(paper_acc, signals)
                actions = paper_acc.auto_trade_check(signals[:50] if signals else None)
                if actions:
                    print(f"[EventLoop] #{_cnt[0]} 触发{len(actions)}个动作: {[(a.get('reason','?')[:20], a.get('symbol','?')) for a in actions]}")
            except Exception as e:
                print(f"[EventLoop] 异常: {e}")
        bus.subscribe("quote", on_quote)
        while _running:
            _t2.sleep(30)
    except ImportError:
        print("[EventLoop] EventBus不可用")
    except Exception as e:
        print(f"[EventLoop] 启动失败: {e}")


def _loop(paper_acc):
    """主循环"""
    _t = time
    _t.sleep(5)
    _report_day = None
    _loop_count = 0
    while _running:
        try:
            _loop_count += 1
            _cur_state = (_can_trade(), paper_acc.auto_enabled, len(paper_acc.positions))
            if _loop_count == 1 or _cur_state != getattr(_loop, '_last_state', None):
                print(f"[Loop] 运行中 (#{_loop_count}) can_trade={_cur_state[0]} auto={_cur_state[1]} positions={_cur_state[2]}")
                setattr(_loop, '_last_state', _cur_state)
            if not _can_trade() or not paper_acc.auto_enabled:
                _t.sleep(INTERVAL)
                continue

            # 1. 获取信号 (旧系统)
            signals = []
            import app as _app
            try:
                cache = getattr(_app,'_FACTOR_CACHE',None)
                if cache and getattr(_app,'_CACHE_READY',False):
                    for s in cache[:200]:
                        sym = getattr(s,'symbol','')
                        if not sym: continue
                        signals.append({"symbol":sym,"name":getattr(s,'name',''),"buy_signal":getattr(s,'buy_signal',0) or 0,"close":getattr(s,'close',0) or 0,"change_pct":getattr(s,'change_pct',0) or 0,"vol_ratio":getattr(s,'vol_ratio',1) or 1,"industry":getattr(s,'industry','') or ''})
            except: pass

            # HTTP兜底
            if not signals:
                try:
                    import urllib.request, json as _j
                    r = urllib.request.urlopen("http://127.0.0.1:5002/api/signal-center",timeout=10)
                    raw = _j.loads(r.read().decode()).get("signals",[])
                    for s in raw[:50]:
                        signals.append({"symbol":s.get("symbol",""),"buy_signal":s.get("buy_signal",0) or 0,"close":s.get("close",0) or 0,"vol_ratio":1,"change_pct":s.get("change_pct",0) or 0})
                except: pass

            # 2. V1-5因子注入
            _inject_v15_signals(paper_acc, signals)

            # 3. 执行
            actions = paper_acc.auto_trade_check(signals[:50] if signals else None)

            # 4. 日终报告
            now = datetime.now()
            if now.hour==15 and now.minute>=5 and _report_day!=now.date():
                try:
                    from daily_report import generate_daily_report
                    generate_daily_report()
                    _report_day = now.date()
                except: pass

            # 5. 策略熔断 (每30循环)
            if not hasattr(_loop,'_cnt'): _loop._cnt=0
            _loop._cnt+=1
            if _loop._cnt%30==0:
                try:
                    from factor_health import check_strategy_circuit_breaker
                    check_strategy_circuit_breaker()
                except: pass

        except Exception as e:
            try: paper_acc._paper_log(f"循环异常: {e}")
            except: pass
        # 每30循环更新市场状态缓存
        if _loop_count % 30 == 0:
            _save_market_state()
        _t.sleep(INTERVAL)
