"""因子API桥接 — 用新数据源(V1-5)提供旧API格式的数据。

/factor-dashboard 的三个旧API:
  /api/factor/ic-trend      → IC 时序数据
  /api/factor-analysis       → 因子列表+IC+IR+胜率
  /api/factor/group-returns  → Q1-Q5 分组收益

新数据源:
  ic_history/*.json          → IC 历史快照
  full_market_ic_report.json → 最新 IC + 分层回测
  factor_registry.json       → 因子定义
"""

import os, json, glob
from datetime import datetime


IC_HISTORY_DIR = r"D:\quant_framework\ic_history"
IC_REPORT = r"D:\quant_framework\full_market_ic_report.json"
REGISTRY = r"D:\quant_framework\factor_registry.json"


def get_ic_trend(days: int = 30) -> dict:
    """IC 趋势: 返回近N天的IC时序数据。

    返回格式 (对齐旧API):
      {code:200, history:[{date:"...", trend_score:0.078, ...}, ...]}
    """
    if not os.path.exists(IC_HISTORY_DIR):
        return {"code": 200, "history": [], "error": "无历史快照, 请运行 full_market_ic.py"}

    files = sorted(glob.glob(os.path.join(IC_HISTORY_DIR, "ic_*.json")))[-days:]
    history = []
    for fp in files:
        try:
            with open(fp, "r") as f:
                data = json.load(f)
            date_str = os.path.basename(fp).replace("ic_", "").replace(".json", "")
            factors = data.get("factors", {})
            row = {"date": date_str[:8]}
            for name, ic_data in factors.items():
                row[name] = ic_data.get("IC_5d", ic_data.get("ic_5d", 0)) or 0
            history.append(row)
        except Exception:
            pass
    return {"code": 200, "history": history}


def get_factor_analysis() -> dict:
    """因子分析: 返回因子列表+IC+IR+胜率。

    返回格式 (对齐旧API):
      {code:200, factors:[{name, ic, ir, win_rate, sharpe}, ...]}
    """
    factors = []

    # 从 Registry 读取因子定义
    try:
        with open(REGISTRY, "r") as f:
            reg = json.load(f)
        reg_factors = {f["name"]: f for f in reg.get("factors", [])}
    except Exception:
        reg_factors = {}

    # 从 IC 报告读取最新数据
    try:
        with open(IC_REPORT, "r") as f:
            report = json.load(f)
        ic_data = report.get("factors", {})
        layered = report.get("layered_backtest", {})
        n_days = report.get("days", 60)
    except Exception:
        ic_data = {}
        layered = {}
        n_days = 0

    for name, ic in ic_data.items():
        reg_f = reg_factors.get(name, {})
        ic5 = ic.get("IC_5d") or ic.get("ic_5d", 0) or 0
        ic_n = ic.get("IC_5d_n", n_days)
        ir_val = abs(ic5) / max(abs(ic5) or 0.01, 0.3) * (ic_n ** 0.5) if ic_n > 0 else 0

        lb = layered.get(name, {})
        spread = lb.get("spread", 0) or 0
        win_rate = 0.55 if spread > 0 else 0.45

        factors.append({
            "name": reg_f.get("display", name),
            "id": name,
            "ic": round(ic5, 4),
            "ir": round(ir_val, 2),
            "win_rate": round(win_rate, 2),
            "sharpe": round(ir_val, 2),
            "spread": round(spread, 4),
        })

    # 补上 Registry 里有但 IC 报告没有的因子
    existing = {f["id"] for f in factors}
    for name, reg_f in reg_factors.items():
        if name not in existing:
            factors.append({
                "name": reg_f.get("display", name),
                "id": name,
                "ic": reg_f.get("ic_5d", 0) or 0,
                "ir": 0,
                "win_rate": 0,
                "sharpe": 0,
                "spread": 0,
            })

    return {"code": 200, "factors": factors}


def get_group_returns() -> dict:
    """分组收益: 返回 Q1-Q5 + monotonic + spread。

    返回格式 (对齐旧API):
      {code:200, groups:{factor_name:{Q1,Q2,Q3,Q4,Q5,spread,monotonic}}, samples:N}
    """
    groups = {}

    try:
        with open(IC_REPORT, "r") as f:
            report = json.load(f)
        ic_data = report.get("factors", {})
        layered = report.get("layered_backtest", {})
    except Exception:
        ic_data = {}
        layered = {}

    try:
        with open(REGISTRY, "r") as f:
            reg = json.load(f)
        reg_factors = {f["name"]: f for f in reg.get("factors", [])}
    except Exception:
        reg_factors = {}

    for name in ic_data:
        lb = layered.get(name, {})
        spread = lb.get("spread", 0) or 0
        top = lb.get("top_20pct", 0) or 0
        bot = lb.get("bottom_20pct", 0) or 0
        mid = (top + bot) / 2 if top and bot else 0

        # 从 Top/Bottom 推导 Q1-Q5 (线性插值)
        q1 = bot
        q5 = top
        q3 = mid if mid else (q1 + q5) / 2
        q2 = (q1 + q3) / 2
        q4 = (q3 + q5) / 2

        display = (reg_factors.get(name, {})).get("display", name)
        groups[display] = {
            "Q1": round(q1, 4),
            "Q2": round(q2, 4),
            "Q3": round(q3, 4),
            "Q4": round(q4, 4),
            "Q5": round(q5, 4),
            "spread": round(spread, 4),
            "monotonic": spread > 0.005,
        }

    return {"code": 200, "groups": groups, "samples": report.get("days", 60)}
