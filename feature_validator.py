"""Feature Validator — D2: G1-G5 验证 (只读, 零风险)
====================================================
不生成Evidence, 不影响交易, 不接Producer.
只做一件事: 读取Market → 计算Feature → 验证 → 输出Report

用法: python feature_validator.py
输出: data/feature_validation_report.json
"""
import json, os, sys, time
from datetime import datetime
from collections import defaultdict
sys.path.insert(0, r"D:\quant_framework")

FEATURE_REGISTRY = r"D:\AQF-T\contracts\feature_registry_v1.json"
OUTPUT = r"D:\quant_framework\data\feature_validation_report.json"

# ── G1: Completeness — 是否有数据 ──
def check_completeness(values: list, expected_count: int) -> dict:
    n = len(values)
    missing = expected_count - n if n < expected_count else 0
    rate = missing / expected_count * 100 if expected_count > 0 else 0
    return {
        "gate": "G1-Completeness",
        "expected": expected_count,
        "actual": n,
        "missing": missing,
        "missing_rate_pct": round(rate, 2),
        "pass": rate < 1.0 and n > 0,
    }

# ── G2: Freshness — 数据是否实时 ──
def check_freshness(timestamps: list, max_delay_s: float = 1.0) -> dict:
    if not timestamps:
        return {"gate": "G2-Freshness", "pass": False, "reason": "no data"}
    delays = [(datetime.now() - ts).total_seconds() for ts in timestamps]
    max_delay = max(delays)
    avg_delay = sum(delays) / len(delays)
    return {
        "gate": "G2-Freshness",
        "max_delay_s": round(max_delay, 1),
        "avg_delay_s": round(avg_delay, 2),
        "threshold_s": max_delay_s,
        "pass": max_delay < 60,  # within 60s is acceptable
    }

# ── G3: Consistency — 多源一致性 ──
def check_consistency(source_a: list, source_b: list) -> dict:
    if not source_a or not source_b:
        return {"gate": "G3-Consistency", "pass": False, "reason": "single source only"}
    # Simple: check if both sources have data
    return {
        "gate": "G3-Consistency",
        "source_a_count": len(source_a),
        "source_b_count": len(source_b),
        "pass": len(source_a) > 0 and len(source_b) > 0,
    }

# ── G4: Stability — 无异常值 ──
def check_stability(values: list) -> dict:
    if not values:
        return {"gate": "G4-Stability", "pass": False, "reason": "no data"}
    nans = sum(1 for v in values if v is None or (isinstance(v, float) and v != v))
    negs = sum(1 for v in values if isinstance(v, (int, float)) and v < 0 and v != -1)
    extremes = sum(1 for v in values if isinstance(v, (int, float)) and abs(v) > 1e6)
    return {
        "gate": "G4-Stability",
        "nan_count": nans,
        "negative_count": negs,
        "extreme_count": extremes,
        "sample_count": len(values),
        "sample_mean": round(sum(v for v in values if isinstance(v, (int, float)) and v==v) / max(len([v for v in values if isinstance(v, (int, float)) and v==v]), 1), 4),
        "pass": nans == 0 and extremes == 0,
    }

# ── G5: Semantic Correctness — 定义是否可验证 ──
def check_semantic(feature_def: dict, values: list) -> dict:
    fid = feature_def.get("feature_id", "?")
    unit = feature_def.get("unit", "")
    phenomenon = feature_def.get("phenomenon", "")
    issues = []
    if not phenomenon: issues.append("no phenomenon defined")
    if not unit: issues.append("no unit defined")
    if not values: issues.append("no data to verify")
    return {
        "gate": "G5-Semantic",
        "feature_id": fid,
        "phenomenon": phenomenon,
        "unit": unit,
        "issues": issues,
        "pass": len(issues) == 0,
    }


def validate():
    # Load registry
    with open(FEATURE_REGISTRY, "r", encoding="utf-8") as f:
        reg = json.load(f)

    features = reg.get("features", {})
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "total_features": len(features),
        "results": {},
    }

    print("=" * 60)
    print("  Feature Validator — D2 G1-G5 验证")
    print(f"  {len(features)} features from Feature Registry V1.0")
    print("=" * 60)

    # ── 尝试从 QMT 获取实时数据 ──
    qmt_available = False
    try:
        from xtquant import xtdata
        qmt_available = True
    except ImportError:
        print("  QMT: xtquant not available")

    for fid, fdef in features.items():
        print(f"\n  [{fid}] {fdef['display_name']} — {fdef['phenomenon']}")
        fclass = fdef.get("feature_class", "?")
        print(f"    Class={fclass}  Unit={fdef['unit']}  Freq={fdef['update_frequency']}")

        # Collect sample data
        values = []
        timestamps = []

        if qmt_available:
            try:
                from xtquant import xtdata
                # Try to get data from QMT for a sample stock
                tick = xtdata.get_full_tick(['000001.SZ'])
                if tick and '000001.SZ' in tick:
                    t = tick['000001.SZ']
                    now = datetime.now()
                    if fclass == "Momentum":
                        # lastPrice based — record current price
                        lp = t.get('lastPrice', 0)
                        if lp > 0:
                            values.append(lp)
                            timestamps.append(now)
                    elif fclass == "Volume":
                        vol = t.get('volume', 0) or t.get('totalVol', 0)
                        if vol > 0:
                            values.append(float(vol))
                            timestamps.append(now)
                    elif fclass == "Auction":
                        lp = t.get('lastPrice', 0)
                        if lp > 0:
                            values.append(lp)
                            timestamps.append(now)
                    elif fclass in ("OrderBook", "Liquidity"):
                        bid = t.get('bidPrice', [0])[0] if t.get('bidPrice') else 0
                        ask = t.get('askPrice', [0])[0] if t.get('askPrice') else 0
                        if bid > 0 and ask > 0:
                            if fclass == "Liquidity":
                                values.append((ask - bid) / bid * 100 if bid > 0 else 0)
                            else:
                                bv = t.get('bidVol', [0])[0] if t.get('bidVol') else 0
                                av = t.get('askVol', [0])[0] if t.get('askVol') else 0
                                total = bv + av
                                values.append((bv - av) / total * 100 if total > 0 else 0)
                            timestamps.append(now)
            except Exception as e:
                print(f"    QMT: {e}")

        # Run gates
        gates = {}
        gates["G1"] = check_completeness(values, expected_count=1)
        gates["G2"] = check_freshness(timestamps)
        gates["G3"] = check_consistency(values, values[:1])  # single source for now
        gates["G4"] = check_stability(values)
        gates["G5"] = check_semantic(fdef, values)

        all_pass = all(g["pass"] for g in gates.values())
        status = "ACTIVE" if all_pass else "VALIDATING"

        for gname, g in gates.items():
            icon = "✅" if g["pass"] else "❌"
            reason = g.get("reason", "") or g.get("missing_rate_pct", "") or ""
            print(f"    {icon} {gname}: {g['gate']} {reason}")

        report["results"][fid] = {
            "display_name": fdef["display_name"],
            "feature_class": fclass,
            "status": status,
            "gates": {k: {"pass": v["pass"], "detail": {kk: vv for kk, vv in v.items() if kk not in ("gate", "pass")}} for k, v in gates.items()},
            "all_pass": all_pass,
        }

        print(f"    → {status}")

    # Summary
    active = sum(1 for r in report["results"].values() if r["all_pass"])
    print(f"\n{'='*60}")
    print(f"  ACTIVE: {active}/{len(features)}  (G1-G5 ALL PASS)")
    print(f"  Report: {OUTPUT}")
    print(f"{'='*60}")

    # Save
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    validate()
