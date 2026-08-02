"""3模块拆分: common + init + body (纯字节, 不依赖行号)"""
import os, sys

BASE = r"D:\quant_framework\qmt_strategies"

# 1. 从备份恢复 main_body
data = open(f"{BASE}\\qmt_full_strategy_BAK_20260720.py", "rb").read()
lines = data.split(b"\n")
body = b"\n".join(lines[185:])
open(f"{BASE}\\main_body.py", "wb").write(body)

# 2. 找 def handlebar 和 def _bd_to_df 的字节位置
hb_pos = body.find(b"\ndef handlebar(")
if hb_pos < 0: hb_pos = body.find(b"\r\ndef handlebar(")
helper_pos = body.find(b"\ndef _bd_to_df(")
if helper_pos < 0: helper_pos = body.find(b"\r\ndef _bd_to_df(")

if hb_pos < 0 or helper_pos < 0:
    print(f"ERROR: handlebar={hb_pos} helper={helper_pos}")
    sys.exit(1)

# 3. 切三块
init_part = body[:hb_pos]                          # init only
signal_part = body[hb_pos+1:helper_pos]             # handlebar + signals
helper_part = body[helper_pos+1:]                   # helpers + callbacks

# 写模块
for name, content in [("init_body", init_part), ("signal_body", signal_part), ("helper_body", helper_part)]:
    path = f"{BASE}\\{name}.py"
    open(path, "wb").write(content)
    print(f"  {name}.py: {content.count(b'\n')} lines")

# 4. 构建
parts = []
for mod in ["common.py", "init_body.py", "signal_body.py", "helper_body.py"]:
    path = f"{BASE}\\{mod}"
    if not os.path.exists(path): continue
    content = open(path, "rb").read()
    if len(parts) > 0:
        content = content.replace(b"#encoding:gbk\r\n", b"").replace(b"#encoding:gbk\n", b"")
        content = content.replace(b"from common import *\r\n", b"").replace(b"from common import *\n", b"")
    parts.append(content)

result = b"\n".join(parts)
out = f"{BASE}\\qmt_full_strategy.py"
open(out, "wb").write(result)
lines = len(open(out, "rb").readlines())
print(f"\n  Built: {out} ({lines} lines, {os.path.getsize(out)/1024:.1f}KB)")
print("Done")
