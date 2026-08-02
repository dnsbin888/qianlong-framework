"""TDX池管理 — 交付验收"""
import json, os, urllib.request

BASE = "http://127.0.0.1:5002"
ok=0; fail=0
def ck(label, cond):
    global ok,fail
    if cond: print(f"  ✅ {label}"); ok+=1
    else: print(f"  ❌ {label}"); fail+=1

print("1. 配置文件")
ck("tdx_pools_config.json", os.path.exists(r"D:\quant_web\data\tdx_pools_config.json"))
cfg = json.load(open(r"D:\quant_web\data\tdx_pools_config.json", encoding="utf-8"))
ck("有pools字段", "pools" in cfg)
ck("QLJCXG存在", "QLJCXG" in cfg["pools"])
ck("target=daban", cfg["pools"]["QLJCXG"].get("target")=="daban")

print("\n2. 实时信号")
live_p = r"D:\quant_web\data\tdx_live_signals.json"
ck("tdx_live_signals存在", os.path.exists(live_p))
live = json.load(open(live_p, encoding="utf-8"))
ck("QLJCXG有信号", "QLJCXG" in live)

print("\n3. API")
for url, name in [
    ("/api/tdx-pools", "TDX信号API"),
    ("/api/tdx-pools-config", "TDX配置API"),
]:
    try:
        d = json.load(urllib.request.urlopen(f"{BASE}{url}", timeout=5))
        ck(name, True)
    except: ck(name, False)

print("\n4. 页面")
for url, name in [("/tdx-pools", "管理页"), ("/terminal", "终端"), ("/ml-signals", "选股器")]:
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=5)
        ck(f"{name} (HTTP {r.status})", r.status==200)
    except: ck(name, False)

print("\n5. QMT策略")
qmt = open(r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py", encoding="gbk").read()
ck("含target路由", "pool_target" in qmt)
ck("含daban分支", '"daban"' in qmt or "'daban'" in qmt)
ck("含wts分支", '"wts"' in qmt or "'wts'" in qmt)

print("\n6. Watcher")
watcher = open(r"D:\quant_framework\tdx_pool_watcher.py", encoding="utf-8").read()
ck("读新配置", "tdx_pools_config.json" in watcher)

print(f"\n{'='*30}")
print(f"通过:{ok} 失败:{fail}")
print(f"{'✅ 验收通过' if fail==0 else '❌ 不合格'}")
