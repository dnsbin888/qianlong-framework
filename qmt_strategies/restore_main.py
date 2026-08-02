"""从备份恢复完整 main_body.py"""
data = open(r"D:\quant_framework\qmt_strategies\qmt_full_strategy_BAK_20260720.py", "rb").read()
lines = data.split(b"\n")
# main_body = 行185以后
body = b"\n".join(lines[185:])
open(r"D:\quant_framework\qmt_strategies\main_body.py", "wb").write(body)
print(f"main_body.py restored: {len(lines[185:])} lines")
