"""因子健康监控引擎 (蓝图 v3.0)
================================
行业标准范式 (2020-2026):
  健康评分 → 自动降权/退役 → 人工审批

Health Score = IC分(40%) + 分层分(30%) + 衰减分(20%) + 相关性分(10%)
自动操作: 健康<70降权50%, <40归零, 连续10天危险→退役
人工干预: 恢复/强制退役/延长观察 (覆盖自动决策)
"""

import json, os, logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"
IC_REPORT_PATH = r"D:\quant_framework\full_market_ic_report.json"
HEALTH_LOG_PATH = r"D:\quant_framework\factor_health_log.jsonl"
HEALTH_HISTORY_DIR = r"D:\quant_framework\ic_history"


# ═══════════════════════════════════════════════════════
#  健康评分引擎
# ═══════════════════════════════════════════════════════

def compute_health(factor: dict, ic_report: dict = None) -> dict:
    """计算单个因子健康度 (0-100)。

    输入: factor_registry.json 中的因子条目 + IC报告 (可选)
    输出: {health, ic_score, layer_score, decay_score, corr_score, status, actions}
    """
    name = factor["name"]
    status = factor.get("status", "active")
    if status == "retired":
        return {"health": 0, "status": "retired", "reason": factor.get("retired_reason", "")}

    # 1. IC分 (40%)
    ic5 = factor.get("ic_5d", 0) or 0
    ic_score = min(100, max(0, abs(ic5) * 1000))  # IC=0.05 → 50分, IC=0.10 → 100分
    if ic5 < 0:
        ic_score *= 0.5  # 负IC惩罚

    # 2. 分层回测分 (30%) — 从 IC 多窗口数据计算
    # 代理: IC 在长窗口保持或增强 → 分层效果好; 衰减 → 分层效果差
    layer_score = 50  # 默认
    if ic_report:
        # 兼容两种格式: factors dict (大写key) 和 ic_results dict (小写key)
        facs = ic_report.get("factors", {}) or ic_report.get("ic_results", {})
        if name in facs:
            fac_data = facs[name]
            _ic5 = abs(float(fac_data.get("IC_5d") or fac_data.get("ic_5d") or fac_data.get("ic_5d", {}).get("mean_ic") or 0))
            _ic20 = abs(float(fac_data.get("IC_20d") or fac_data.get("ic_20d") or fac_data.get("ic_20d", {}).get("mean_ic") or 0))
            # IC 稳定性 = 长窗口IC / 短窗口IC, 稳定或增长 → 高分
            if _ic5 > 0.01:
                stability = min(2.0, (_ic20 / _ic5) if _ic5 > 0 else 1.0)
                layer_score = min(100, int(40 + stability * 30))
            else:
                layer_score = 40

    # 3. 衰减分 (20%) — 检查 IC 历史趋势
    decay_score = _compute_decay(name)

    # 4. 相关性分 (10%) — 是否与其他因子高度相关
    corr_score = _compute_uniqueness(name)

    # 综合
    health = min(100, int(ic_score * 0.4 + layer_score * 0.3 + decay_score * 0.2 + corr_score * 0.1))

    # 状态判定
    if health >= 70:
        level, actions = "healthy", []
    elif health >= 40:
        level, actions = "watch", ["权重减半"]
    else:
        level, actions = "danger", ["权重归零", "停止实盘"]

    # 连续危险天数
    danger_days = _count_consecutive_danger(name, level)
    if level == "danger" and danger_days >= 10:
        level, actions = "retiring", ["自动退役", "通知老板"]

    return {
        "health": health, "status": level,
        "ic_score": ic_score, "layer_score": layer_score,
        "decay_score": decay_score, "corr_score": corr_score,
        "danger_days": danger_days,
        "actions": actions,
        "checked_at": datetime.now().isoformat(),
    }


def _compute_decay(name: str) -> int:
    """从历史快照检查 IC 衰减。稳定=100, 急降=0。"""
    snapshots = _load_history(name)
    if len(snapshots) < 2:
        return 70  # 无历史, 默认中等
    ics = [(s.get("IC_5d") or s.get("ic_5d") or 0) for s in snapshots[-5:]]
    if len(ics) < 2:
        return 70
    trend = ics[-1] - ics[0]
    return min(100, max(0, int(70 + trend * 2000)))  # +0.01=90分, -0.02=30分


def _compute_uniqueness(name: str) -> int:
    """检查是否与最强因子高度相关。"""
    # 简化: 从 Registry 读取相关性数据, 暂无则返回默认
    try:
        with open(REGISTRY_PATH, "r") as f:
            data = json.load(f)
        factors = data.get("factors", [])
        # 找 IC 最强的因子
        best = max(factors, key=lambda f: abs(f.get("ic_5d", 0) or 0))
        if best["name"] == name:
            return 90  # 自己就是最强, 不加惩罚
        # TODO: 真实相关性矩阵 — 当前简化, 返回中等
        return 70
    except Exception:
        return 70


# ═══════════════════════════════════════════════════════
#  历史快照
# ═══════════════════════════════════════════════════════

def save_snapshot():
    """保存当前 IC 报告为历史快照。"""
    os.makedirs(HEALTH_HISTORY_DIR, exist_ok=True)
    try:
        with open(IC_REPORT_PATH, "r") as f:
            report = json.load(f)
    except Exception:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(HEALTH_HISTORY_DIR, f"ic_{ts}.json")
    with open(path, "w") as f:
        json.dump(report, f, ensure_ascii=False)


def _load_history(name: str) -> list[dict]:
    """加载某因子的 IC 历史。"""
    snapshots = []
    if not os.path.exists(HEALTH_HISTORY_DIR):
        return snapshots
    for fn in sorted(os.listdir(HEALTH_HISTORY_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(HEALTH_HISTORY_DIR, fn)) as f:
                data = json.load(f)
            factors = data.get("factors", {})
            if name in factors:
                snapshots.append(factors[name])
        except Exception:
            pass
    return snapshots


def _count_consecutive_danger(name: str, current_level: str) -> int:
    """统计连续危险天数。"""
    try:
        path = HEALTH_LOG_PATH
        if not os.path.exists(path):
            return 1 if current_level == "danger" else 0
        count = 0
        for line in reversed(open(path).readlines()[-20:]):
            try:
                entry = json.loads(line)
                if entry.get("factor") == name and entry.get("status") in ("danger", "retiring"):
                    count += 1
                else:
                    break
            except Exception:
                break
        return count + (1 if current_level in ("danger", "retiring") else 0)
    except Exception:
        return 1 if current_level == "danger" else 0


# ═══════════════════════════════════════════════════════
#  自动决策 + 人工干预
# ═══════════════════════════════════════════════════════

def run_health_check() -> dict:
    """对所有 active 因子执行健康检查, 返回完整报告。"""
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
    except Exception:
        return {"error": "Registry不可用"}

    try:
        with open(IC_REPORT_PATH, "r") as f:
            ic_report = json.load(f)
    except Exception:
        ic_report = {}

    results = []
    auto_actions = []
    for factor in reg.get("factors", []):
        if factor.get("status") != "active":
            continue
        h = compute_health(factor, ic_report)
        results.append({"name": factor["name"], "display": factor.get("display", ""), **h})

        # P0-2: 同步 IC 到 registry (统一数据源)
        _sync_ic_to_registry(factor["name"], ic_report)

        # 恢复健康 → 清除乘数
        if h["status"] == "healthy":
            _clear_multiplier(factor["name"])

        # 自动操作
        for action in h.get("actions", []):
            auto_actions.append({"factor": factor["name"], "action": action,
                                 "health": h["health"], "time": datetime.now().isoformat()})
            _apply_action(factor["name"], action)

    # 写日志
    for a in auto_actions:
        _log_action(a)

    # 保存快照
    save_snapshot()

    # P0-1: 同步权重到 factor_weights.json (选股器读取)
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
    """执行自动操作。权重乘数写入 registry, factor_registry.get_ic_weights() 自动读取。"""
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
        for fac in reg["factors"]:
            if fac["name"] != name:
                continue
            if action == "权重减半":
                fac["weight_multiplier"] = 0.5
                fac["_health_action"] = f"权重减半 ({datetime.now().strftime('%m-%d %H:%M')})"
            elif action == "权重归零":
                fac["weight_multiplier"] = 0.0
                fac["_health_action"] = f"权重归零 ({datetime.now().strftime('%m-%d %H:%M')})"
            elif action == "自动退役":
                fac["status"] = "retired"
                fac["retired_reason"] = f"健康度不足, 自动退役 ({datetime.now().strftime('%Y-%m-%d')})"
                fac.pop("weight_multiplier", None)
            elif action == "停止实盘":
                fac["_trading_paused"] = True
        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(REGISTRY_PATH, "w") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"自动操作失败 {name}/{action}: {e}")


def _clear_multiplier(name: str):
    """健康恢复时清除权重乘数。"""
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
        for fac in reg["factors"]:
            if fac["name"] == name and "weight_multiplier" in fac:
                del fac["weight_multiplier"]
                fac.pop("_health_action", None)
                with open(REGISTRY_PATH, "w") as f2:
                    json.dump(reg, f2, ensure_ascii=False, indent=2)
                break
    except Exception:
        pass


def _sync_weights_to_json():
    """P0-1: 将带乘数的权重同步到 factor_weights.json (选股器消费)。"""
    try:
        from factor_registry import get_ic_weights
        weights = get_ic_weights("5d")
        wpath = r"D:\quant_web\data\factor_weights.json"
        import os as _os2
        _os2.makedirs(_os2.dirname(wpath), exist_ok=True)
        with open(wpath, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _sync_ic_to_registry(name: str, ic_report: dict):
    """P0-2: 将 IC 报告中的值同步到 registry, 确保单一事实源。"""
    try:
        ic_results = ic_report.get("ic_results", {})
        if name not in ic_results:
            return
        ics = ic_results[name]
        ic_map = {}
        for key in ["ic_1d", "ic_3d", "ic_5d", "ic_7d", "ic_10d", "ic_12d", "ic_15d", "ic_20d"]:
            entry = ics.get(key, {})
            val = entry.get("mean_ic") if isinstance(entry, dict) else entry
            if val is not None and isinstance(val, (int, float)):
                ic_map[key] = round(float(val), 4)
        if ic_map:
            from factor_registry import update_ic
            update_ic(name, ic_map)
    except Exception:
        pass


def _log_action(action: dict):
    """写健康日志。"""
    try:
        with open(HEALTH_LOG_PATH, "a") as f:
            f.write(json.dumps(action, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
#  人工干预 API
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
#  行业标配: 相关性矩阵 + IR + 衰减图
# ═══════════════════════════════════════════════════════

def compute_correlation_matrix() -> dict:
    """因子相关性矩阵 (Spearman)。
    从 factor_registry 读取活跃因子的 IC 时间序列，计算两两 Spearman 相关。
    >0.80 → 高度冗余, >0.93 → 严重冗余
    """
    try:
        from factor_registry import get_active_factors
        active = get_active_factors()
        if len(active) < 2:
            return {"error": "活跃因子不足", "matrix": [], "redundant_pairs": []}

        # 从历史快照收集各因子的 IC 序列
        snapshots = sorted(os.listdir(HEALTH_HISTORY_DIR))[-20:] if os.path.exists(HEALTH_HISTORY_DIR) else []
        if len(snapshots) < 5:
            return {"error": "历史快照不足(需>=5)", "matrix": [], "redundant_pairs": []}

        # 构建 {factor_name: [ic_values...]} — 兼容 dict 和 list 两种格式
        factor_ic_series = {}
        for sn in snapshots:
            try:
                with open(os.path.join(HEALTH_HISTORY_DIR, sn)) as f:
                    snap = json.load(f)
                facs = snap.get("factors", {})
                if isinstance(facs, dict):
                    for name, ic_data in facs.items():
                        ic = ic_data.get("IC_5d") or ic_data.get("ic_5d") or 0
                        if name not in factor_ic_series:
                            factor_ic_series[name] = []
                        factor_ic_series[name].append(ic)
                elif isinstance(facs, list):
                    for fac in facs:
                        name = fac.get("name", "")
                        ic = fac.get("ic_score") or fac.get("IC_5d") or fac.get("ic_5d") or 0
                        if name not in factor_ic_series:
                            factor_ic_series[name] = []
                        factor_ic_series[name].append(ic)
            except Exception:
                continue

        # 只保留活跃且有足够数据的因子
        valid = {}
        for f in active:
            name = f["name"]
            if name in factor_ic_series and len(factor_ic_series[name]) >= 5:
                valid[name] = factor_ic_series[name]

        if len(valid) < 2:
            return {"error": "有效IC序列不足", "matrix": [], "redundant_pairs": []}

        # 计算两两 Spearman 相关
        import numpy as np
        from scipy.stats import spearmanr

        names = sorted(valid.keys())
        n = len(names)
        matrix = []
        redundant_pairs = []

        for i in range(n):
            row = []
            for j in range(n):
                if i == j:
                    row.append(1.0)
                else:
                    try:
                        corr, _ = spearmanr(valid[names[i]], valid[names[j]])
                        corr = round(float(corr), 4)
                    except Exception:
                        corr = 0.0
                    row.append(corr)
                    # 冗余检测 (只记录上三角)
                    if i < j and abs(corr) > 0.80:
                        level = "严重冗余" if abs(corr) > 0.93 else "高度冗余"
                        redundant_pairs.append({
                            "factor_a": names[i], "factor_b": names[j],
                            "correlation": corr, "level": level,
                        })
            matrix.append({"factor": names[i], "correlations": row})

        redundant_pairs.sort(key=lambda x: -abs(x["correlation"]))

        return {
            "factors": names,
            "matrix": matrix,
            "redundant_pairs": redundant_pairs,
            "snapshots_used": len(snapshots),
            "note": "Spearman秩相关, >0.80高度冗余, >0.93严重冗余",
        }
    except Exception as e:
        logger.error(f"相关性矩阵计算失败: {e}")
        return {"error": str(e), "matrix": [], "redundant_pairs": []}


def compute_ir(name: str) -> dict:
    """信息比率 IR = IC_mean / IC_std × √breadth"""
    snapshots = _load_history(name)
    if len(snapshots) < 5:
        return {"ir": None, "note": "需至少5次快照"}
    ics = [(s.get("IC_5d") or s.get("ic_5d") or 0) for s in snapshots]
    import numpy as np
    mean_ic = np.mean(ics)
    std_ic = np.std(ics) if len(ics) > 1 else 1
    ir = mean_ic / max(std_ic, 0.001) * np.sqrt(len(ics))
    return {
        "ir": round(float(ir), 4),
        "mean_ic": round(float(mean_ic), 4),
        "std_ic": round(float(std_ic), 4),
        "snapshots": len(snapshots),
        "verdict": "优秀" if ir > 0.5 else ("合格" if ir > 0.3 else "不足"),
    }


def compute_ic_decay(name: str) -> dict:
    """IC 衰减分析: 从历史快照提取 IC 时间序列"""
    snapshots = _load_history(name)
    if len(snapshots) < 2:
        return {"trend": "insufficient", "values": []}
    ics = []
    for s in snapshots[-10:]:
        ics.append({
            "ic_5d": s.get("ic_5d", 0) or 0,
            "ic_20d": s.get("ic_20d", 0) or 0,
            "n": s.get("ic_5d_n", 0),
        })
    first = ics[0]["ic_5d"]
    last = ics[-1]["ic_5d"]
    change = last - first
    if change > 0.01: trend = "↗ 改善"
    elif change < -0.01: trend = "↘ 衰减"
    else: trend = "→ 稳定"
    return {"trend": trend, "change": round(change, 4), "values": ics}


# ═══════════════════════════════════════════════════════
#  G2: 策略自动熔断
# ═══════════════════════════════════════════════════════

def check_strategy_circuit_breaker() -> list[dict]:
    """对所有 sim_running/real 策略执行自动熔断检查。

    规则:
      - 连续亏损 ≥5次 → 自动退役
      - 连续亏损 ≥3次 → 自动暂停
      - Sharpe <0 持续7天 → 自动降级
      - 胜率 <30% 持续5天 → 自动暂停

    Returns:
        触发的熔断动作列表
    """
    try:
        from strategy_approval import get_all_approvals, pause_strategy, retire_strategy, update_performance
    except ImportError:
        return []

    strategies = get_all_approvals()
    actions = []

    for s in strategies:
        state = s.get("state", "draft")
        if state not in ("sim_running", "real"):
            continue

        perf = s.get("performance", {})
        history = s.get("history", [])
        name = s["name"]

        # 1. 连续亏损检查
        trades = perf.get("recent_trades", [])
        consec_losses = _count_consecutive_losses(trades)
        if consec_losses >= 5:
            r = retire_strategy(name, f"自动熔断: 连续亏损{consec_losses}次≥5")
            if r.get("success"):
                actions.append({"strategy": name, "action": "retired", "reason": f"连续亏损{consec_losses}次"})
                _notify_dingtalk(name, "retired", f"连续亏损{consec_losses}次≥5, 已自动退役")
            continue
        elif consec_losses >= 3:
            r = pause_strategy(name, f"自动熔断: 连续亏损{consec_losses}次≥3")
            if r.get("success"):
                actions.append({"strategy": name, "action": "paused", "reason": f"连续亏损{consec_losses}次"})
                _notify_dingtalk(name, "paused", f"连续亏损{consec_losses}次≥3, 已自动暂停")

        # 2. Sharpe检查 (持续7天<0)
        sharpe_history = perf.get("sharpe_history", [])
        if len(sharpe_history) >= 7 and all(sh < 0 for sh in sharpe_history[-7:]):
            r = pause_strategy(name, "自动熔断: Sharpe<0持续7天")
            if r.get("success"):
                actions.append({"strategy": name, "action": "paused", "reason": "Sharpe<0持续7天"})

        # 3. 胜率检查 (持续5天<30%)
        wr_history = perf.get("win_rate_history", [])
        if len(wr_history) >= 5 and all(wr < 0.30 for wr in wr_history[-5:]):
            r = pause_strategy(name, "自动熔断: 胜率<30%持续5天")
            if r.get("success"):
                actions.append({"strategy": name, "action": "paused", "reason": "胜率<30%持续5天"})

    return actions


def _count_consecutive_losses(trades: list) -> int:
    """从最近交易倒推连续亏损次数。"""
    count = 0
    for t in reversed(trades[-20:]):
        pnl = t.get("pnl", t.get("profit_loss", 0)) or 0
        if pnl < 0:
            count += 1
        else:
            break
    return count


# ═══════════════════════════════════════════════════════
#  G3: 钉钉实时推送
# ═══════════════════════════════════════════════════════

def _notify_dingtalk(strategy: str, action: str, reason: str):
    """推送策略事件到钉钉。"""
    try:
        from dingtalk_alerts import send_alert
        labels = {"paused": "⏸️ 暂停", "retired": "⚫ 退役", "approved": "✅ 通过",
                  "deployed": "🚀 部署", "rejected": "❌ 退回"}
        label = labels.get(action, action)
        send_alert(f"策略:{label}", f"{strategy}\n{reason}", "info")
    except ImportError:
        logger.info(f"[Notify] {strategy}: {action} — {reason}")


def notify_strategy_event(name: str, action: str, detail: str = ""):
    """主动推送策略事件通知。"""
    _notify_dingtalk(name, action, detail)


def human_override(name: str, command: str, reason: str = "") -> dict:
    """人工干预: restore / force_retire / extend_watch。

    Args:
        name: 因子名
        command: "restore" | "force_retire" | "extend_watch"
        reason: 人工备注

    Returns:
        {success, message}
    """
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
    except Exception:
        return {"success": False, "message": "Registry不可用"}

    for f in reg["factors"]:
        if f["name"] != name:
            continue

        if command == "restore":
            f["status"] = "active"
            f.pop("retired_reason", None)
            f.pop("_trading_paused", None)
        elif command == "force_retire":
            f["status"] = "retired"
            f["retired_reason"] = f"人工强制退役: {reason}"
        elif command == "extend_watch":
            f["_extended_watch"] = True
            f["_watch_reason"] = reason
        else:
            return {"success": False, "message": f"未知命令: {command}"}

        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(REGISTRY_PATH, "w") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)

        _log_action({"factor": name, "action": f"human:{command}", "reason": reason,
                     "time": datetime.now().isoformat()})
        return {"success": True, "message": f"{name}: {command} 已执行"}

    return {"success": False, "message": f"因子 {name} 不存在"}
