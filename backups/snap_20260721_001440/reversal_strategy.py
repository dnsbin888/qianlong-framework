"""反转策略信号 v1.1 — 弱转强 + 超跌反弹
对标: 游资复盘流程, 信号格式对齐 strategy_engine.py
参数治理: 止损/止盈上限从 trade_config_master.json 读取
"""
import sys, os, json, numpy as np

_IND_MAP = None

def _get_industry(sym):
    global _IND_MAP
    if _IND_MAP is None:
        _IND_MAP = {}
        try:
            p = r"D:\quant_web\data\stock_industry_map.json"
            if os.path.exists(p):
                raw = json.load(open(p, encoding="utf-8"))
                _IND_MAP = raw.get("symbol_to_industry", raw)
        except: pass
    clean = sym.replace('sh','').replace('sz','').replace('bj','')
    return _IND_MAP.get(sym, _IND_MAP.get(clean, ""))

def _sector_ok(ind, sd):
    try:
        from generate_signal_table import get_industry
        chgs = []
        for sym2, df2 in sd.items():
            try:
                if get_industry(sym2) == ind:
                    c2 = df2['close'].values
                    if len(c2) >= 2:
                        chgs.append((c2[-1]-c2[-2])/max(c2[-2],0.01))
                        if len(chgs) >= 20: break
            except: pass
        if chgs and len(chgs) >= 3:
            return float(np.mean(chgs)) > -0.02
    except: pass
    return True

def _is_hot_sector(ind):
    """陈小群: 只做主线热点板块。用龙虎榜板块热度判断。"""
    try:
        import json, os
        p = r"D:\quant_web\data\lhb_daily.json"
        if os.path.exists(p):
            data = json.load(open(p, encoding="utf-8"))
            hots = data.get("hot_sectors", data.get("sectors", {}))
            if isinstance(hots, dict) and ind in hots:
                return hots[ind].get("score", 0) > 0
            if isinstance(hots, list):
                return ind in hots
    except: pass
    return True  # 数据不可用时放行

def _load_stop_caps():
    try:
        m = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
        sl = m.get("stop_loss", {})
        return {"soft_max": abs(sl.get("soft",0.03)), "hard_max": abs(sl.get("hard",0.055))}
    except: return {"soft_max":0.03, "hard_max":0.055}


def generate_weak_to_strong(sd, factor_cache=None):
    """弱转强: 昨日分歧放量 + 今日阳线 -> 博次日溢价

    条件:
      1. 昨日跌幅 > 波动率自适应阈值
      2. 昨日量比 > 1.5 (放量洗盘)
      3. 今日阳线 (close > open)
      4. 非ST, 价格 > 5元
      5. 换手 3-50%
      6. 行业不集体在跌
    """
    candidates = []
    for sym, df in sd.items():
        try:
            c = df['close'].values
            v = df['volume'].values
            o = df['open'].values
            if len(c) < 21: continue
            close = float(c[-1])
            open_p = float(o[-1])

            if 'ST' in sym.upper(): continue
            if close < 5: continue
            if 'outstanding' in df.columns:
                _cap = float(df['outstanding'].values[-1]) * close / 1e8
                if _cap < 50 or _cap > 200: continue  # 陈小群: 50-200亿最优
            if not (close > open_p): continue

            yest_chg = (c[-2] - c[-3]) / max(c[-3], 0.01)
            yest_vol = v[-2]
            avg_vol = float(np.mean(v[-6:-1]))
            vol_ratio = yest_vol / max(avg_vol, 1)

            turnover = 5.0
            if 'outstanding' in df.columns:
                out = float(df['outstanding'].values[-1])
                if out > 0:
                    turnover = yest_vol / out * 100
                    if turnover < 3 or turnover > 50: continue

            rets = [(c[i]-c[i-1])/max(c[i-1],0.01) for i in range(1,21)]
            vol = (sum(r*r for r in rets)/20)**0.5 if rets else 0.02
            threshold = max(-0.03, -vol*2, -0.08)

            ind = _get_industry(sym)
            if ind and not _sector_ok(ind, sd): continue
            # 热点板块加分 (陈小群: 只做有板块效应的, LHB不可用时放行)
            _hot_bonus = 15 if ind and _is_hot_sector(ind) else 0

            if not (yest_chg < threshold and vol_ratio > 1.5): continue

            score = 50
            if yest_chg < threshold * 2.5: score += 0
            elif yest_chg < threshold * 1.8: score += 10
            else: score += 20
            if 1.5 <= vol_ratio <= 3: score += 20
            elif 3 < vol_ratio <= 5: score += 10
            if 3 <= turnover <= 10: score += 15
            elif 10 < turnover <= 30: score += 8
            body_pct = abs(close-open_p)/max(open_p,0.01)
            if body_pct > 0.03: score += 15
            elif body_pct > 0.01: score += 8
            else: score += 3

            sector_chg = 0
            try:
                from generate_signal_table import get_industry
                ind2 = get_industry(sym) or ''
                if ind2:
                    chgs2 = []
                    for sym2, df2 in list(sd.items())[:300]:
                        try:
                            if get_industry(sym2) == ind2:
                                c2 = df2['close'].values
                                if len(c2)>=2: chgs2.append((c2[-1]-c2[-2])/max(c2[-2],0.01))
                        except: pass
                    sector_chg = float(np.mean(chgs2)) if chgs2 else 0
            except: pass
            if sector_chg > 0.02: score += 15
            elif sector_chg > 0: score += 8
            score += _hot_bonus  # 热点板块加分

            caps = _load_stop_caps()
            rets_atr = np.diff(c[-21:])/(c[-21:-1]+1e-9)
            vol_atr = float(np.std(rets_atr)) if len(rets_atr)>1 else 0.02
            soft_pct = max(0.03, min(vol_atr*1.2, min(caps["soft_max"],0.05)))
            hard_pct = max(0.04, min(soft_pct*1.5, 0.05))

            candidates.append({
                "symbol": sym, "score": round(min(100,score),1),
                "action": "buy", "close": round(close,2),
                "soft_stop_loss": round(close*(1-soft_pct),2),
                "stop_loss": round(close*(1-hard_pct),2),
                # 追踪止盈: 涨5%→成本止损, 涨10%→跟2%回落
                "take_profit": round(close*(1+soft_pct*2.5),2),
                "trailing_tp1": round(close*1.05,2),  # 第一目标: 保本
                "trailing_tp2": round(close*1.10,2),  # 第二目标: 跟2%回落
                "reason": f"弱转强 chg={yest_chg*100:.1f}% vol_ratio={vol_ratio:.1f}",
                "hold_days": 5,  # 陈小群: 弱转强发酵3-5天
                "strategy_id": "weak_to_strong", "strategy_type": "reversal",
            })
        except Exception: continue

    candidates.sort(key=lambda x: -x["score"])
    return candidates[:15]


def generate_oversold_bounce(sd, factor_cache=None):
    """超跌反弹: Connors RSI超卖 + Z-score跌幅 + 企稳确认"""
    candidates = []
    for sym, df in sd.items():
        try:
            c = df['close'].values; v = df['volume'].values; o = df['open'].values
            if len(c) < 22: continue
            close = float(c[-1]); open_p = float(o[-1])
            if 'ST' in sym.upper(): continue
            if close < 5: continue
            if not (close > open_p): continue

            yest_chg = (c[-2]-c[-3])/max(c[-3],0.01)
            ret5 = (c[-1]-c[-6])/max(c[-6],0.01) if len(c)>=6 else 0
            rets = [(c[i]-c[i-1])/max(c[i-1],0.01) for i in range(1,21)]
            vol_atr = (sum(r*r for r in rets)/20)**0.5 if rets else 0.02
            z_score = (ret5-(sum(rets)/20))/max(vol_atr+0.001,0.01)
            if z_score > -1.5: continue

            yest_vol = v[-2]; avg_vol = float(np.mean(v[-7:-2]))
            vol_ratio = yest_vol/max(avg_vol,1)
            if vol_ratio < 1.0: continue

            score = 50 + abs(min(0,z_score+1.5))*10 + min(vol_ratio,3)*5
            caps = _load_stop_caps()
            soft_pct = max(0.02, min(vol_atr*1.2, min(caps["soft_max"],0.05)))
            hard_pct = max(0.04, min(soft_pct*1.5, 0.05))

            candidates.append({
                "symbol": sym, "score": round(min(100,score),1),
                "action": "buy", "close": round(close,2),
                "soft_stop_loss": round(close*(1-soft_pct),2),
                "stop_loss": round(close*(1-hard_pct),2),
                "take_profit": round(close*(1+soft_pct*2.5),2),
                "reason": f"超跌反弹 Z={z_score:.1f}",
                "hold_days": 3, "strategy_id": "oversold_bounce", "strategy_type": "reversal",
            })
        except Exception: continue

    candidates.sort(key=lambda x: -x["score"])
    return candidates[:15]
