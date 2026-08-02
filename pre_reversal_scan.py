"""游资弱转强盘前扫描 v1.0 — 对标游资复盘流程
每日 08:30 运行, 扫描全市场日线, 找出"弱"的票
写入 auto_trade_plan 追加到 QMT 次日监控池 (每日 15:10 由板后预选池同时触发)

逻辑 (游资标准):
  弱日: 昨日烂板/断板/分歧 → 放量 (成交量 > 前5日均量 × 1.5)
       昨日跌幅 > 3% → 大分歧
       昨日换手 > 5% → 筹码交换充分
  次日: QMT 监控这些票, 竞价抢筹/盘中突破 自动触发 = 转强确认

用法: python pre_reversal_scan.py
"""
import sys, os, json, numpy as np
from datetime import datetime

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"


def _sector_ok(ind, sd):
    """行业当日平均涨跌 > -2% (不集体跌停)"""
    try:
        from generate_signal_table import get_industry
        chgs = []
        for sym, df in list(sd.items())[:500]:
            try:
                if get_industry(sym) == ind:
                    c = df['close'].values
                    if len(c) >= 2:
                        chgs.append((c[-1]-c[-2])/max(c[-2],0.01))
            except: pass
        return np.mean(chgs) > -0.02 if chgs else True
    except: return True


def scan_weak_stocks(sd, max_candidates=30):
    """扫描昨日"弱"的票 (游资复盘第1步: 扫跌幅榜+连板梯队)

    条件:
      1. 昨日跌幅 > 3% (大分歧)
      2. 昨日成交量 > 前5日均量 × 1.5 (放量洗盘)
      3. 非ST, 非停牌
      4. 价格 > 5元 (游资不做低价)
    """
    candidates = []
    for sym, df in sd.items():
        try:
            c = df['close'].values; v = df['volume'].values; o = df['open'].values
            if len(c) < 21: continue
            close = c[-1]; open_p = o[-1]

            # 过滤
            if 'ST' in sym.upper() or '*ST' in sym.upper(): continue
            if close < 5: continue

            # 昨日
            yest_chg = (c[-1] - c[-2]) / max(c[-2], 0.01)
            yest_vol = v[-1]
            avg_vol = np.mean(v[-6:-1])
            vol_ratio = yest_vol / max(avg_vol, 1)

            # 阳线: close>open = 净买入代理
            if not (close > open_p): continue

            # 换手率 (有流通股本数据时才检查, 无数据默认通过)
            turnover = 5.0
            if 'outstanding' in df.columns:
                out = float(df['outstanding'].values[-1])
                if out > 0:
                    turnover = yest_vol / out * 100
                    if turnover < 3 or turnover > 50: continue

            # 波动率自适应阈值
            rets = [(c[i]-c[i-1])/max(c[i-1],0.01) for i in range(1,21)]
            vol = (sum(r*r for r in rets)/20)**0.5 if rets else 0.02
            threshold = max(-0.03, -vol*2, -0.08)

            # 板块过滤: 行业不能集体在跌
            ind = ''
            try:
                from generate_signal_table import get_industry
                ind = get_industry(sym) or ''
            except: pass
            if ind and _sector_ok(ind, sd): pass  # 行业OK
            elif ind: continue  # 行业集体跌, 跳过

            # 弱的条件: 跌幅达标 + 放量
            if not (yest_chg < threshold and vol_ratio > 1.5): continue

            # ═══ 信号质量打分 (私募多因子评分) ═══
            score = 50  # 基准分

            # 1. 跌幅维度 (0-20): 越接近阈值越好(跌刚好), 跌太猛降分
            if yest_chg < threshold * 2.5:
                score += 0   # 跌太猛, 可能是崩盘
            elif yest_chg < threshold * 1.8:
                score += 10  # 适度超跌
            else:
                score += 20  # 刚好在阈值附近, 最优

            # 2. 量能维度 (0-20): 放量适中最好, 太大=出货嫌疑
            if 1.5 <= vol_ratio <= 3:
                score += 20
            elif 3 < vol_ratio <= 5:
                score += 10
            else:
                score += 0   # >5x 可能出货

            # 3. 换手维度 (0-15): 3-10%最优, >30%降分
            if 3 <= turnover <= 10:
                score += 15
            elif 10 < turnover <= 30:
                score += 8
            else:
                score += 0

            # 4. K线维度 (0-15): 实体越大越好
            body_pct = abs(close - open_p) / max(open_p, 0.01)
            if body_pct > 0.03: score += 15
            elif body_pct > 0.01: score += 8
            else: score += 3

            # 5. 板块维度 (0-15): 行业强=加分
            sector_chg = 0
            try:
                from generate_signal_table import get_industry
                ind2 = get_industry(sym) or ''
                chgs = []
                for s2, d2 in list(sd.items())[:300]:
                    try:
                        if get_industry(s2) == ind2:
                            cc = d2['close'].values
                            if len(cc) >= 2:
                                chgs.append((cc[-1]-cc[-2])/max(cc[-2],0.01))
                    except: pass
                sector_chg = np.mean(chgs) if chgs else 0
            except: pass
            if sector_chg > 0.01: score += 15
            elif sector_chg > 0: score += 8
            else: score += 0

            # 6. 缩量形态 (0-15): 游资核心 — 跌的三天量递减=卖盘枯竭
            if len(v) >= 8:
                v5, v4, v3 = v[-5], v[-4], v[-3]  # 跌的前三天
                if v5 > 0 and v3 < v4 < v5:
                    score += 10  # 三日递减
                    if v[-2] > v[-3] and v[-1] > v[-2]:
                        score += 5  # 最后两天量放大=转强确认

            # 7. 加分项 (0-15): 持续缩量后的放量
            if len(v) >= 8:
                pre_avg = np.mean(v[-8:-3])
                if pre_avg > 0 and yest_vol > pre_avg * 1.3:
                    score += 15

            # 分级
            tier = 'S' if score >= 80 else ('A' if score >= 65 else 'B')
            pos_pct = 3.0 if tier == 'S' else (2.5 if tier == 'A' else 0)
            if tier == 'B': continue  # B级不入池

            candidates.append({
                'symbol': sym,
                'chg': round(yest_chg * 100, 1),
                'vol_ratio': round(vol_ratio, 2),
                'close': round(close, 2),
                'turnover': round(turnover, 1),
                'vol_pct': round(vol*100, 1),
                'threshold_pct': round(threshold*100, 1),
                'score': score,
                'tier': tier,
                'pos_pct': pos_pct,
                'yest_vol': int(yest_vol),
            })

        except Exception: pass

    candidates.sort(key=lambda x: -x['score'])
    return candidates[:max_candidates]


def scan_limit_up_pullback(sd, max_candidates=15):
    """昨涨停+今回调扫描 (游资连板梯队)

    条件:
      昨涨停 (涨幅>9.5%) + 今低开 (open<昨收) + 回调<5% (不是崩盘)
      + 放量 (分歧充分) + 非ST

    Returns: 候选列表, 与跌幅候选合并
    """
    candidates = []
    for sym, df in sd.items():
        try:
            c = df['close'].values; o = df['open'].values; v = df['volume'].values
            if len(c) < 6: continue
            close = c[-1]; open_today = o[-1]
            yest_close = c[-2]; yest_open = o[-2]
            yest_chg = (yest_close - yest_open) / max(yest_open, 0.01)

            # 过滤
            if 'ST' in sym.upper() or '*ST' in sym.upper(): continue
            if close < 5: continue

            # 昨涨停
            if yest_chg < 0.095: continue

            # 今低开回调
            pullback = (open_today - yest_close) / max(yest_close, 0.01)
            if pullback > -0.05: continue  # 不是低开回调
            if pullback < -0.05: pass  # 回调<5%, OK

            # 放量
            yest_vol = v[-1]
            avg_vol = np.mean(v[-6:-1])
            if yest_vol < avg_vol * 1.5: continue

            # 阳线
            if not (close > open_today): continue

            # 换手 (有数据才查, 无默认通过)
            turnover = 5.0
            if 'outstanding' in df.columns:
                out = float(df['outstanding'].values[-1])
                if out > 0:
                    turnover = yest_vol / out * 100
                    if turnover < 3 or turnover > 50: continue

            # 板块
            ind = ''
            try:
                from generate_signal_table import get_industry
                ind = get_industry(sym) or ''
            except: pass
            if ind and not _sector_ok(ind, sd): continue

            # 打分 (简化版)
            score = 60  # 连板回调基础分低于跌幅型
            if 1.5 <= yest_vol/avg_vol <= 3: score += 15
            if 3 <= turnover <= 10: score += 10
            if abs(pullback) < 0.03: score += 10  # 回调浅=强

            tier = 'S' if score >= 80 else ('A' if score >= 65 else 'B')
            if tier == 'B': continue

            candidates.append({
                'symbol': sym,
                'chg': round(pullback * 100, 1),
                'vol_ratio': round(yest_vol/avg_vol, 2),
                'close': round(close, 2),
                'turnover': round(turnover, 1),
                'score': score, 'tier': tier,
                'pos_pct': 2.5 if tier == 'S' else 2.0,
                'type': '连板回调',
                'yest_vol': int(yest_vol),
            })
        except Exception: pass

    candidates.sort(key=lambda x: -x['score'])
    return candidates[:max_candidates]


def add_to_plan(candidates):
    """追加弱转强候选到计划文件"""
    if not os.path.exists(PLAN_PATH):
        print("[Reversal] plan文件不存在")
        return 0

    with open(PLAN_PATH, encoding='utf-8') as f:
        plan = json.load(f)

    stocks = plan.get("stocks", {})
    added = 0
    for c in candidates:
        sym = c['symbol']
        if sym not in stocks:
            stocks[sym] = {
                "enabled": False,
                "auto_reason": f"弱转强{c['tier']}级 跌{c['chg']}% 量{c['vol_ratio']}x 得分{c['score']}",
                "max_position_pct": c.get('pos_pct', 2),
                "min_ml_score": 0,
                "signal_types": ["竞价抢筹", "盘中突破"],
                "time_window": "09:30-10:30",
                "yesterday_volume": int(c.get('yest_vol', 0)),  # 竞价量占昨比用
                "max_order_qty": 0,
                "close": c['close'],
            }
            added += 1
        else:
            # 已有: 追加信号类型
            sig = set(stocks[sym].get("signal_types", []))
            sig.update(["竞价抢筹", "盘中突破"])
            stocks[sym]["signal_types"] = list(sig)

    plan["stocks"] = stocks
    plan["global_limits"]["_reversal_candidates"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PLAN_PATH, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"[Reversal] 弱转强候选 {added}只 → 追加到计划 (总{len(stocks)}只)")
    return added


if __name__ == "__main__":
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)

    # 市场门控: 熊市不做弱转强 (假信号太多)
    try:
        from market_regime import detect_regime
        regime = detect_regime(sd)
        regime_str = regime.get("regime", "sideways") if regime else "sideways"
    except: regime_str = "sideways"

    # 检查弱转强组合是否被选中
    combo_active = True
    try:
        combo = json.load(open(r"D:\quant_framework\strategy_combos.json", encoding="utf-8"))
        combo_active = (combo.get("current") == "弱转强")
    except: pass

    if not combo_active:
        print(f"[Reversal] ⏸ 弱转强组合未选中 → 跳过扫描")
    elif regime_str == "bear":
        print(f"[Reversal] 🐻 熊市 → 跳过弱转强扫描 (假反弹多)")
    else:
        print(f"[Reversal] {regime_str} → 扫描 {len(sd)}只...")
        candidates = scan_weak_stocks(sd, max_candidates=30)
        # 两种候选源: 跌幅型 + 连板回调型
        candidates_drop = scan_weak_stocks(sd, max_candidates=30)
        candidates_lb = scan_limit_up_pullback(sd, max_candidates=15)
        candidates = candidates_drop + candidates_lb
        # 去重 + 按分排
        seen = set()
        merged = []
        for c in sorted(candidates, key=lambda x: -x.get('score', 0)):
            if c['symbol'] not in seen:
                seen.add(c['symbol'])
                merged.append(c)
        candidates = merged[:30]
        print(f"[Reversal] 跌幅型{len(candidates_drop)}只 + 连板回调{len(candidates_lb)}只 → 合并{len(candidates)}只")
        for c in candidates[:5]:
            t = c.get('type', '跌幅')
            print(f"  {c['symbol']} [{t}] {c['tier']}级 得分{c['score']} 仓位{c.get('pos_pct',2)}%")
        if candidates:
            add_to_plan(candidates)
    print("[Reversal] 完成")
