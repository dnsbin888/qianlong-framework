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
            mk = ContextInfo.get_market_data()
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
        _wts_mk = ContextInfo.get_market_data()
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
            _daban_mk = ContextInfo.get_market_data()
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
                        # 🆕 按target路由
                        if _pool_target == "daban":
                            # → 打板候选池 → confirm_board确认
                            _daban_candidates.append({"symbol": _tdx_sym, "score": 60, "industry": "", "source": f"TDX·{_pool_label}"})
                            print(f"[TDX] 🎯 {_tdx_sym} → 打板候选池 ({_pool_label})")
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
        enabled, pos_pct, stop_loss, take_profit, signal_set, best_ml, min_ml, industry, time_window, yesterday_volume = cache

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

