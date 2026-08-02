"""弱转强回测 v2 — 参数灵活对比"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")
from data_loader import load_stock_data_cache

print("=" * 60)
print("  弱转强 A1 参数灵活回测")
print("=" * 60)

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=0)
sd = {k: v for k, v in sd.items() if not k.startswith(('sh000','sz399','bj','sh88','sz88','sh51','sz15'))}
print(f"  股票池: {len(sd)}只\n")

# 灵活版信号生成
def gen_wts_flex(sd, vol_min=1.2, turnover_min=1, cap_min=10, cap_max=500, score_min=40):
    """灵活版弱转强 — 可调参数"""
    signals = {}
    for sym, df in sd.items():
        try:
            c = df['close'].values; v = df['volume'].values; o = df['open'].values
            if len(c) < 21: continue
            close = float(c[-1]); open_p = float(o[-1])
            if 'ST' in sym.upper(): continue
            if close < 3: continue  # 放宽: >3元
            if not (close > open_p): continue  # 今日阳线
            if 'outstanding' in df.columns:
                _cap = float(df['outstanding'].values[-1]) * close / 1e8
                if _cap < cap_min or _cap > cap_max: continue

            yest_chg = (c[-2] - c[-3]) / max(c[-3], 0.01)
            yest_vol = v[-2]; avg_vol = float(np.mean(v[-6:-1]))
            vol_ratio = yest_vol / max(avg_vol, 1)
            if vol_ratio < vol_min: continue
            turnover = 5.0
            if 'outstanding' in df.columns:
                out = float(df['outstanding'].values[-1])
                if out > 0: turnover = yest_vol / out * 100
                if turnover < turnover_min or turnover > 50: continue

            rets = [(c[i]-c[i-1])/max(c[i-1],0.01) for i in range(1,21)]
            vol = (sum(r*r for r in rets)/20)**0.5 if rets else 0.02
            threshold = max(-0.03, -vol*2, -0.08)
            if not (yest_chg < threshold): continue

            score = 50 + min(20, abs(yest_chg)*200) + min(20, vol_ratio*10) + min(10, turnover)
            if score < score_min: continue
            signals.setdefault(sym, pd.Series(dtype=float))
            date_str = str(df.index[-1])[:10]
            signals[sym].loc[pd.Timestamp(date_str)] = round(score, 1)
        except: continue
    return signals

# 逐日回滚生成
print("[1] 逐日生成信号...")
dates = sorted(set(str(d)[:10] for df in sd.values() for d in df.index[-750:]))[-750:]
signal_store_all = {}
for i, date_str in enumerate(dates):
    snap = {}
    for sym, df in sd.items():
        try:
            mask = df.index <= pd.Timestamp(date_str)
            sliced = df[mask]
            if len(sliced) >= 21: snap[sym] = sliced
        except: pass
    if len(snap) < 100: continue
    sigs = gen_wts_flex(snap, vol_min=1.2, turnover_min=1, cap_min=10, cap_max=500, score_min=40)
    for sym, s in sigs.items():
        signal_store_all.setdefault(sym, pd.Series(dtype=float))
        signal_store_all[sym].loc[pd.Timestamp(date_str)] = s.values[-1] if isinstance(s, pd.Series) else s
    if (i+1) % 150 == 0:
        n = sum(len(v) for v in signal_store_all.values())
        print(f"  {i+1}/{len(dates)}... {n}条")

n_total = sum(len(v) for v in signal_store_all.values())
print(f"  总信号: {n_total}条\n")

if n_total < 20:
    print("❌ 信号仍不足, 需继续放宽")
    sys.exit(1)

# 回测对比
print("[2] 运行回测...")
from ruler_trade import measure, compare_table

name_map = {}
try:
    with open(r"D:\quant_web\stock_names_full.csv", "r", encoding="utf-8") as f:
        for line in f:
            p = line.strip().split(",")
            if len(p) >= 2: name_map[p[0]] = p[1]
except: pass

configs = [
    ("hold=2 ATR=2.0",  2, None, 2.0),
    ("hold=3 ATR=2.0",  3, None, 2.0),
    ("hold=5 ATR=2.0",  5, None, 2.0),
    ("hold=5 ATR=1.5",  5, None, 1.5),
    ("hold=5 ATR=2.0 TP=5%", 5, 0.05, 2.0),
]

reports = []
for label, hold, tp, atr in configs:
    print(f"  {label}...")
    try:
        r = measure(
            strategy="custom", signal_field="score",
            stock_data=sd, name_map=name_map, signal_store=signal_store_all,
            start="2024-01-01", end="2026-07-19",
            max_positions=3, position_pct=0.3,
            hold_days=hold, take_profit=tp, atr_multiplier=atr,
            initial_capital=1_000_000,
            formula_symbols=list(signal_store_all.keys()),
        )
        r["strategy"] = label
        reports.append(r)
        print(f"    胜率={r['win_rate_pct']:.1f}% 盈亏比={r['profit_factor']:.2f} Sharpe={r['sharpe']:.2f}")
    except Exception as e:
        print(f"    失败: {e}")

if reports:
    print(f"\n" + compare_table(reports))
    best = max(reports, key=lambda r: r["sharpe"])
    print(f"\n✅ 最优: {best['strategy']}")
