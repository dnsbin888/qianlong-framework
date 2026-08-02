"""LGBM 因子精简: 读IC → 砍哑因子 → 对比IC"""
import json, sys, numpy as np
sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")

# 1. 读取因子IC
reg = json.load(open(r"D:\quant_framework\factor_registry.json", encoding="utf-8"))
factors = reg.get("factors", [])

# 2. 排序
ranked = []
for f in factors:
    ic5 = f.get("ic_5d", 0) or 0
    status = f.get("status", "active")
    verified = f.get("ic_verified_days", 0)
    name = f.get("name", "?")
    if status == "retired":
        continue
    ranked.append((name, ic5, verified, f.get("ic_10d", 0) or 0, f.get("ic_20d", 0) or 0))

ranked.sort(key=lambda x: -abs(x[1]))
print(f"活跃因子: {len(ranked)}个\n")

# 3. 分组
keep = [r for r in ranked if abs(r[1]) >= 0.03 and r[2] >= 60]
border = [r for r in ranked if abs(r[1]) >= 0.02 and abs(r[1]) < 0.03]
drop = [r for r in ranked if abs(r[1]) < 0.02 or r[2] < 60]

print(f"保留 (IC≥0.03+验证≥60天): {len(keep)}个")
print(f"边界 (IC 0.02-0.03):           {len(border)}个")
print(f"丢弃 (IC<0.02或未验证):         {len(drop)}个\n")

print("--- 保留 ---")
for name, ic5, v, ic10, ic20 in keep:
    print(f"  {name:30s} ic5={ic5:+.4f} ic20={ic20:+.4f} ({v}d)")

if border:
    print("\n--- 边界 (可保留) ---")
    for name, ic5, v, ic10, ic20 in border[:10]:
        print(f"  {name:30s} ic5={ic5:+.4f} ic20={ic10:+.4f} ({v}d)")

if drop:
    print(f"\n--- 丢弃 ({len(drop)}个) ---")
    for name, ic5, v, ic10, ic20 in drop[:10]:
        print(f"  {name:30s} ic5={ic5:+.4f} ic20={ic10:+.4f} ({v}d)")
    if len(drop) > 10:
        print(f"  ... 共{len(drop)}个")

# 4. 建议
print(f"\n=== 建议 ===")
print(f"  保留: {len(keep)}个 (IC≥0.03)")
_border_keep = [r for r in border if abs(r[1]) >= 0.025]
print(f"  +边界: {len(_border_keep)}个 (IC≥0.025) → 共{len(keep)+len(_border_keep)}个")
print(f"  目标: ≤30因子 ✅" if len(keep) + len(_border_keep) <= 30 else f"  仍需精简: {len(keep)+len(_border_keep)}个 > 30目标")

# 5. 写精简名单
keep_names = [r[0] for r in keep] + [r[0] for r in border if abs(r[1]) >= 0.025]
with open(r"D:\quant_framework\factors_keep.json", "w", encoding="utf-8") as f:
    json.dump(keep_names, f, ensure_ascii=False, indent=2)
print(f"\n精简名单 → D:\\quant_framework\\factors_keep.json ({len(keep_names)}因子)")
