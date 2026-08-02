
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
            if _now >= '091500' and 'A' not in _five_frame:
                _five_frame['A'] = ContextInfo.get_market_data()
            elif _now >= '091800' and 'B' not in _five_frame:
                _five_frame['B'] = ContextInfo.get_market_data()
            elif _now >= '091930' and 'C' not in _five_frame:
                _five_frame['C'] = ContextInfo.get_market_data()
            elif _now >= '091957' and 'D' not in _five_frame:
                _five_frame['D'] = ContextInfo.get_market_data()
            elif _now >= '092001' and 'E' not in _five_frame:
                _five_frame['E'] = ContextInfo.get_market_data()
                # E帧到手→分析D→E撤单
                _five_d = _five_frame.get('D', {})
                _five_e = _five_frame.get('E', {})
                if _five_d and _five_e:
                    _five_pd = float(_five_d.get('lastPrice',0)) if isinstance(_five_d,dict) else 0
                    _five_pe = float(_five_e.get('lastPrice',0)) if isinstance(_five_e,dict) else 0
                    _five_vd = float(_five_d.get('volume',0)) if isinstance(_five_d,dict) else 0
                    _five_ve = float(_five_e.get('volume',0)) if isinstance(_five_e,dict) else 0
                    if _five_pd > 0 and _five_vd > 0:
                        _five_delta_p = (_five_pe / _five_pd - 1)
                        _five_delta_v = (_five_ve / _five_vd - 1)
                        _cancel_risk = bool(_five_delta_p < -0.01 and _five_delta_v < -0.3)
                        print(f"[竞价] 五帧: D9:19:57→E9:20:01 价变{_five_delta_p*100:.1f}% 量变{_five_delta_v*100:.0f}% {'🔴撤单!' if _cancel_risk else '✅正常'}")
            try:
                if not _plan2:
                    _plan2 = _plan or json.load(open(PLAN_PATH, encoding='utf-8'))
                _limits = _plan2.get("global_limits", {})
                _is_sim = not _limits.get("qmt_fast_enabled", False)
                _ac = _limits.get("_call_auction_confirmed", 0)
                if _ac > 0:
                    # 板块热度(游资分层晋级率): 1进2率>0才算主线
                    _all_mk = ContextInfo.get_market_data()
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
                    # ══ 弱转强竞价买点 (9:25, 陈小群: 半仓试错) ══
                    if _SIG_READY and _sig_wts and _wts_candidates:
                        _wts_mk = ContextInfo.get_market_data()
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
                # ── 弱转强竞价 (独立于_ac, 9:25触发) ──
                if _now >= '092500' and _SIG_READY and _sig_wts and _wts_candidates:
                    _wts_mk = ContextInfo.get_market_data()
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
                            _fmk = ContextInfo.get_market_data()
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

