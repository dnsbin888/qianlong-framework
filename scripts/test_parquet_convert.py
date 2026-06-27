"""P0-2: Test parquet conversion — convert 284MB gzip to ~80MB parquet (zstd)."""
import sys, os, time
sys.path.insert(0, r"D:\quant_web")

try:
    import pyarrow
    print(f"[Test] pyarrow {pyarrow.__version__} available")
except ImportError:
    print("[Test] ERROR: pyarrow not available! pip install pyarrow")
    sys.exit(1)

from data_loader import load_stock_data_cache, convert_legacy_cache_to_parquet

pq_path = r"D:\quant_web\stock_data.parquet"
gz_path = r"D:\quant_web\stock_data.pkl.gz"
gz_backup = gz_path + ".legacy_backup"

# Delete old snappy parquet if exists
if os.path.exists(pq_path):
    size_old_pq = os.path.getsize(pq_path) / (1024 * 1024)
    print(f"[Test] Removing old snappy parquet ({size_old_pq:.0f}MB)...")
    os.remove(pq_path)

# Determine source: live gz or backup
src = gz_path if os.path.exists(gz_path) else (gz_backup if os.path.exists(gz_backup) else None)
if not src:
    print("[Test] ERROR: no source file found")
    sys.exit(1)

print(f"[Test] Source: {src} ({os.path.getsize(src)/1024/1024:.0f}MB)")
print(f"[Test] Converting with zstd compression...")
t0 = time.time()
ok = convert_legacy_cache_to_parquet(src, pq_path)
if not ok:
    print("[Test] Conversion FAILED")
    sys.exit(1)

size_old = os.path.getsize(src) / (1024 * 1024)
size_new = os.path.getsize(pq_path) / (1024 * 1024)
print(f"[Test] ✓ {size_old:.0f}MB → {size_new:.0f}MB parquet (zstd) = {size_new/size_old*100:.0f}%, {time.time()-t0:.1f}s")

# Verify round-trip
print(f"[Test] Verifying load...")
t0 = time.time()
sd = load_stock_data_cache(pq_path)
if sd:
    print(f"[Test] ✓ Load OK: {len(sd)} stocks in {time.time()-t0:.1f}s")
    sample = list(sd.keys())[:3]
    for s in sample:
        print(f"[Test]   {s}: {len(sd[s])} rows, cols={list(sd[s].columns)}")
else:
    print("[Test] ✗ Load FAILED")
