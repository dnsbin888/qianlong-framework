"""每日 Evidence Health 报告 — 收盘后运行 (Phase 1 P0-3)
用法: python daily_health_report.py
输出: D:\quant_framework\data\evidence_health.json
"""
import sys, os, json
from datetime import datetime
sys.path.insert(0, r"D:\quant_framework")
from evidence_health import EvidenceHealthMonitor, ENABLE_HEALTH

if not ENABLE_HEALTH:
    print("[HealthReport] Feature Toggle OFF")
    sys.exit(0)

monitor = EvidenceHealthMonitor()

# ── Feed real IC data ──
# Source 1: full_market_ic_report.json (LGBM IC)
ic_path = r"D:\quant_framework\full_market_ic_report.json"
if os.path.exists(ic_path):
    with open(ic_path, "r", encoding="utf-8") as f:
        ic_data = json.load(f)
    factors = ic_data.get("factors", {}) or ic_data.get("ic_results", {})

    # Map factor IC to producer
    for name, fd in factors.items():
        ic_val = fd.get("IC_5d") or fd.get("ic_5d") or fd.get("IC", 0)
        if ic_val:
            # LGBM uses many factors → aggregate to TrendML
            if abs(float(ic_val)) > 0.01:
                monitor.feed_ic("trend_ml", float(ic_val))

# Source 2: factor_ic_results.csv (XGBoost IC)
csv_path = r"D:\quant_framework\factor_ic_results.csv"
if os.path.exists(csv_path):
    with open(csv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines[1:]:  # skip header
        parts = line.strip().split(",")
        if len(parts) >= 3:
            try:
                ic_val = float(parts[-1])
                if abs(ic_val) > 0.01:
                    monitor.feed_ic("momentum_ml", ic_val)
            except ValueError:
                pass

# ── Check health ──
reports = {}
for pid in ["trend_ml", "momentum_ml", "tdx_formula"]:
    r = monitor.check(pid)
    if r:
        reports[pid] = {
            "health_id": r.health_id,
            "drift_score": r.drift_score,
            "drift_level": r.drift_level,
            "health_status": r.health_status,
            "ic_trend": r.ic_trend,
            "checked_at": r.checked_at,
        }

# ── Output ──
out_path = r"D:\quant_framework\data\evidence_health.json"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "producers": reports,
    }, f, ensure_ascii=False, indent=2)

print(f"[HealthReport] {len(reports)} producers checked → data/evidence_health.json")
print(monitor.summary())
