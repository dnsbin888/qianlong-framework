"""日终自动报告 (蓝图 v4.0 P3-2)

每天 15:05 自动生成: PnL + 持仓诊断 + 因子健康 + 策略绩效
输出: Markdown文件 + 钉钉推送
"""

import os, json, logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

REPORT_DIR = r"D:\quant_framework\reports"
PAPER_ACCOUNT = r"D:\quant_framework\paper_account.json"
TRACK_FILE = r"D:\quant_framework\live_positions_track.json"
REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"
APPROVAL_PATH = r"D:\quant_framework\strategy_approvals.json"


def generate_daily_report() -> dict:
    """生成日终报告。返回报告dict + 保存Markdown。"""
    today = date.today().isoformat()
    report = {
        "date": today,
        "generated_at": datetime.now().strftime("%H:%M:%S"),
        "sections": {},
    }

    # 1. PnL
    report["sections"]["pnl"] = _section_pnl()

    # 2. 持仓
    report["sections"]["positions"] = _section_positions()

    # 3. 因子健康
    report["sections"]["factors"] = _section_factors()

    # 4. 策略绩效
    report["sections"]["strategies"] = _section_strategies()

    # 保存 Markdown
    md = _render_markdown(report)
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"daily_report_{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"日终报告已生成: {path}")

    # 推送到钉钉
    try:
        from factor_health import notify_strategy_event
        summary = _summary_line(report)
        notify_strategy_event("日终报告", "daily", summary)
    except Exception: pass

    return report


def _section_pnl() -> dict:
    """PnL 摘要。"""
    try:
        if os.path.exists(PAPER_ACCOUNT):
            with open(PAPER_ACCOUNT, "r") as f:
                data = json.load(f)
            cash = data.get("cash", 0)
            positions = data.get("positions", {})
            pos_value = sum(
                p.get("qty", 0) * p.get("last_price", p.get("avg_cost", 0))
                for p in positions.values()
            )
            total = cash + pos_value
            initial = 1_000_000
            pnl = total - initial
            return {
                "total_asset": round(total, 2),
                "cash": round(cash, 2),
                "position_value": round(pos_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / initial * 100, 2),
                "position_count": len(positions),
            }
    except Exception: pass
    return {"error": "paper_account不可用"}


def _section_positions() -> list[dict]:
    """持仓诊断。"""
    try:
        if os.path.exists(PAPER_ACCOUNT):
            with open(PAPER_ACCOUNT, "r") as f:
                data = json.load(f)
            positions = data.get("positions", {})
            result = []
            for sym, pos in positions.items():
                qty = pos.get("qty", 0)
                cost = pos.get("avg_cost", 0)
                last = pos.get("last_price", cost)
                pnl_pct = (last - cost) / max(cost, 0.01) * 100 if cost > 0 else 0
                result.append({
                    "symbol": sym,
                    "qty": qty,
                    "cost": round(cost, 2),
                    "last": round(last, 2),
                    "pnl_pct": round(pnl_pct, 1),
                    "status": "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < -3 else "🟡"),
                })
            return sorted(result, key=lambda x: x["pnl_pct"], reverse=True)
    except Exception: pass
    return []


def _section_factors() -> dict:
    """因子健康摘要。"""
    try:
        with open(REGISTRY_PATH, "r") as f:
            data = json.load(f)
        factors = data.get("factors", [])
        active = [f for f in factors if f.get("status") == "active"]
        retired = [f for f in factors if f.get("status") == "retired"]
        top3 = sorted(active, key=lambda x: abs(x.get("ic_5d", 0) or 0), reverse=True)[:3]
        return {
            "active_count": len(active),
            "retired_count": len(retired),
            "top3": [{"name": f.get("display", f["name"]), "ic_5d": f.get("ic_5d")} for f in top3],
        }
    except Exception: pass
    return {"error": "Registry不可用"}


def _section_strategies() -> list[dict]:
    """策略运行状态。"""
    try:
        from strategy_approval import get_all_approvals
        strategies = get_all_approvals()
        running = [s for s in strategies if s.get("state") in ("sim_running", "real")]
        return [
            {"name": s["name"], "state": s.get("state", "?"),
             "perf": s.get("performance", {}).get("sharpe", "?")}
            for s in running
        ]
    except Exception: pass
    return []


def _summary_line(report) -> str:
    """生成摘要行给钉钉。"""
    pnl = report["sections"].get("pnl", {})
    fac = report["sections"].get("factors", {})
    strat = report["sections"].get("strategies", [])
    return (
        f"PnL: {pnl.get('pnl_pct','?')}% | "
        f"持仓: {pnl.get('position_count','?')}只 | "
        f"因子: {fac.get('active_count','?')}活跃 | "
        f"策略: {len(strat)}运行中"
    )


def _section_strategy_perf() -> list[dict]:
    """策略绩效摘要"""
    try:
        import urllib.request, json
        r = urllib.request.urlopen("http://127.0.0.1:5002/api/strategy-performance", timeout=5)
        data = json.loads(r.read().decode())
        return data.get("strategies", [])
    except: return []


def _render_markdown(report: dict) -> str:
    """渲染 Markdown。"""
    pnl = report["sections"].get("pnl", {})
    pos = report["sections"].get("positions", [])
    fac = report["sections"].get("factors", {})
    strat = report["sections"].get("strategies", [])

    lines = [
        f"# 潜龙日终报告 — {report['date']}",
        f"生成时间: {report['generated_at']}",
        "",
        "## 💰 PnL",
        f"- 总资产: ¥{pnl.get('total_asset',0):,.0f}",
        f"- 可用资金: ¥{pnl.get('cash',0):,.0f}",
        f"- 持仓市值: ¥{pnl.get('position_value',0):,.0f}",
        f"- 累计盈亏: {pnl.get('pnl_pct',0):+.2f}%",
        f"- 持仓数: {pnl.get('position_count',0)}只",
        "",
        "## 📊 持仓诊断",
    ]
    if pos:
        lines.append("| 代码 | 数量 | 成本 | 现价 | 盈亏 | 状态 |")
        lines.append("|------|------|------|------|------|------|")
        for p in pos[:20]:
            lines.append(f"| {p['symbol']} | {p['qty']} | {p['cost']:.2f} | {p['last']:.2f} | {p['pnl_pct']:+.1f}% | {p['status']} |")
    else:
        lines.append("空仓")
    lines += [
        "",
        "## 🧬 因子健康",
        f"- 活跃因子: {fac.get('active_count','?')} | 退役: {fac.get('retired_count','?')}",
    ]
    for f in fac.get("top3", []):
        ic = f.get("ic_5d", 0) or 0
        lines.append(f"  - {f['name']}: IC(5d)={ic:+.4f}")
    lines += [
        "",
        "## 🎯 策略绩效",
    ]
    perf = report.get("strategy_perf", _section_strategy_perf())
    if perf:
        lines.append("| 策略 | Sharpe | 胜率 | 笔数 | 状态 |")
        lines.append("|------|--------|------|------|------|")
        for s in perf[:10]:
            lines.append(f"| {s.get('name','?')} | {s.get('sharpe',0):.1f} | {(s.get('win_rate',0)*100):.0f}% | {s.get('total_trades',0)} | {s.get('risk','?')} |")
    else:
        lines.append("无活跃策略")
    return "\n".join(lines)
