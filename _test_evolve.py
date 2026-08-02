"""auto-evolve 全链路测试"""
import json, urllib.request, sys

FLASK = "http://127.0.0.1:5002"
print("="*60)
print("  auto-evolve 全链路测试")
print("="*60)

# API 测试
apis = [
    ("GET /api/auto-evolve/status", "/api/auto-evolve/status"),
    ("GET /api/auto-evolve/summary", "/api/auto-evolve/summary"),
    ("GET /api/auto-evolve/history", "/api/auto-evolve/history"),
]

for name, path in apis:
    try:
        r = urllib.request.urlopen(f"{FLASK}{path}", timeout=5)
        code = r.status
        data = json.loads(r.read())
        if isinstance(data, dict):
            print(f"  ✅ {name}: {code} keys={list(data.keys())[:5]}")
        else:
            print(f"  ✅ {name}: {code} type={type(data).__name__}")
    except Exception as e:
        print(f"  ❌ {name}: {e}")

# 前端模板
import os
tpl = r"D:\quant_web\templates\auto_evolve.html"
print(f"\n  模板: {'✅ 存在' if os.path.exists(tpl) else '❌ 缺失'} ({os.path.getsize(tpl)}B)")

# 模块导入
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')
try:
    from auto_evolve import evo_engine, evo_scheduler, StrategyAuditor
    from auto_evolve import _MASTER_DFL
    print(f"  模块导入: ✅")
    print(f"  _MASTER_DFL: ✅ stop_loss={_MASTER_DFL.get('stop_loss')}")
except Exception as e:
    print(f"  模块导入: ❌ {e}")

print("="*60)
