"""次日买入回测: 信号日不限涨停, 次日开盘买(非一字板即可)"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd, random, time as _time
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 2000

def load_stock(market, fname):
    code = fname.replace(market, "").replace(".day", "")
    if len(code) != 6 or not code.isdigit(): return None
    path = os.path.join(ROOT, market, "lday", fname)
    if not os.path.exists(path): return None
    with open(path, "rb") as f: raw = f.read()
    dates, o, h, l, c, v = [], [], [], [], [], []
    for i in range(len(raw)//32):
        vals = struct.unpack_from("<I I I I I f I I", raw, i*32)
        d, o_, h_, l_, c_, amt, vol = vals[0], vals[1]/100., vals[2]/100., vals[3]/100., vals[4]/100., vals[5], vals[6]
        if 20100101 <= d <= 20270101 and o_ > 0:
            dates.append(d); o.append(o_); h.append(h_); l.append(l_); c.append(c_); v.append(vol)
    return {"code": code, "dates": dates, "o": o, "h": h, "l": l, "c": c, "v": v}

def compute_f1(df):
    c, v = df["close"].values, df["volume"].values
    hhv30 = pd.Series(c).rolling(30).max().shift(1)
    pressure = hhv30.rolling(2).mean().values
    ema20 = pd.Series(c).ewm(span=20, adjust=False).mean()
    dev = ((pd.Series(c) - ema20)**2).rolling(20).mean()**0.5
    upper = (ema20 + 2*dev).shift(1).values
    vol_ratio = v / pd.Series(v).rolling(5).mean().shift(1).replace(0, np.nan)
    qlj_raw = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if qlj_raw[i] and qlj_raw[i-7:i].sum() <= 1: qlj[i] = 1
    cost99 = pd.Series(c).rolling(60).quantile(0.99)
    profit100 = cost99.ewm(span=5, adjust=False).mean()
    ztxf_raw = c > profit100.values
    ztxf = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if ztxf_raw[i] and ztxf_raw[i-7:i].sum() <= 1: ztxf[i] = 1
    return (qlj & ztxf).astype(int)

print("Loading & computing...")
stocks = {}
for market in ["sh", "sz"]:
    lday_dir = os.path.join(ROOT, market, "lday")
    if not os.path.isdir(lday_dir): continue
    for fname in os.listdir(lday_dir):
        if not fname.endswith(".day"): continue
        s = load_stock(market, fname)
        if s and len(s["dates"]) >= 300: stocks[s["code"]] = s

codes = list(stocks.keys())
random.seed(42)
if len(codes) > N: codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}

# Collect: signal day → next day buy
signals = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"], "close": s["c"], "volume": s["v"]})
    try: f1 = compute_f1(df)
    except: continue
    for i in range(250, len(df)-1):  # -1 because we need next day
        if f1[i]:
            next_idx = i + 1
            next_open = s["o"][next_idx]
            next_pc = s["c"][i]  # signal day close as prev_close for limit calc
            limit_up = round(next_pc * 1.10, 2)
            # Only skip if next day is一字板 (open=high=close) or open already at limit-up
            if next_open >= limit_up - 0.01: continue
            if s["o"][next_idx] == s["h"][next_idx] == s["c"][next_idx]: continue
            signals.append({"code": code, "signal_date": s["dates"][i],
                          "buy_date": s["dates"][next_idx],
                          "entry_price": next_open,
                          "buy_idx": next_idx})

print(f"Signals: {len(signals)} (signal day不限涨停, 次日开盘可买即可)\n")

# ═══════════════════ 出场方案对比 ═══════════════════
def bt(name, hs, tp_pct, tp_drop, tp_sell, dp_exit, time_d):
    trades = []
    for sig in signals:
        code, idx, ep = sig["code"], sig["buy_idx"], sig["entry_price"]
        s = stocks[code]
        peak = ep; remain = 100; dc = 0; tp_done = False; sl3_done = False
        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            if j > idx:
                pc = s["c"][j-1]; lu = round(pc*1.10,2)
                if p >= lu-0.01: continue  # 涨停持有
            # TP
            if not tp_done and pp >= tp_pct and (p-peak)/ep <= tp_drop:
                s_pct = tp_sell if tp_sell < 100 else remain
                trades.append(dict(pnl=(pnl-0.0013)*(s_pct/100),days=dc,r="TP"))
                tp_done = True; remain -= s_pct; peak = p
                if remain <= 0: break; continue
            # SL T1: -3% 减半
            if not sl3_done and pnl <= -0.03:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=dc,r="-3%减半"))
                sl3_done = True; remain -= 50; peak = p
                if remain <= 0: break; continue
            # SL T2: hard stop
            if pnl <= hs:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r=f"止损{hs:.0%}"))
                break
            # 不涨停回落
            if pp >= 0.02 and (p-peak)/ep <= dp_exit:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r=f"回落{dp_exit*100:.0f}%"))
                break
            # Time
            if dc >= time_d and pnl < 0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r=f"时间{time_d}天"))
                break
    if not trades: return (name, 0,0,0,0,0,0,{})
    w=[t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return (name, len(trades), wr, aw, al, pf, np.mean([t["days"] for t in trades]), sum(t["pnl"] for t in trades))

TESTS = [
    ("C: +5%回1.5全清(次日买)",  -0.05, 0.05, -0.015, 100, -0.03, 5),
    ("B: +7%回1.5减半(次日买)",  -0.05, 0.07, -0.015, 50,  -0.03, 5),
    ("F: +5%回1.5减半(次日买)",  -0.05, 0.05, -0.015, 50,  -0.03, 5),
    ("G: +8%回2减半(次日买)",    -0.05, 0.08, -0.020, 50,  -0.03, 5),
    ("H: +5%回1.5减半+8天(次日)",-0.05, 0.05, -0.015, 50,  -0.03, 8),
]

results = []
for args in TESTS:
    r = bt(*args)
    results.append(r)
    print(f"  {r[0]:<30} T={r[1]:>5} WR={r[2]:>5.1%} AW={r[3]:>5.2%} AL={r[4]:>5.2%} PF={r[5]:>5.2f} D={r[6]:>4.1f} PnL={r[7]:>+8.2f}")

results.sort(key=lambda x: -x[5])
print(f"\n  Ranking:")
for rank, r in enumerate(results):
    print(f"  {rank+1}. {r[0]:<30} PF={r[5]:.2f} WR={r[2]:.1%} T={r[1]}")

# Compare with same-day entry
print(f"\n  vs 当日买入C方案 PF=1.24:")
next_best = results[0]
improvement = (next_best[5] - 1.24) / 1.24 * 100
print(f"  次日买入效果: {'更好' if improvement>0 else '略差'} ({improvement:+.0f}%)")
print("  Done!")
