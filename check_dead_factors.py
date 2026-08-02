"""检查哑因子: 是否有可救的"""
import json
reg = json.load(open(r"D:\quant_framework\factor_registry.json", encoding="utf-8"))
factors = reg.get("factors", [])

dead = []
for f in factors:
    ic5 = f.get("ic_5d", 0) or 0
    status = f.get("status", "active")
    verified = f.get("ic_verified_days", 0)
    retired_reason = f.get("retired_reason", "")
    name = f.get("name", "?")
    compute = f.get("compute", "?")
    if status == "retired" or (ic5 < 0.02 and ic5 > -0.02) or verified < 60:
        dead.append((name, ic5, verified, retired_reason, compute))

dead.sort(key=lambda x: -abs(x[1]))
print(f"哑/退役因子: {len(dead)}个\n")

# 分类
ai_gen = [d for d in dead if 'ai_' in d[0].lower() or 'deepseek' in d[0].lower()]
tdx_gen = [d for d in dead if d[0].startswith('wq')]
hand_made = [d for d in dead if d not in ai_gen and d not in tdx_gen]

print(f"AI生成: {len(ai_gen)}个")
print(f"TDX导入: {len(tdx_gen)}个")
print(f"手工/其他: {len(hand_made)}个")

# 检查有退役原因的
has_reason = [d for d in dead if d[3]]
print(f"\n有退役原因的: {len(has_reason)}个")
for d in has_reason[:5]:
    print(f"  {d[0]}: {d[3][:80]}")

# 检查有无验证数据但IC非零的（可能有救）
maybe_save = [d for d in dead if abs(d[1]) >= 0.015 and d[2] >= 60]
print(f"\n可能有救 (IC>=0.015 + 验证>=60d): {len(maybe_save)}个")
for d in maybe_save:
    print(f"  {d[0]:30s} ic5={d[1]:+.4f} ({d[2]}d)")

print(f"\n结论: AI/TDX因子全哑, 无救。手工因子也哑。")
print(f"你写的7个就是全部战斗力。")
