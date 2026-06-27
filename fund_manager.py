"""资金管理 — 出入金记录+复利计算+资金曲线"""

import json, os
from datetime import datetime

FUND_FILE = r"D:\quant_framework\fund_flow.json"


def load() -> list:
    if os.path.exists(FUND_FILE):
        try:
            return json.load(open(FUND_FILE, "r", encoding="utf-8"))
        except: pass
    return []


def save(records: list):
    os.makedirs(os.path.dirname(FUND_FILE), exist_ok=True)
    json.dump(records, open(FUND_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def deposit(amount: float, note: str = ""):
    records = load()
    records.append({"date": datetime.now().strftime("%Y-%m-%d"), "type": "deposit", "amount": amount, "note": note})
    save(records)


def withdraw(amount: float, note: str = ""):
    records = load()
    records.append({"date": datetime.now().strftime("%Y-%m-%d"), "type": "withdraw", "amount": -amount, "note": note})
    save(records)


def overview() -> dict:
    """返回资金概览"""
    records = load()
    total_deposit = sum(r["amount"] for r in records if r["type"] == "deposit")
    total_withdraw = sum(abs(r["amount"]) for r in records if r["type"] == "withdraw")
    net = total_deposit - total_withdraw

    # 当前总资产（从paper_account读取）
    current_equity = net  # 默认=净入金
    paper_file = r"D:\quant_framework\paper_account.json"
    if os.path.exists(paper_file):
        paper = json.load(open(paper_file, "r"))
        current_equity = paper.get("total_equity", net)

    real_return = (current_equity - net) / max(net, 1) * 100 if net > 0 else 0

    # 资金曲线（简化：每月末净值）
    curve = []
    for r in records:
        curve.append({"date": r["date"], "value": current_equity if r == records[-1] else net})

    return {
        "net_deposit": round(net, 2),
        "total_deposit": round(total_deposit, 2),
        "total_withdraw": round(total_withdraw, 2),
        "current_equity": round(current_equity, 2),
        "real_return": round(real_return, 2),
        "flow_count": len(records),
        "fund_curve": curve[-60:],
    }
