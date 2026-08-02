import json, urllib.request
# 检查 ML 信号 API
r = urllib.request.urlopen('http://127.0.0.1:5002/api/signal-table', timeout=5)
data = json.loads(r.read())
print(f'信号总数: {len(data)}')
if data:
    s = data[0]
    print(f'首条: {s["symbol"]} decision={s.get("decision")} score={s.get("combined_score")}')
    print(f'字段: {[k for k in s.keys() if "current" in k or "strategy" in k]}')
    # 检查有多少条ML信号
    ml_count = sum(1 for d in data if not any(e in d.get('decision','') for e in ['🔄','🎯']))
    print(f'ML信号: {ml_count}条')
else:
    print('❌ 无数据')
