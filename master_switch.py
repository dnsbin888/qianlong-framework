"""master_switch.py — 风控总闸 v1.0 (2026-07-08)
架构: 不新建文件, 扩展 auto_trade_plan.json global_limits 段
  所有交易通道下单前统一检查点
  原子写入: .tmp + fsync + os.replace (行业标准)
  零依赖, 纯 stdlib
"""
import json, os, time
from datetime import datetime

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"

# 默认值 (当 plan 文件不存在或字段缺失时使用)
DEFAULTS = {
    "circuit_breaker": False,
    "qmt_fast_enabled": True,
    "ai_auto_enabled": False,
}


def read_plan():
    """读取 plan 文件, 返回 global_limits 字典 (公开API)"""
    if not os.path.exists(PLAN_PATH):
        return dict(DEFAULTS)
    try:
        with open(PLAN_PATH, "r", encoding="utf-8") as f:
            plan = json.load(f)
        limits = plan.get("global_limits", {})
        # 补全缺失字段
        for k, v in DEFAULTS.items():
            limits.setdefault(k, v)
        return limits
    except Exception:
        return dict(DEFAULTS)


def _atomic_write(limits_dict):
    """原子写入 plan 的 global_limits 段 (.tmp + fsync + os.replace)"""
    # 读完整 plan (保留 stocks 等其他段)
    plan = {"stocks": {}, "global_limits": {}}
    if os.path.exists(PLAN_PATH):
        try:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception:
            pass
    plan["global_limits"].update(limits_dict)  # merge: 保留 generate_signal_table 写入的止盈止损字段
    plan["global_limits"]["kill_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tmp = PLAN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PLAN_PATH)  # 原子替换


# ═══════════════════════════════════════════
# 公共 API — 所有通道调用
# ═══════════════════════════════════════════

def can_buy(channel="sim"):
    """买入前检查 — QMT/live_trader/paper_engine 下单前调用

    Args:
        channel: "real" = 实盘, "sim" = 模拟盘

    Returns:
        True = 可以买入, False = 被拦截
    """
    limits = read_plan()
    if limits.get("circuit_breaker", False):
        return False
    if channel == "real" and not limits.get("qmt_fast_enabled", True):
        return False
    return True


def can_sell():
    """卖出前检查 — 永远 True (FIA 2024 铁律: 减仓永不拦截)"""
    return True


def get_status():
    """返回当前三开关状态 (供 API 和 UI 使用)"""
    limits = read_plan()
    return {
        "circuit_breaker": limits.get("circuit_breaker", False),
        "qmt_fast_enabled": limits.get("qmt_fast_enabled", True),
        "ai_auto_enabled": limits.get("ai_auto_enabled", False),
        "updated_at": limits.get("kill_updated_at", ""),
    }


def toggle(switch_name, value, operator="老板"):
    """切换指定开关

    Args:
        switch_name: "circuit_breaker" | "qmt_fast_enabled" | "ai_auto_enabled"
        value: True | False
        operator: 操作人标识
    """
    limits = read_plan()
    old_val = limits.get(switch_name, False)
    limits[switch_name] = value
    _atomic_write(limits)
    print(f"[MasterSwitch] {operator} {switch_name}: {old_val} → {value}")
    return True


def emergency_halt(operator="老板"):
    """紧急停止 — 一键全断 (总闸+QMT+AI 全关)"""
    limits = read_plan()
    limits["circuit_breaker"] = True
    limits["qmt_fast_enabled"] = False
    limits["ai_auto_enabled"] = False
    _atomic_write(limits)
    print(f"[MasterSwitch] 🚨 {operator} 触发紧急停止! 所有通道已关闭")
    return True


# ═══════════════════════════════════════════
# generate_signal_table 用 — 保留开关字段
# ═══════════════════════════════════════════

def preserve_switch_state(old_plan):
    """从旧 plan 中提取开关字段, 供 generate_signal_table.py 复用

    Args:
        old_plan: 旧 auto_trade_plan.json 的 dict

    Returns:
        dict of switch fields to preserve
    """
    old_limits = old_plan.get("global_limits", {}) if old_plan else {}
    return {
        "circuit_breaker": old_limits.get("circuit_breaker", False),
        "qmt_fast_enabled": old_limits.get("qmt_fast_enabled", True),
        "ai_auto_enabled": old_limits.get("ai_auto_enabled", False),
    }
