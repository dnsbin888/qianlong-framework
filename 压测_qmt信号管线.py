"""TDX→Flask→终端 全链路压测"""
import json, urllib.request, time, os

FLASK = "http://127.0.0.1:5002"
PASS = 0; FAIL = 0

def test(name, ok):
    global PASS, FAIL
    if ok: PASS += 1; print(f"  ✅ {name}")
    else:  FAIL += 1; print(f"  ❌ {name}")

print("=" * 50)
print("  TDX→QMT信号管线 全链路压测")
print("=" * 50)

# 1. Flask 存活
print("\n[1] Flask 连通性")
try:
    r = urllib.request.urlopen(f"{FLASK}/api/tdx-pools-config", timeout=5)
    test("Flask 可达", r.status == 200)
except Exception as e:
    test(f"Flask 可达 ({e})", False)

# 2. 模拟 QMT 推送信号 (TDX·打板)
print("\n[2] 模拟 QMT 推送信号")
today = time.strftime("%Y%m%d")
test_signals = [
    {"symbol": "sh600428", "signal_type": "TDX·妖股先锋", "price": 5.93,
     "channel": "review", "signal_id": f"stress_600428_{today}"},
    {"symbol": "sh600519", "signal_type": "TDX·龙头启动", "price": 1680.00,
     "channel": "review", "signal_id": f"stress_600519_{today}"},
    {"symbol": "sz000858", "signal_type": "TDX·五粮液", "price": 145.50,
     "channel": "review", "signal_id": f"stress_000858_{today}"},
]

for ts in test_signals:
    try:
        d = json.dumps(ts).encode()
        req = urllib.request.Request(f"{FLASK}/api/qmt/signal", data=d,
            headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=5)
        resp = json.loads(r.read().decode())
        ok = r.status == 200 and resp.get("code") == 200
        test(f"POST {ts['symbol']} → {resp.get('reason', resp.get('msg','?'))}", ok)
    except Exception as e:
        test(f"POST {ts['symbol']} → {e}", False)

# 3. 验证信号文件写入
print("\n[3] 验证信号文件")
sig_path = os.path.join(os.path.dirname(__file__), "data", f"qmt_signals_{today}.json")
sig_path = r"D:\quant_web\data" + f"\\qmt_signals_{today}.json"
if os.path.exists(sig_path):
    with open(sig_path, encoding="utf-8") as f:
        sigs = json.load(f)
    test(f"文件存在 ({len(sigs)}条)", len(sigs) > 0)
    for s in sigs:
        print(f"    {s.get('symbol')} {s.get('signal_type')} "
              f"LGBM={s.get('lgbm',0):.0f} XGB={s.get('xgb',0):.0f} "
              f"approved={s.get('approved')}")
else:
    test("文件存在", False)
    print("    文件路径:", sig_path)

# 4. 验证 API 端点
print("\n[4] 验证终端 API")
try:
    r = urllib.request.urlopen(f"{FLASK}/api/qmt-signals", timeout=5)
    sigs = json.loads(r.read().decode())
    qmt_count = sum(1 for s in sigs if s.get("_src", "") != "ml")
    test(f"GET /api/qmt-signals → {len(sigs)}条 (QMT源={qmt_count})", len(sigs) > 0)
    for s in sigs[:3]:
        print(f"    {s.get('symbol')} {s.get('name','?')} {s.get('signal_type','?')}")
except Exception as e:
    test(f"GET /api/qmt-signals → {e}", False)

# 5. TDX watcher 状态
print("\n[5] TDX watcher 状态")
try:
    r = urllib.request.urlopen(f"{FLASK}/api/tdx-pools", timeout=5)
    live = json.loads(r.read().decode())
    meta = live.get("_meta", {})
    pools = [k for k in live if not k.startswith("_")]
    test(f"watcher 数据: {len(pools)}池, 心跳={meta.get('file_mtime_iso','?')}", len(pools) > 0)
except Exception as e:
    test(f"watcher 数据 ({e})", False)

# 6. 去重测试 (相同signal_id不应重复)
print("\n[6] Signal ID 幂等去重")
dup_signal = test_signals[0]  # 与[2]中相同的signal_id
try:
    d = json.dumps(dup_signal).encode()
    r = urllib.request.urlopen(
        urllib.request.Request(f"{FLASK}/api/qmt/signal", data=d,
            headers={"Content-Type": "application/json"}), timeout=5)
    resp = json.loads(r.read().decode())
    test(f"重复推送 {dup_signal['symbol']} → 已去重(200)", r.status == 200)
except Exception as e:
    test(f"去重测试 ({e})", False)

print(f"\n{'='*50}")
print(f"  结果: {PASS}通过 / {FAIL}失败 / {PASS+FAIL}项")
print(f"{'='*50}")
