"""从备份拆出 common.py 和 main_body.py (强制GBK转换)"""
# QMT实际用GBK, 但源文件可能混了UTF-8字节 → 用errors=replace
raw = open(r"D:\quant_framework\qmt_strategies\qmt_full_strategy_BAK_20260720.py", "rb").read()
# 尝UTF-8先, 失败则GBK replace
try: text = raw.decode("utf-8")
except: text = raw.decode("gbk", errors="replace")
lines = text.splitlines(True)

# common.py = 行1-185
common = "".join(lines[:185]).encode("gbk", errors="replace").decode("gbk")
open(r"D:\quant_framework\qmt_strategies\common.py", "w", encoding="gbk").write(common)
print(f"common.py: {len(lines[:185])} lines")

# main_body.py = 行186-末尾
body = "".join(lines[185:]).encode("gbk", errors="replace").decode("gbk")
open(r"D:\quant_framework\qmt_strategies\main_body.py", "w", encoding="gbk").write(body)
print(f"main_body.py: {len(lines[185:])} lines")

print("Done")
