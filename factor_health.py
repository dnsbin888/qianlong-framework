"""因子健康监控引擎 v4.0 (Alphalens风格)
================================
行业标准: 统计显著性判定 — 替代手写40/30/20/10权重

规则:
  |IC|>0.05 + 方向一致 -> healthy
  |IC|>0.02 -> watch
  其他 -> danger
辅助维度(分层/衰减/相关)只影响权重乘数, 不影响生死判定
"""
import json, os, logging, numpy as np
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 自动退役开关: 因系统数据不稳定, 2026-07-12起暂停自动退役
AUTO_RETIRE_ENABLED = False

REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"
IC_REPORT_PATH = r"D:\quant_framework\full_market_ic_report.json"
HEALTH_LOG_PATH = r"D:\quant_framework\factor_health_log.jsonl"
HEALTH_HISTORY_DIR = r"D:\quant_framework\ic_history"


def compute_health(factor: dict, ic_report: dict = None) -> dict:
    """v4.0: IC绝对值决定生死, 辅助维度只调权重"""
    name = factor["name"]
    if factor.get("status") == "retired":
        return {"health": 0, "status": "retired", "reason": factor.get("retired_reason", "")}

    ic5 = factor.get("ic_5d", 0) or 0
    direction = factor.get("direction", "long")
    abs_ic = abs(ic5)
    eff_ic = -ic5 if direction == "short" else ic5  # short: neg=good

    # 主维度: IC绝对值定生死
    if abs_ic > 0.05 and eff_ic > 0:
        level, actions, h = "healthy", [], 85
    elif abs_ic > 0.02:
        level, actions, h = "watch", [], 55
    else:
        level, actions, h = "danger", [], 25

    # 辅助维度: 衰减检测只调权重
    if ic_report:
        facs = ic_report.get("factors", {}) or ic_report.get("ic_results", {})
        if name in facs:
            fd = facs[name]
            ic20 = abs(float(fd.get("IC_20d") or 0))
            if ic20 > 0 and abs_ic > 0.03 and ic20 < abs_ic * 0.5:
                actions.append("权重减半")

    danger_days = _count_consecutive_danger(name, level)
    if AUTO_RETIRE_ENABLED and level == "danger" and danger_days >= 10:
        level, actions = "retiring", ["自动退役", "通知老板"]

    return {
        "health": h, "status": level,
        "ic_score": min(100, int(abs_ic * 1000)),
        "layer_score": 50, "decay_score": 50, "corr_score": 50,
        "danger_days": danger_days, "actions": list(set(actions)),
        "checked_at": datetime.now().isoformat(),
    }


def _compute_decay(name: str) -> int:
    snapshots = _load_history(name)
    if len(snapshots) < 2: return 70
    ics = [(s.get("IC_5d") or s.get("ic_5d") or 0) for s in snapshots[-5:]]
    if len(ics) < 2: return 70
    trend = ics[-1] - ics[0]
    return min(100, max(0, int(70 + trend * 2000)))


def _compute_uniqueness(name: str) -> int:
    try:
        reg = json.load(open(REGISTRY_PATH, "r"))
        factors = reg.get("factors", [])
        best = max(factors, key=lambda f: abs(f.get("ic_5d", 0) or 0))
        return 90 if best["name"] == name else 70
    except Exception:
        return 70


# History
def save_snapshot():
    os.makedirs(HEALTH_HISTORY_DIR, exist_ok=True)
    try:
        report = json.load(open(IC_REPORT_PATH, "r"))
    except Exception: return
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    with open(os.path.join(HEALTH_HISTORY_DIR, f"ic_{ts}.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False)


def _load_history(name: str) -> list[dict]:
    snapshots = []
    if not os.path.exists(HEALTH_HISTORY_DIR): return snapshots
    for fn in sorted(os.listdir(HEALTH_HISTORY_DIR)):
        if not fn.endswith(".json"): continue
        try:
            data = json.load(open(os.path.join(HEALTH_HISTORY_DIR, fn)))
            factors = data.get("factors", {})
            if name in factors: snapshots.append(factors[name])
        except Exception: pass
    return snapshots


def _count_consecutive_danger(name: str, current_level: str) -> int:
    try:
        if not os.path.exists(HEALTH_LOG_PATH): return 1 if current_level == "danger" else 0
        count = 0
        for line in reversed(open(HEALTH_LOG_PATH).readlines()[-20:]):
            try:
                entry = json.loads(line)
                if entry.get("factor") == name and entry.get("status") in ("danger", "retiring"):
                    count += 1
                else: break
            except Exception: break
        return count + (1 if current_level in ("danger", "retiring") else 0)
    except Exception: return 1 if current_level == "danger" else 0


# Run all
def run_health_check() -> dict:
    try: reg = json.load(open(REGISTRY_PATH, "r"))
    except Exception: return {"error": "Registry unavailable"}
    try: ic_report = json.load(open(IC_REPORT_PATH, "r"))
    except Exception: ic_report = {}

    results, auto_actions = [], []
    for factor in reg.get("factors", []):
        if factor.get("status") != "active": continue
        h = compute_health(factor, ic_report)
        results.append({"name": factor["name"], "display": factor.get("display", ""), **h})
        _sync_ic_to_registry(factor["name"], ic_report)
        if h["status"] in ("healthy", "watch"): _clear_all_flags(factor["name"])
        for action in h.get("actions", []):
            auto_actions.append({"factor": factor["name"], "action": action, "health": h["health"], "time": datetime.now().isoformat()})
            _apply_action(factor["name"], action)

    for a in auto_actions: _log_action(a)
    save_snapshot()
    _sync_weights_to_json()
    return {
        "checked_at": datetime.now().isoformat(),
        "summary": {
            "healthy": sum(1 for r in results if r["status"] == "healthy"),
            "watch": sum(1 for r in results if r["status"] == "watch"),
            "danger": sum(1 for r in results if r["status"] == "danger"),
            "retiring": sum(1 for r in results if r["status"] == "retiring"),
        },
        "factors": sorted(results, key=lambda r: r["health"], reverse=True),
        "auto_actions": auto_actions,
    }


def _apply_action(name: str, action: str):
    try:
        reg = json.load(open(REGISTRY_PATH, "r"))
        for fac in reg["factors"]:
            if fac["name"] != name: continue
            ts = datetime.now().strftime('%m-%d %H:%M')
            if action == "权重减半": fac["weight_multiplier"] = 0.5; fac["_health_action"] = f"权重减半 ({ts})"
            elif action == "权重归零": fac["weight_multiplier"] = 0.0; fac["_health_action"] = f"权重归零 ({ts})"
            elif action == "自动退役" and AUTO_RETIRE_ENABLED: fac["status"] = "retired"; fac["retired_reason"] = f"Auto retire ({datetime.now():%Y-%m-%d})"; fac.pop("weight_multiplier", None)
            elif action == "停止实盘": fac["_trading_paused"] = True
        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(reg, open(REGISTRY_PATH, "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Auto action failed {name}/{action}: {e}")


def _clear_all_flags(name: str):
    """v4.0: 清除所有惩罚标记 (healthy/watch都清)"""
    try:
        reg = json.load(open(REGISTRY_PATH, "r"))
        for fac in reg["factors"]:
            if fac["name"] == name:
                fac.pop("weight_multiplier", None)
                fac.pop("_health_action", None)
                fac.pop("_trading_paused", None)
                json.dump(reg, open(REGISTRY_PATH, "w"), ensure_ascii=False, indent=2)
                break
    except Exception: pass


def _clear_multiplier(name: str):
    try:
        reg = json.load(open(REGISTRY_PATH, "r"))
        for fac in reg["factors"]:
            if fac["name"] == name:
                fac.pop("weight_multiplier", None)
                fac.pop("_health_action", None)
                json.dump(reg, open(REGISTRY_PATH, "w"), ensure_ascii=False, indent=2)
                break
    except Exception: pass


def _sync_weights_to_json():
    try:
        from factor_registry import get_ic_weights
        wpath = r"D:\quant_web\data\factor_weights.json"
        os.makedirs(os.path.dirname(wpath), exist_ok=True)
        json.dump(get_ic_weights("5d"), open(wpath, "w"), ensure_ascii=False, indent=2)
    except Exception: pass


def _sync_ic_to_registry(name: str, ic_report: dict):
    try:
        ic_results = ic_report.get("ic_results", {})
        if name not in ic_results: return
        ic_map = {}
        for key in ["ic_1d","ic_3d","ic_5d","ic_7d","ic_10d","ic_12d","ic_15d","ic_20d"]:
            entry = ic_results[name].get(key, {})
            val = entry.get("mean_ic") if isinstance(entry, dict) else entry
            if val is not None and isinstance(val, (int, float)): ic_map[key] = round(float(val), 4)
        if ic_map:
            from factor_registry import update_ic
            update_ic(name, ic_map)
    except Exception: pass


def _log_action(action: dict):
    try:
        with open(HEALTH_LOG_PATH, "a") as f:
            f.write(json.dumps(action, ensure_ascii=False) + "\n")
    except Exception: pass


# Correlation matrix
def compute_correlation_matrix() -> dict:
    try:
        from factor_registry import get_active_factors
        from scipy.stats import spearmanr
        active = get_active_factors()
        if len(active) < 2: return {"error": "Not enough factors"}
        snapshots = sorted(os.listdir(HEALTH_HISTORY_DIR))[-20:] if os.path.exists(HEALTH_HISTORY_DIR) else []
        if len(snapshots) < 5: return {"error": "Need >=5 snapshots"}
        factor_ic_series = {}
        for sn in snapshots:
            try:
                snap = json.load(open(os.path.join(HEALTH_HISTORY_DIR, sn)))
                for name, ic_data in snap.get("factors", {}).items():
                    ic = ic_data.get("IC_5d") or ic_data.get("ic_5d") or 0
                    if name not in factor_ic_series: factor_ic_series[name] = []
                    factor_ic_series[name].append(ic)
            except Exception: continue
        valid = {n: factor_ic_series[n] for n in [f["name"] for f in active] if n in factor_ic_series and len(factor_ic_series[n]) >= 5}
        if len(valid) < 2: return {"error": "Insufficient IC series"}
        names = sorted(valid.keys())
        matrix, redundant = [], []
        for i in range(len(names)):
            row = []
            for j in range(len(names)):
                corr = 1.0 if i == j else round(float(spearmanr(valid[names[i]], valid[names[j]])[0]), 4)
                row.append(corr)
                if i < j and abs(corr) > 0.80:
                    redundant.append({"factor_a": names[i], "factor_b": names[j], "correlation": corr, "level": "high" if abs(corr) > 0.93 else "medium"})
            matrix.append({"factor": names[i], "correlations": row})
        return {"factors": names, "matrix": matrix, "redundant_pairs": redundant}
    except Exception as e:
        return {"error": str(e)}


def compute_ir(name: str) -> dict:
    snapshots = _load_history(name)
    if len(snapshots) < 5: return {"ir": None, "note": "Need >=5 snapshots"}
    ics = [(s.get("IC_5d") or s.get("ic_5d") or 0) for s in snapshots]
    mean_ic, std_ic = np.mean(ics), np.std(ics) if len(ics) > 1 else 1
    ir = mean_ic / max(std_ic, 0.001) * np.sqrt(len(ics))
    return {"ir": round(float(ir), 4), "mean_ic": round(float(mean_ic), 4), "verdict": "excellent" if ir > 0.5 else ("ok" if ir > 0.3 else "weak")}


def compute_ic_decay(name: str) -> dict:
    snapshots = _load_history(name)
    if len(snapshots) < 2: return {"trend": "insufficient"}
    ics = [{"ic_5d": s.get("ic_5d", 0) or 0, "ic_20d": s.get("ic_20d", 0) or 0} for s in snapshots[-10:]]
    chg = ics[-1]["ic_5d"] - ics[0]["ic_5d"]
    return {"trend": "up" if chg > 0.01 else ("down" if chg < -0.01 else "flat"), "change": round(chg, 4), "values": ics}


# Strategy circuit breaker
def check_strategy_circuit_breaker() -> list[dict]:
    try:
        from strategy_approval import get_all_approvals, pause_strategy, retire_strategy
    except ImportError: return []
    strategies = get_all_approvals()
    actions = []
    for s in strategies:
        if s.get("state") not in ("sim_running", "real"): continue
        trades = s.get("performance", {}).get("recent_trades", [])
        losses = sum(1 for t in reversed(trades[-20:]) if (t.get("pnl", 0) or 0) < 0)
        if not trades: continue
        losses = 0
        for t in reversed(trades[-20:]):
            if (t.get("pnl", 0) or 0) < 0: losses += 1
            else: break
        if losses >= 5:
            r = retire_strategy(s["name"], f"Auto: {losses} consecutive losses")
            if r.get("success"): actions.append({"strategy": s["name"], "action": "retired"})
        elif losses >= 3:
            r = pause_strategy(s["name"], f"Auto: {losses} consecutive losses")
            if r.get("success"): actions.append({"strategy": s["name"], "action": "paused"})
    return actions


# DingTalk
def _notify_dingtalk(strategy: str, action: str, reason: str):
    try:
        from dingtalk_alerts import send_alert
        send_alert(f"Strategy:{action}", f"{strategy}\n{reason}", "info")
    except ImportError: pass


def notify_strategy_event(name: str, action: str, detail: str = ""):
    _notify_dingtalk(name, action, detail)


# Human override
def human_override(name: str, command: str, reason: str = "") -> dict:
    try: reg = json.load(open(REGISTRY_PATH, "r"))
    except Exception: return {"success": False, "message": "Registry unavailable"}
    for f in reg["factors"]:
        if f["name"] != name: continue
        if command == "restore": f["status"] = "active"; f.pop("retired_reason", None); f.pop("_trading_paused", None)
        elif command == "force_retire": f["status"] = "retired"; f["retired_reason"] = f"Manual: {reason}"
        elif command == "extend_watch": f["_extended_watch"] = True; f["_watch_reason"] = reason
        else: return {"success": False, "message": f"Unknown: {command}"}
        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(reg, open(REGISTRY_PATH, "w"), ensure_ascii=False, indent=2)
        _log_action({"factor": name, "action": f"human:{command}", "reason": reason, "time": datetime.now().isoformat()})
        return {"success": True, "message": f"{name}: {command} done"}
    return {"success": False, "message": f"Factor {name} not found"}
