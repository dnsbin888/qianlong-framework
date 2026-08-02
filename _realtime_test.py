"""盘中实时信号全链路测试"""
import json, urllib.request, time

FLASK = "http://127.0.0.1:5002"

print("="*60)
print(f"  盘中实时信号测试 {time.strftime('%H:%M:%S')}")
print("="*60)

# 1. 检查 QMT 信号
print("\n[1] QMT 信号")
r = urllib.request.urlopen(f"{FLASK}/api/qmt-signals", timeout=3)
qmt = json.loads(r.read())
qmt_list = qmt if isinstance(qmt, list) else []
print(f"  QMT信号: {len(qmt_list)}条")
if qmt_list:
    for s in qmt_list[-5:]:
        print(f"    {s.get('time','?')} {s.get('symbol','?')} {s.get('signal_type','?')}")

# 2. 推送测试信号验证全链路
print("\n[2] 链路测试")
test = {"symbol":"sh600000","signal_type":"测试盘中信号","price":12.5,"lgbm":80,"xgb":70,"position_pct":3,"time":time.strftime("%H:%M:%S"),"source":"qmt"}
req = urllib.request.Request(f"{FLASK}/api/qmt/signal",
    data=json.dumps(test).encode(),
    headers={"Content-Type":"application/json"})
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=3).read())
    print(f"  推送: code={resp.get('code')} approved={resp.get('approved')}")
    # 验证接收
    r2 = urllib.request.urlopen(f"{FLASK}/api/qmt-signals", timeout=3)
    qmt2 = json.loads(r2.read())
    q2 = qmt2 if isinstance(qmt2, list) else []
    print(f"  接收: {len(q2)}条 (新增{len(q2)-len(qmt_list)}条)")

    # 3. 信号表 (ML信号)
    print(f"\n[3] ML信号表")
    r3 = urllib.request.urlopen(f"{FLASK}/api/signal-table", timeout=3)
    ml = json.loads(r3.read())
    print(f"  ML信号: {len(ml)}条")
    print(f"  策略类型: ML={sum(1 for s in ml if 'ML' in s.get('decision','') or 'LGBM' in s.get('decision','') or 'XGB' in s.get('decision',''))}, 反转={sum(1 for s in ml if '🔄' in s.get('decision',''))}, 打板={sum(1 for s in ml if '🎯' in s.get('decision',''))}")

    # 4. 前端验证
    print(f"\n[4] 前端验证")
    print(f"  刷新 http://localhost:5002/terminal")
    print(f"  ⚡ 盘中信号 应显示 QMT+ML 合并列表")
    print(f"  QMT信号显示 📡 标签, ML信号显示 🧠 标签")

except Exception as e:
    print(f"  ❌ {e}")

print(f"\n{'='*60}")
