"""按固定行号拆分 — 全覆盖版"""
data = open(r"D:\quant_framework\qmt_strategies\main_body.py", "rb").read()
lines = data.split(b"\n")
total = len(lines)

# 全覆盖分段
sections = {
    "init":      (1, 330),      # init + auction + TDX fast + handlebar wrapper
    "wts":       (331, 394),    # weak-to-strong
    "daban":     (395, 459),    # daban reseal
    "tdx_hb":    (460, 559),    # TDX handlebar + signal detection start
    "signals":   (560, 845),    # signal detection body + execution
    "callbacks": (846, total),  # helpers + callbacks
}

BASE = r"D:\quant_framework\qmt_strategies"
for name, (start, end) in sorted(sections.items()):
    content = b"\n".join(lines[start-1:end])
    path = f"{BASE}\\{name}_body.py"
    open(path, "wb").write(content)
    print(f"  {name}_body.py: lines {start}-{end} ({end-start+1} lines)")

covered = sum(e - s + 1 for s, e in sections.values())
print(f"\n  覆盖: {covered}/{total} lines")
print(f"  {'OK' if covered >= total else 'MISSING: ' + str(total - covered)}")

# dispatcher
main = [b"from common import *\n\n"]
for name in sections:
    main.append(f"# >>> {name}_body.py\n".encode())

open(f"{BASE}\\main_body.py", "wb").write(b"\n".join(main))
print(f"  main_body.py: {len(main)} lines (source reference only)")
print("\nDone")
