"""板后接力预选池 v1.0 (P1-6)
每日收盘后扫描昨涨停股, 追加到 auto_trade_plan.json 供 QMT 次日监控
用法: python pre_cache_limit_up.py
建议: 每日 15:10 自动执行
"""
import sys, os, json, numpy as np
from datetime import datetime

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"

def get_limit_pct(code):
    c = code.replace('sh','').replace('sz','').replace('bj','')
    if c.startswith(('30','688')): return 0.20
    if c.startswith(('8','4')): return 0.30
    return 0.10

def scan_yesterday_limit_up():
    """扫描昨天涨停股"""
    try:
        from data_loader import load_stock_data_cache
        sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=3)
    except Exception as e:
        print(f"[LimitUp] 数据加载失败: {e}")
        return []

    limit_up_stocks = []
    for sym, df in sd.items():
        try:
            c = df['close'].values
            if len(c) < 2:
                continue
            prev_close = c[-2]
            close = c[-1]
            if prev_close <= 0:
                continue
            limit_pct = get_limit_pct(sym)
            if close >= round(prev_close * (1 + limit_pct), 2) * 0.995:
                limit_up_stocks.append(sym)
        except Exception:
            continue

    print(f"[LimitUp] 昨涨停: {len(limit_up_stocks)}只")
    return limit_up_stocks

def add_to_plan(stocks):
    """将昨涨停股追加到 auto_trade_plan, 只监控板后接力信号"""
    if not os.path.exists(PLAN_PATH):
        print("[LimitUp] plan文件不存在")
        return 0

    with open(PLAN_PATH, encoding='utf-8') as f:
        plan = json.load(f)

    stocks_dict = plan.get("stocks", {})
    added = 0
    for sym in stocks:
        if sym not in stocks_dict:
            stocks_dict[sym] = {
                "enabled": False,  # 不自动买, 只监控信号
                "auto_reason": "板后接力预选池",
                "max_position_pct": 2,
                "min_ml_score": 0,
                "signal_types": ["板后接力"],
                "max_order_qty": 0,
            }
            added += 1
        else:
            # 已有: 追加板后接力到信号集
            sig = set(stocks_dict[sym].get("signal_types", []))
            sig.add("板后接力")
            stocks_dict[sym]["signal_types"] = list(sig)
            stocks_dict[sym]["auto_reason"] = stocks_dict[sym].get("auto_reason", "") + "+板后预选"

    plan["stocks"] = stocks_dict
    plan["global_limits"]["_limit_up_precache"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PLAN_PATH, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"[LimitUp] 新增{added}只, 总监控{len(stocks_dict)}只")
    return added

if __name__ == "__main__":
    stocks = scan_yesterday_limit_up()
    if stocks:
        add_to_plan(stocks)
    print("[LimitUp] 完成")
