"""反转策略 0 信号根因分析"""
import sys, numpy as np
sys.path.insert(0, 'D:/quant_framework')
sys.path.insert(0, 'D:/quant_web')

from data_loader import load_stock_data_cache
from generate_signal_table import get_industry

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)

# 逐条件统计
stats = {
    "total": 0,          # 总扫描
    "st_filtered": 0,    # ST排除
    "price_filtered": 0, # 价格<5排除
    "no_bullish": 0,     # 不是阳线
    "no_decline": 0,     # 跌幅不够
    "no_volume": 0,      # 放量不够
    "turnover_bad": 0,   # 换手不达标
    "sector_bad": 0,     # 板块集体跌
    "passed": 0,         # 全部通过
}

# 额外统计：跌幅分布和量比分布
declines = []
vol_ratios = []
bullish_count = 0

for sym, df in sd.items():
    try:
        c = df['close'].values
        v = df['volume'].values
        o = df['open'].values
        if len(c) < 21: continue
        stats["total"] += 1

        close = float(c[-1])
        open_p = float(o[-1])

        if 'ST' in sym.upper():
            stats["st_filtered"] += 1
            continue

        if close < 5:
            stats["price_filtered"] += 1
            continue

        yest_chg = (c[-1] - c[-2]) / max(c[-2], 0.01)
        yest_vol = v[-1]
        avg_vol = float(np.mean(v[-6:-1]))
        vol_ratio = yest_vol / max(avg_vol, 1)

        declines.append(yest_chg * 100)
        vol_ratios.append(vol_ratio)

        if close > open_p:
            bullish_count += 1

        # 逐条件检查
        if not (close > open_p):
            stats["no_bullish"] += 1
            continue

        # 波动率自适应阈值
        rets = [(c[i] - c[i-1]) / max(c[i-1], 0.01) for i in range(1, 21)]
        vol = (sum(r*r for r in rets)/20)**0.5 if rets else 0.02
        threshold = max(-0.03, -vol*2, -0.08)

        if not (yest_chg < threshold):
            stats["no_decline"] += 1
            continue

        if not (vol_ratio > 1.5):
            stats["no_volume"] += 1
            continue

        # 换手率
        if 'outstanding' in df.columns:
            out = float(df['outstanding'].values[-1])
            if out > 0:
                turnover = yest_vol / out * 100
                if turnover < 3 or turnover > 50:
                    stats["turnover_bad"] += 1
                    continue

        # 板块
        ind = get_industry(sym) or ''
        if ind:
            chgs = []
            for sym2, df2 in list(sd.items())[:300]:
                try:
                    if get_industry(sym2) == ind:
                        c2 = df2['close'].values
                        if len(c2) >= 2:
                            chgs.append((c2[-1]-c2[-2])/max(c2[-2],0.01))
                except: pass
            if chgs and np.mean(chgs) <= -0.02:
                stats["sector_bad"] += 1
                continue

        stats["passed"] += 1

    except: pass

print("=" * 50)
print("  弱转强 逐条件漏斗")
print("=" * 50)
total = max(stats["total"], 1)
for k, v in stats.items():
    bar = "█" * int(v / total * 50)
    print(f"  {k:<20}: {v:>6} ({v/total*100:5.1f}%) {bar}")

print(f"\n跌幅分布: mean={np.mean(declines):.2f}% median={np.median(declines):.2f}%")
print(f"跌幅<阈值样本: {sum(1 for d in declines if d < -3):>5} / {len(declines)}")
print(f"量比>1.5样本: {sum(1 for v in vol_ratios if v > 1.5):>5} / {len(vol_ratios)}")
print(f"阳线样本: {bullish_count} / {stats['total']}")
print(f"阳线且跌幅达标: {sum(1 for d,v in zip(declines,vol_ratios) if d < -3 and v > 1.5)}")
