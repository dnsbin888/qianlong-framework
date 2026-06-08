"""F1同步 v4 — 管道模式可选"""
import struct, os, numpy as np, pandas as pd, json, shutil
from datetime import datetime

ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
NAMES_FILE = r"d:\quant_framework\stock_names.json"

# ═══════════════════ 管道模式选择 ═══════════════════
# 'full'  = M+S+T+V+P+L  全开 (PF=16.7, 日均2-3只, 大盘强时用)
# 'weak'  = T+V+P+L      弱市放宽 (大盘MA20下方时用)
# 'loose' = T+V+P        最宽 (信号少时启用)
PIPELINE_MODE = "full"  # 'full'='weak'/'loose'

# 过滤参数
CONFIG = {
    "min_price": 18, "max_price": 100,
    "min_turnover": 2e8,
    "min_vol_ratio": 3.0,
    "sector_heat": 0.6,
}

KB_DIRS = [
    r"d:\通信达技术指标\1键盘管理软件\24键专业版 ID条件单",
    r"d:\通信达技术指标\1键盘管理软件\24键64专业版7",
    r"d:\1键盘\24键专业版 ID条件单",
]

# ═══════════════════ 数据加载 ═══════════════════
def ld_day(path):
    if not os.path.exists(path): return None
    with open(path, 'rb') as fh: raw = fh.read()
    d, c = [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
            d.append(vs[0]); c.append(vs[4]/100.)
    return {'d': d, 'c': c}

def ld_stock(m, f):
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

def f1(df):
    c, v = df['close'].values, df['volume'].values
    hhv30 = pd.Series(c).rolling(30).max().shift(1); pr = hhv30.rolling(2).mean().values
    e20 = pd.Series(c).ewm(span=20, adjust=False).mean()
    dv = ((pd.Series(c)-e20)**2).rolling(20).mean()**0.5; up = (e20+2*dv).shift(1).values
    vr = v / pd.Series(v).rolling(5).mean().shift(1).replace(0, np.nan)
    qr = (c > pr) & (c > up) & (vr > 1.8); q = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if qr[i] and qr[i-7:i].sum() <= 1: q[i] = 1
    c99 = pd.Series(c).rolling(60).quantile(0.99); p100 = c99.ewm(span=5, adjust=False).mean()
    zr = c > p100.values; z = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if zr[i] and zr[i-7:i].sum() <= 1: z[i] = 1
    return (q & z).astype(int)

# ═══════════════════ 主程序 ═══════════════════
mode_labels = {"full": "全开 M-S-T-V-P-L (PF=16.7)", "weak": "弱市 T-V-P-L (无大盘/板块)", "loose": "最宽 T-V-P"}
print("=" * 60)
print(f"  F1 Sync v4 — Mode: {PIPELINE_MODE} ({mode_labels[PIPELINE_MODE]})")
print("=" * 60)
print(f"  切换: 改 PIPELINE_MODE = 'full'/'weak'/'loose'")
print()

enable_M = PIPELINE_MODE == "full"
enable_S = PIPELINE_MODE in ("full",)

# Phase 1: Market & Block data
print("[1/3] Loading market data...")
blocks = {}
lday_dir = os.path.join(ROOT, "sh", "lday")
for f in os.listdir(lday_dir):
    if f.startswith("sh880") and f.endswith(".day"):
        s = ld_day(os.path.join(lday_dir, f))
        if s and len(s['d']) > 300: blocks[f.replace("sh","").replace(".day","")] = s

block_heat = {}
if enable_S:
    all_dates = sorted(set(d for b in blocks.values() for d in b['d'] if 20200101 <= d <= 20260630))
    for d in all_dates:
        up = total = 0
        for code, b in blocks.items():
            if d in b['d']:
                i = b['d'].index(d)
                if i >= 60 and b['c'][i] > np.mean(b['c'][i-20:i+1]): up += 1
                total += 1
        block_heat[d] = up/total if total > 0 else 0
    print(f"  {len(blocks)} blocks, {len(block_heat)} heat dates")
else:
    print(f"  Skipped (mode={PIPELINE_MODE})")

# Phase 2: Stocks
print("\n[2/3] Loading stocks & F1 signals...")
stocks = {}
latest_date = 0
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld_stock(m, f)
        if s:
            stocks[s['code']] = s
            for d in s['d']:
                if len(str(d)) == 8 and d > latest_date and d <= 20260630:
                    latest_date = d

idx = stocks.get('999999') or stocks.get('000001')
idx_map = {}
if idx and enable_M:
    idx_c = np.array(idx['c']); idx_ma = pd.Series(idx_c).rolling(20).mean().values
    idx_map = {idx['d'][i]: idx_c[i] > idx_ma[i] for i in range(20, len(idx['d']))}

date_str = f"{str(latest_date)[:4]}-{str(latest_date)[4:6]}-{str(latest_date)[6:8]}"
print(f"  {len(stocks)} stocks, latest: {date_str}")

# Phase 3: Pipeline
print(f"\n[3/3] Pipeline: {'M-' if enable_M else ''}{'S-' if enable_S else ''}T-V-P-L...")

names = {}
if os.path.exists(NAMES_FILE):
    with open(NAMES_FILE, 'r', encoding='utf-8') as f: names = json.load(f)

signals = []
stats = {"total": 0, "M": 0, "S": 0, "T": 0, "V": 0, "P": 0, "L": 0, "OK": 0}

for code, s in stocks.items():
    if latest_date not in s['d']: continue
    i = s['d'].index(latest_date)
    if i < 250: continue

    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    if not ff[i]: continue
    stats["total"] += 1

    sig_d = s['d'][i]; sc = s['c'][i]; sv = s['v'][i]

    if enable_M and idx is not None and sig_d in idx_map and not idx_map[sig_d]:
        stats["M"] += 1; continue
    if enable_S and block_heat.get(sig_d, 0.5) < CONFIG["sector_heat"]:
        stats["S"] += 1; continue
    if sv * sc < CONFIG["min_turnover"]: stats["T"] += 1; continue
    avg20 = np.mean(s['v'][max(0, i-20):i])
    if sv < avg20 * CONFIG["min_vol_ratio"]: stats["V"] += 1; continue
    if sc < CONFIG["min_price"] or sc > CONFIG["max_price"]: stats["P"] += 1; continue

    ni = i+1
    if ni >= len(s['d']): continue
    no = s['o'][ni]; lu = round(sc*1.10, 2)
    if no >= lu-0.01: stats["L"] += 1; continue
    if s['o'][ni] == s['h'][ni] == s['c'][ni]: stats["L"] += 1; continue

    stats["OK"] += 1
    prefix = "SH" if code[0] in "56" else "SZ"
    signals.append({"code": f"{prefix}{code}", "sym": code,
                    "name": names.get(code, ""), "price": sc})

pipe_label = f"M:{stats['M']} " if enable_M else ""
pipe_label += f"S:{stats['S']} " if enable_S else ""
print(f"  F1:{stats['total']} -> {pipe_label}T:{stats['T']} V:{stats['V']} P:{stats['P']} L:{stats['L']} OK:{stats['OK']}")
print(f"  Candidates: {len(signals)}")

# Write Table.txt
top_n = min(len(signals), 100)
content = "代码\n\n" + "\n\n".join(s["code"] for s in signals[:top_n]) + "\n\n"

for kd in KB_DIRS:
    if os.path.isdir(kd):
        tp = os.path.join(kd, "Table.txt")
        if os.path.exists(tp):
            shutil.copy(tp, os.path.join(kd, f"Table.{date_str}.bak"))
        with open(tp, 'w', encoding='gbk') as f: f.write(content)
        print(f"  Synced: {os.path.basename(kd)} ({min(top_n, len(signals))} stocks)")

print(f"\n  Top 10:")
for s in signals[:10]:
    print(f"  {s['sym']} {s['name'][:10]:<10} {s['price']:>7.2f}")

if not signals:
    print(f"\n  今日无信号。市场弱势，等待大盘站回MA20。")
    print(f"  临时放宽: 改 PIPELINE_MODE = 'weak' 或 'loose'")

print(f"  Done! {date_str}")
