"""方案C: 专业版信号表 — 12列, 可直接执行交易"""
import sys, os, json
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.special import erfinv

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")


def _load_names():
    names = {}
    csv_path = os.path.join(os.path.dirname(__file__), "stock_names_full.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip().split(",")
                if len(p) >= 2: names[p[0]] = p[1]
    return names

_NAMES = None

def get_name(sym):
    global _NAMES
    if _NAMES is None: _NAMES = _load_names()
    clean = sym.replace('sh','').replace('sz','').replace('bj','')
    return _NAMES.get(sym, _NAMES.get(clean, sym))


_IND_MAP = None

# ── 策略生命周期门控 ──
_LIFECYCLE_CACHE = None

def _load_lifecycle():
    """加载策略注册表, 返回 {strategy_id: lifecycle}"""
    global _LIFECYCLE_CACHE
    if _LIFECYCLE_CACHE is None:
        try:
            reg = json.load(open(r"D:\quant_framework\strategy_registry.json", encoding="utf-8"))
            _LIFECYCLE_CACHE = {s['id']: s.get('lifecycle', 'draft')
                              for s in reg.get('strategies', [])}
        except Exception:
            _LIFECYCLE_CACHE = {}
    return _LIFECYCLE_CACHE

def _strategy_lifecycle_ok(sid):
    """策略生命周期是否允许生成信号: draft/backtested/paper/live 可以, degraded/retired 不行
    draft=信号可出供观察, 但auto_enabled=false(不自动交易)。degraded/retired=完全不生成信号。"""
    lc = _load_lifecycle().get(sid, 'draft')
    return lc not in ('degraded', 'retired')

def get_industry(sym):
    global _IND_MAP
    if _IND_MAP is None:
        _IND_MAP = {}
        p = os.path.join(os.path.dirname(__file__), "data", "stock_industry_map.json")
        if os.path.exists(p):
            try:
                raw = json.load(open(p, encoding="utf-8"))
                _IND_MAP = raw.get("symbol_to_industry", raw)
            except Exception: pass
    clean = sym.replace('sh','').replace('sz','').replace('bj','')
    return _IND_MAP.get(sym, _IND_MAP.get(clean, ""))


def classify(sym):
    s = sym.lower()
    if any(s.startswith(p) for p in ('sh000','sz399','sz98','bj8','bj9','sh88','sz88')): return 'INDEX'
    if any(s.startswith(p) for p in ('sh51','sh58','sh56','sh15','sz15','sz16','sh50')): return 'ETF'
    if any(s.startswith(p) for p in ('sh11','sh12','sh13','sh14','sz11','sz12','sz13','sz18')): return 'BOND'
    return 'STOCK'


def compute_change(df):
    """计算今日涨跌幅"""
    try:
        c = df['close'].values
        if len(c) < 2: return 0
        return round((c[-1] - c[-2]) / c[-2] * 100, 2)
    except: return 0


def compute_vol_ratio(df):
    """计算量比"""
    try:
        v = df['volume'].values
        n = len(v)
        if n < 6: return 1.0
        v5 = np.mean(v[-5:])
        v20 = np.mean(v[-20:]) if n >= 20 else v5
        return round(v5 / (v20 + 1e-9), 2)
    except: return 1.0


def compute_ret_5d(df):
    """计算5日涨幅"""
    try:
        c = df['close'].values
        n = len(c)
        if n < 6: return 0
        return round((c[-1] - c[-6]) / c[-6] * 100, 2)
    except: return 0


def compute_turnover(df):
    """真实换手率 (成交量/流通股本) — 有outstanding字段时精确, 无时估算"""
    try:
        v = df['volume'].values
        n = len(v)
        if n < 5: return 0
        avg_vol = np.mean(v[-5:])
        # 优先用真实流通股本
        if 'outstanding' in df.columns:
            out = float(df['outstanding'].values[-1])
            if out > 0:
                return round(avg_vol / out * 100, 2)  # 真实换手%
        # 兜底: 对数估算
        c = df['close'].values
        avg_price = np.mean(c[-5:])
        amount = avg_vol * avg_price
        return round(np.log10(max(amount, 1)) * 2, 1)
    except: return 0


# ═══ 共享 ML 管线函数 (供 signals/ml/daily.py 复用, 确保训推同源) ═══

def rank_gaussianize(scores_dict):
    """Rank→Gaussianize: 原始分→截面排名→正态分布映射 (Numerai方法)
    优势: ①分数跨天可比 ②模型间可比 ③不受原始尺度影响
    返回: score映射到N(50,15), 68%在35-65, 95%在20-80
    """
    if not scores_dict:
        return {}
    items = [(s, v['score']) for s, v in scores_dict.items() if v and v.get('score')]
    if len(items) < 5:
        return scores_dict
    items.sort(key=lambda x: x[1])
    n = len(items)
    result = {}
    for i, (sym, _) in enumerate(items):
        pct = max(0.001, min(0.999, (i + 0.5) / n))
        gauss = np.sqrt(2) * erfinv(2 * pct - 1)
        score = round(50 + gauss * 15, 1)
        result[sym] = {**scores_dict[sym], 'score': score, '_rank_pct': round(pct*100, 1)}
    return result


def ml_combine_scores(all_signals, sd):
    """ML 秩平均 + 行业市值中性化
    Args:
        all_signals: {sym: {lgbm:{score,lv}, xgb:{score,lv}, ridge:{score,lv}, cb?:{score,lv}}}
        sd: {sym: DataFrame} 股票数据 (中性化需要)
    Returns:
        (neutralized, pct_map): neutralized={sym: score}, pct_map={model: {sym: {score, ...}}}
    """
    # 高斯化各模型
    pct_map = {}
    for key in ['lgbm', 'xgb', 'ridge', 'cb']:
        subset = {s: m.get(key) for s, m in all_signals.items() if m.get(key)}
        pct_map[key] = rank_gaussianize(subset)

    # 秩平均
    syms, scores = [], []
    for sym in all_signals:
        sv = []
        for key in ['lgbm', 'xgb', 'ridge', 'cb']:
            v = pct_map[key].get(sym, {})
            if v.get('score'):
                sv.append(v['score'])
        if sv:
            syms.append(sym)
            scores.append(np.mean(sv))

    if len(syms) < 10:
        return dict(zip(syms, [round(s, 1) for s in scores])), pct_map

    # 行业+市值中性化
    try:
        from exposure import rank_neutralize, market_cap_neutralize
        scores = rank_neutralize(syms, scores)
        scores = market_cap_neutralize(syms, scores, sd)
        neutralized = {s: round(sc, 1) for s, sc in zip(syms, scores)}
    except Exception:
        neutralized = {s: round(sc, 1) for s, sc in zip(syms, scores)}

    return neutralized, pct_map


def main():
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)

    # === 第0层 + 第0.5层: 过滤器链 (v3.0 可扩展) ===
    _orig_count = len(sd)
    try:
        from stock_filters import apply_all
        _pool, _stats = apply_all(sd)
        sd = {k: v for k, v in sd.items() if k in _pool}
        print(f"候选池过滤: {len(sd)}/{_orig_count} 只通过")
        for name, n in sorted(_stats.items(), key=lambda x: -x[1]):
            if n: print(f"  └ {name}: {n}只")
    except Exception as e:
        print(f"候选池过滤跳过: {e}")

    all_signals = {}

    # LGBM-Stock — 只推A股(与XGBoost共振), 全市场模型已废弃(被覆盖+R²≤0)
    # P1-3: 用 model_path 参数直接指定模型, 消除文件交换竞态
    if _strategy_lifecycle_ok("ml_daily"):
        try:
            _stock_model = r"D:\quant_framework\lgbm_model_stock.pkl"
            if os.path.exists(_stock_model):
                _skip_s = ('sh000','sh11','sh12','sh13','sh14','sh15','sh2','sh5','sz399','sz11','sz12','sz13','sz15','sz16','sz18','sz5','bj')
                _sd_stock = {k:v for k,v in sd.items() if not k.startswith(_skip_s)}
                from lgbm_strategy import generate_lgbm_signals
                for s in generate_lgbm_signals(_sd_stock, top_k=50, min_score=25, model_path=_stock_model):
                    sym = s['symbol']
                    all_signals.setdefault(sym, {})['lgbm'] = {'score': s['score'], 'lv': s['buy_signal']}
        except Exception as e: print(f"LGBM-Stock: {e}")
    else:
        print("LGBM: lifecycle未通过验证, 跳过")

    # XGBoost: 与LGBM同池 (统一起跑线)
    _skip_s = ('sh000','sz399','sz98','bj','sh88','sz88','sh51','sh58','sh56','sh15','sh50','sz15','sz16','sh11','sh12','sh13','sh14','sz11','sz12','sz13','sz18','sh2','sh5','sz5')
    if _strategy_lifecycle_ok("ml_daily"):
        try:
            from xgb_factor_weight import generate_xgb_signals
            for s in generate_xgb_signals(_sd_stock, top_k=30, min_score=20):
                sym = s['symbol']
                all_signals.setdefault(sym, {})['xgb'] = {'score': s['score'], 'lv': s['buy_signal']}
        except Exception as e: print(f"XGB: {e}")
    else:
        print("XGB: lifecycle未通过验证, 跳过")

    # 市场状态 regime — 多处依赖(阈值/仓位/中性化),必须无条件定义(原在CatBoost块内,禁用后需上提)
    from market_regime import detect_regime
    regime = detect_regime(sd)
    # 情绪周期检测 (游资铁律: 退潮期→全策略降仓/空仓)
    _sentiment_stage = "unknown"
    _sentiment_mult = 1.0  # 情绪仓位乘数: startup=0.5, ferment=1.5, climax=1.0, retreat=0
    _sent_advance, _sent_limit_down, _sent_limit_up = 0.5, 0, 0  # 兜底
    try:
        from sentiment_cycle import classify as _sent_cls
        _sent_result = _sent_cls(sd, regime)
        _sentiment_stage = _sent_result.get("stage", "unknown")
        _sentiment_mult = {"startup": 0.5, "ferment": 1.5, "climax": 1.0, "retreat": 0}.get(_sentiment_stage, 1.0)
        # 保存QMT透传字段 (防后续循环变量_shadow)
        _sent_advance = _sent_result.get("advance_ratio", 0.5)
        _sent_limit_down = int(_sent_result.get("limit_down", 0))
        _sent_limit_up = int(_sent_result.get("limit_up", 0))
        if _sentiment_stage == "retreat":
            print(f"⚠️ 情绪退潮期 → 反转/打板策略暂停, ML降仓50%")
    except Exception as e:
        print(f"情绪周期跳过: {e}")
        _sent_advance, _sent_limit_down, _sent_limit_up = 0.5, 0, 0
    # CatBoost Meta — ⚠️已禁用(2026-07-12): 标签退化(y=过去5日收益=特征mom)+stacking泄露,待带回测重建
    _USE_CATBOOST = False
    try:
        if _USE_CATBOOST and os.path.exists(r"D:\quant_framework\catboost_model.cbm"):
            from train_catboost import generate_meta_score
            regime = detect_regime(sd)
            for sym in all_signals:
                l = all_signals[sym].get('lgbm', {})
                x = all_signals[sym].get('xgb', {})
                if not l and not x: continue
                ms = generate_meta_score(l.get('score'), x.get('score'), sd, sym, regime)
                if ms and ms >= 10:
                    all_signals[sym]['cb'] = {'score': ms, 'lv': 5 if ms>=90 else 4 if ms>=80 else 3 if ms>=70 else 2 if ms>=60 else 1}
    except Exception as e: print(f"CB-Meta: {e}")

    # === Ridge 线性模型 — 第三票 (最大算法多样性, 2026-07-13) ===
    if _strategy_lifecycle_ok("ml_daily"):
        try:
            print("[Ridge] 开始...", flush=True)
            from ridge_model import predict_scores as ridge_predict
            _ridge_scores = ridge_predict(sd)
            print(f"[Ridge] 预测完成: {len(_ridge_scores)}只", flush=True)
            _ridge_top = sorted(_ridge_scores.items(), key=lambda x: -x[1])[:30]
            for sym, sc in _ridge_top:
                all_signals.setdefault(sym, {})['ridge'] = {'score': sc, 'lv': 5 if sc>=90 else 4 if sc>=80 else 3 if sc>=70 else 2 if sc>=60 else 1}
            print(f"Ridge: {len(_ridge_scores)} 只 → Top30入信号表")
        except Exception as e:
            print(f"Ridge跳过: {e}")
    else:
        print("Ridge: lifecycle未通过验证, 跳过")

    # === 反转策略: 弱转强 + 超跌反弹 (策略架构蓝图 v1.0 阶段1) ===
    _reversal_signals = {}
    if _sentiment_stage == "retreat":
        print(f"反转策略: 情绪退潮期→空仓")
    elif _strategy_lifecycle_ok("oversold_bounce") or _strategy_lifecycle_ok("weak_to_strong"):
        try:
            from reversal_strategy import generate_weak_to_strong, generate_oversold_bounce
            if _strategy_lifecycle_ok("weak_to_strong"):
                for s in generate_weak_to_strong(sd):
                    sym = s.pop('symbol')
                    _reversal_signals.setdefault(sym, {})['weak_to_strong'] = s
                print(f"弱转强: {len(_reversal_signals)} 候选")
            if _strategy_lifecycle_ok("oversold_bounce"):
                for s in generate_oversold_bounce(sd):
                    sym = s.pop('symbol')
                    _reversal_signals.setdefault(sym, {})['oversold_bounce'] = s
                total_rev = sum(1 for v in _reversal_signals.values() if v)
                print(f"超跌反弹: 并入, 反转信号覆盖{total_rev}只")
        except Exception as e:
            print(f"反转策略跳过: {e}")
    else:
        print("反转策略: lifecycle未通过验证, 跳过")

    # === 打板策略候选池 (日线扫描 → QMT次日实时确认) ===
    _daban_signals = []
    if _strategy_lifecycle_ok("daban"):
        # 情绪周期门控: 退潮期空仓, 发酵期放宽 (2026-07-14)
        _sentiment_stage = "unknown"
        _daban_boost = 1.0
        try:
            from sentiment_cycle import classify as _sentiment_classify
            _sc = _sentiment_classify(sd, regime)
            _sentiment_stage = _sc.get("stage", "unknown")
            if _sentiment_stage == "retreat":
                print(f"打板候选池: 退潮期→空仓 (不生成候选)")
                _daban_signals = []
            else:
                _daban_boost = {"startup": 0.5, "ferment": 1.5, "climax": 1.0}.get(_sentiment_stage, 1.0)
        except Exception as e:
            print(f"情绪周期跳过: {e}")
        if not _daban_signals and _sentiment_stage != "retreat":
            try:
                from daban_quality import generate_daban_candidates
                _daban_signals = generate_daban_candidates(sd)
                if _daban_boost > 1.0:
                    _daban_signals = [s for s in _daban_signals if s.get('score',0) > 25]
                print(f"打板候选池: {len(_daban_signals)} 只 (情绪:{_sentiment_stage})")
            except Exception as e:
                print(f"打板策略跳过: {e}")
    else:
        print("打板策略: lifecycle未通过验证, 跳过")

    # ═══ 原始分秩平均 (行业标准: 树模型不需要Gaussianize+中性化, 截面标准化会损害LGBM) ═══
    # 广发金工2025: 截面标准化→树模型退化; 中性化→显著退化。直接用原始分。
    neutralized = {}
    pct_map = {}
    for key in ['lgbm', 'xgb', 'ridge', 'cb']:
        pct_map[key] = {s: m.get(key) for s, m in all_signals.items() if m.get(key) and m.get(key).get('score')}
    # 分数同尺度化: 每模型独立min-max→0-100 (防LGBM 90-100淹没XGB 60-67)
    _norm_range = {}
    for key in ['lgbm', 'xgb', 'ridge']:
        vals = [v.get('score', 0) for v in pct_map[key].values() if v.get('score', 0) > 0]
        if len(vals) >= 5:
            _min, _max = min(vals), max(vals)
            _norm_range[key] = (_min, _max, _max - _min)
        else:
            _norm_range[key] = (0, 100, 100)
    for key in ['lgbm', 'xgb', 'ridge']:
        _min, _max, _rng = _norm_range[key]
        for sym, v in pct_map[key].items():
            _raw = v.get('score', 0)
            if _raw > 0 and _rng > 0:
                v['score_norm'] = round((_raw - _min) / _rng * 100, 1)
            else:
                v['score_norm'] = _raw

    for sym in all_signals:
        scores = []
        for key in ['lgbm', 'xgb', 'ridge']:
            m = all_signals[sym].get(key)
            if m and m.get('score'):
                scores.append(m['score'])
        if scores:
            neutralized[sym] = round(sum(scores) / len(scores), 1)

    # 动态阈值: 提前加载, 供信号表字段计算 (复用前面 detect_regime 的结果)
    _regime_str_early = regime.get("regime", "sideways") if regime else "sideways"
    try:
        _master2 = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
    except Exception:
        _master2 = {}
    _auto_cfg2 = _master2.get("auto_trade", {})
    _adaptive2 = _auto_cfg2.get("market_adaptive", {})
    _ad2 = _adaptive2.get(_regime_str_early, _adaptive2.get("sideways", {}))
    _ml_min2 = _ad2.get("min_ml_score", _auto_cfg2.get("min_ml_score", 70))
    _pos_max2 = _ad2.get("max_auto_position", _auto_cfg2.get("max_auto_position", 12))
    _n2_auto2 = _ad2.get("n2_auto", True)
    # 置信度门控 (对标游资: 没把握就空仓)
    _cg = _master2.get("confidence_gate", {}) if _master2 else {}
    _min_consensus = _cg.get("min_consensus_for_auto", 2)
    _min_cs_auto = _cg.get("min_combined_score_for_auto", 65)

    # Connors RSI(2) 标记 (超卖 < 10)
    _oversold = {}
    try:
        from stock_filters import connors_rsi
        _oversold = connors_rsi(sd)
    except: pass

    # 龙虎榜标记 (游资关注)
    _lhb = {}
    _hot_sectors = {}
    try:
        from stock_filters import lhb_mark, lhb_sector_heat
        _lhb = lhb_mark()
        _hot_sectors = lhb_sector_heat(sd)
    except: pass

    table = []
    _sector_totals = {}  # 板块集中度累计
    # 排除不可交易品种 (B股/指数/北交所/ETF/债券)
    _skip = ('sh000','sz399','sz98','bj','sh88','sz88','sh51','sh58','sh56','sh15','sh50','sz15','sz16','sh11','sh12','sh13','sh14','sz11','sz12','sz13','sz18','sh2','sh5','sz5')
    for sym, models in all_signals.items():
        if sym.lower().startswith(_skip):
            continue
        l = pct_map['lgbm'].get(sym, models.get('lgbm', {}))
        x = pct_map['xgb'].get(sym, models.get('xgb', {}))
        r = pct_map['ridge'].get(sym, models.get('ridge', {}))
        c = pct_map['cb'].get(sym, models.get('cb', {}))

        # 归一化后取最优 (L/X/R同尺度0-100可比)
        l_score = l.get('score_norm', 0) if isinstance(l, dict) else 0
        x_score = x.get('score_norm', 0) if isinstance(x, dict) else 0
        r_score = r.get('score_norm', 0) if isinstance(r, dict) else 0
        raw_combined = max(l_score, x_score) if (l_score > 0 or x_score > 0) else r_score
        combined = raw_combined  # LGBM主力排序, neutralized作参考
        n_models = sum(1 for m in [l, x, r, c] if m)
        _ens_n = 3 if not _USE_CATBOOST else 4
        consensus = f"{n_models}/{_ens_n}"
        n_dots = '🟢' * n_models + '🔴' * (_ens_n - n_models)

        # 信号等级由百分位统一算 (见下方)

        # 行情
        df = sd.get(sym)
        close = round(float(df['close'].values[-1]), 2) if df is not None and len(df) > 0 else 0
        chg = compute_change(df)
        vol_r = compute_vol_ratio(df)
        ret5 = compute_ret_5d(df)
        turnover = compute_turnover(df)

        # 仓位先用兜底值, 下面统一算百分位
        pos_pct = 2
        pos_pct = max(2, min(20, pos_pct)) if pos_pct > 0 else 0

        # 板块集中度: 同类累计≤25%  (小型私募标配)
        ind = get_industry(sym)
        if ind and ind != "未知" and pos_pct > 0:
            sector_used = _sector_totals.get(ind, 0)
            if sector_used + pos_pct > 30:
                pos_pct = max(2, 30 - sector_used)
            _sector_totals[ind] = sector_used + pos_pct

        # 两级ATR止损: 软止损(ATR动态)卖半仓 / 硬止损(master)全清
        stop_loss, take_profit, soft_stop_loss = 0, 0, 0
        # 硬止损从master读取 (统一参数源)
        _hard_sl_pct = abs(_master2.get("stop_loss", {}).get("hard", -0.055)) if _master2 else 0.055
        try:
            c_arr2 = df['close'].values; n3 = len(c_arr2)
            rets_atr = np.diff(c_arr2[max(0,n3-21):n3]) / (c_arr2[max(0,n3-21):n3-1] + 1e-9)
            vol_atr = float(np.std(rets_atr)) if len(rets_atr) > 1 else 0.02
            # 软止损: ATR动态, 2% ~ 硬止损之间
            soft_stop_pct = max(0.02, min(vol_atr * 1.0, _hard_sl_pct))
            soft_stop_loss = round(close * (1 - soft_stop_pct), 2)
            # 硬止损: master固定值
            stop_loss = round(close * (1 - _hard_sl_pct), 2)
            take_profit = round(close * (1 + soft_stop_pct * 2), 2)  # 盈亏比基于软止损
        except Exception: pass
        soft_stop_loss = soft_stop_loss or round(close * 0.98, 2)
        stop_loss = stop_loss or round(close * 0.945, 2)
        take_profit = take_profit or round(close * 1.05, 2)

        # HRP 微调 + 行业检查
        try:
            from decision_adapter import process_signals
            sig = [{"symbol": sym, "buy_signal": best_lv or 3, "score": combined, "close": close}]
            orders = process_signals(sig, sd, {"total_equity": 1_000_000, "positions": []})
            if orders:
                o = orders[0]
                stop_loss = o.get("stop_loss", stop_loss)
                take_profit = o.get("take_profit", [take_profit])[0]
        except Exception: pass

        # 六状态决策建议
        l_pct = l.get('score', 0) or 0
        x_pct = x.get('score', 0) or 0
        r_pct = r.get('score', 0) or 0
        ridge_pct = r_pct  # 别名，供 decision 逻辑用
        cb_raw = c.get('score', 0) or 0

        # 1. 行业拦截
        sector_blocked = False
        try:
            from sector_limit import check_sector_limit
            ok, _ = check_sector_limit(sym, [], 1_000_000)
            sector_blocked = not ok
        except Exception: pass

        # 2. 分歧检测
        both = l_pct > 0 and x_pct > 0
        diverge = both and abs(l_pct - x_pct) > 30

        # 3. CB裁判判断 (CB有分才判断, 没分不理)
        cb_scored = c and c.get('score') is not None and c.get('score', 0) > 0
        cb_deny = cb_scored and n_models < 2 and not both and cb_raw < 40

        # 决策
        if sector_blocked:
            decision = '🚫 行业超限'
        elif cb_deny:
            decision = f'⏸️ CB降级({cb_raw:.0f})'
        elif diverge:
            hi = 'L' if l_pct > x_pct else 'X'
            decision = f'⚠️ 分歧({hi}高)'
        elif both and not diverge:
            pct_str = f'{pos_pct:.0f}%' if pos_pct > 0 else f'Lv{best_lv}'
            decision = '✅ 共振'
        elif l_pct > 0:
            decision = '📊 LGBM'
        elif x_pct > 0:
            decision = '📊 XGB'
        elif ridge_pct > 0:
            decision = '📊 Ridge'
        else:
            decision = '—'

        # 模型名称 (替代无意义的 1/3)
        _model_names = []
        if l_pct > 0: _model_names.append('L')
        if x_pct > 0: _model_names.append('X')
        if ridge_pct > 0: _model_names.append('R')
        if len(_model_names) >= 2:
            _consensus_label = '+'.join(_model_names)
            _n_dots = '🟢🟢🟢'[:len(_model_names)] if len(_model_names)>=3 else '🟢🟢'
        elif len(_model_names) == 1:
            _consensus_label = _model_names[0]
            _n_dots = '🔵'
        else:
            _consensus_label = '—'; _n_dots = '🔴'
        _real_n = len(_model_names)

        # 动态持有天数 + 动态止盈 (市场自适应)
        _dyn_hold = 7
        _dyn_tp = take_profit
        if _regime_str_early == 'bull': _dyn_hold = 10; _dyn_tp = round(close * 1.15, 2)
        elif _regime_str_early == 'bear': _dyn_hold = 5; _dyn_tp = round(close * 1.08, 2)

        # 决策排序: 共振=1, L=2, X=3, R=4, 分歧=5, 降级=6, 拦截=7
        dp = (1 if both and not diverge
              else 2 if l_pct > 0 else 3 if x_pct > 0 else 4 if ridge_pct > 0
              else 5 if diverge else 6 if cb_deny else 7 if sector_blocked else 9)
        table.append({
            "decision": decision,
            "decision_priority": dp,
            "signal_id": f"{sym}_{datetime.now().strftime('%Y%m%d')}_{decision.split()[0].replace('✅','').replace('📊','').replace('🔄','').replace('🎯','').replace('✓','').replace('⚠️','').strip()[:10]}",
            "symbol": sym,
            "name": get_name(sym),
            "industry": get_industry(sym),
            "type": classify(sym),
            "close": close,
            "change_pct": chg,
            "vol_ratio": vol_r,
            "ret_5d": ret5,
            "turnover": turnover,
            "combined_score": combined,
            "quality_score": round(min(100, max(10,
                (min(combined,100)/100) * 35 +
                (10 if vol_r > 1.5 else 5 if vol_r > 1.0 else 0) +
                (10 if chg > 2 else 5 if chg > 0 else 0) +
                (10 if _oversold.get(sym, False) else 0) +
                (15 if _regime_str_early in ('bull','sideways') else 10) +
                (10 if _real_n >= 2 else 5)
            )), 1),
            "consensus": _consensus_label,
            "consensus_dots": _n_dots,
            "n_models": _real_n,
            "position_pct": round(pos_pct, 1),
            "hold_days": _dyn_hold,
            "stop_loss": stop_loss,
            "soft_stop_loss": soft_stop_loss,
            "soft_stop_loss_pct": round(soft_stop_pct, 4),
            "take_profit": _dyn_tp,
            "lgbm_score": round(l.get('score_norm', l.get('score', 0) or 0), 1),
            "xgb_score": round(x.get('score_norm', x.get('score', 0) or 0), 1),
            "ridge_score": round(r.get('score_norm', r.get('score', 0) or 0), 1),
            "cb_score": c.get('score', '') or '',
            # 门控: 排序分达标即可(≥1模型), 2模型→满仓 1模型→半仓
            "auto_enabled": bool(
                combined > max(_ml_min2, _min_cs_auto)
                and round(pos_pct, 1) <= _pos_max2
                and (n_models >= 2 or _n2_auto2)  # 1模型需市场允许
            ),
            "oversold": '超卖' if _oversold.get(sym, False) else '',
            "lhb": bool(_lhb.get(sym, False)),
            "lhb_sector": bool(_hot_sectors.get(get_industry(sym), 0) > 0),
            # 增强: Connors超卖(技术) + LHB板块(资金) → 仓位放大
            "position_pct": round(min(
                pos_pct * (1.3 if _oversold.get(sym) else 1.0) * (1.3 if _hot_sectors.get(get_industry(sym), 0) > 0 else 1.0),
                15  # 硬上限
            ), 1) if pos_pct > 0 else round(pos_pct, 1),
        })

    # === 反转策略信号追加到信号表 ===
    for sym, rev_types in _reversal_signals.items():
        df = sd.get(sym)
        if df is None or len(df) < 5:
            continue
        close = round(float(df['close'].values[-1]), 2)
        chg = compute_change(df)
        # 取该票最强的反转信号
        best = max(rev_types.values(), key=lambda x: x.get('score', 0))
        table.append({
            "symbol": sym,
            "name": get_name(sym),
            "industry": get_industry(sym),
            "type": classify(sym),
            "close": close,
            "change_pct": chg,
            "vol_ratio": compute_vol_ratio(df),
            "ret_5d": compute_ret_5d(df),
            "turnover": compute_turnover(df),
            "signal_id": f"{sym}_{datetime.now().strftime('%Y%m%d')}_反转",
            "combined_score": best.get('score', 50),
            "quality_score": round(min(100, 40 + (best.get('score',50)-50)*0.6), 1),
            "consensus": "反转",
            "consensus_dots": "🔄",
            "n_models": 1,
            "decision": f"🔄 {best.get('strategy_id', 'reversal')}",
            "decision_priority": 3,
            "position_pct": round(max(2, best.get('score', 50) / 50 * 5), 1),
            "hold_days": best.get('hold_days', 3),
            "stop_loss": best.get('stop_loss', round(close * 0.945, 2)),
            "soft_stop_loss": best.get('soft_stop_loss', round(close * 0.97, 2)),
            "take_profit": best.get('take_profit', round(close * 1.05, 2)),
            "lgbm_score": "", "xgb_score": "", "cb_score": "",
            "auto_enabled": False,  # 策略终判通道: confirm_wts/oversold后才执行
            "oversold": False, "lhb": False, "lhb_sector": False,
            "reason": best.get('reason', ''),
        })

    # === 打板候选追加到信号表 ===
    for _ds in _daban_signals:
        _dsym = _ds['symbol']
        _ddf = sd.get(_dsym)
        if _ddf is None or len(_ddf) < 5:
            continue
        _dclose = round(float(_ddf['close'].values[-1]), 2)
        table.append({
            "symbol": _dsym,
            "name": get_name(_dsym),
            "industry": get_industry(_dsym),
            "type": classify(_dsym),
            "close": _dclose,
            "change_pct": compute_change(_ddf),
            "vol_ratio": compute_vol_ratio(_ddf),
            "ret_5d": compute_ret_5d(_ddf),
            "turnover": compute_turnover(_ddf),
            "signal_id": f"{_dsym}_{datetime.now().strftime('%Y%m%d')}_打板",
            "combined_score": _ds.get('score', 50),
            "quality_score": round(min(100, 35 + (_ds.get('score',50)-50)*0.5), 1),
            "consensus": "打板",
            "consensus_dots": "🎯",
            "n_models": 1,
            "decision": f"🎯 {_ds.get('strategy_id', 'daban')}",
            "decision_priority": 4,
            "position_pct": round(max(1, _ds.get('score', 50) / 100 * 3), 1),
            "hold_days": 1,
            "stop_loss": _ds.get('stop_loss', round(_dclose * 0.95, 2)),
            "soft_stop_loss": round(_dclose * 0.97, 2),
            "take_profit": _ds.get('take_profit', round(_dclose * 1.05, 2)),
            "lgbm_score": "", "xgb_score": "", "cb_score": "",
            "auto_enabled": False,  # 策略终判通道: confirm_board实时确认后才执行
            "oversold": False, "lhb": False, "lhb_sector": False,
            "reason": _ds.get('reason', ''),
        })

    table.sort(key=lambda r: (-r['n_models'], -r['combined_score']))

    # 市场状态 (一次检测, 多处复用)
    try:
        from market_regime import detect_regime
        _regime = detect_regime(sd)
    except Exception:
        _regime = {"regime": "sideways", "position_scale": 0.7}
    _regime_str = _regime.get("regime", "sideways")

    # 连续百分位: 仓位+信号等级 (行业标准)
    _n = len(table)
    lv_map = {5: 'Lv5 强买', 4: 'Lv4 买入', 3: 'Lv3 关注', 2: 'Lv2 观察', 1: 'Lv1', 0: '-'}
    for _i, _r in enumerate(table):
        _pct_rank = _i / max(_n - 1, 1)
        _pos = round(12 - _pct_rank * 10, 0)
        _pos = max(2, min(12, _pos))
        try:
            _pos = round(_pos * _regime.get('position_scale', 1.0), 0)
            _pos = round(_pos * _reg.get('position_scale', 1.0), 0)
        except Exception: pass
        _r['position_pct'] = round(_pos, 1)
        # 信号质量调整: 共识+尾部风险 → 优质多买, 劣质少买
        try:
            _sym = _r.get('symbol', '')
            _df = sd.get(_sym)
            if _df is not None and len(_df) >= 20:
                _c = _df["close"].values
                # 尾部风险: 20日最大单日跌幅
                _dr = np.diff(_c[-21:]) / (_c[-21:-1] + 1e-9)
                _max_drop = min(_dr[-20:]) if len(_dr) >= 20 else 0
                _tail_penalty = min(0, _max_drop * 3)  # -10%→-0.3
                # 共识加分: 3模型+10%, 2模型+0%
                _nm = _r.get('n_models', 2)
                _cons_bonus = 0.10 if _nm >= 3 else 0.0
                _quality = max(0.6, min(1.2, 1.0 + _cons_bonus + _tail_penalty))
                _r['position_pct'] = round(max(2, _pos * _quality), 1)
                _r['_quality'] = round(_quality, 2)
                # 动态持有天数: 基础(市场) × 质量 × 波动率
                _base_days = 10 if _regime.get('regime') == 'bull' else 7 if _regime.get('regime') == 'sideways' else 5
                _vol = float(np.std(_dr[-20:])) if len(_dr) >= 20 else 0.02
                _vol_mult = 1.2 if _vol < 0.02 else 0.7 if _vol > 0.04 else 1.0
                _hold_days = round(_base_days * _quality * _vol_mult)
                _r['hold_days'] = max(2, min(21, _hold_days))
        except Exception:
            pass
        # 信号等级: 百分位→Lv (前10%=Lv5, 前30%=Lv4, 前50%=Lv3, 其余=Lv2)
        _r['signal'] = lv_map.get(
            5 if _pct_rank < 0.1 else 4 if _pct_rank < 0.3 else 3 if _pct_rank < 0.5 else 2 if _pct_rank < 0.8 else 1, 'Lv1'
        )

    # 写ML缓存(供QMT实时策略用)
    try:
        from ml_factors import update_cache_from_table
        from ml_score_cache import update_cache
        update_cache(table)
        update_cache_from_table()
        n = update_cache_from_table()
        print(f"ML cache: {n} entries")
    except Exception as e: print(f"ML cache: {e}")

    out_path = os.path.join(os.path.dirname(__file__), "data", "signal_table.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 给第一条加时间戳供前端显示
    if table: table[0]['_generated_at'] = datetime.now().strftime("%m-%d %H:%M")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as _f:
        json.dump(table, _f, ensure_ascii=False, indent=2, default=str)
    # 写入后校验
    with open(tmp, "r", encoding="utf-8") as _f:
        if len(_f.read()) < 10:
            raise IOError("signal_table写入校验失败(文件过短)")
    os.replace(tmp, out_path)  # 原子替换
    print(f"{len(table)} rows → {out_path}")

    # QMT执行配置 (研究-执行分离: 潜龙算好, QMT只读不思考)
    qmt_config = {}
    for r in table:
        if r.get('type') not in ('STOCK', 'ETF'): continue
        qmt_config[r['symbol']] = {
            "lgbm": r.get('lgbm_score', 0) or 0,
            "xgb": r.get('xgb_score', 0) or 0,
            "cb": r.get('cb_score', 0) or 0,
            "position_pct": r.get('position_pct', 0) or 0,
            "stop_loss": round(r.get('stop_loss', 0) or 0, 2),
            "take_profit": round(r.get('take_profit', 0) or 0, 2),
            "industry": r.get('industry', ''),
            "decision": r.get('decision', ''),
        }
    qmt_config["_daily_limits"] = {"max_loss_pct": 5, "max_trades": 5, "signal_limits": {
        "竞价抢筹": {"max_pct": 30, "max_count": 1},
        "打板·一封": {"max_pct": 15, "max_count": 3},
        "盘中突破": {"max_pct": 30, "max_count": 2},
        "尾盘急拉": {"max_pct": 20, "max_count": 1},
        "打板·二封": {"max_pct": 25, "max_count": 2},
    }}
    # 连板天数计算 (供QMT加权用)
    def _count_board_days(sym, df):
        try:
            c = df['close'].values; h = df['high'].values
            n = len(c); lim = 0.20 if sym.startswith(('sh68','sz30')) else 0.098
            days = 0
            for i in range(n-1, max(n-10, -1), -1):
                if c[i] >= c[i-1] * (1 + lim) * 0.99:
                    days += 1
                else: break
            return days
        except: return 0
    _board_map = {s: _count_board_days(s, sd.get(s, pd.DataFrame())) for s in sd}
    qmt_config["board_days_map"] = {s: d for s, d in _board_map.items() if d > 0}  # 只传有连板的

    qmt_config["daban_candidates"] = [
        {"symbol": s["symbol"], "score": s.get("score", 0), "close": s.get("close", 0),
         "industry": get_industry(s["symbol"])}
        for s in _daban_signals if s.get("score", 0) > 30]

    qmt_config["weak_to_strong_candidates"] = [
        {"symbol": s["symbol"], "score": s.get("score", 0), "close": s.get("close", 0),
         "industry": get_industry(s["symbol"]),
         "board_days": _board_map.get(s["symbol"], 0)}
        for s in _reversal_signals.values() if s and "weak_to_strong" in s
        for s in [s["weak_to_strong"]]]
    # 情绪数据透传 → QMT (打板双刀+全局门控)
    qmt_config["sentiment_stage"] = _sentiment_stage
    qmt_config["advance_ratio"] = round(_sent_advance, 2)
    qmt_config["limit_down_count"] = _sent_limit_down
    qmt_config["limit_up_count"] = _sent_limit_up
    qmt_config["market_regime"] = regime.get("regime", "sideways") if regime else "sideways"

    qmt_path = os.path.join(os.path.dirname(__file__), "data", "qmt_trade_config.json")
    _qtmp = qmt_path + ".tmp"
    with open(_qtmp, "w", encoding="utf-8") as _f:
        json.dump(qmt_config, _f, ensure_ascii=False, indent=2, default=str)
    os.replace(_qtmp, qmt_path)
    print(f"QMT config: {len(qmt_config)-1} stocks + limits → {qmt_path}")

    # E369: 自动交易计划 (预计算文件, QMT读本地)
    def _isIndexSymbol(sym):
        s = sym.lower()
        return s.startswith('sh88') or s.startswith('sz88') or s.startswith('sh000') or s.startswith('sz399')
    # 保留上次的风控开关状态 (不被每日信号生成覆盖)
    _saved_switches = {"circuit_breaker": False, "qmt_fast_enabled": True, "ai_auto_enabled": False}
    try:
        from master_switch import preserve_switch_state
        _old_plan = {}
        _old_pp = plan_path  # auto_trade_plan.json 路径
        if os.path.exists(_old_pp):
            with open(_old_pp, "r", encoding="utf-8") as _of:
                _old_plan = json.load(_of)
        _saved_switches = preserve_switch_state(_old_plan)
    except Exception:
        pass

    # 板块强弱: 行业内平均涨跌 → QMT反转时校验 (板块不能也在跌)
    _sector_chg = {}
    for r in table:
        _ind = r.get('industry', '')
        if _ind:
            _sector_chg.setdefault(_ind, []).append(r.get('change_pct', 0) or 0)
    _sector_strength = {k: round(sum(v)/len(v), 2) for k, v in _sector_chg.items() if len(v) >= 2}

    # 统一止盈止损参数: 从 trade_config_master 透传到 auto_trade_plan
    _tp_master = _master2.get("take_profit", {})
    _sl_master = _master2.get("stop_loss", {})
    auto_plan = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "L4",
        "global_limits": {
            "max_daily_trades": qmt_config["_daily_limits"]["max_trades"],
            "max_daily_loss_pct": qmt_config["_daily_limits"]["max_loss_pct"],
            "circuit_breaker": _saved_switches.get("circuit_breaker", False),
            "qmt_fast_enabled": _saved_switches.get("qmt_fast_enabled", True),
            "ai_auto_enabled": _saved_switches.get("ai_auto_enabled", False),
            "_regime": _regime_str,
            "_sector_strength": _sector_strength,
            # 统一止盈止损 (来源: trade_config_master.json, 双通道共用)
            "hard_stop_loss_pct": _sl_master.get("hard", -0.055),
            "soft_stop_loss_pct": _sl_master.get("soft", -0.03),
            "tp1": _tp_master.get("tp1", {}),
            "tp2": _tp_master.get("tp2", {}),
            "tp3": _tp_master.get("tp3", {}),
            "limit_up_hold": True,
            "limit_up_drop_sell": -0.03,
        },
        "stocks": {}
    }
    # 复用表循环中已加载的阈值
    _ml_min = _ml_min2; _pos_max = _pos_max2; _n2_auto = _n2_auto2
    _ml_min = _ad2.get("min_ml_score", _auto_cfg2.get("min_ml_score", 70))
    _pos_max = _ad2.get("max_auto_position", _auto_cfg2.get("max_auto_position", 12))
    _n2_auto = _ad2.get("n2_auto", True)

    print(f"  Auto-trade regime={_regime_str} ML>{_ml_min} pos≤{_pos_max}% n2_auto={_n2_auto}")

    # 策略分池: 各策略独立Top-K, 防止ML挤占打板/反转 (2026-07-14)
    _ml_candidates, _rev_candidates, _dab_candidates = [], [], []
    for r in table:
        sym = r['symbol']
        if r.get('type') not in ('STOCK', 'ETF'): continue
        if _isIndexSymbol(sym): continue
        dec = r.get('decision', '')
        if '🔄' in dec: _rev_candidates.append(r)
        elif '🎯' in dec: _dab_candidates.append(r)
        else: _ml_candidates.append(r)
    # 各取Top-K
    _ml_candidates.sort(key=lambda x: -x.get('combined_score',0))
    _rev_candidates.sort(key=lambda x: -x.get('combined_score',0))
    _dab_candidates.sort(key=lambda x: -x.get('combined_score',0))
    _selected = _ml_candidates + _rev_candidates + _dab_candidates
    print(f"  QMT候选池: ML={len(_ml_candidates)} 反转={len(_rev_candidates)} 打板={len(_dab_candidates)} → 共{len(_selected)}只")

    for r in _selected:
        sym = r['symbol']
        if r.get('type') not in ('STOCK', 'ETF'): continue
        if _isIndexSymbol(sym): continue
        cs = r.get('combined_score', 0)
        pos = r.get('position_pct', 0)
        nm = r.get('n_models', 0)  # 共识模型数 (来自triple_vote, 存在每条记录里)

        # 动态双通道: 市场自适应ML门槛 + 共识分级
        auto_ok = cs > _ml_min and pos <= _pos_max
        if auto_ok:
            if nm >= 3:       # 三模型共振 → 全仓位自动
                auto_pos = min(pos, _pos_max)
                reason = f"共振 {_regime_str} ML={cs:.0f} pos={auto_pos:.0f}%"
            elif nm >= 2 and _n2_auto:  # 双模型 + 市场允许 → 半仓自动
                auto_pos = max(2, round(pos * 0.5, 0))
                reason = f"双模 {_regime_str} ML={cs:.0f} pos={auto_pos:.0f}%"
            else:             # 单模型 或 熊市n=2不自动 → 待审批
                auto_ok = False
                auto_pos = 0
                reason = f"单模 待审批" if nm < 2 else f"熊市双模 待审批"
        else:
            reason = f"ML={cs:.0f} pos={pos:.0f}%"
            auto_pos = 0

        auto_plan["stocks"][sym] = {
            "enabled": auto_ok,
            "auto_reason": reason,
            "max_position_pct": auto_pos if auto_ok else 0,
            "min_ml_score": _ml_min,
            "stop_loss": r.get('stop_loss', 0),
            "soft_stop_loss": r.get('soft_stop_loss', 0),
            "soft_stop_loss_pct": r.get('soft_stop_loss_pct', 0.03),
            "take_profit": r.get('take_profit', 0),
            "signal_types": ["竞价抢筹","打板追封","盘中突破","尾盘急拉"],
            "max_order_qty": 0,  # 由QMT侧实时计算
            "approved_at": datetime.now().strftime("%H:%M:%S") if auto_ok else "",
            "name": r.get('name', ''),
            "close": r.get('close', 0),
            "industry": r.get('industry', ''),
        }
    plan_path = os.path.join(os.path.dirname(__file__), "data", "auto_trade_plan.json")
    _ptmp = plan_path + ".tmp"
    with open(_ptmp, "w", encoding="utf-8") as _f:
        json.dump(auto_plan, _f, ensure_ascii=False, indent=2, default=str)
    os.replace(_ptmp, plan_path)
    auto_count = sum(1 for s in auto_plan["stocks"].values() if s["enabled"])
    print(f"Auto plan: {auto_count} auto-enabled / {len(auto_plan['stocks'])} total → {plan_path}")

    # 摘要
    print(f"\n{'='*80}")
    print(f"  {'#':2s} {'代码':12s} {'名称':8s} {'行业':6s} {'现价':>7s} {'涨跌':>6s} {'量比':>5s} {'综合':>4s} {'共识':>6s} {'信号':10s} {'仓位':>6s}")
    print(f"  {'-'*78}")
    for i, r in enumerate(table[:12]):
        print(f"  {i+1:2d} {r['symbol']:12s} {(r['name'] or r['symbol'])[:8]:8s} {r['industry'][:6]:6s} "
              f"¥{r['close']:<6.2f} {r['change_pct']:+5.1f}% {r['vol_ratio']:4.1f}x "
              f"{r['combined_score']:4.0f} {r['consensus_dots']:6s} {r['signal']:10s} {r['position_pct']:5.1f}%")
    print(f"{'='*80}\n")

    # 因子重要性追踪 (Feature Importance Tracking)
    try:
        import sys as _fits; _fits.path.insert(0, r"D:\quant_framework")
        from factor_importance import extract
        extract()
    except Exception as _fe:
        print(f"[FactorImp] 提取失败: {_fe}")

# ═══ 回测模式: 统一引擎 (策略函数→适配器→回测→写registry) ═══

class _BacktestAdapter:
    """策略评分函数 → 回测适配器 (对标 LEAN IAlphaModel)
    引擎每推进一天 → 切数据到当日 → 跑策略函数 → 返候选。无未来函数。
    """
    def __init__(self, strategy_func, stock_data, min_score=50):
        self.strategy_func = strategy_func
        self._sd = stock_data
        self.min_score = min_score
        self._cache = {}
        self.name = "backtest_adapter"

    def handle_bar(self, context, date, data_portal):
        date_str = str(date)[:10]
        if self._cache.get('_date') == date_str:
            signals = self._cache.get('_signals', [])
        else:
            day_data = {}
            for sym, df in self._sd.items():
                sliced = df[df.index <= date]
                if len(sliced) >= 30:
                    day_data[sym] = sliced
            try:
                signals = self.strategy_func(day_data)
            except Exception as _e:
                print(f"  ⚠️ [{self.name}] {date_str}: {_e}")
                signals = []
            if not signals and self._cache.get('_first_empty') is None:
                self._cache['_first_empty'] = date_str
                print(f"  🔍 [{self.name}] {date_str}: {len(day_data)}只数据, 策略返回0信号")
            self._cache = {'_date': date_str, '_signals': signals, '_first_empty': self._cache.get('_first_empty')}
        return [{"symbol": s['symbol'], "price": s.get("close", 0),
                 "power_score": s.get("score", 0), "change_pct": 0, "vol_ratio": 1,
                 "stop_loss": s.get("stop_loss"), "soft_stop_loss": s.get("soft_stop_loss"),
                 "take_profit": s.get("take_profit")}
                for s in signals if s.get('score', 0) >= self.min_score]

    def before_trading(self, *a, **kw): pass
    def after_trading(self, *a, **kw): pass
    def on_trade(self, *a, **kw): pass


def _load_backtest_func(func_path):
    """'module.function' → callable"""
    import importlib
    mod_name, fn_name = func_path.rsplit('.', 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def run_backtest(registry_path=None, start=None, end=None,
                 strategy_filter=None):
    """统一引擎回测模式: 读注册表 → 遍历策略 → 回测 → 门控 → 写registry

    生产 (main) 和回测 (run_backtest) 共享: rank_gaussianize + ml_combine_scores
    """
    import numpy as np, shutil
    from datetime import datetime

    if registry_path is None:
        registry_path = r"D:\quant_framework\strategy_registry.json"

    with open(registry_path, encoding='utf-8') as f:
        registry = json.load(f)

    from data_loader import load_stock_data_cache, get_data_range
    print("📂 加载数据...")
    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=0)  # 0=全量, 不截断
    print(f"   {len(stock_data)} 只股票")

    # 自动对齐回测区间到数据范围
    data_min, data_max = get_data_range()
    if data_min and data_max:
        print(f"   数据范围: {data_min} ~ {data_max}")
        if start is None or end is None:
            d_min, d_max = pd.Timestamp(data_min), pd.Timestamp(data_max)
            if start is None:
                # 铁律: 默认最近2年 (A股非平稳, 旧数据噪声>信号)
                start = (d_max - pd.Timedelta(days=730)).strftime('%Y-%m-%d')
            if end is None:
                end = data_max
        print(f"   回测区间: {start} ~ {end}")

    for strat in registry.get('strategies', []):
        sid = strat['id']
        if strategy_filter and sid != strategy_filter:
            continue
        if not strat.get('enabled', True):
            continue

        func_path = strat.get('signal_func', '')
        profile = strat.get('metrics_profile', 'trend')
        min_score = strat.get('validation', {}).get('min_score', 50)

        print(f"\n{'='*50}")
        print(f"  {strat['name']} ({sid})  type={strat.get('type')}  profile={profile}")
        print(f"{'='*50}")

        try:
            strat_func = _load_backtest_func(func_path)
            adapter = _BacktestAdapter(strat_func, stock_data, min_score=min_score)

            from ruler_trade import measure
            # ML排名策略: 简单止盈+硬止损, 不用三级移动止盈 (CSRank 72%验证配置)
            _is_ml = strat.get('type') == 'trend'
            report = measure(
                stock_data,
                strategy="tdx_resonance", signal_field="",
                start=start, end=end,
                max_positions=strat.get('max_positions', 3),
                position_pct=strat.get('weight', 20) / 100.0,
                hold_days=strat.get('hold_days', 3),
                strategy_obj=adapter, min_power=0,
                entry_buffer=0.01,  # T+1限价缓冲1%
                trail1_profit=0.05 if not _is_ml else 0,
                trail1_drop=0.01 if not _is_ml else 0,   # drop=0 → 禁用移动止盈
                trail2_profit=0.07 if not _is_ml else 0,
                trail2_drop=0.02 if not _is_ml else 0,
            )

            # 衍生指标
            trades = report.get('_trades', [])
            rets = [t.get('return_pct', 0) for t in trades]
            wins = [r for r in rets if r > 0]
            losses = [abs(r) for r in rets if r < 0]
            streak = max_streak = 0
            for r in rets:
                if r < 0:
                    streak += 1; max_streak = max(max_streak, streak)
                else:
                    streak = 0
            short = [t for t in trades if t.get('hold_days', 999) <= 2]

            extra = {
                "avg_win_pct": round(float(np.mean(wins)) * 100, 2) if wins else 0,
                "avg_loss_pct": round(float(np.mean(losses)) * 100, 2) if losses else 0,
                "max_consecutive_losses": max_streak,
                "next_day_positive_rate": round(
                    sum(1 for t in short if t.get('return_pct', 0) > 0) / max(len(short), 1), 2),
            }

            v = strat.get('validation', {})
            v['last_backtest'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            v['period'] = f"{start}→{end}"
            v['sharpe'] = report['sharpe']
            v['calmar'] = report['calmar']
            v['profit_factor'] = report['profit_factor']
            v['win_rate_pct'] = report['win_rate_pct']
            v['n_trades'] = report['n_trades']
            v['total_return_pct'] = report['total_return_pct']
            v['max_drawdown_pct'] = report['max_drawdown_pct']
            v['oos_sharpe'] = report.get('oos', {}).get('avg_test_sharpe', 0)
            v['oos_decay_pct'] = report.get('oos', {}).get('sharpe_decay_pct', 0)
            v.update(extra)
            strat['validation'] = v

            from strategy_metrics import evaluate
            verdict = evaluate(report, extra, profile)

            if verdict['passed']:
                old = strat.get('lifecycle', 'draft')
                strat['lifecycle'] = 'backtested'
                print(f"  ✅ 通过 → lifecycle: {old} → backtested")
            else:
                failed = [k for k, v2 in verdict['gates'].items() if not v2['pass']]
                print(f"  ⚠️ 未通过: {failed} → lifecycle: {strat.get('lifecycle', 'draft')}")

            print(f"  Sharpe={report['sharpe']:.2f}  胜率={report['win_rate_pct']:.0f}%  "
                  f"盈亏比={report['profit_factor']:.2f}  笔数={report['n_trades']}  "
                  f"回撤={report['max_drawdown_pct']:.1f}%")

        except Exception as e:
            print(f"  ❌ 失败: {e}")
            import traceback; traceback.print_exc()

    registry['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    registry['_last_backtest'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    if os.path.exists(registry_path):
        shutil.copy2(registry_path, registry_path + '.bak')
        try: os.chmod(registry_path, 0o666)  # 解除只读锁
        except: pass
    tmp = registry_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    os.replace(tmp, registry_path)
    print(f"\n📝 注册表已更新: {registry_path}")
    return registry


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--mode', default='live', choices=['live', 'backtest'])
    p.add_argument('--strategy', help='回测模式: 只跑指定策略')
    p.add_argument('--start', default=None)
    p.add_argument('--end', default=None)
    args = p.parse_args()

    if args.mode == 'backtest':
        print("=" * 60)
        print("  统一引擎 · 回测模式")
        print("=" * 60)
        run_backtest(start=args.start, end=args.end, strategy_filter=args.strategy)
    else:
        main()
