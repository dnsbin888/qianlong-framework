"""插入ASCII标记 → 按标记拆分 → build拼接 (无行号依赖)"""
import os, re

BASE = r"D:\quant_framework\qmt_strategies"
data = open(f"{BASE}\\main_body.py", "rb").read()

# 插入标记 (GBK-safe ASCII markers)
MARKER_FMT = b"\n# ==== MODULE: {} ====\n"
markers = [
    (b"\ndef init(", "init"),
    (b"\n    # \xa1\xf9", "wts"),       # ══ 弱转强竞价 in GBK
    (b"\n    # \xa1\xf9", "daban"),     # ══ 打板·二封 in GBK
    (b"\n    # \xa1\xf9", "tdx_hb"),    # ══ TDX原生池 in GBK
    (b"\n        # \xa1\xf9", "signals"), # ══ TDX公式命中
    (b"\ndef _bd_to_df(", "callbacks"),
]

# 只需在已知位置插入标记
# 从 grep 结果: init at line 2, wts at ~331, daban at ~395, tdx_hb at ~460, signals at ~560, callbacks at ~808
# 但行号可能不准。换策略: 搜 def/class 关键字定位
lines = data.split(b"\n")
line_funcs = []
for i, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith(b"def ") and not stripped.startswith(b"def _sim_order") and not stripped.startswith(b"def _post") and not stripped.startswith(b"def _tdx_fast") and not stripped.startswith(b"def _auction_watch"):
        func_name = stripped.split(b"(")[0].replace(b"def ", b"").decode("gbk", errors="replace")
        line_funcs.append((i+1, func_name))

print("Functions found:")
for ln, name in line_funcs:
    print(f"  line {ln}: def {name}")

# 用def位置作为模块边界
boundaries = {
    "init":      line_funcs[0][0],   # def init
    "wts":       line_funcs[1][0],   # def handlebar
    "daban":     line_funcs[2][0],   # def _handlebar_impl
    "tdx_hb":    line_funcs[3][0],   # def _bd_to_df
    "signals":   line_funcs[4][0],   # def _tdx_notice
    "callbacks": line_funcs[5][0],   # def _check_tdx
}

# 不太对——_handlebar_impl 包含所有信号。换思路: 只拆到函数级, 不拆 _handlebar_impl 内部。

# 简化: 只拆 def 级别的函数到独立文件
# init() → init_body.py
# handlebar() + _handlebar_impl() → signals_body.py
# 辅助函数 → helpers_body.py

print("\nSimple split: 3 modules")
print("  init_body: def init")
print("  signals_body: def handlebar + def _handlebar_impl")
print("  helpers_body: def _bd_to_df + def _tdx_notice + def _check_tdx + callbacks")

# 实际拆分
n = len(lines)
init_end = max(i for i, l in enumerate(lines) if b"_tdx_fast_loop" in l or (b"Thread" in l and b"_tdx_fast" in l)) + 5

signal_start = next(i for i, l in enumerate(lines) if b"def handlebar(ContextInfo)" in l)
helper_start = next(i for i, l in enumerate(lines) if b"def _bd_to_df(" in l)

init_body = b"\n".join(lines[:helper_start])
helpers_body = b"\n".join(lines[helper_start:])

open(f"{BASE}\\init_body.py", "wb").write(init_body)
open(f"{BASE}\\helpers_body.py", "wb").write(helpers_body)

print(f"\ninit_body.py: {helper_start} lines")
print(f"helpers_body.py: {n - helper_start} lines")

# 构建
deploy = [b"#encoding:gbk\n"]
for fname in ["common.py", "init_body.py", "helpers_body.py"]:
    content = open(f"{BASE}\\{fname}", "rb").read()
    content = content.replace(b"#encoding:gbk\r\n", b"").replace(b"#encoding:gbk\n", b"")
    content = content.replace(b"from common import *\r\n", b"").replace(b"from common import *\n", b"")
    deploy.append(content)

result = b"\n".join(deploy)
open(f"{BASE}\\qmt_full_strategy.py", "wb").write(result)
lines_out = len(open(f"{BASE}\\qmt_full_strategy.py", "rb").readlines())
print(f"\nqmt_full_strategy.py: {lines_out} lines ({os.path.getsize(f'{BASE}\\qmt_full_strategy.py')/1024:.1f}KB)")
print("Done")
