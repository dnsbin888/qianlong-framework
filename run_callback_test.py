"""回调策略回测 — 仅2024-2026数据"""
import struct, os, numpy as np, pandas as pd, random
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 5000
START, END = 20240101, 20260601

def ld(m, f):
    c = f.replace(m, '').replace('.day', '')
    if len(c) != 6 or not c.isdigit(): return None
    p = os.path.join(ROOT, m, 'lday', f)
    if not os.path.exists(p): return None
    with open(p, 'rb') as fh: raw = fh.read()
    d, o, h, l, cl, v = [], [], [], [], [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
            d.append(vs[0]); o.append(vs[1]/100.); h.append(vs[2]/100.)
            l.append(vs[3]/100.); cl.append(vs[4]/100.); v.append(vs[6])
    return {'code': c, 'd': d, 'o': o, 'h': h, 'l': l, 'c': cl, 'v': v}

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _every(c, n): return c.rolling(n, min_periods=1).min().fillna(0).astype(bool)
def _exist(c, n): return c.rolling(n, min_periods=1).max().fillna(0).astype(bool)

stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

codes = list(stocks.keys()); random.seed(42)
if len(codes) > N: codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}
print(f"{len(stocks)} stocks, period {START}-{END}")

def simulate(entries):
    ts = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = lh = False
        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if zt: lh = True; continue
            if lh and not zt: lh = False
            r, sp = None, 0
            if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "T1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "T2", 100
            elif pnl <= -0.05: r, sp = "SL", 100
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Drop", 100
            elif dc >= 5 and pnl < 0.01: r, sp = "Time", 100
            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break
    if not ts: return (0, 0, 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    mcl = cur = 0
    for t in ts:
        if t["pnl"] <= 0: cur += 1
        else: cur = 0
        mcl = max(mcl, cur)
    return (len(entries), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts), mcl, len(entries)/500)

def calc(name, entries):
    t = simulate(entries)
    if t[1] > 0:
        print(f"  {name:<30} Ent={t[0]:>4} WR={t[2]:>5.0%} PF={t[5]:>6.1f} Daily={t[8]:>4.1f} MCL={t[7]:>3}")
    else:
        print(f"  {name:<30} Ent={t[0]:>4} 0 trades")

# ===== Strategy 1: Callback after breakout =====
def signal_callback_after_breakout(df):
    """突破后缩量回踩: 前一天放量突破+今天缩量回踩到突破价附近"""
    c, o, h, l, v = df['close'], df['open'], df['high'], df['low'], df['volume']
    n = len(c)

    # Yesterday: breakout signal
    hhv10 = _hhv(h, 10)
    pressure = _ref(hhv10, 1)
    y_breakout = (_ref(c, 1) > _ref(pressure, 1)) & (_ref(c, 1) > _ref(o, 1))
    y_vol_ok = _ref(v, 1) / _ref(v, 2) >= 1.5
    y_signal = y_breakout & y_vol_ok

    # Today: pullback + shrinking volume + close above signal close
    today_pullback = (l < _ref(c, 1) * 1.005) & (c > _ref(c, 1) * 0.97)
    vol_shrink = v < _ref(v, 1) * 0.8
    trend_ok = _ema(c, 13) > _ema(c, 50)

    result = np.zeros(n, dtype=int)
    for i in range(1, n):
        if y_signal.iloc[i] and today_pullback.iloc[i] and vol_shrink.iloc[i] and trend_ok.iloc[i]:
            result[i] = 1
    return result

# ===== Strategy 2: Low+volume shrink bounce =====
def signal_low_bounce(df):
    """低位缩量止跌反弹: 跌到20日低点附近+缩量+收阳"""
    c, o, l, v = df['close'], df['open'], df['low'], df['volume']
    llv20 = _llv(l, 20)
    near_low = l / llv20 < 1.03  # Within 3% of 20-day low
    vol_shrink = v < v.rolling(20).mean() * 0.7  # Volume < 70% of MA20
    green_candle = c > o  # Green candle
    trend_ok = _ema(c, 13) > _ema(c, 50)

    return (near_low & vol_shrink & green_candle & trend_ok).astype(int).values

# ===== Strategy 3: DC5 callback =====
def signal_dc5_callback(df):
    """DC5信号后缩量回踩买入"""
    c, o, h, l, v = df['close'], df['open'], df['high'], df['low'], df['volume']
    n = len(c)

    g3 = c > o; g6 = (c/_ref(c, 1) > 1.098) & (h == c)
    g8 = _every(pd.Series(g6), 2); g9 = _every(pd.Series(g6), 5)
    g10 = (_ref(c, 1)/_ref(c, 2) < 1.098) & (_ref(h, 1) > _ref(c, 1))
    g11 = ~_exist(pd.Series(g9), 90); g12 = ~_exist(pd.Series(g8), 7)
    g4 = (h-np.maximum(c, o))/_ref(c, 1); g2 = np.round(_ref(c, 1)*0.90, 2)
    dc = (v/_ref(v, 1) >= 2) & (v/v.rolling(100).mean() >= 2)
    dc1 = (v/_ref(v, 1) >= 3) & (v/v.rolling(100).mean() > 1.1)
    bb = (l == g2).values
    dc2 = ((dc1|dc) & g11 & g12 & (pd.Series(bb).rolling(10, min_periods=1).sum() < 2) & g10 & g3)
    dc3 = dc2.copy(); lt = -999
    for i in range(len(dc3)):
        if dc3.iloc[i] and i-lt > 3: lt = i
        elif i-lt <= 3: dc3.iloc[i] = False
    dc4 = ((h/_ref(c, 1) >= 1.07) & g3 & g10 & (h > c) & _exist(pd.Series(dc3), 5) & (g4 > 0.01) & g12 & g11 & (v == v.rolling(5).max()))
    dc5_arr = dc4.copy(); lt = -999
    for i in range(len(dc5_arr)):
        if dc5_arr.iloc[i] and i-lt > 15: lt = i
        elif i-lt <= 15: dc5_arr.iloc[i] = False

    # After DC5, wait for pullback
    result = np.zeros(n, dtype=int)
    for i in range(5, n):
        # DC5 happened in last 5 days
        if dc5_arr[i-1:i].sum() > 0:  # DC5 within last day
            # Today: pullback to near DC5 signal close + vol shrink + green
            yc = c.iloc[i-1]
            if (l.iloc[i] < yc * 1.01 and c.iloc[i] > yc * 0.97 and
                v.iloc[i] < v.iloc[i-1] * 0.8 and c.iloc[i] > o.iloc[i]):
                result[i] = 1
    return result

# ===== Collect entries =====
print("\nCollecting entries (2024-2026 only)...")
all_results = []

for sig_fn, sig_name, mp, mt, mvr in [
    (signal_callback_after_breakout, "突破后缩量回踩", 15, 1e8, 0),
    (signal_low_bounce, "低位缩量止跌反弹", 15, 1e8, 0),
    (signal_dc5_callback, "DC5后缩量回踩", 15, 1e8, 0),
    (signal_callback_after_breakout, "突破后回踩+P18", 18, 2e8, 2.0),
    (signal_low_bounce, "低位止跌+P18+V2", 18, 2e8, 2.0),
]:
    entries = []
    for code in codes:
        s = stocks[code]
        df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                           'close': s['c'], 'volume': s['v']})
        try: sig = sig_fn(df)
        except: continue
        for i in range(250, len(df)-1):
            if not sig[i]: continue
            d = s['d'][i]
            if d < START or d > END: continue
            sc = s['c'][i]; sv = s['v'][i]
            if sc < mp: continue
            if mt > 0 and sv*sc < mt: continue
            if mvr > 0:
                avg20 = np.mean(s['v'][max(0, i-20):i+1])
                if sv < avg20 * mvr: continue
            ni = i+1
            if ni >= len(s['d']): continue
            no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01 or (s['o'][ni] == s['h'][ni] == s['c'][ni]): continue
            entries.append({'code': code, 'idx': ni, 'ep': no})

    calc(sig_name, entries)

print(f"\nRef: DC5 T+1(P18+T2e8) on 2024-2026: avg +0.4% WR=48%")
print("Done!")
