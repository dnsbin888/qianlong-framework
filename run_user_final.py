"""用户最终规则 — 次日买入 + 精确出场"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd, random
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

print("Loading & computing F1 signals...")
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

signals = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"], "close": s["c"], "volume": s["v"]})
    try: f1 = compute_f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if f1[i]:
            next_idx = i + 1
            next_open = s["o"][next_idx]
            limit_up = round(s["c"][i] * 1.10, 2)
            if next_open >= limit_up - 0.01: continue
            if s["o"][next_idx] == s["h"][next_idx] == s["c"][next_idx]: continue
            signals.append({"code": code, "idx": next_idx, "ep": next_open})

print(f"Signals: {len(signals)} (次日开盘买入)\n")

# ═══════════════════ 用户规则回测 ═══════════════════
def bt_user(signals, name):
    """用户规则:
       +7%回落1.5% → 减半仓
       → 后面涨停 → 持有到次日
       → 后面不涨停回落2% → 全清
       -3%减半, -5%全清, 涨停持有, 不涨停回落3%全清, 5天时限"""
    trades = []
    for sig in signals:
        code, idx, ep = sig["code"], sig["idx"], sig["ep"]
        s = stocks[code]
        peak = ep; remain = 100; dc = 0
        half7 = sl3 = False
        limit_held = False  # 涨停持有标记
        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep

            # 涨停?
            is_zt = False
            if j > idx and s["c"][j-1] > 0:
                lu = round(s["c"][j-1]*1.10, 2)
                is_zt = (p >= lu - 0.01)

            # ── 涨停 → 持有 ──
            if is_zt:
                limit_held = True
                continue

            # ── 昨天涨停持有, 今天重新判断所有规则 ──
            if limit_held:
                limit_held = False  # 重置, 今天重新来过
                peak = p  # 从今天的开盘重新计算peak
                # 不涨停且回落2% → 全清
                if not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.02:
                    trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="封板次日回落2%"))
                    break
                continue  # 否则今天正常走下面的规则

            # ── +7%回落1.5% → 减半仓 (只触发一次) ──
            if not half7 and pp >= 0.07 and (p-peak)/ep <= -0.015:
                trades.append(dict(pnl=(pnl-0.0013)*0.5, days=dc, r="+7%回1.5减半"))
                half7 = True; remain -= 50; peak = p
                if remain <= 0: break; continue

            # ── 减半后: +7%后不涨停回落2% → 全清 ──
            if half7 and pp >= 0.07 and not is_zt and (p-peak)/ep <= -0.02:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="+7%不涨停回2%清"))
                break

            # ── -3%减半 ──
            if not sl3 and pnl <= -0.03:
                trades.append(dict(pnl=(pnl-0.0013)*0.5, days=dc, r="-3%减半"))
                sl3 = True; remain -= 50; peak = p
                if remain <= 0: break; continue

            # ── -5%全清 ──
            if pnl <= -0.05:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="-5%全清"))
                break

            # ── 不涨停回落3%全清 ──
            if not limit_held and pp >= 0.02 and (p-peak)/ep <= -0.03:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="回落3%"))
                break

            # ── 时间5天 ──
            if dc >= 5 and pnl < 0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=dc, r="时间5天"))
                break

    if not trades: return (name, 0,0,0,0,0,0,{},0)
    w=[t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    rc={}
    for t in trades: rc[t["r"]]=rc.get(t["r"],0)+1
    total=sum(t["pnl"] for t in trades)
    return (name, len(trades), wr, aw, al, pf, np.mean([t["days"] for t in trades]), rc, total)

# ═══════════════════ 对比: C方案(次日买) ═══════════════════
def bt_c(signals):
    trades = []
    for sig in signals:
        code, idx, ep = sig["code"], sig["idx"], sig["ep"]
        s = stocks[code]
        peak = ep; remain = 100; dc = 0; tp = sl3 = False
        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            if j > idx and s["c"][j-1]>0 and p>=round(s["c"][j-1]*1.10,2)-0.01: continue
            if not tp and pp>=0.05 and (p-peak)/ep<=-0.015:
                trades.append(dict(pnl=(pnl-0.0013),days=dc,r="+5%回1.5全清")); break
            if not sl3 and pnl<=-0.03:
                trades.append(dict(pnl=(pnl-0.0013)*0.5,days=dc,r="-3%减半"))
                sl3=True; remain-=50; peak=p
                if remain<=0: break; continue
            if pnl<=-0.05:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r="-5%全清")); break
            if pp>=0.02 and (p-peak)/ep<=-0.03:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r="回落3%")); break
            if dc>=5 and pnl<0.01:
                trades.append(dict(pnl=(pnl-0.0013)*(remain/100),days=dc,r="时间5天")); break
    if not trades: return (0,0,0,0,0)
    w=[t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return (len(trades), wr, aw, al, pf, sum(t["pnl"] for t in trades))

# ═══════════════════ Run ═══════════════════
r_user = bt_user(signals, "你的规则(次日买)")
r_c = bt_c(signals)

print("  方案                          交易    胜率    均盈    均亏    PF    总盈亏")
print("  ────────────────────────────── ───── ────── ────── ────── ───── ────────")

for name, nt, wr, aw, al, pf, avg_d, rc, total in [r_user]:
    print(f"  {name:<30} {nt:>5} {wr:>5.1%} {aw:>5.2%} {al:>5.2%} {pf:>5.2f} {total:>+8.2f}")

print(f"  {'C:+5%回1.5全清(次日买)':<30} {r_c[0]:>5} {r_c[1]:>5.1%} {r_c[2]:>5.2%} {r_c[3]:>5.2%} {r_c[4]:>5.2f} {r_c[5]:>+8.2f}")

print(f"\n  你的规则出场分布:")
for rc, count in sorted(r_user[7].items(), key=lambda x: -x[1]):
    print(f"    {rc}: {count}笔 ({count/r_user[1]*100:.0f}%)")

print(f"\n  对比:")
if r_user[5] > r_c[4]:
    print(f"  ✅ 你的规则 PF={r_user[5]:.2f} > C方案 PF={r_c[4]:.2f}")
else:
    print(f"  ❌ C方案 PF={r_c[4]:.2f} > 你的规则 PF={r_user[5]:.2f}")
print("  Done!")
