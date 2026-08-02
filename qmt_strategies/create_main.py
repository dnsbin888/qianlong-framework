lines = open(r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py", "r", encoding="utf-8").readlines()
body = lines[11:]  # skip encoding+doc+imports+from common
open(r"D:\quant_framework\qmt_strategies\main_body.py", "w", encoding="utf-8").writelines(body)
print(f"main_body.py: {len(body)} lines")
