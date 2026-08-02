import json, urllib.request
BASE = "http://127.0.0.1:5002"

checks = [
    ("/api/signal-table", "ML信号表"),
    ("/api/paper-trade/v2", "模拟盘"),
    ("/api/tdx-pools", "TDX信号池"),
]

for url, name in checks:
    try:
        d = json.load(urllib.request.urlopen(f"{BASE}{url}", timeout=5))
        if isinstance(d, list):
            print(f"  {name}: {len(d)}条")
        elif isinstance(d, dict):
            keys = list(d.keys())[:3]
            print(f"  {name}: keys={keys}")
        else:
            print(f"  {name}: {type(d).__name__}")
    except Exception as e:
        print(f"  {name}: FAIL - {e}")

print("\nTerminal page:")
try:
    r = urllib.request.urlopen(f"{BASE}/terminal", timeout=5)
    print(f"  /terminal: HTTP {r.status} ({len(r.read())} bytes)")
except Exception as e:
    print(f"  /terminal: FAIL - {e}")
