"""反转信号 v2.0 可靠性验证 — 模拟QMT检测逻辑"""
import sys, json, numpy as np
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)

# 加载行业强度
plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
sector_strength = plan["global_limits"].get("_sector_strength", {})
regime = plan["global_limits"].get("_regime", "bear")

# 行业映射
ind_map = {}
try:
    raw = json.load(open(r"D:\quant_web\data\stock_industry_map.json", encoding="utf-8"))
    ind_map = raw.get("symbol_to_industry", raw)
except: pass

print(f"验证条件: regime={regime} (仅超跌反弹生效)")
print(f"行业强度: {len(sector_strength)}个行业")
print("=" * 60)

# ── 模拟QMT检测 ──
v1_signals = []  # 旧版固定阈值
v2_signals = []  # 新版游资+私募

for sym, df in list(sd.items())[:4000]:
    try:
        c = df['close'].values; v = df['volume'].values; o = df['open'].values
        if len(c) < 21: continue

        # 波动率
        rets = [(c[i]-c[i-1])/max(c[i-1],0.01) for i in range(1, min(len(c),21))]
        vol = (sum(r*r for r in rets)/max(len(rets),1))**0.5 if rets else 0.02

        # 跌幅
        ret3 = (c[-1]-c[-4])/max(c[-4],0.01); ret_today = (c[-1]-c[-2])/max(c[-2],0.01)
        ret5 = (c[-1]-c[-6])/max(c[-6],0.01) if len(c)>=6 else 0
        ret20 = (c[-1]-c[-21])/max(c[-21],0.01) if len(c)>=21 else 0
        ma10 = np.mean(c[-10:]); avg_v5 = np.mean(v[-6:-1])
        vr = v[-1]/max(avg_v5,1)
        shrink3 = len(v)>=6 and v[-4]<v[-5] and v[-3]<v[-4]
        shrink2 = len(v)>=5 and v[-3]<v[-4]
        bullish = c[-1] > o[-1]

        # 行业
        code = sym.replace('sh','').replace('sz','').replace('bj','')
        ind = ind_map.get(sym, ind_map.get(code, ''))
        sct = sector_strength.get(ind, 0) if ind else 0

        # ── v1 旧版固定阈值 ──
        t3_old = -0.08
        if regime=="bear" and ret3<t3_old and ret_today>0.015 and vr>2:
            v1_signals.append((sym, ret3))

        # ── v2 新版游资+私募 ──
        t3_new = max(-0.08, -vol*3, -0.15)
        if regime=="bear" and ret3<t3_new and ret_today>0.015 and vr>2 and shrink2 and bullish and sct>-2:
            v2_signals.append((sym, ret3, vol, vr, shrink2, bullish, sct))

    except Exception: pass

print(f"\n旧版(v1固定阈值): {len(v1_signals)}只")
for s in v1_signals[:10]: print(f"  {s[0]} 5日跌{s[1]*100:.1f}%")
print(f"\n新版(v2游资+私募): {len(v2_signals)}只")
for s in v2_signals[:10]: print(f"  {s[0]} 5日跌{s[1]*100:.1f}% vol={s[2]*100:.1f}% vr={s[3]:.1f} shrink={s[4]} bull={s[5]} sct={s[6]}")
print(f"\nv1拦截: {len(v1_signals)-len(v2_signals)}只 假反弹(缩量/阳线/板块校验过滤)")
print(f"v2新增: {len(v2_signals)-len(v1_signals)}只 (波动率阈值放宽)")

# 分析被v2过滤的v1信号
if v1_signals and v2_signals:
    v1_set = {s[0] for s in v1_signals}
    v2_set = {s[0] for s in v2_signals}
    filtered = v1_set - v2_set
    if filtered:
        print(f"\n被v2过滤的v1信号 (假反弹): {len(filtered)}只")
        for sym in list(filtered)[:5]:
            # 找出被过滤原因
            s = next(s for s in v1_signals if s[0]==sym)
            # 模拟v2条件看哪个不满足
            print(f"  {sym}: ", end="")
            c2 = sd[sym]['close'].values; v2 = sd[sym]['volume'].values; o2 = sd[sym]['open'].values
            vr2 = v2[-1]/max(np.mean(v2[-6:-1]),1)
            s2 = len(v2)>=5 and v2[-3]<v2[-4]
            b2 = c2[-1]>o2[-1]
            t_new = max(-0.08, -vol*3, -0.15)
            reasons = []
            if not s2: reasons.append("缩量不满足")
            if not b2: reasons.append("非阳线")
            if sct<=-2: reasons.append(f"板块{sct}%")
            print(", ".join(reasons))
