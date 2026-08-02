"""Evidence 历史归档 — 每日收盘后运行
用法: python archive_evidence.py
产出: data/evidence_history/YYYYMMDD.json
"""
import sys, os, json, shutil
from datetime import datetime
sys.path.insert(0, r"D:\quant_framework")

today = datetime.now().strftime("%Y%m%d")
history_dir = r"D:\quant_framework\data\evidence_history"
os.makedirs(history_dir, exist_ok=True)

sources = {
    "evidence": r"D:\quant_web\data\ml_evidence.json",
    "exit": r"D:\quant_framework\data\exit_attribution.json",
    "health": r"D:\quant_framework\data\evidence_health.json",
}

snapshot = {"date": today, "archived_at": datetime.now().isoformat()}
for name, path in sources.items():
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            snapshot[name] = json.load(f)
        print(f"  ✅ {name}: {os.path.getsize(path)} bytes")
    else:
        print(f"  ⚠️  {name}: not found")

out_path = os.path.join(history_dir, f"{today}.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, ensure_ascii=False, indent=2)

# Count history
history_files = sorted([f for f in os.listdir(history_dir) if f.endswith(".json")])
print(f"\n📦 Evidence 历史: {len(history_files)} 天 → {history_dir}")
for f in history_files[-7:]:
    print(f"    {f}")
