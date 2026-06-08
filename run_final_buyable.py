"""最真实回测: 只交易可买信号 + 多层止盈止损 + 参数优化对比"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd, itertools
import random, time as _time
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 5000

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
    ma_vol5 = pd.Series(v).rolling(5).mean().shift(1)
    vol_ratio = v / ma_vol5.replace(0, np.nan)
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

def compute_f2(df):
    c, h, l = df["close"].values, df["high"].values, df["low"].values
    t1 = (2.15*c + l + h) / 4; t2 = (3.48*c + h + l) / 4
    ema23 = pd.Series(c).ewm(span=23, adjust=False).mean().values
    w = np.abs(t2 - ema23) / np.maximum(ema23, 1e-9); w = np.clip(w, 0, 1)
    dma = np.zeros(len(c)); dma[0] = t1[0]
    for i in range(1, len(c)): dma[i] = w[i]*t1[i] + (1-w[i])*dma[i-1]
    bull = pd.Series(dma).ewm(span=200, adjust=False).mean().values * 1.118
    dif = pd.Series(c).ewm(span=12,adjust=False).mean() - pd.Series(c).ewm(span=26,adjust=False).mean()
    dea = dif.ewm(span=9,adjust=False).mean()
    xg = np.zeros(len(c), dtype=int)
    for i in range(250, len(c)):
        if (c[i-1] <= bull[i-1] and c[i] > bull[i] and dif.iloc[i] > dea.iloc[i] and c[i]/c[i-1] > 1.09):
            xg[i] = 1
    return xg

# ═══════════════════ 收集所有可买信号 ═══════════════════
print("Collecting buyable signals...")
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

# Collect signal events: (code, date, entry_price)
buyable_signals = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"],
                       "close": s["c"], "volume": s["v"]})
    try: f1 = compute_f1(df); f2 = compute_f2(df)
    except: continue
    for i in range(250, len(df)):
        if not (f1[i] and f2[i]): continue
        p = s["c"][i]; o = s["o"][i]; h = s["h"][i]; pc = s["c"][i-1]
        lu = round(pc*1.10, 2) if pc > 0 else 999
        # 可买: 没涨停 + 非一字板
        if p < lu - 0.01 and o < h and p < h:
            buyable_signals.append({"code": code, "date": s["dates"][i], "entry_price": p})

print(f"Buyable signals: {len(buyable_signals)}")

# ═══════════════════ 出场规则 ═══════════════════
def run_exit_rules(name, hard_stop, tp1_pct, tp1_drop, tp1_sell,
                   tp2_pct, tp2_drop, tp2_sell, tp3_drop, drop_exit):
    """模拟出场: 找到信号后, 向前遍历日线直到触发出场条件"""
    trades = []
    for sig in buyable_signals:
        code = sig["code"]; entry_date = sig["date"]; ep = sig["entry_price"]
        s = stocks[code]
        if entry_date not in s["dates"]: continue
        idx = s["dates"].index(entry_date)
        peak = ep; remain = 100; d_count = 0
        tp1_done = tp2_done = sl3_done = False

        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]; o = s["o"][j]
            d_count += 1
            if h > peak: peak = h
            pnl = (p - ep) / ep; pp = (peak - ep) / ep

            # 涨停? 跳过(持有)
            if j > idx:
                pc = s["c"][j-1]; lu = round(pc*1.10, 2)
                if p >= lu - 0.01: continue

            # TP T1: +tp1%回落tp1_drop → 卖 tp1_sell
            if not tp1_done and pp >= tp1_pct and (p-peak)/ep <= tp1_drop:
                s_pct = tp1_sell
                trades.append(dict(pnl=(pnl-0.0013)*(s_pct/100), days=d_count, r="TP1"))
                tp1_done = True; remain -= s_pct; peak = p
                if remain <= 0: break; continue

            # TP T2: +tp2%回落tp2_drop → 卖 tp2_sell
            if tp1_done and not tp2_done and pp >= tp2_pct and (p-peak)/ep <= tp2_drop:
                s_pct = tp2_sell
                if s_pct <= remain:
                    trades.append(dict(pnl=(pnl-0.0013)*(s_pct/100), days=d_count, r="TP2"))
                    tp2_done = True; remain -= s_pct; peak = p
                if remain <= 0: break; continue

            # TP T3: +tp2%回落tp3_drop → 全清
            if tp2_done and pp >= tp2_pct and (p-peak)/ep <= tp3_drop:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=d_count, r="TP3全清"))
                break

            # SL T1: -3%减半
            if not sl3_done and pnl <= -0.03:
                trades.append(dict(pnl=(pnl-0.0013)*0.5, days=d_count, r="-3%减半"))
                sl3_done = True; remain -= 50; peak = p
                if remain <= 0: break; continue

            # SL T2: hard_stop全清
            if pnl <= hard_stop:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=d_count, r=f"止损{hard_stop:.0%}"))
                break

            # 不涨停回落
            if not (p >= lu-0.01) and pp >= 0.02 and (p-peak)/ep <= drop_exit:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=d_count, r=f"回落{drop_exit*100:.0f}%"))
                break

            # 时间
            if d_count >= 5 and pnl < 0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=d_count, r="时间5天"))
                break

    if not trades: return (name, 0,0,0,0,0,0,{})
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    rc={}
    for t in trades: rc[t["r"]]=rc.get(t["r"],0)+1
    return(name,len(trades),wr,aw,al,pf,np.mean([t["days"] for t in trades]),rc)

# ═══════════════════ 对比测试 ═══════════════════
TESTS = [
    # (name, hs, tp1%, tp1_drop, tp1_sell%, tp2%, tp2_drop, tp2_sell%, tp3_drop, drop_exit%)
    ("A:用户规则",       -0.05, 0.05, -0.015, 33,  0.07, -0.015, 33,  -0.03, -0.03),
    ("B:+5%回1.5减半",   -0.05, 0.05, -0.015, 50,  0.07, -0.015, 0,   -0.03, -0.03),
    ("C:+7%回1.5减半",   -0.05, 0.07, -0.015, 50,  0.10, -0.020, 0,   -0.04, -0.02),
    ("D:+5%回1.5全清",   -0.05, 0.05, -0.015, 100, 0.00,  0.000, 0,    0.00, -0.03),
    ("E:宽止损+早止盈",  -0.08, 0.05, -0.015, 50,  0.08, -0.020, 0,   -0.04, -0.025),
]

print(f"\nTesting {len(TESTS)} exit strategies...\n")
results = []
for args in TESTS:
    r = run_exit_rules(*args)
    results.append(r)
    print(f"  {r[0]:<22} T={r[1]:>4} WR={r[2]:>5.1%} AW={r[3]:>5.2%} AL={r[4]:>5.2%} PF={r[5]:>5.2f} D={r[6]:>4.1f}")

results.sort(key=lambda x: -x[5])

print(f"\n{'='*65}")
print(f"  最优方案排名")
print(f"{'='*65}")
for rank, r in enumerate(results):
    print(f"  {rank+1}. {r[0]:<22} PF={r[5]:.2f} WR={r[2]:.1%} T={r[1]} AW={r[3]:.2%} AL={r[4]:.2%}")

best = results[0]
print(f"\n{'='*65}")
print(f"  推荐: {best[0]} PF={best[5]:.2f}")
print(f"{'='*65}")
print(f"  出场分布:")
for reason, count in sorted(best[7].items(), key=lambda x: -x[1]):
    print(f"    {reason}: {count}笔 ({count/best[1]*100:.0f}%)")
print(f"\n  键盘设置:")
print(f"    亏损=3(减半) 亏损=5(全清)")
print(f"    盈利=7 冲高回落=1.5")
print(f"    回落比例=3")
print("  Done!")
