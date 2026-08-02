"""策略守护 v1.0 — 自动停策略 (游资铁律: 连亏3天/20日Sharpe<0→关)
对标: 游资"错了就停" + 私募自动降级
"""
import json, os, numpy as np
from datetime import datetime, timedelta

REGISTRY = r"D:\quant_framework\strategy_registry.json"
PAPER = r"D:\quant_framework\paper_account.json"

def check_strategies():
    """检查所有策略, 不达标的自动暂停"""
    if not os.path.exists(REGISTRY): return
    reg = json.load(open(REGISTRY, encoding="utf-8"))

    # 读交易记录
    trades = []
    if os.path.exists(PAPER):
        pp = json.load(open(PAPER, encoding="utf-8"))
        trades = pp.get("trade_log", [])

    changed = False
    for s in reg.get("strategies", []):
        sid = s["id"]
        lc = s.get("lifecycle", "draft")
        if lc not in ("live", "paper_trading"): continue

        # 筛选该策略的交易(最近20日)
        cutoff = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        recent = [t for t in trades if t.get("strategy_id")==sid and t.get("date","")[:10] >= cutoff]
        sells = [t for t in recent if t.get("side")=="sell" and t.get("pnl") is not None]

        if len(sells) < 5: continue  # 样本不够

        pnls = [t["pnl"] for t in sells]
        sharpe = np.mean(pnls) / max(np.std(pnls), 0.01) * np.sqrt(252) if len(pnls)>=5 else 0

        # 检查连续亏损
        streak = 0
        for t in sorted(sells, key=lambda x: x.get("date",""), reverse=True):
            if t.get("pnl", 0) < 0: streak += 1
            else: break

        if sharpe < 0 or streak >= 5:
            old_lc = lc
            s["lifecycle"] = "degraded"
            print(f"[StrategyGuard] {s['name']}({sid}): {old_lc}→degraded (Sharpe={sharpe:.2f} 连亏{streak})")
            changed = True

    if changed:
        reg["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp = REGISTRY + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        try: os.chmod(REGISTRY, 0o666)
        except: pass
        os.replace(tmp, REGISTRY)


if __name__ == "__main__":
    check_strategies()
