"""潜龙 策略绩效卡 — 纯读数据, 零风险 (Phase: Capability Audit)
============================================================
每个战法独立统计: 笔数 / 胜率 / 收益 / 盈亏比 / 持仓天数

用法: python strategy_scorecard.py
输出: 控制台 + D:\quant_framework\data\strategy_scorecard.json
"""
import json, os, sys
from datetime import datetime
from collections import defaultdict
sys.path.insert(0, r"D:\quant_framework")
from exit_attribution import ExitAttributionEngine

# ── 数据源 ──
PAPER = r"D:\quant_framework\paper_account.json"
SIGNAL_TABLE = r"D:\quant_web\data\signal_table.json"
SIGNAL_CFG = r"D:\quant_framework\signal_config.json"
AUTO_PLAN = r"D:\quant_web\data\auto_trade_plan.json"

# ── 策略名标准化 ──
STRATEGY_ALIASES = {
    "LightGBM": "TrendML",
    "LightGBM-v1": "TrendML",
    "XGBoost": "MomentumML",
    "XGBoost-v1": "MomentumML",
    "ML": "ML综合",
    "auto": "ML综合",
    "qmt": "QMT战法",
}

def classify_strategy(trade: dict) -> str:
    """从交易记录推断策略来源"""
    reason = trade.get("reason", "")
    source = trade.get("signal_source", "")
    trade_type = trade.get("type", "")

    # QMT 战法
    if source == "qmt" or "QMT" in reason:
        if "突破" in reason: return "盘中突破"
        if "竞价" in reason: return "竞价抢筹"
        if "尾盘" in reason: return "尾盘急拉"
        if "打板" in reason or "追封" in reason: return "打板追封"
        if "回封" in reason: return "打板-回封"
        return "QMT战法"

    # ML
    if "ML" in reason or source == "auto" or trade_type == "auto":
        if "LGBM" in reason or "LightGBM" in reason: return "TrendML"
        if "XGB" in reason: return "MomentumML"
        return "ML综合"

    # 手动
    if source == "manual" or trade_type == "manual":
        return "手动"

    return source or "未知"


def compute():
    # 加载数据
    with open(PAPER, "r", encoding="utf-8") as f:
        pa = json.load(f)

    trades = pa.get("trade_log", [])
    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]

    # 加载信号表 (用于匹配)
    st = []
    if os.path.exists(SIGNAL_TABLE):
        with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
            st = json.load(f)

    # ── 每个策略的绩效统计 ──
    stats = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "total_return_pct": 0.0,
        "avg_hold_days": 0, "holding_days": [],
        "buy_count": 0, "sell_count": 0,
    })

    for t in sells:
        strat = classify_strategy(t)
        pnl = t.get("pnl", 0) or 0
        stats[strat]["trades"] += 1
        stats[strat]["sell_count"] += 1
        if pnl > 0:
            stats[strat]["wins"] += 1
        else:
            stats[strat]["losses"] += 1
        stats[strat]["total_pnl"] += pnl
        if t.get("cost_price") and t.get("cost_price", 0) > 0:
            cost_total = t["cost_price"] * t.get("qty", 1)
            if cost_total > 0:
                stats[strat]["total_return_pct"] += pnl / cost_total * 100

    for t in buys:
        strat = classify_strategy(t)
        stats[strat]["buy_count"] += 1

    # ── 退出归因 ──
    engine = ExitAttributionEngine()
    attrs = engine.classify_batch(sells)
    exit_dist = engine.stats().get("distribution", {})

    # ── 汇总 ──
    lines = []
    lines.append("=" * 65)
    lines.append("  潜龙 策略绩效卡 (Capability Audit)")
    lines.append(f"  统计: {len(sells)}笔卖出, {len(buys)}笔买入")
    lines.append("=" * 65)
    lines.append(f"  {'策略':20s} {'笔数':>4s} {'胜率':>6s} {'总盈亏':>10s} {'均收益':>8s} {'盈亏比':>6s}")
    lines.append("  " + "-" * 57)

    scorecard = {}
    total_pnl_all = 0
    for strat in sorted(stats.keys()):
        s = stats[strat]
        n = s["trades"]
        if n == 0: continue
        wr = s["wins"] / n * 100
        avg_pnl = s["total_pnl"] / n
        avg_ret = s["total_return_pct"] / n if n > 0 else 0
        total_pnl_all += s["total_pnl"]

        # 盈亏比
        win_avg = sum(t.get("pnl", 0) for t in sells if classify_strategy(t) == strat and (t.get("pnl") or 0) > 0) / max(s["wins"], 1)
        loss_avg = abs(sum(t.get("pnl", 0) for t in sells if classify_strategy(t) == strat and (t.get("pnl") or 0) < 0) / max(s["losses"], 1))
        pf = win_avg / loss_avg if loss_avg > 0 else 0

        lines.append(f"  {strat:20s} {n:4d} {wr:5.0f}% {s['total_pnl']:+10,.0f} {avg_ret:+7.1f}% {pf:5.1f}")

        scorecard[strat] = {
            "trades": n, "buys": s["buy_count"], "sells": s["sell_count"],
            "win_rate": round(wr, 1), "total_pnl": round(s["total_pnl"], 2),
            "avg_return_pct": round(avg_ret, 2), "profit_factor": round(pf, 2),
        }

    lines.append("  " + "-" * 57)
    lines.append(f"  {'合计':20s} {len(sells):4d} {'':6s} {total_pnl_all:+10,.0f}")
    lines.append("=" * 65)

    # 退出分布
    if exit_dist:
        lines.append(f"\n  退出原因分布:")
        for rc, count in sorted(exit_dist.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"    {rc:30s} {count:3d}")

    lines.append(f"\n  注: 策略分类从 reason/signal_source 推断, 非精确标签")
    lines.append(f"  建议: 在 trade record 中增加 strategy_id 字段以精确归属")
    lines.append("=" * 65)

    # 输出
    report = "\n".join(lines)
    print(report)

    # 保存
    out_path = r"D:\quant_framework\data\strategy_scorecard.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_sells": len(sells),
            "total_buys": len(buys),
            "total_pnl": round(total_pnl_all, 2),
            "strategies": scorecard,
            "exit_distribution": exit_dist,
        }, f, ensure_ascii=False, indent=2)

    return scorecard


if __name__ == "__main__":
    compute()
