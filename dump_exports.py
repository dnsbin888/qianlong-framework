"""dump DLL exports for 联动精灵"""
import ctypes, os

dlls = {
    "thscmd": r"D:\联动精灵\dlls\thscmd.dll",
    "thscode": r"D:\联动精灵\dlls\thscode.dll",
    "jzcode": r"D:\联动精灵\dlls\jzcode.dll",
    "hqapi": r"D:\联动精灵\hqapi.dll",
}

for name, path in dlls.items():
    print(f"\n=== {name} ===")
    if not os.path.exists(path):
        print(f"  NOT FOUND: {path}")
        continue
    try:
        # Use dumpbin-like approach
        import subprocess
        r = subprocess.run(["dumpbin", "/EXPORTS", path], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.split("\n"):
                if "ordinal" not in line.lower() and "exports" not in line.lower():
                    if any(c.isalpha() for c in line) and len(line.strip()) > 5:
                        print(f"  {line.strip()}")
        else:
            # Try Python ctypes
            dll = ctypes.WinDLL(path)
            print(f"  Loaded OK (no symbol info via ctypes)")
    except FileNotFoundError:
        # No dumpbin, try ctypes
        try:
            dll = ctypes.WinDLL(path)
            print(f"  Loaded OK: {path}")
        except Exception as e:
            print(f"  Load failed: {e}")
    except Exception as e:
        print(f"  Error: {e}")
