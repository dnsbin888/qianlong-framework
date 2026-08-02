"""分层指标体系 v1.0 — 按策略类型选用不同评估标准
对标: 游资(封板率/盈亏比) × 私募(Sharpe/DSR/OOS衰减) 双层验证

核心原则: 趋势策略用 Sharpe/DSR，反转策略用胜率/盈亏比，打板策略用封板率/次日溢价。
不同策略类型天然有不同的统计特征，不能用同一把尺子量。
"""

# ── 门控阈值定义 ──
GATES = {
    "trend": {
        # 私募标准: 趋势策略看 Sharpe + DSR + OOS衰减
        "sharpe":        {"threshold": 0.5,  "label": "Sharpe比率"},
        "profit_factor": {"threshold": 1.3,  "label": "盈亏比"},
        "oos_decay_pct": {"threshold": 50.0, "label": "OOS衰减%", "max": True},
    },
    "reversal": {
        # 游资标准: 反转策略看胜率 + 盈亏比 + 连续亏损
        "win_rate_pct":         {"threshold": 45.0, "label": "胜率%"},
        "profit_factor":        {"threshold": 1.5,  "label": "盈亏比"},
        "max_consecutive_losses": {"threshold": 5,   "label": "最大连亏", "max": True},
    },
    "pattern": {
        # 游资标准: 打板策略看次日正收益比 + 盈亏比
        "next_day_positive_rate": {"threshold": 0.50, "label": "次日正收益比"},
        "profit_factor":          {"threshold": 1.5,  "label": "盈亏比"},
        "max_consecutive_losses":  {"threshold": 4,   "label": "最大连亏", "max": True},
    },
    "signal": {
        # 信号标准: 看方向准确率 (用 win_rate 代理)
        "win_rate_pct":  {"threshold": 50.0, "label": "方向准确率%"},
        "profit_factor": {"threshold": 1.2,  "label": "盈亏比"},
        "n_trades":      {"threshold": 20,   "label": "最少笔数"},
    },
}


def evaluate(report, extra_metrics, profile="trend"):
    """按策略类型判定回测结果是否通过门控

    Args:
        report: ruler_trade.measure() 返回的标准报告
        extra_metrics: _compute_extra_metrics() 返回的衍生指标
        profile: 'trend' | 'reversal' | 'pattern' | 'signal'

    Returns:
        {"profile": str, "gates": {metric: {actual, threshold, pass}}, "passed": bool}
    """
    gates_def = GATES.get(profile, GATES["trend"])

    # 合并所有可用指标
    all_metrics = {}
    all_metrics.update(report)
    all_metrics.update(extra_metrics)

    gate_results = {}
    for metric, cfg in gates_def.items():
        actual = all_metrics.get(metric, 0)
        threshold = cfg["threshold"]
        is_max = cfg.get("max", False)  # True=越小越好 (如回撤、连亏)

        if is_max:
            passed = actual <= threshold
        else:
            passed = actual >= threshold

        gate_results[metric] = {
            "actual": round(actual, 2),
            "threshold": threshold,
            "pass": passed,
            "label": cfg["label"],
        }

    all_pass = all(g["pass"] for g in gate_results.values())

    return {
        "profile": profile,
        "gates": gate_results,
        "passed": all_pass,
    }


def profile_for_strategy(strategy_type):
    """策略 type → metrics_profile 映射"""
    return {
        "trend":    "trend",
        "reversal": "reversal",
        "pattern":  "pattern",
        "signal":   "signal",
    }.get(strategy_type, "trend")


def gate_summary(verdict):
    """门控结果 → 一行文字摘要"""
    parts = []
    for metric, g in verdict["gates"].items():
        icon = "✅" if g["pass"] else "❌"
        parts.append(f"{icon}{g['label']}={g['actual']}(阈{g['threshold']})")
    status = "通过" if verdict["passed"] else "未通过"
    return f"[{status}] " + " ".join(parts)
