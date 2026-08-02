"""交付质量验收"""
import json, os, urllib.request

BASE = "http://127.0.0.1:5002"
ok, fail = 0, 0

def ck(label, cond):
    global ok, fail
    if cond: print(f"  ✅ {label}"); ok += 1
    else: print(f"  ❌ {label}"); fail += 1

print("1. 数据文件")
ck("signal_table.json", os.path.exists(r"D:\quant_web\data\signal_table.json"))
ck("auto_trade_plan.json", os.path.exists(r"D:\quant_web\data\auto_trade_plan.json"))
ck("delisted_stocks.parquet", os.path.exists(r"D:\quant_framework\delisted_stocks.parquet"))

print("\n2. 信号表内容")
st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
ck(f"信号数 >= 50 ({len(st)}条)", len(st) >= 50)
l_n = sum(1 for r in st if r.get('lgbm_score',''))
x_n = sum(1 for r in st if r.get('xgb_score',''))
ck(f"LGBM覆盖 ({l_n}只)", l_n > 0)
ck(f"XGB覆盖 ({x_n}只)", x_n > 0)
auto_n = sum(1 for r in st if r.get('auto_enabled'))
ck(f"自动交易 ({auto_n}只)", auto_n > 0)

print("\n3. 来源分类")
srcs = {}
for r in st:
    c = r.get('consensus','?')
    if 'L' in str(c) or 'X' in str(c) or 'R' in str(c): srcs['ML'] = srcs.get('ML',0) + 1
    elif c == '打板': srcs['打板'] = srcs.get('打板',0) + 1
    elif c == '反转': srcs['反转'] = srcs.get('反转',0) + 1
for k, v in srcs.items():
    ck(f"来源-{k} ({v}只)", v > 0)

print("\n4. API连通")
for url, name in [("/api/signal-table", "信号表"), ("/api/paper-trade/v2", "纸交易"),
                  ("/api/market-regime", "市场状态")]:
    try:
        d = json.load(urllib.request.urlopen(f"{BASE}{url}", timeout=5))
        ck(name, True)
    except: ck(name, False)

print("\n5. 前端页")
for url, name in [("/ml-signals", "选股器"), ("/terminal", "终端"), ("/factor-health", "因子")]:
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=5)
        ck(f"{name} (HTTP {r.status})", r.status == 200)
    except: ck(name, False)

# 6. combined = LGBM (不是平均)
print("\n6. combined校验")
ml_signals = [r for r in st if r.get('lgbm_score','')]
mis = 0
for r in ml_signals[:30]:
    l = r.get('lgbm_score', 0) or 0
    c = r.get('combined_score', 0)
    x = r.get('xgb_score', 0) or 0
    if c == 0: continue
    if abs(l - c) > 5 and abs(x - c) > 5: mis += 1
ck(f"combined= L或X ({mis}/30不匹配)", mis < 5)

print(f"\n{'='*30}")
print(f"通过:{ok} 失败:{fail}")
print(f"{'✅ 验收通过' if fail == 0 else '❌ 不合格'}")
