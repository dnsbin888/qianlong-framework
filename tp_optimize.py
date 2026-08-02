"""TP optimization - grid search on 3-tier trailing take-profit parameters
Usage: python D:\quant_framework\tp_optimize.py
"""
import sys, os, json, itertools, numpy as np, time
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

# ═══════════════════════════════════════
# 信号源: 从 qmt_trade_config.json 取信号
# ═══════════════════════════════════════
SIGNAL_PATH = r"D:\quant_web\data\qmt_trade_config.json"
STOCK_DATA_PATH = r"D:\quant_web\stock_data.parquet"


def load_signals():
    """从 auto_trade_plan.json 取信号 (有close价格)"""
    plan_path = r"D:\quant_web\data\auto_trade_plan.json"
    cfg_path = r"D:\quant_web\data\qmt_trade_config.json"

    plan = json.load(open(plan_path, encoding="utf-8")) if os.path.exists(plan_path) else {"stocks": {}}
    ml_cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}

    signals = []
    for sym, info in plan.get("stocks", {}).items():
        if not sym.startswith(('sh', 'sz')):
            continue
        ml = ml_cfg.get(sym, {})
        lgbm = ml.get("lgbm", 0) or 0
        xgb = ml.get("xgb", 0) or 0
        cb = ml.get("cb", 0) or 0
        best = max(lgbm, xgb, cb)
        lv = 5 if best >= 90 else 4 if best >= 80 else 3 if best >= 70 else 2 if best >= 60 else 1
        close = info.get("close", 0)
        if close <= 0:
            continue
        signals.append({
            "symbol": sym,
            "score": best,
            "buy_signal": lv,
            "close": close,
            "enabled": info.get("enabled", False),
            "pos_pct": info.get("max_position_pct", 0),
        })
    return sorted(signals, key=lambda s: -s["score"])


def load_price_history(signals, lookback=120):
    """使用系统标准data_loader加载价格历史"""
    try:
        from data_loader import load_stock_data_cache
        stock_data = load_stock_data_cache(STOCK_DATA_PATH, keep_days=lookback)
        if not stock_data:
            return None
        price_data = {}
        for sig in signals:
            sym = sig["symbol"]
            df = stock_data.get(sym)
            if df is not None and len(df) >= 20:
                price_data[sym] = df["close"].values[-lookback:]
        return price_data if price_data else None
    except Exception as e:
        print(f"  价格加载异常: {e}")
        return None


# ═══════════════════════════════════════
# 退出模拟器: 给定价格序列 + TP参数, 算出退出价和收益
# ═══════════════════════════════════════

def simulate_exit(entry_price, price_series, tp_config):
    """模拟移动止盈退出

    Args:
        entry_price: 买入价
        price_series: 买入后每日收盘价 (numpy array)
        tp_config: {tp1_profit, tp1_trail, tp1_sell, tp1_stop,
                    tp2_profit, tp2_trail, tp2_sell, tp2_stop,
                    tp3_profit, tp3_trail, tp3_sell, tp3_stop,
                    hard_stop, soft_stop}

    Returns:
        {exit_price, exit_reason, pnl_pct, hold_days, remaining_pct}
    """
    peak = entry_price
    remaining = 1.0
    sold_pct = 0.0
    exit_info = {"exit_price": entry_price, "exit_reason": "持有到期",
                 "pnl_pct": 0, "hold_days": len(price_series), "remaining_pct": 1.0}

    for i, p in enumerate(price_series):
        if p <= 0:
            continue
        pnl = (p / entry_price - 1) * 100
        peak_pnl = (peak / entry_price - 1) * 100

        # 全局硬止损
        if pnl <= tp_config["hard_stop"] * 100:
            remaining = 0
            exit_info = {"exit_price": p, "exit_reason": f"硬止损({pnl:.1f}%)",
                         "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": 0}
            break

        # 全局软止损
        if pnl <= tp_config["soft_stop"] * 100:
            sell = min(0.5, remaining)
            remaining -= sell
            sold_pct += sell
            exit_info = {"exit_price": p, "exit_reason": f"软止损({pnl:.1f}%)",
                         "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
            if remaining <= 0:
                break

        # TP3: 涨≥10%, 回落3%全清
        if peak_pnl >= tp_config["tp3_profit"] * 100:
            if pnl <= peak_pnl - abs(tp_config["tp3_trail"]) * 100:
                sell = tp_config["tp3_sell"]
                remaining -= sell
                sold_pct += sell
                exit_info = {"exit_price": p, "exit_reason": f"TP3回落(峰值{peak_pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
                if remaining <= 0:
                    break
            # TP3止损
            if pnl <= tp_config["tp3_stop"] * 100:
                remaining = 0
                exit_info = {"exit_price": p,
                             "exit_reason": f"TP3止损(峰值{peak_pnl:.1f}%→{pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": 0}
                break

        # TP2: 涨≥7%, 回落2%卖1/3
        elif peak_pnl >= tp_config["tp2_profit"] * 100:
            if pnl <= peak_pnl - abs(tp_config["tp2_trail"]) * 100:
                sell = tp_config["tp2_sell"]
                remaining -= sell
                sold_pct += sell
                exit_info = {"exit_price": p, "exit_reason": f"TP2回落(峰值{peak_pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
            # TP2止损
            if pnl <= tp_config["tp2_stop"] * 100:
                remaining = 0 if tp_config.get("tp2_stop_action") != "卖半仓" else remaining * 0.5
                exit_info = {"exit_price": p,
                             "exit_reason": f"TP2止损({pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
                if remaining <= 0:
                    break

        # TP1: 涨≥5%, 回落1%卖1/3
        elif peak_pnl >= tp_config["tp1_profit"] * 100:
            if pnl <= peak_pnl - abs(tp_config["tp1_trail"]) * 100:
                sell = tp_config["tp1_sell"]
                remaining -= sell
                sold_pct += sell
                exit_info = {"exit_price": p, "exit_reason": f"TP1回落(峰值{peak_pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
            # TP1止损: 卖半仓
            if pnl <= tp_config["tp1_stop"] * 100:
                sell_half = remaining * 0.5
                remaining -= sell_half
                exit_info = {"exit_price": p,
                             "exit_reason": f"TP1止损({pnl:.1f}%)",
                             "pnl_pct": pnl, "hold_days": i + 1, "remaining_pct": remaining}
                if remaining <= 0:
                    break

        # 更新峰值
        if p > peak:
            peak = p

    # 计算最终收益
    total_pnl = sold_pct * (exit_info["pnl_pct"] / 100) * entry_price
    if remaining > 0:
        final_val = remaining * price_series[-1]
        total_pnl += final_val - remaining * entry_price
    exit_info["total_pnl_pct"] = round((total_pnl / entry_price) * 100, 2)

    return exit_info


# ═══════════════════════════════════════
# 跑单组参数
# ═══════════════════════════════════════

def run_simulation(tp_config, signals, price_data=None, n_days=60):
    """对信号列表模拟退出, 汇总统计"""
    results = []
    for sig in signals[:30]:  # Top 30
        entry = sig["close"]
        if entry <= 0:
            continue
        sym = sig["symbol"]

        # 用真实价格或随机模拟
        if price_data and sym in price_data:
            prices = price_data[sym][-n_days:]
        else:
            # 随机模拟: 基于波动率生成60天价格路径
            vol = 0.02
            np.random.seed(hash(sym) % 2**31)
            rets = np.random.normal(0.0005, vol, n_days)
            prices = entry * np.cumprod(1 + rets)

        if len(prices) < 5:
            continue

        exit_info = simulate_exit(entry, prices, tp_config)
        exit_info["symbol"] = sym
        exit_info["entry"] = entry
        exit_info["score"] = sig["score"]
        exit_info["buy_signal"] = sig["buy_signal"]
        results.append(exit_info)

    if not results:
        return {}

    pnls = [r["total_pnl_pct"] for r in results]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return {
        "n": len(results),
        "win_rate": len(wins) / len(pnls) * 100 if pnls else 0,
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "profit_factor": abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 999,
        "avg_pnl": np.mean(pnls),
        "max_win": max(pnls) if pnls else 0,
        "max_loss": min(pnls) if pnls else 0,
        "avg_hold_days": np.mean([r["hold_days"] for r in results]),
        "exit_reasons": {r["exit_reason"]: sum(1 for x in results if x["exit_reason"] == r["exit_reason"])
                         for r in results},
    }


# ═══════════════════════════════════════
# 主程序: 网格搜索
# ═══════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  TP参数优化 — 三级移动止盈网格搜索")
    print("=" * 70)

    # 加载信号
    signals = load_signals()
    print(f"\n[1] 信号: {len(signals)} 只 (Top 30 用于回测)")
    for s in signals[:5]:
        print(f"  {s['symbol']} score={s['score']:.0f} Lv{s['buy_signal']} close={s['close']:.2f}")

    # 加载价格数据
    print("\n[2] 加载价格数据...")
    price_data = load_price_history(signals)
    if price_data:
        print(f"  真实数据: {len(price_data)} 只")
    else:
        print("  使用随机模拟 (基于波动率)")

    # ═══ 定义测试组合 ═══
    # 基准 (当前 master)
    baseline = {
        "tp1_profit": 0.05, "tp1_trail": -0.01, "tp1_sell": 0.33, "tp1_stop": -0.03,
        "tp2_profit": 0.07, "tp2_trail": -0.02, "tp2_sell": 0.33, "tp2_stop": -0.05,
        "tp3_profit": 0.10, "tp3_trail": -0.03, "tp3_sell": 1.0,  "tp3_stop": -0.07,
        "hard_stop": -0.055, "soft_stop": -0.03,
    }

    # 测试组合
    configs = {
        "基准(master)": baseline,
        # 变体: TP3卖1/3而非全清
        "TP3卖1/3": {**baseline, "tp3_sell": 0.34},
        # 变体: TP触发阈值降低
        "TP阈值降低": {**baseline,
            "tp1_profit": 0.03, "tp2_profit": 0.05, "tp3_profit": 0.08},
        # 变体: 止损收紧
        "止损收紧": {**baseline,
            "tp1_stop": -0.02, "tp2_stop": -0.04, "tp3_stop": -0.06, "hard_stop": -0.04},
        # 变体: 止损放宽
        "止损放宽": {**baseline,
            "tp1_stop": -0.05, "tp2_stop": -0.07, "tp3_stop": -0.10, "hard_stop": -0.08},
        # 变体: 回落敏感 (更早锁利润)
        "回落敏感": {**baseline,
            "tp1_trail": -0.005, "tp2_trail": -0.01, "tp3_trail": -0.015},
        # 变体: 回落宽松 (给更多空间)
        "回落宽松": {**baseline,
            "tp1_trail": -0.02, "tp2_trail": -0.03, "tp3_trail": -0.05},
        # 变体: sell_ratio激进 (每级卖更多)
        "卖出激进": {**baseline,
            "tp1_sell": 0.50, "tp2_sell": 0.50},
        # 组合: 紧止损+低阈值+敏感回落
        "保守组合": {
            "tp1_profit": 0.03, "tp1_trail": -0.005, "tp1_sell": 0.50, "tp1_stop": -0.02,
            "tp2_profit": 0.05, "tp2_trail": -0.01,  "tp2_sell": 0.50, "tp2_stop": -0.04,
            "tp3_profit": 0.08, "tp3_trail": -0.015, "tp3_sell": 1.0,  "tp3_stop": -0.06,
            "hard_stop": -0.04, "soft_stop": -0.02,
        },
        # 组合: 宽止损+高阈值+宽松回落
        "激进组合": {
            "tp1_profit": 0.07, "tp1_trail": -0.02, "tp1_sell": 0.25, "tp1_stop": -0.05,
            "tp2_profit": 0.10, "tp2_trail": -0.03, "tp2_sell": 0.25, "tp2_stop": -0.07,
            "tp3_profit": 0.15, "tp3_trail": -0.05, "tp3_sell": 0.34, "tp3_stop": -0.10,
            "hard_stop": -0.08, "soft_stop": -0.05,
        },
    }

    # ═══ 跑回测 ═══
    print(f"\n[3] 回测 {len(configs)} 组参数...")
    print("-" * 70)

    results = {}
    for name, cfg in configs.items():
        t0 = time.time()
        r = run_simulation(cfg, signals, price_data)
        results[name] = r
        elapsed = (time.time() - t0) * 1000
        if r:
            print(f"\n  [{name}] ({elapsed:.0f}ms)")
            print(f"    胜率: {r['win_rate']:.1f}%  |  平均盈亏: {r['avg_pnl']:+.2f}%")
            print(f"    盈亏比: {r['profit_factor']:.2f}  |  最大赢: {r['max_win']:+.2f}%  |  最大亏: {r['max_loss']:+.2f}%")
            print(f"    平均持有: {r['avg_hold_days']:.0f}天")
            top_reasons = sorted(r["exit_reasons"].items(), key=lambda x: -x[1])[:3]
            print(f"    退出原因: {' | '.join(f'{k}({v}次)' for k,v in top_reasons)}")

    # ═══ 排名 ═══
    print("\n" + "=" * 70)
    print("  综合排名 (按 胜率×盈亏比)")
    print("=" * 70)
    ranked = sorted(
        [(name, r) for name, r in results.items() if r],
        key=lambda x: x[1]["win_rate"] * x[1]["profit_factor"],
        reverse=True
    )
    print(f"  {'排名':<4} {'参数组':<16} {'胜率':>6} {'盈亏比':>7} {'平均盈亏':>8} {'最大亏':>7} {'持有天':>6}")
    print("-" * 70)
    for i, (name, r) in enumerate(ranked, 1):
        flag = "⭐" if name == "基准(master)" else "  "
        print(f"  {i:<4} {flag} {name:<14} {r['win_rate']:>5.1f}% {r['profit_factor']:>6.2f} "
              f"{r['avg_pnl']:>+7.2f}% {r['max_loss']:>+6.2f}% {r['avg_hold_days']:>5.0f}天")

    # ═══ 建议 ═══
    if ranked:
        best_name, best = ranked[0]
        base_name = "基准(master)"
        base = results.get(base_name, {})
        print(f"\n{'='*70}")
        print(f"  优化建议")
        print(f"{'='*70}")
        print(f"  最优: {best_name} (胜率{best['win_rate']:.1f}% PF={best['profit_factor']:.2f})")
        if base:
            print(f"  基准: {base_name} (胜率{base['win_rate']:.1f}% PF={base['profit_factor']:.2f})")
            delta_win = best['win_rate'] - base['win_rate']
            delta_pf = best['profit_factor'] - base['profit_factor']
            if delta_win > 0 or delta_pf > 0:
                print(f"  提升: 胜率+{delta_win:+.1f}%, PF+{delta_pf:+.2f}")
                print(f"\n  建议: 将 {best_name} 的参数纳入 trade_config_master.json")
            else:
                print(f"  结论: 基准参数已是最优, 无需调整")
    print()
