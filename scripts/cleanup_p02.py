"""P0-2 cleanup: remove legacy backup files after successful parquet conversion."""
import os

# Files to clean up
cleanups = [
    r"D:\quant_web\stock_data.pkl.gz.legacy_backup",
    r"D:\quant_web\stock_data.pkl.gz.legacy_backup.legacy_backup",
    r"D:\quant_web\stock_data.pkl.gz.broken.20260622_012113",
    r"D:\quant_web\stock_data.pkl.gz.broken.20260622_014603",
]

total_saved = 0
for p in cleanups:
    if os.path.exists(p):
        size_mb = os.path.getsize(p) / (1024 * 1024)
        os.remove(p)
        total_saved += size_mb
        print(f"  ✓ 已删除: {os.path.basename(p)} ({size_mb:.0f}MB)")

# Verify current state
pq = r"D:\quant_web\stock_data.parquet"
if os.path.exists(pq):
    print(f"\n  当前缓存: stock_data.parquet = {os.path.getsize(pq)/1024/1024:.0f}MB")
print(f"  释放空间: {total_saved:.0f}MB")
