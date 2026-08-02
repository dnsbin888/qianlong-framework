"""从工作备份拆出模块 (纯二进制, 无编码转换)"""
data = open(r"D:\quant_framework\qmt_strategies\qmt_full_strategy_BAK_20260720.py", "rb").read()
lines = data.split(b"\n")

# common.py: 行0-184 (encoding+doc+imports+globals+utils, 到 _limit_pct 结束)
# main_body.py: 行185-末尾 (init + handlebar)
common = b"\n".join(lines[:185])
body = b"\n".join(lines[185:])

open(r"D:\quant_framework\qmt_strategies\common.py", "wb").write(common)
open(r"D:\quant_framework\qmt_strategies\main_body.py", "wb").write(body)
print(f"common.py: {len(lines[:185])} lines, {len(common)} bytes")
print(f"main_body.py: {len(lines[185:])} lines, {len(body)} bytes")
print("Done - pure bytes, no encoding damage")
