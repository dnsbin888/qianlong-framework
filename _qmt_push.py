"""QMT实时信号推送诊断"""
import json, urllib.request, sys

print("="*60)
print("  QMT实时信号推送诊断")
print("="*60)

# 1. QMT信号API
print("\n[1] /api/qmt-signals 数据")
try:
    r = urllib.request.urlopen("http://localhost:5002/api/qmt-signals", timeout=3)
    data = json.loads(r.read())
    if isinstance(data, list):
        print(f"  返回: {len(data)}条")
        for s in data[:3]:
            print(f"    {s.get('symbol','?')} type={s.get('signal_type','?')} time={s.get('time','?')}")
    else:
        print(f"  返回类型: {type(data).__name__}, keys={list(data.keys())[:5] if isinstance(data,dict) else '?'}")
except Exception as e:
    print(f"  ❌ {e}")

# 2. QMT策略是否在运行
print("\n[2] QMT posting API")
try:
    # 模拟QMT发一个信号到Flask
    import urllib.request as req
    test_data = json.dumps({
        "symbol": "sh600000",
        "signal_type": "盘中突破",
        "close": 10.5,
        "lgbm": 75,
        "xgb": 68,
        "position_pct": 3,
        "time": "09:35:00",
        "source": "qmt"
    }).encode()
    r = req.urlopen(req.Request("http://localhost:5002/api/qmt/signal",
                   data=test_data,
                   headers={"Content-Type": "application/json"}),
                   timeout=3)
    resp = json.loads(r.read())
    print(f"  模拟QMT推送: {resp.get('code')} - {resp.get('message','?')}")
except Exception as e:
    print(f"  ❌ POST失败: {e}")

# 3. QMT策略文件检查
print("\n[3] QMT策略文件")
qmt_file = r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py"
try:
    content = open(qmt_file, encoding='utf-8').read()
    has_flask_post = '127.0.0.1:5002' in content
    has_passorder = 'passorder' in content
    print(f"  Flask推送: {'✅' if has_flask_post else '❌ 没有5002地址'}")
    print(f"  passorder: {'✅' if has_passorder else '❌'}")
except Exception as e:
    print(f"  ❌ {e}")

# 4. 检查QMT信号的session存储
print("\n[4] QMT信号存储")
import os
qmt_sig_dir = r"D:\quant_web\data"
qmt_files = [f for f in os.listdir(qmt_sig_dir) if 'qmt_signal' in f.lower() or 'qmt' in f.lower()]
print(f"  QMT相关文件: {qmt_files if qmt_files else '无'}")

print("\n" + "="*60)
print("  结论:")
print("  如果 /api/qmt-signals 返回空 → QMT策略未运行或未配置push")
print("  如果 POST /api/qmt/signal 失败 → Flask路由问题")
print("  如果都正常 → 检查QMT终端是否加载了策略并启动")
print("="*60)
