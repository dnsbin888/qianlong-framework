"""QLJCXG blk → 妖股先锋+波段擒妖 pool 数据迁移"""
import json

path = r"D:\quant_web\data\tdx_live_signals.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)

old = data.pop("QLJCXG", {})
old_sigs = old.get("_signals", [])
print(f"QLJCXG 历史信号: {len(old_sigs)} 只")

new_name = "妖股先锋 + 波段擒妖"
if new_name in data:
    existing = data[new_name].get("_signals", [])
    seen = {(s["symbol"], s.get("date","")) for s in existing}
    migrated = 0
    for s in old_sigs:
        key = (s["symbol"], s.get("date", ""))
        if key not in seen:
            existing.append(s)
            seen.add(key)
            migrated += 1
    data[new_name]["_signals"] = existing
    print(f"已迁移: {migrated} 只新信号 (去重后), 池内总计: {len(existing)} 只")
else:
    data[new_name] = {
        "label": "妖股先锋∩波段擒妖·实时",
        "type": "pool",
        "_signals": old_sigs
    }
    print(f"新建池, 迁移 {len(old_sigs)} 只")

# 加上元数据标记
old["_archived"] = True
old["_note"] = "已迁移至妖股先锋 + 波段擒妖"
data["QLJCXG_archive"] = old

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ 迁移完成")
for k in data:
    if not k.startswith("_"):
        sigs = data[k].get("_signals", [])
        print(f"  {k}: {len(sigs)} 只")
