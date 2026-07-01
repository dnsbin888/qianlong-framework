"""回测-实盘一致性监控 (P8-4: 对标vnpy)"""
import json, os, numpy as np
from datetime import datetime

BACKTEST_DIR = r"D:\quant_framework\user_customizations"
LIVE_LOG = r"D:\quant_framework\live_equity_log.json"
PAPER_LOG = r"D:\quant_framework\equity_log.json"

def check() -> dict:
    report = {"time": datetime.now().isoformat(), "status": "ok", "issues": []}
    bt_return = None
    sp = os.path.join(BACKTEST_DIR, "user_strategies.json")
    if os.path.exists(sp):
        data = json.load(open(sp, encoding="utf-8"))
        for s in data.get("strategies", []):
            bt = s.get("backtest")
            if bt and bt.get("avg_return_5d"):
                bt_return = bt["avg_return_5d"]
                break

    if os.path.exists(PAPER_LOG):
        eq = json.load(open(PAPER_LOG)).get("log", [])
        if len(eq) >= 10:
            vals = [e[1] for e in eq[-20:]]
            live_std = np.std(vals)
            live_mean = np.mean(np.diff(vals)) / max(np.mean(vals), 1)
            if bt_return and abs(bt_return - live_mean) > 0.02:
                report["issues"].append({
                    "type": "return_drift",
                    "backtest": bt_return, "live": round(float(live_mean), 4),
                    "severity": "warning"
                })

    if report["issues"]:
        report["status"] = "warning"
    return report

if __name__ == "__main__":
    r = check()
    print(json.dumps(r, ensure_ascii=False, indent=2))
