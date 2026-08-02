import shutil, os
src = r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py"
dst = r"D:\quant_framework\qmt_strategies\qmt_full_strategy_BAK_20260720.py"
shutil.copy2(src, dst)
size = os.path.getsize(dst)
print(f"Backup: {dst} ({size} bytes)")
