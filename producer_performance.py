"""Producer × Regime 绩效矩阵 — 纯读数据, 零风险
==================================================
回答: 每个 Evidence Domain 在哪种市场环境下可靠？

输出: data/producer_performance.json
"""
import json, os, sys
from datetime import datetime
from collections import defaultdict
sys.path.insert(0, r"D:\quant_framework")

PAPER = r"D:\quant_framework\paper_account.json"
SIGNAL_TABLE = r"D:\quant_web\data\signal_table.json"
EVIDENCE_HISTORY = r"D:\quant_framework\data\evidence_history"

def load_regime(date_str: str) -> str:
    """从证据历史或市场状态推断当日 Regime"""
    # 尝试从 evidence history 读取
    hist_file = os.path.join(EVIDENCE_HISTORY, f"{date_str.replace('-','')}.json")
    if os.path.exists(hist_file):
        try:
            with open(hist_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            health = data.get("health", {}).get("producers", {})
            # Fallback: 从信号表推断
            return "unknown"
        except:
            pass

    # 简易推断: 从日期的信号分布近似
    return "unknown"


def build_matrix():
    with open(PAPER, "r", encoding="utf-8") as f:
        pa = json.load(f)

    trades = pa.get("trade_log", [])
    sells = [t for t in trades if t.get("side") == "sell"]

    # 加载信号表用于推断 LGBM vs XGB 贡献
    st = []
    if os.path.exists(SIGNAL_TABLE):
        with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
            st = json.load(f)

    # 为每笔卖出推断 Producer 和 Regime
    # Producer: 从信号表中的 lgbm_score / xgb_score 推断
    # Regime: 近似推断 (样本太少, 标记为 unknown)
    matrix = defaultdict(lambda: defaultdict(lambda: {
        "trades": 0, "wins": 0, "total_pnl": 0.0,
        "avg_return_pct": 0.0, "returns": [],
    }))

    for t in sells:
        sym = t["symbol"]
        pnl = t.get("pnl", 0) or 0
        cost_price = t.get("cost_price", 0)
        qty = t.get("qty", 1)
        ret_pct = (pnl / (cost_price * qty) * 100) if cost_price > 0 and qty > 0 else 0

        # 推断 Regime (目前: unknown, 积累数据后会精确)
        # 实际需要从 market_regime.detect_regime() 的历史记录获取
        regime = "unknown"

        # 推断 Producer 贡献
        producers = {}
        for s in st:
            if s["symbol"] == sym:
                lgbm = s.get("lgbm_score", 0) or 0
                xgb = s.get("xgb_score", 0) or 0
                if lgbm > 0:
                    producers["TrendML"] = lgbm
                if xgb > 0:
                    producers["MomentumML"] = xgb
                break

        # 归属: 高分者为主要贡献
        if producers:
            primary = max(producers, key=producers.get)
            # 也记录次要贡献
            secondary = [p for p in producers if p != primary]

            # 主要 Producer
            pm = matrix[primary][regime]
            pm["trades"] += 1
            if pnl > 0: pm["wins"] += 1
            pm["total_pnl"] += pnl
            pm["returns"].append(ret_pct)

            # 次要 Producer (部分贡献)
            for sec in secondary:
                sm = matrix[sec][regime]
                # 次要贡献按比例折算 (简单: 50%权重)
                sm["trades"] = sm.get("trades", 0)
                # 不重复计数, 只记录关联

        # 也记录为综合
        cm = matrix["综合"][regime]
        cm["trades"] += 1
        if pnl > 0: cm["wins"] += 1
        cm["total_pnl"] += pnl
        cm["returns"].append(ret_pct)

    # ── 输出 ──
    lines = []
    lines.append("=" * 70)
    lines.append("  Producer × Regime 绩效矩阵")
    lines.append(f"  样本: {len(sells)}笔卖出 | 数据来源: signal_table推断")
    lines.append("  Regime=unknown, Producer=inferred")
    lines.append("=" * 70)
    lines.append(f"  {'Producer':15s} {'Regime':10s} {'笔数':>4s} {'胜率':>6s} {'总盈亏':>10s} {'均收益':>7s} {'可靠度':>6s}")
    lines.append("  " + "-" * 60)

    for producer in sorted(matrix.keys()):
        for regime in sorted(matrix[producer].keys()):
            m = matrix[producer][regime]
            n = m["trades"]
            if n == 0: continue
            wr = m["wins"] / n * 100
            avg_ret = sum(m["returns"]) / n if m["returns"] else 0
            # 可靠度: 胜率 × 样本量因子 (样本越多越可靠)
            reliability = min(100, wr * (1 - 1.0 / (n + 1)))
            bar = "█" * int(reliability / 10) + "░" * (10 - int(reliability / 10))
            lines.append(f"  {producer:15s} {regime:10s} {n:4d} {wr:5.0f}% {m['total_pnl']:+10,.0f} {avg_ret:+6.1f}% [{bar}]")

    lines.append("=" * 70)
    lines.append(f"\n  📌 注:")
    lines.append(f"  1. Regime 目前为 'unknown' — 需要在 trade record 中增加 regime 字段")
    lines.append(f"  2. Producer 归属从 signal_table 推断 — 非精确, 仅供参考")
    lines.append(f"  3. 可靠度 = 胜率 × 样本量因子 — 样本越多越可信")
    lines.append(f"  4. 积累 100+ 笔交易后, 此矩阵将具有统计显著性")
    lines.append("=" * 70)

    print("\n".join(lines))

    # 保存
    out = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_sells": len(sells),
        "note": "Producer attribution from signal_table inference — NOT precise",
        "matrix": {
            producer: {
                regime: {
                    k: v for k, v in m.items() if k != "returns"
                }
                for regime, m in regimes.items()
            }
            for producer, regimes in matrix.items()
        },
    }
    out_path = r"D:\quant_framework\data\producer_performance.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    return matrix


if __name__ == "__main__":
    build_matrix()
