"""策略审批状态机 (蓝图 v4.0 G1)

行业对标: Quantopian多级审批 + 盈透Advisor审批
架构: 状态机。加节点不改已有逻辑。长期可靠。
"""

import json, os, logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

APPROVAL_FILE = r"D:\quant_framework\strategy_approvals.json"

# 状态定义
STATES = {
    "draft":         "📝 草稿",
    "review":        "⏳ 待玄策审核",
    "approved_sim":  "✅ 已通过(模拟盘)",
    "sim_running":   "🚀 模拟盘运行中",
    "sim_failed":    "❌ 模拟盘不达标",
    "review_real":   "⏳ 待老板审批(实盘)",
    "real":          "🏦 实盘运行中",
    "paused":        "⏸️ 已暂停",
    "retired":       "⚫ 已退役",
}

# 合法转换
TRANSITIONS = {
    "draft":       ["review", "retired"],
    "review":      ["approved_sim", "draft"],
    "approved_sim": ["sim_running"],
    "sim_running": ["review_real", "sim_failed", "paused", "retired"],
    "sim_failed":  ["draft", "retired"],
    "review_real": ["real", "draft", "retired"],
    "real":        ["paused", "retired"],
    "paused":      ["sim_running", "real", "retired"],
    "retired":     ["draft"],  # 可复活
}

# 自动转换条件
AUTO_RULES = {
    "approved_sim": {"to": "sim_running", "delay_hours": 0, "reason": "审批通过,自动部署"},
    "sim_running":  {"to": "review_real", "delay_days": 7, "reason": "模拟盘运行满7天,绩效达标,提交实盘审批",
                     "condition": "perf_ok"},
    "sim_running":  {"to": "sim_failed", "delay_days": 7, "reason": "模拟盘绩效不达标",
                     "condition": "perf_fail"},
}


def get_approval(name: str) -> dict | None:
    """获取策略审批状态。"""
    data = _load()
    return data.get("strategies", {}).get(name)


def submit_for_review(name: str) -> dict:
    """用户提交审核: draft → review"""
    return _transition(name, "review", "用户提交审核")


def approve_sim(name: str, reviewer: str = "玄策") -> dict:
    """玄策审核通过: review → approved_sim"""
    return _transition(name, "approved_sim", f"{reviewer}审核通过,部署模拟盘")


def reject_to_draft(name: str, reviewer: str = "玄策", reason: str = "") -> dict:
    """审核退回: review → draft"""
    return _transition(name, "draft", f"{reviewer}退回: {reason}" if reason else f"{reviewer}退回修改")


def approve_real(name: str, reviewer: str = "老板") -> dict:
    """老板审批实盘: review_real → real"""
    return _transition(name, "real", f"{reviewer}审批通过,可部署实盘")


def pause_strategy(name: str, reason: str = "") -> dict:
    return _transition(name, "paused", reason or "手动暂停")


def retire_strategy(name: str, reason: str = "") -> dict:
    return _transition(name, "retired", reason or "手动退役")


def check_auto_transitions() -> list[dict]:
    """检查所有策略的自动转换条件 (每5分钟调用一次)。"""
    data = _load()
    actions = []
    now = datetime.now()

    for name, s in data.get("strategies", {}).items():
        state = s.get("state", "draft")
        if state not in AUTO_RULES:
            continue

        rule = AUTO_RULES[state]
        entered_at = s.get("state_entered_at")
        if not entered_at:
            continue
        entered = datetime.fromisoformat(entered_at)

        # 时间检查
        delay_h = rule.get("delay_hours", 0)
        delay_d = rule.get("delay_days", 0)
        if delay_h > 0 and (now - entered) < timedelta(hours=delay_h):
            continue
        if delay_d > 0 and (now - entered) < timedelta(days=delay_d):
            continue

        # 条件检查
        condition = rule.get("condition")
        if condition == "perf_ok":
            perf = s.get("performance", {})
            if perf.get("win_rate", 0) < 0.40 or perf.get("sharpe", 0) < 0:
                continue  # 不满足绩效条件
        elif condition == "perf_fail":
            perf = s.get("performance", {})
            if perf.get("win_rate", 0) >= 0.40 and perf.get("sharpe", 0) >= 0:
                continue  # 绩效还行, 不触发失败

        result = _transition(name, rule["to"], rule["reason"])
        if result.get("success"):
            actions.append({"strategy": name, "from": state, "to": rule["to"], "reason": rule["reason"]})

    return actions


def get_all_approvals() -> list[dict]:
    data = _load()
    return [
        {"name": k, **v}
        for k, v in data.get("strategies", {}).items()
    ]


def _transition(name: str, to_state: str, reason: str) -> dict:
    if to_state not in STATES:
        return {"success": False, "message": f"无效状态: {to_state}"}

    data = _load()
    strategies = data.setdefault("strategies", {})
    s = strategies.get(name, {})
    from_state = s.get("state", "draft")

    allowed = TRANSITIONS.get(from_state, [])
    if to_state not in allowed:
        return {"success": False, "message": f"不允许: {STATES.get(from_state)} → {STATES.get(to_state)}"}

    s["state"] = to_state
    s["state_entered_at"] = datetime.now().isoformat()
    history = s.get("history", [])
    history.append({
        "from": from_state, "to": to_state, "reason": reason,
        "time": datetime.now().isoformat(),
    })
    s["history"] = history[-20:]  # 保留最近20条

    strategies[name] = s
    data["last_updated"] = datetime.now().isoformat()
    _save(data)

    logger.info(f"[Approval] {name}: {STATES.get(from_state)} → {STATES.get(to_state)} ({reason})")
    return {"success": True, "state": to_state, "label": STATES[to_state], "reason": reason}


def _load() -> dict:
    try:
        if os.path.exists(APPROVAL_FILE):
            with open(APPROVAL_FILE, "r") as f:
                return json.load(f)
    except Exception: pass
    return {"strategies": {}, "last_updated": ""}


def _save(data: dict):
    os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
    with open(APPROVAL_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 工具: 策略部署时自动注册审批
def register_strategy(name: str):
    """策略创建时调用, 初始化审批记录"""
    data = _load()
    if name not in data.get("strategies", {}):
        data.setdefault("strategies", {})[name] = {
            "state": "draft",
            "state_entered_at": datetime.now().isoformat(),
            "history": [],
            "performance": {},
        }
        data["last_updated"] = datetime.now().isoformat()
        _save(data)


def update_performance(name: str, perf: dict):
    """更新策略绩效 (PaperAutoLoop调用)。"""
    data = _load()
    s = data.get("strategies", {}).get(name, {})
    s["performance"] = perf
    data["strategies"][name] = s
    _save(data)
