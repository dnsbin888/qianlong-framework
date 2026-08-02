"""超跌反弹 逐条件漏斗"""
import sys, numpy as np
sys.path.insert(0,'D:/quant_framework'); sys.path.insert(0,'D:/quant_web')
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=90)

z_scores = []; connors_ok_count = 0; stable_count = 0; vol_ok_count = 0
passed = 0; total = 0
detail = []  # 存通过所有条件的样本

for sym, df in list(sd.items())[:3000]:
    try:
        c = df['close'].values; v = df['volume'].values
        if len(c) < 60: continue
        total += 1
        close = c[-1]
        if 'ST' in sym.upper() or close < 3: continue

        rets = np.diff(c[-61:]) / np.maximum(np.abs(c[-61:-1]), 0.01)
        std = float(np.std(rets)) if len(rets)>=20 else 0.03
        ret_3d = (c[-1] - c[-4]) / max(c[-4], 0.01)
        z = ret_3d / max(std, 0.005)
        z_scores.append(z)

        if z > -1.5: continue
        z_pass = True

        # Connors
        try:
            from stock_filters import connors_rsi
            cr = connors_rsi(sd)
            cok = cr.get(sym, False)
        except: cok = False
        if cok: connors_ok_count += 1

        today_chg = (c[-1] - c[-2]) / max(c[-2], 0.01)
        if today_chg > -0.05: stable_count += 1

        vr = v[-1] / max(np.mean(v[-6:-1]), 1)
        if vr > 1.2: vol_ok_count += 1

        if z < -1.5 and today_chg > -0.05 and vr > 1.2:
            passed += 1
            detail.append((sym, z, today_chg, vr, cok))

    except: pass

print(f"总量: {total}")
print(f"Z分布: mean={np.mean(z_scores):.2f} std={np.std(z_scores):.2f} min={np.min(z_scores):.2f} max={np.max(z_scores):.2f}")
print(f"Z<-1σ: {sum(1 for z in z_scores if z<-1)} | Z<-1.5σ: {sum(1 for z in z_scores if z<-1.5)} | Z<-2σ: {sum(1 for z in z_scores if z<-2)}")
print(f"Connors超卖: {connors_ok_count}")
print(f"今日企稳(today>-5%): {stable_count}")
print(f"量比>1.2: {vol_ok_count}")
print(f"全部通过: {passed}")
if detail:
    print("通过样本:")
    for s, z, tc, vr, cok in detail[:10]:
        print(f"  {s} z={z:.1f}σ today={tc*100:.1f}% vol={vr:.1f}x connors={cok}")
