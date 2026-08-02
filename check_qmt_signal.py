"""检查 QMT 策略是否推送了信号到 Flask"""
import json, os

print("=" * 50)
print("  QMT 信号推送检查")
print("=" * 50)

# 1. 检查最近的信号记录
signal_file = r"D:\quant_web\data\qmt_signal_log.json"
if os.path.exists(signal_file):
    mtime = os.path.getmtime(signal_file)
    with open(signal_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\n信号日志: {len(data) if isinstance(data, list) else '?'}条")
    print(f"最后更新: {__import__('time').strftime('%H:%M:%S', __import__('time').localtime(mtime))}")
    if isinstance(data, list) and data:
        for s in data[-3:]:
            print(f"  {s}")
else:
    print("\n信号日志文件不存在 — QMT 尚未推送任何信号")

# 2. 检查 auto_trade_plan 有没有被更新
plan_path = r"D:\quant_web\data\auto_trade_plan.json"
if os.path.exists(plan_path):
    mtime = os.path.getmtime(plan_path)
    print(f"\nauto_trade_plan 最后修改: {__import__('time').strftime('%H:%M:%S', __import__('time').localtime(mtime))}")

# 3. 直接探测 Flask 是否收到了 POST
print("\n检查 Flask 是否收到过 QMT POST...")
log = r"D:\quant_web\app_run.log"
if os.path.exists(log):
    with open(log, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    qmt_lines = [l for l in lines if "qmt" in l.lower() or "QMT" in l]
    recent = [l for l in lines[-100:] if "127.0.0.1" in l and "POST" in l]
    if qmt_lines:
        print(f"找到 {len(qmt_lines)} 条 QMT 相关日志 (共 {len(lines)} 行)")
        for l in qmt_lines[-5:]:
            print(f"  {l.strip()[:150]}")
    else:
        print(f"共 {len(lines)} 行日志, 0 条 QMT 相关")
        print("→ QMT 策略尚未推送任何审核信号")
else:
    print("日志文件不存在")
