"""模拟QMT实时推送——验证全链路"""
import json, urllib.request, time

FLASK = "http://127.0.0.1:5002"

# 1. 发送前查看已有信号
print("=" * 60)
print("  模拟QMT实时信号推送")
print("=" * 60)

print("\n[1] 推送前 QMT 信号数")
r = urllib.request.urlopen(f"{FLASK}/api/qmt-signals", timeout=3)
before = json.loads(r.read())
print(f"  现有: {len(before) if isinstance(before,list) else '?'}条")

# 2. 模拟推送 3 个不同信号
test_signals = [
    {"symbol":"sh600519","signal_type":"盘中突破","price":1850.00,"lgbm":85,"xgb":72,"position_pct":3,"time":"09:45:30","source":"qmt"},
    {"symbol":"sz000858","signal_type":"竞价抢筹","price":168.50,"lgbm":78,"xgb":65,"position_pct":2,"time":"09:35:00","source":"qmt"},
    {"symbol":"sh601012","signal_type":"打板追封","price":42.30,"lgbm":92,"xgb":88,"position_pct":5,"time":"10:15:00","source":"qmt"},
]

for i, sig in enumerate(test_signals):
    data = json.dumps(sig).encode()
    req = urllib.request.Request(f"{FLASK}/api/qmt/signal",
        data=data,
        headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=3).read())
        print(f"  [{i+1}] {sig['symbol']} {sig['signal_type']} → {resp.get('code')} {resp.get('message',resp.get('error',''))}")
    except Exception as e:
        print(f"  [{i+1}] {sig['symbol']} ❌ {e}")

# 3. 确认信号已接收
print("\n[2] 推送后 QMT 信号")
time.sleep(0.5)
r2 = urllib.request.urlopen(f"{FLASK}/api/qmt-signals", timeout=3)
after = json.loads(r2.read())
if isinstance(after, list):
    print(f"  现有: {len(after)}条")
    for s in after[-3:]:
        print(f"    {s.get('symbol')} {s.get('signal_type')} time={s.get('time')} lgbm={s.get('lgbm')}")

# 4. 检查终端页面是否可见
print("\n[3] 验证")
print(f"  刷新 http://localhost:5002/terminal")
print(f"  ⚡ 盘中信号 应显示 {len(after) if isinstance(after,list) else 0} 条（含QMT推送的3条）")
print(f"  信号卡片显示: 📡 QMT信号 标签 + 批准按钮")

# 5. 清理测试信号（可选）
print("\n" + "=" * 60)
print("  ✅ 如果Flask正常运行, 上面3条QMT信号应出现在前端")
print("  ⚠️ 这些是测试信号, 不要批准实盘")
print("=" * 60)
