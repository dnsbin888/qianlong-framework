"""批量导入TDX股票池历史日志"""
import os, json, sys, time, glob as gb
sys.path.insert(0, r"D:\quant_web")

POOL_DIR = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\T0002\tpool"
OUTPUT = r"D:\quant_web\data\tdx_live_signals.json"

# 初始化名称库
try:
    from stock_names import get_stock_name, init_names
    init_names()
    print(f"[Names] Ready: {len(get_stock_name.__globals__.get('_NAME_CACHE',{}))} names")
except: pass

# 读取现有数据
existing = {}
if os.path.exists(OUTPUT):
    with open(OUTPUT, encoding="utf-8") as f:
        existing = json.load(f)

# 扫描所有池的所有历史日志
imported = 0
for pool_dir in os.listdir(POOL_DIR):
    pool_path = os.path.join(POOL_DIR, pool_dir)
    if not os.path.isdir(pool_path):
        continue
    for num_dir in os.listdir(pool_path):
        num_path = os.path.join(pool_path, num_dir)
        if not os.path.isdir(num_path):
            continue
        log_files = gb.glob(os.path.join(num_path, "*.log"))
        for lf in sorted(log_files):
            try:
                from xml.etree import ElementTree as ET
                tree = ET.parse(lf)
                fresh = []
                seen = set()
                if pool_dir in existing and "_signals" in existing[pool_dir]:
                    for s in existing[pool_dir]["_signals"]:
                        seen.add((s.get("symbol",""), s.get("date",""), s.get("time","")))
                for stk in tree.getroot().iter("stk"):
                    mkt = stk.get("market", "")
                    code = stk.get("code", "").zfill(6)
                    prefix = {"0": "sz", "1": "sh", "2": "bj"}.get(mkt, "")
                    if not prefix:
                        continue
                    sym = prefix + code
                    date = stk.get("indate", "")
                    time_str = stk.get("intime", "")
                    key = (sym, date, time_str)
                    if key in seen:
                        continue
                    seen.add(key)
                    # 查名称
                    name = ""
                    try: n=get_stock_name(sym); name=n if n and n!=sym and not str(n).isdigit() else ""
                    except: pass
                    fresh.append({
                        "symbol": sym,
                        "name": name,
                        "date": date,
                        "time": time_str,
                        "price": float(stk.get("inprice", 0) or 0),
                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                if fresh:
                    if pool_dir not in existing:
                        existing[pool_dir] = {"type": "pool", "label": pool_dir, "_signals": []}
                    existing[pool_dir]["_signals"].extend(fresh)
                    imported += len(fresh)
                    print(f"  {pool_dir}/{num_dir}/{os.path.basename(lf)}: +{len(fresh)}只")
            except Exception as e:
                pass  # 跳过非XML或格式不对的文件

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)
print(f"\n✅ 批量导入完成: {imported} 只信号, {len(existing)} 个池")
for k, v in existing.items():
    if not k.startswith("_"):
        print(f"  {k}: {len(v.get('_signals',[]))} 只")
