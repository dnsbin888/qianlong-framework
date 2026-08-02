"""日终归因报告 v1.0 - Phase 8.1 (2026-07-09)
P&L 分解: 行业贡献 + 因子贡献 + 模型信号质量
输出: D:\quant_web\data\daily_attribution.json
"""
import json, os, sys
from datetime import datetime
sys.path.insert(0, r"D:\quant_web")

OUTPUT = r"D:\quant_web\data\daily_attribution.json"


def run_attribution():
    """运行归因分析: 基于今日持仓和信号"""
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "pnl_summary": {},
        "industry_pnl": [],
        "factor_contribution": [],
        "model_performance": {},
        "alerts": []
    }

    # 1. PnL 摘要 (从模拟盘)
    try:
        from paper_engine import paper
        positions = paper.positions
        total_value = paper.cash
        for sym, pos in positions.items():
            total_value += pos.get("market_value", pos.get("qty", 0) * pos.get("last_price", 0))
        report["pnl_summary"] = {
            "cash": round(paper.cash, 2),
            "position_count": len(positions),
            "total_equity": round(total_value, 2),
            "pnl_pct": round((total_value - 1_000_000) / 1_000_000 * 100, 2) if total_value > 0 else 0
        }
    except Exception as e:
        report["pnl_summary"]["error"] = str(e)

    # 2. 行业归因
    try:
        from stock_names import get_industry
        industry_pnl = {}
        for sym, pos in positions.items():
            ind = get_industry(sym) or "未知"
            pnl = pos.get("profit_amt", 0) or (pos.get("market_value", 0) - pos.get("cost_value", 0))
            if ind not in industry_pnl:
                industry_pnl[ind] = {"count": 0, "pnl": 0.0}
            industry_pnl[ind]["count"] += 1
            industry_pnl[ind]["pnl"] += float(pnl)
        report["industry_pnl"] = sorted(
            [{"industry": k, **v} for k, v in industry_pnl.items()],
            key=lambda x: -x["pnl"]
        )
    except Exception as e:
        report["alerts"].append(f"行业归因失败: {e}")

    # 3. 因子贡献 (从 factor_registry IC)
    try:
        reg_path = r"D:\quant_framework\factor_registry.json"
        if os.path.exists(reg_path):
            reg = json.load(open(reg_path, encoding="utf-8"))
            factors = reg.get("factors", {})
            ic_list = []
            for name, info in factors.items():
                ic = info.get("ic", 0) or 0
                if ic != 0:
                    ic_list.append({"name": name, "label": info.get("label", name), "ic": round(ic, 4)})
            ic_list.sort(key=lambda x: -abs(x["ic"]))
            report["factor_contribution"] = ic_list[:10]
            # 衰减告警
            for f in ic_list:
                if abs(f["ic"]) < 0.01:
                    report["alerts"].append(f"因子 {f['label']} IC={f['ic']:.4f} 接近零，建议关注")
    except Exception as e:
        report["alerts"].append(f"因子贡献失败: {e}")

    # 4. 模型信号表现
    try:
        sig_path = os.path.join(os.path.dirname(__file__), "..", "quant_web", "data", "signal_table.json")
        if os.path.exists(sig_path):
            signals = json.load(open(sig_path, encoding="utf-8"))
            models = {"LGBM": [], "XGBoost": [], "CatBoost": []}
            for s in signals[:20]:
                if s.get("lgbm_score"): models["LGBM"].append(s["lgbm_score"])
                if s.get("xgb_score"): models["XGBoost"].append(s["xgb_score"])
                if s.get("cb_score"): models["CatBoost"].append(s["cb_score"])
            report["model_performance"] = {
                m: {
                    "count": len(v),
                    "avg_score": round(sum(v) / len(v), 1) if v else 0
                }
                for m, v in models.items()
            }
    except Exception as e:
        report["alerts"].append(f"模型表现失败: {e}")

    # 保存
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[Attribution] 归因报告已保存: {OUTPUT}")
    return report


if __name__ == "__main__":
    r = run_attribution()
    print(f"  总权益: {r['pnl_summary'].get('total_equity', 'N/A')}")
    print(f"  行业数: {len(r['industry_pnl'])}")
    print(f"  因子数: {len(r['factor_contribution'])}")
    print(f"  告警:   {len(r['alerts'])}")
