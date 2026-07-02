"""回测报告生成器 v1.0 — 标准化IC+绩效报告

用法: python report_generator.py → 生成 reports/ic_report.html
"""
import json, os, numpy as np
from datetime import datetime

FW = r"D:\quant_framework"
REPORT_DIR = os.path.join(FW, "reports")
IC_FILE = os.path.join(FW, "full_market_ic_report.json")
REGISTRY = os.path.join(FW, "factor_registry.json")
PAPER = os.path.join(FW, "paper_account.json")
EQUITY = os.path.join(FW, "equity_log.json")

os.makedirs(REPORT_DIR, exist_ok=True)


def generate(filename="ic_report.html"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # ── IC 表格 ──
    ic_rows = ""
    try:
        ic_data = json.load(open(IC_FILE, encoding="utf-8"))["factors"]
        reg = json.load(open(REGISTRY, encoding="utf-8"))
        active_factors = [f for f in reg["factors"] if f.get("status") == "active"]
        for fac in active_factors[:12]:
            name = fac["name"]
            display = fac.get("display", name)
            direction = fac.get("direction", "long")
            ic = ic_data.get(name, {})
            if not ic: continue
            periods = [1, 3, 5, 7, 10, 15, 20]
            vals = []
            for p in periods:
                v = ic.get(f"IC_{p}d")
                vals.append(f"{v:+.4f}" if v is not None else "-")
            ic_rows += f"<tr><td>{display}</td><td>{direction}</td>" + "".join(f"<td>{v}</td>" for v in vals) + "</tr>\n"
    except Exception as e:
        ic_rows = f"<tr><td colspan='9'>加载失败: {e}</td></tr>"

    # ── 绩效汇总 ──
    perf = ""
    try:
        p = json.load(open(PAPER, encoding="utf-8"))
        eq = json.load(open(EQUITY, encoding="utf-8")).get("log", [])
        cash = p["cash"]
        positions = p.get("positions", {})
        mv = sum(pos.get("last_price", pos["avg_cost"]) * pos["qty"] for pos in positions.values())
        total_eq = cash + mv
        total_return = (total_eq - 1_000_000) / 1_000_000 * 100
        trades = p.get("trade_log", [])
        sells = [t for t in trades if t.get("side") == "sell"]
        wins = sum(1 for t in sells if (t.get("pnl", 0) or 0) > 0)
        wr = round(wins / len(sells) * 100, 1) if sells else 0
        total_pnl = sum(t.get("pnl", 0) or 0 for t in sells)

        # 收益曲线
        eq_vals = [e[1] for e in eq[-30:]] if eq else [1_000_000]
        peak = eq_vals[0]
        max_dd = 0
        for v in eq_vals:
            if v > peak: peak = v
            dd = (v - peak) / peak * 100
            if dd < max_dd: max_dd = dd

        perf = f"""
        <div class="kpi">总资产<span>¥{total_eq:,.0f}</span></div>
        <div class="kpi">累计收益<span>{total_return:+.1f}%</span></div>
        <div class="kpi">累计盈亏<span>¥{total_pnl:,.0f}</span></div>
        <div class="kpi">胜率<span>{wr}%</span></div>
        <div class="kpi">最大回撤<span>{max_dd:.1f}%</span></div>
        <div class="kpi">交易笔数<span>{len(trades)}</span></div>
        """
    except Exception as e:
        perf = f"<div class='kpi'>加载失败<span>{e}</span></div>"

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>潜龙回测报告 {now}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d0e10;color:#b4b6b8;font-family:sans-serif;padding:20px;max-width:1200px;margin:0 auto}}
h1{{color:#e3e3e3;border-bottom:1px solid #2a2c30;padding-bottom:10px;margin-bottom:20px}}
h2{{color:#e3e3e3;margin:20px 0 10px}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}}
th,td{{padding:6px 10px;text-align:center;border-bottom:1px solid #1a1c20}}
th{{color:#888;font-weight:500;background:#141619}}
td{{color:#e3e3e3}}
.kpi-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0}}
.kpi{{background:#141619;border:1px solid #2a2c30;border-radius:6px;padding:15px;text-align:center}}
.kpi span{{display:block;font-size:22px;font-weight:700;color:#409eff;margin-top:6px}}
.green{{color:#00b96b}} .red{{color:#FF4051}}
.desc{{font-size:11px;color:#888;margin-top:2px}}
</style></head><body>
<h1>🐉 潜龙回测报告 <span style="font-size:14px;color:#888">生成: {now}</span></h1>

<h2>📊 绩效总览</h2>
<div class="kpi-row">{perf}</div>

<h2>🧬 因子IC周期表</h2>
<p class="desc">Spearman截面IC, 120天 × 500只, 81交易日</p>
<table><thead><tr>
<th>因子</th><th>方向</th><th>IC 1d</th><th>IC 3d</th><th>IC 5d</th><th>IC 7d</th><th>IC 10d</th><th>IC 15d</th><th>IC 20d</th>
</tr></thead><tbody>{ic_rows}</tbody></table>

<p class="desc">生成时间: {now} | 数据源: full_market_ic_report.json | 方法: Spearman cross-sectional</p>
</body></html>"""

    path = os.path.join(REPORT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告: {path}")
    return path


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="潜龙回测报告生成器")
    p.add_argument("--ic", action="store_true", default=True, help="包含IC周期表")
    p.add_argument("--perf", action="store_true", default=True, help="包含绩效总览")
    p.add_argument("--days", type=int, default=30, help="权益曲线天数")
    p.add_argument("--output", type=str, default="ic_report.html", help="输出文件名")
    args = p.parse_args()
    path = generate(filename=args.output)
    os.startfile(path) if os.name == 'nt' else None
