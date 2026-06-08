"""多层止盈止损大对比 — 同一数据集, 5种方案PK"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd
import random, time as _time
import warnings; warnings.filterwarnings("ignore")

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 2000  # 用2000只快速跑

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

# ═══════════════════ 收集F1信号 ═══════════════════
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
    df = pd.DataFrame({"open": s["o"], "high": s["h"], "low": s["l"],
                       "close": s["c"], "volume": s["v"]})
    try: f1 = compute_f1(df)
    except: continue
    for i in range(250, len(df)):
        if f1[i]:
            signals.append({"code": code, "date": s["dates"][i], "entry_price": s["c"][i]})

print(f"F1 signals: {len(signals)} on {N} stocks\n")

# ═══════════════════ 5种出场方案 ═══════════════════
def simulate(signals, name, rules):
    """rules: [(condition, sell_pct, label), ...]  条件按优先级排列"""
    trades = []
    for sig in signals:
        code = sig["code"]; ep = sig["entry_price"]; d = sig["date"]
        s = stocks[code]
        if d not in s["dates"]: continue
        idx = s["dates"].index(d)
        peak = ep; remain = 100; d_count = 0
        done = set()

        for j in range(idx+1, min(idx+30, len(s["dates"]))):
            p = s["c"][j]; h = s["h"][j]
            d_count += 1
            if h > peak: peak = h
            pnl = (p - ep) / ep; pp = (peak - ep) / ep

            # 涨停持有
            if j > idx:
                pc = s["c"][j-1]; lu = round(pc*1.10, 2)
                if p >= lu - 0.01: continue

            triggered = False
            for rule_label, cond_fn, sell_pct in rules:
                if rule_label in done: continue
                if cond_fn(pnl, pp, p, peak, ep, d_count):
                    s_pct = sell_pct(remain) if callable(sell_pct) else sell_pct
                    if s_pct > 0 and s_pct <= remain:
                        trades.append(dict(pnl=(pnl-0.0013)*(s_pct/100), days=d_count, r=rule_label))
                        done.add(rule_label)
                        remain -= s_pct; peak = p
                        triggered = True
                        if remain <= 0: break
                    elif s_pct >= remain:
                        trades.append(dict(pnl=(pnl-0.0013)*(remain/100), days=d_count, r=rule_label))
                        remain = 0
                        break
            if remain <= 0: break

    if not trades: return (name, 0,0,0,0,0,0,{})
    w = [t for t in trades if t["pnl"]>0]; l_=[t for t in trades if t["pnl"]<=0]
    wr=len(w)/len(trades); aw=np.mean([t["pnl"] for t in w]) if w else 0
    al=np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf=abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    rc={}
    for t in trades: rc[t["r"]]=rc.get(t["r"],0)+1
    total_pnl=sum(t["pnl"] for t in trades)
    return(name,len(trades),wr,aw,al,pf,np.mean([t["days"] for t in trades]),total_pnl,rc)

# ═══════════════════ 方案定义 ═══════════════════
PLANS = []

# 方案A: 你的多层规则
def plan_a():
    rules = [
        ("+5%回1.5减1/3", lambda pnl,pp,p,peak,ep,dc: pp>=0.05 and (p-peak)/ep<=-0.015 and dc>=1, 33),
        ("+7%回1.5减1/3", lambda pnl,pp,p,peak,ep,dc: pp>=0.07 and (p-peak)/ep<=-0.015, 33),
        ("+7%回3%全清",   lambda pnl,pp,p,peak,ep,dc: pp>=0.07 and (p-peak)/ep<=-0.03, 100),
        ("-3%减半",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.03, 50),
        ("-5%全清",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.05, 100),
        ("不涨停回落3%",  lambda pnl,pp,p,peak,ep,dc: pp>=0.02 and (p-peak)/ep<=-0.03, 100),
        ("时间5天",       lambda pnl,pp,p,peak,ep,dc: dc>=5 and pnl<0.01, 100),
    ]
    PLANS.append(("A:你的多层规则", rules))

# 方案B: +7%回1.5减半(简版)
rules_b = [
    ("+7%回1.5减半",  lambda pnl,pp,p,peak,ep,dc: pp>=0.07 and (p-peak)/ep<=-0.015, 50),
    ("-5%全清",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.05, 100),
    ("不涨停回落3%",  lambda pnl,pp,p,peak,ep,dc: pp>=0.02 and (p-peak)/ep<=-0.03, 100),
    ("时间5天",       lambda pnl,pp,p,peak,ep,dc: dc>=5 and pnl<0.01, 100),
]
PLANS.append(("B:+7%回1.5减半(简版)", rules_b))

# 方案C: +5%回1.5全清(最简单)
rules_c = [
    ("+5%回1.5全清",  lambda pnl,pp,p,peak,ep,dc: pp>=0.05 and (p-peak)/ep<=-0.015, 100),
    ("-5%全清",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.05, 100),
    ("不涨停回落3%",  lambda pnl,pp,p,peak,ep,dc: pp>=0.02 and (p-peak)/ep<=-0.03, 100),
    ("时间5天",       lambda pnl,pp,p,peak,ep,dc: dc>=5 and pnl<0.01, 100),
]
PLANS.append(("C:+5%回1.5全清(最简单)", rules_c))

# 方案D: 分层止损+分层止盈(优化版)
rules_d = [
    ("+5%回1.5减1/3", lambda pnl,pp,p,peak,ep,dc: pp>=0.05 and (p-peak)/ep<=-0.015, 33),
    ("+7%回1.5减1/3", lambda pnl,pp,p,peak,ep,dc: pp>=0.07 and (p-peak)/ep<=-0.015, 33),
    ("+10%回3%全清",  lambda pnl,pp,p,peak,ep,dc: pp>=0.10 and (p-peak)/ep<=-0.03, 100),
    ("-3%减半",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.03, 50),
    ("-5%全清",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.05, 100),
    ("不涨停回落4%",  lambda pnl,pp,p,peak,ep,dc: pp>=0.02 and (p-peak)/ep<=-0.04, 100),
    ("时间8天",       lambda pnl,pp,p,peak,ep,dc: dc>=8 and pnl<0.01, 100),
]
PLANS.append(("D:优化版(+10%回3+回落4%+8天)", rules_d))

# 方案E: 激进出场
rules_e = [
    ("+7%回2%减半",   lambda pnl,pp,p,peak,ep,dc: pp>=0.07 and (p-peak)/ep<=-0.02, 50),
    ("+10%回3%全清",  lambda pnl,pp,p,peak,ep,dc: pp>=0.10 and (p-peak)/ep<=-0.03, 100),
    ("-3%减半",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.03, 50),
    ("-5%全清",       lambda pnl,pp,p,peak,ep,dc: pnl<=-0.05, 100),
    ("不涨停回落3%",  lambda pnl,pp,p,peak,ep,dc: pp>=0.02 and (p-peak)/ep<=-0.03, 100),
    ("时间5天",       lambda pnl,pp,p,peak,ep,dc: dc>=5 and pnl<0.01, 100),
]
PLANS.append(("E:激进(+7%回2减半+10%回3清)", rules_e))

# ═══════════════════ 运行 ═══════════════════
print("Testing 5 exit strategies...\n")
results = []
for name, rules in PLANS:
    r = simulate(signals, name, rules)
    results.append(r)
    print(f"  {name:<32} T={r[1]:>5} WR={r[2]:>5.1%} AW={r[3]:>5.2%} AL={r[4]:>5.2%} PF={r[5]:>5.2f} D={r[6]:>4.1f} PnL={r[7]:>+8.2f}")

results.sort(key=lambda x: -x[5])

print(f"\n{'='*75}")
print(f"  最终排名")
print(f"{'='*75}")
print(f"  {'#':<3} {'方案':<32} {'交易':>5} {'胜率':>6} {'均盈':>6} {'均亏':>6} {'PF':>6} {'持仓':>5} {'总盈亏':>10}")
print(f"  {'-'*3} {'-'*32} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5} {'-'*10}")
for rank, r in enumerate(results):
    print(f"  {rank+1:<3} {r[0]:<32} {r[1]:>5} {r[2]:>5.1%} {r[3]:>5.2%} {r[4]:>5.2%} {r[5]:>6.2f} {r[6]:>5.1f} {r[7]:>+10.2f}")

best = results[0]
print(f"\n{'='*75}")
print(f"  🏆 最优: {best[0]}")
print(f"{'='*75}")
print(f"  PF={best[5]:.2f} | 胜率={best[2]:.1%} | 交易={best[1]} | 持仓={best[6]:.1f}天")
print(f"  出场分布:")
for rc, count in sorted(best[8].items(), key=lambda x: -x[1]):
    print(f"    {rc}: {count}笔 ({count/best[1]*100:.0f}%)")

# 用户方案排名
print(f"\n{'='*75}")
print(f"  你的方案排名: ")
for rank, r in enumerate(results):
    if "你的" in r[0] or "A:" in r[0]:
        print(f"    第{rank+1}名: {r[0]} PF={r[5]:.2f}")
        break

print("  Done!")
