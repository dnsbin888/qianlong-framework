"""P0-2: Final verification and cleanup."""
import sys, os, time
sys.path.insert(0, r"D:\quant_web")

from data_loader import load_stock_data_cache

pq_path = r"D:\quant_web\stock_data.parquet"

if os.path.exists(pq_path):
    size_mb = os.path.getsize(pq_path) / (1024 * 1024)
    print(f"[Verify] stock_data.parquet: {size_mb:.1f} MB")

    t0 = time.time()
    sd = load_stock_data_cache(pq_path)
    elapsed = time.time() - t0
    if sd:
        print(f"[Verify] ✓ Load OK: {len(sd)} stocks in {elapsed:.1f}s")
        # Show some stats
        total_rows = sum(len(df) for df in sd.values())
        print(f"[Verify]   Total rows: {total_rows:,}, Avg rows/stock: {total_rows//max(1,len(sd))}")
    else:
        print("[Verify] ✗ Load FAILED")
else:
    print("[Verify] No parquet file found")

# Check what legacy files remain
for p in [r"D:\quant_web\stock_data.pkl.gz", r"D:\quant_web\stock_data.pkl.gz.legacy_backup"]:
    if os.path.exists(p):
        print(f"[Verify] Legacy: {os.path.basename(p)} = {os.path.getsize(p)/1024/1024:.0f}MB")
