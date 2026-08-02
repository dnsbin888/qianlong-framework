"""前后端连通性测试"""
import json, urllib.request, sys

def test(url, label):
    try:
        r = urllib.request.urlopen(url, timeout=5)
        data = json.load(r)
        print(f"  ✅ {label} (HTTP {r.status})")
        return data
    except Exception as e:
        print(f"  ❌ {label}: {e}")
        return None

BASE = "http://127.0.0.1:5002"
ok = 0
total = 5

# 1. 信号表API
d = test(f"{BASE}/api/signal-table", "信号表JSON")
if d and len(d) > 0:
    ok += 1
    auto = sum(1 for r in d if r.get("auto_enabled"))
    print(f"     → {len(d)} 条, {auto} 条自动交易")

# 2. 纸交易
d = test(f"{BASE}/api/paper-account", "纸交易账户")
if d and d.get("code") == 200:
    ok += 1
    print(f"     → 总资产: ¥{d.get('total_equity',0):,.0f}")

# 3. ML信号
d = test(f"{BASE}/api/ml-signals-json", "ML信号明细")
if d:
    ok += 1

# 4. 市场状态
d = test(f"{BASE}/api/market-regime", "市场状态")
if d:
    ok += 1
    print(f"     → regime={d.get('regime','?')}")

# 5. QMT配置
d = test(f"{BASE}/api/qmt-config", "QMT配置")
if d:
    ok += 1

print(f"\n{'✅ 前后端全通' if ok == total else '❌ 有断点'} ({ok}/{total})")

# 6. 页面渲染检查
try:
    r = urllib.request.urlopen(f"{BASE}/ml-signals", timeout=5)
    html = r.read().decode()
    has_table = 'signal-table' in html or 'ml_signals' in html
    print(f"  {'✅' if has_table else '⚠️'} 选股器页面 (HTTP {r.status}, {len(html)} bytes)")
except Exception as e:
    print(f"  ❌ 选股器页面: {e}")
