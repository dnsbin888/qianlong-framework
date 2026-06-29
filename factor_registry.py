"""因子注册中心 (蓝图 v3.0 — FactorRegistry)

单一事实源。所有因子定义、IC、权重、状态从此读取。

加新因子: 只需在 factor_registry.json 加一条 + 写一个 compute 函数。
不改任何已有代码。系统零变动。
"""

import json, os, sys, importlib, logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"


def _load() -> dict:
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"FactorRegistry 加载失败: {e}")
        return {"factors": []}


def _save(data: dict):
    import tempfile
    tmp = REGISTRY_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REGISTRY_PATH)
    except Exception as e:
        logger.error(f"FactorRegistry 保存失败: {e}")


# ═══════════════════════════════════════════════════════
#  查询 API (不改代码, 只读 Registry)
# ═══════════════════════════════════════════════════════

def get_active_factors() -> list[dict]:
    """获取所有 active 状态的因子。"""
    return [f for f in _load()["factors"] if f.get("status") == "active"]


def get_retired_factors() -> list[dict]:
    """获取退役因子 (代码保留)。"""
    return [f for f in _load()["factors"] if f.get("status") == "retired"]


def get_all_factors() -> list[dict]:
    return _load()["factors"]


def get_factor(name: str) -> Optional[dict]:
    for f in _load()["factors"]:
        if f["name"] == name:
            return f
    return None


# ═══════════════════════════════════════════════════════
#  Compute 函数解析
# ═══════════════════════════════════════════════════════

def resolve_compute(compute_path: str) -> Callable:
    """将 "full_market_ic._factor_trend_old" 解析为可调用函数。

    支持格式:
      - "module.function" → 同目录模块
      - "package.module.function" → 完整路径
    """
    parts = compute_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid compute path: {compute_path}")
    mod_path, fn_name = parts
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, fn_name)
    except (ImportError, AttributeError) as e:
        logger.warning(f"无法解析 {compute_path}: {e}")
        raise


def get_compute_fn(name: str) -> Optional[Callable]:
    """获取因子的计算函数。"""
    f = get_factor(name)
    if not f:
        return None
    try:
        return resolve_compute(f["compute"])
    except Exception:
        return None


def get_all_compute_fns() -> dict[str, Callable]:
    """获取所有 active 因子的计算函数字典。"""
    result = {}
    for f in get_active_factors():
        try:
            result[f["name"]] = resolve_compute(f["compute"])
        except Exception:
            pass
    return result


# ═══════════════════════════════════════════════════════
#  IC → 策略权重 (自动)
# ═══════════════════════════════════════════════════════

def get_ic_weights(window: str = "5d", min_ic: float = 0.02) -> dict[str, float]:
    """根据 IC 自动计算策略权重。

    权重 ∝ max(IC - min_ic, 0), 归一化到总和=1。
    负 IC 因子权重=0。

    Args:
        window: "5d" | "10d" | "20d"
        min_ic: IC 低于此值的因子不参与权重分配

    Returns:
        {"trend_score": 0.20, "chip_v2": 0.18, ...}
    """
    ic_key = f"ic_{window}"
    active = get_active_factors()
    raw = {}
    for f in active:
        ic = f.get(ic_key, 0) or 0
        mult = f.get("weight_multiplier", 1.0)  # P0-1: 健康引擎写入的乘数
        if isinstance(mult, (int, float)) and mult >= 0:
            ic = ic * mult  # 乘数降权: 0.5=减半, 0.0=归零
        if ic > min_ic:
            raw[f["name"]] = ic - min_ic
        else:
            raw[f["name"]] = 0

    total = sum(raw.values())
    if total <= 0:
        # 所有因子IC都不足以分配 → 等权
        n = len(active)
        return {f["name"]: 1.0 / n for f in active}

    return {k: round(v / total, 4) for k, v in raw.items() if v > 0}


# ═══════════════════════════════════════════════════════
#  Registry 更新 (IC 重算后自动写入)
# ═══════════════════════════════════════════════════════

def update_ic(name: str, ic_values: dict[str, float]):
    """更新单个因子的 IC 值。"""
    data = _load()
    for f in data["factors"]:
        if f["name"] == name:
            for k, v in ic_values.items():
                f[k] = round(v, 4)
            from datetime import datetime
            data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            break
    _save(data)


def update_all_ic_from_report(report_path: str = None):
    """从 full_market_ic_report.json 批量更新所有因子 IC。"""
    if report_path is None:
        report_path = r"D:\quant_framework\full_market_ic_report.json"
    try:
        with open(report_path, "r") as f:
            report = json.load(f)
    except Exception as e:
        logger.error(f"无法读取IC报告: {e}")
        return

    factors_data = report.get("factors", {})
    data = _load()
    for f in data["factors"]:
        name = f["name"]
        ic_data = factors_data.get(name)
        if ic_data:
            for k, v in ic_data.items():
                if k.startswith("IC_") and not k.endswith("_n"):
                    f[k.lower()] = round(v, 4)
            f["ic_verified_days"] = report.get("days", 60)
            f["ic_verified_sample"] = report.get("sample", 500)
    from datetime import datetime
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(data)
    logger.info(f"FactorRegistry IC已更新: {len(factors_data)}个因子")


def retire_factor(name: str) -> bool:
    """切换因子 active ↔ retired。"""
    data = _load()
    for f in data["factors"]:
        if f["name"] == name:
            f["status"] = "retired" if f["status"] == "active" else "active"
            _save(data)
            return True
    return False


def add_factor(name: str, display: str, compute: str, category: str = "自定义",
               **kwargs) -> bool:
    """添加新因子到 Registry。不改任何代码。"""
    data = _load()
    if any(f["name"] == name for f in data["factors"]):
        logger.warning(f"因子 {name} 已存在")
        return False
    new_factor = {
        "name": name, "display": display, "compute": compute,
        "category": category, "status": "active",
        "ic_5d": None, "ic_10d": None, "ic_20d": None,
        "ic_verified_days": 0, "ic_verified_sample": 0,
        "direction": "long",
    }
    new_factor.update(kwargs)
    data["factors"].append(new_factor)
    _save(data)
    logger.info(f"因子已注册: {name}")
    return True


# ═══════════════════════════════════════════════════════
#  快速诊断
# ═══════════════════════════════════════════════════════

def summary() -> str:
    """打印因子池摘要。"""
    active = get_active_factors()
    retired = get_retired_factors()
    lines = [
        f"FactorRegistry: {len(active)} active, {len(retired)} retired",
        "─" * 50,
    ]
    for f in sorted(active, key=lambda x: abs(x.get("ic_5d", 0) or 0), reverse=True):
        ic5 = f.get("ic_5d", "?")
        lines.append(f"  {f['name']:<20s} IC(5d)={ic5:+.3f}" if isinstance(ic5, (int, float)) else f"  {f['name']:<20s} IC(5d)=?")
    if retired:
        lines.append("  ─ 退役 ─")
        for f in retired:
            lines.append(f"  {f['name']:<20s} {f.get('retired_reason', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
    print("\nIC权重 (5d):", get_ic_weights("5d"))
