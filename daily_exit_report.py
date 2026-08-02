"""每日退出归因报告 — 收盘后自动运行 (Phase 1 P0-2)
用法: python daily_exit_report.py
输出: D:\quant_framework\data\exit_attribution.json
"""
import sys, os, json
from datetime import datetime
sys.path.insert(0, r"D:\quant_framework")
from exit_attribution import ExitAttributionEngine, ENABLE_EXIT_ATTRIBUTION

if not ENABLE_EXIT_ATTRIBUTION:
    print("[ExitReport] Feature Toggle OFF — 跳过")
    sys.exit(0)

# Read trades
pa = r"D:\quant_framework\paper_account.json"
if not os.path.exists(pa):
    print("[ExitReport] paper_account.json not found")
    sys.exit(0)

with open(pa, "r", encoding="utf-8") as f:
    data = json.load(f)

trades = data.get("trade_log", [])
sells = [t for t in trades if t.get("side") == "sell"]

engine = ExitAttributionEngine()
attrs = engine.classify_batch(sells)
stats = engine.stats()

# Output
out = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "total_sells": len(sells),
    "classified": len(attrs),
    "distribution": stats["distribution"],
    "categories": stats["categories"],
    "details": [
        {
            "symbol": a.symbol,
            "reason_code": a.reason_code,
            "category": a.category,
            "pnl_pct": a.pnl_pct,
            "original": a.original_reason[:80],
        }
        for a in attrs
    ],
}

os.makedirs(os.path.dirname(r"D:\quant_framework\data\exit_attribution.json"), exist_ok=True)
with open(r"D:\quant_framework\data\exit_attribution.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"[ExitReport] {len(attrs)}/{len(sells)} exits classified → data/exit_attribution.json")
print(engine.summary())
