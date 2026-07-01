"""策略构建器 (蓝图 v3.0 Phase 3)

用户选因子 → 设阈值 → 回测 → 部署。
模拟盘自由实验，实盘需老板审批。
"""

import json, os, sys, logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

STRATEGIES_FILE = r"D:\quant_framework\user_customizations\user_strategies.json"
REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"

# 策略状态: draft → backtested → sim → real
VALID_STATUS = ["draft", "backtested", "sim", "real", "paused", "retired"]


def create_strategy(name: str, factors: list[dict], trigger: dict,
                    stop_loss: float = -0.03, take_profit: list = None,
                    hold_days: int = 5, target: str = "sim") -> dict:
    """创建策略。

    Args:
        name: 策略名
        factors: [{name, weight, threshold}, ...]
        trigger: {type: "weighted_sum", min_score: 70}
        stop_loss: 止损 (如 -0.03)
        take_profit: [0.05, 0.07, 0.12]
        hold_days: 持仓天数
        target: "draft" | "sim" | "real"

    Returns:
        {success, message, strategy}
    """
    if take_profit is None:
        take_profit = [0.05, 0.07, 0.12]

    if target not in VALID_STATUS:
        return {"success": False, "message": f"无效状态: {target}"}
    if target == "real":
        return {"success": False, "message": "实盘部署需老板审批，请先设为 sim"}

    data = _load_strategies()
    strategies = data.get("strategies", [])

    if any(s.get("name") == name for s in strategies):
        return {"success": False, "message": f"策略 {name} 已存在"}

    strategy = {
        "name": name,
        "display_name": name,
        "type": "builder",
        "factors": factors,
        "trigger": trigger,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "hold_days": hold_days,
        "status": target,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "backtest": None,
    }
    strategies.append(strategy)
    data["strategies"] = strategies
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save_strategies(data)

    # G1: 注册到审批系统
    try:
        from strategy_approval import register_strategy
        register_strategy(name)
    except ImportError: pass

    logger.info(f"策略已创建: {name} ({target})")
    return {"success": True, "strategy": strategy}


def run_backtest(name: str, days: int = 90, sample: int = 300, walk_forward: bool = True) -> dict:
    """策略回测 (行业对标: Walk-Forward 滚动窗口验证)。

    Args:
        walk_forward: True=Walk-Forward样本外, False=传统全区间
    """
    data = _load_strategies()
    strategy = next((s for s in data.get("strategies", []) if s["name"] == name), None)
    if not strategy:
        return {"success": False, "message": "策略不存在"}

    factors_cfg = strategy.get("factors", [])
    if not factors_cfg:
        return {"success": False, "message": "策略未配置因子"}

    try:
        from factor_registry import get_all_compute_fns
        compute_fns = get_all_compute_fns()
    except Exception:
        compute_fns = {}

    try:
        import numpy as np, pandas as pd, sys
        sys.path.insert(0, r"D:\quant_web")
        from data_loader import load_stock_data_from_cache
        stock_data = load_stock_data_from_cache()
        if not stock_data:
            # 兜底: 旧路径
            import gzip, pickle
            sp = r"D:\quant_web\stock_data.pkl.gz"
            if not os.path.exists(sp): sp = r"D:\quant_web\stock_data.pkl"
            stock_data = pickle.load(gzip.open(sp, "rb")) if sp.endswith(".gz") else pickle.load(open(sp, "rb"))
    except Exception as e:
        return {"success": False, "message": f"数据加载失败: {e}"}

    all_syms = sorted(stock_data.keys())
    import random as _rnd; _rnd.seed(42)
    best_df = max(stock_data.values(), key=lambda df: len(df) if isinstance(df, pd.DataFrame) else 0)
    all_dates = sorted(set(str(ts)[:10] for ts in best_df.index))[-days:]
    print(f"[BT] stocks={len(stock_data)}, all_syms={len(all_syms)}, dates={len(all_dates)}, sample={sample}")
    if len(all_dates) < 30:
        return {"success": False, "message": f"交易日不足 ({len(all_dates)}天), 请检查数据"}

    def _evaluate_window(date_list, trigger_min):
        """在一个日期列表上评估策略，返回日收益序列。"""
        daily = []
        for di, date_str in enumerate(date_list):
            pool = []
            for s in all_syms[:sample*2]:
                if s in stock_data:
                    try: stock_data[s].index.get_loc(pd.Timestamp(date_str)); pool.append(s)
                    except KeyError: continue
                if len(pool) >= sample: break
            if len(pool) < 30: continue
            scores = []; checked = 0; passed = 0
            for sym in pool:
                df = stock_data[sym]
                try: idx = df.index.get_loc(pd.Timestamp(date_str))
                except KeyError: continue
                if idx < 20: continue
                past = df.iloc[max(0,idx-60):idx+1]
                if len(past) < 20: continue
                total_score = 0; total_weight = 0; valid_count = 0
                for fc in factors_cfg:
                    fn = compute_fns.get(fc["name"])
                    if not fn: continue
                    val = fn(past)
                    if val is None: continue
                    w = fc.get("weight", 1.0)
                    total_weight += w
                    total_score += val * w
                    valid_count += 1
                checked += 1
                if valid_count < 2: continue
                if total_weight > 0:
                    total_score = total_score / total_weight
                if total_score < trigger_min: continue
                if idx + 5 >= len(df): continue
                fwd = (float(df.iloc[idx+5]["close"]) - float(df.iloc[idx]["close"])) / max(float(df.iloc[idx]["close"]), 0.01)
                scores.append(fwd)
                if idx + 5 >= len(df): continue
                fwd = (float(df.iloc[idx+5]["close"]) - float(df.iloc[idx]["close"])) / max(float(df.iloc[idx]["close"]), 0.01)
                scores.append(fwd); passed += 1
            checked += 1
            if di == 0: print(f"[BT] date={date_str} pool={len(pool)} checked={checked} passed={passed}")
            if scores: daily.append(float(np.mean(scores)))
        return daily

    # Phase 6c: 并行窗口评估
    def _evaluate_windows_parallel(date_chunks, trigger_min_val, max_workers=3):
        """使用进程池并行评估多个窗口"""
        from concurrent.futures import ProcessPoolExecutor
        # 打包参数: (窗口日期, 触发阈值)
        tasks = [(chunk, trigger_min_val) for chunk in date_chunks]
        with ProcessPoolExecutor(max_workers=min(len(tasks), max_workers)) as pool:
            futures = [pool.submit(_evaluate_window, t[0], t[1]) for t in tasks]
            return [f.result() for f in futures]

    trigger_min = strategy.get("trigger", {}).get("min_score", 60)

    if walk_forward:
        # P8-2: Purged Walk-Forward (Prado标准: 训练-测试间embargo)
        n = len(all_dates)
        w_size = n // 3
        purge = max(5, w_size // 20) if n >= 90 else 0  # 数据足够才purge
        if w_size < 30:
            walk_forward = False  # 降级
        if purge > 0:
            print(f"[StrategyBuilder] Purged WF: {n}d → 3窗×{w_size}d, purge={purge}d")

    if walk_forward:
        oos_returns = []
        # Phase 6c: 并行执行3个窗口
        try:
            test_chunks = []
            for w in range(3):
                train_end = (w + 1) * w_size - purge
                test_start = train_end + purge
                test_end = min(test_start + w_size // 2, n)
                if test_end <= test_start: continue
                # 训练段 (先串行预热, 确保数据加载)
                _evaluate_window(all_dates[:max(1,train_end)], trigger_min)
                test_chunks.append(all_dates[test_start:test_end])
            if len(test_chunks) >= 2:
                results = _evaluate_windows_parallel(test_chunks, trigger_min)
                for r in results: oos_returns.extend(r)
            else:
                for chunk in test_chunks:
                    oos_returns.extend(_evaluate_window(chunk, trigger_min))
        except Exception as _pe:
            print(f"[StrategyBuilder] 并行回退到串行: {_pe}")
            for w in range(3):
                train_end = (w + 1) * w_size - purge
                test_start = train_end + purge
                test_end = min(test_start + w_size // 2, n)
                if test_end <= test_start: continue
                _evaluate_window(all_dates[:max(1,train_end)], trigger_min)
                oos_returns.extend(_evaluate_window(all_dates[test_start:test_end], trigger_min))

        if len(oos_returns) < 10:
            walk_forward = False  # 降级到全区间

    if not walk_forward:
        oos_returns = _evaluate_window(all_dates, trigger_min)

    if len(oos_returns) < 10:
        return {"success": False, "message": f"回测天数不足 ({len(oos_returns)}天)"}

    avg_ret = float(np.mean(oos_returns))
    std_ret = float(np.std(oos_returns))
    sharpe = avg_ret / max(std_ret, 0.0001) * np.sqrt(252)
    win_rate = sum(1 for r in oos_returns if r > 0) / len(oos_returns)
    max_dd = _calc_max_dd(oos_returns)

    # 稳定性: 收益序列的自相关越低越好(说明不是运气)
    stability = "🟢 稳定" if std_ret < 0.02 else ("🟡 中等" if std_ret < 0.04 else "🔴 波动大")

    result = {
        "success": True,
        "method": "Walk-Forward" if walk_forward else "全区间",
        "backtest": {
            "days": len(oos_returns),
            "avg_return_5d": round(avg_ret * 100, 2),
            "annualized_sharpe": round(float(sharpe), 2),
            "win_rate": round(float(win_rate), 2),
            "max_drawdown": round(max_dd * 100, 2),
            "stability": stability,
            "method": "Walk-Forward 样本外" if walk_forward else "60天全区间",
        }
    }

    for s in data["strategies"]:
        if s["name"] == name:
            s["backtest"] = result["backtest"]
            s["status"] = "backtested"
            break
    _save_strategies(data)
    return result


def _calc_max_dd(returns: list) -> float:
    """计算最大回撤。"""
    peak = -999; dd = 0
    for r in returns:
        peak = max(peak, r) if peak == -999 else peak + r if peak != -999 else r
        dd = min(dd, 0 if peak == -999 else peak - abs(r))
    return abs(dd) / max(abs(peak), 0.01) if peak > 0 else 0


def get_active_user_strategies() -> list[dict]:
    """获取所有状态为 sim (模拟盘) 的用户构建策略。"""
    data = _load_strategies()
    return [s for s in data.get("strategies", [])
            if s.get("status") == "sim" and s.get("type") == "builder"]


def evaluate_strategy(strategy: dict, stock_code: str, compute_fns: dict, stock_data: dict = None) -> dict | None:
    """用用户策略评估单只股票, 返回信号或None。

    Args:
        strategy: 策略定义 (含 factors, trigger, stop_loss 等)
        stock_code: 股票代码
        compute_fns: {factor_name: compute_function} 字典
        stock_data: 预加载的 {symbol: DataFrame}（可选，传入则跳过加载）

    Returns:
        None 或 {strategy, signal, score, entry_price, stop_loss, take_profit, reason}
    """
    factors_cfg = strategy.get("factors", [])
    if not factors_cfg or not compute_fns:
        return None

    # 加载股票数据（如果调用方没传）
    import pandas as pd
    if stock_data is None:
        try:
            import numpy as np, sys
            sys.path.insert(0, r"D:\quant_web")
            from data_loader import load_stock_data_from_cache
            stock_data = load_stock_data_from_cache()
            if not stock_data:
                import gzip, pickle, os
                sp = r"D:\quant_web\stock_data.pkl.gz"
                if not os.path.exists(sp): sp = r"D:\quant_web\stock_data.pkl"
                stock_data = pickle.load(gzip.open(sp, "rb")) if sp.endswith(".gz") else pickle.load(open(sp, "rb"))
        except Exception:
            return None

    # 代码格式适配
    df = None
    for key in [stock_code, "sh"+stock_code, "sz"+stock_code, stock_code.replace("sh","").replace("sz","")]:
        if key in stock_data:
            df = stock_data[key]
            break
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 60:
        return None

    past = df.iloc[-60:]
    if len(past) < 20:
        return None

    # 计算加权分
    total_weight = 0
    weighted_score = 0
    all_pass = True

    # 因子方向: 从registry获取direction, short因子分数取反
    import json as _j2, os as _os2
    _factor_dir = {}
    try:
        _reg = _j2.load(open(r"D:\quant_framework\factor_registry.json","r",encoding="utf-8"))
        for _f in _reg.get("factors",[]):
            _factor_dir[_f["name"]] = _f.get("direction","long")
    except: pass
    for fc in factors_cfg:
        fn = compute_fns.get(fc["name"])
        if not fn:
            continue
        val = fn(past)
        if val is None:
            continue
        # Short因子: 高分=利空。映射: score → 100-score (保留排名, 方向翻转)
        if _factor_dir.get(fc["name"]) == "short":
            val = max(0, 100 - val)
        if val < fc.get("threshold", 0):
            all_pass = False
            continue
        w = fc.get("weight", 50)
        weighted_score += val * w
        total_weight += w

    if not all_pass or total_weight == 0:
        return None

    final_score = weighted_score / total_weight
    trigger = strategy.get("trigger", {})
    min_score = trigger.get("min_score", 60)

    if final_score < min_score:
        return None

    entry_price = float(df.iloc[-1]["close"])
    tp = strategy.get("take_profit", [0.05, 0.07, 0.12])
    return {
        "strategy": strategy["name"],
        "signal": "buy",
        "score": round(final_score, 2),
        "entry_price": entry_price,
        "stop_loss": round(entry_price * (1 + strategy.get("stop_loss", -0.03)), 2),
        "take_profit": [round(entry_price * (1 + t), 2) for t in tp],
        "reason": f"用户策略:{strategy['name']} 得分{final_score:.0f}",
    }


def deploy_strategy(name: str, target: str) -> dict:
    """部署策略: sim → 模拟盘, real → 实盘(需审批)。"""
    if target not in ("sim", "real"):
        return {"success": False, "message": f"无效目标: {target}"}
    if target == "real":
        return {"success": False, "message": "实盘部署需老板审批，请先在健康监控页确认"}

    data = _load_strategies()
    for s in data.get("strategies", []):
        if s["name"] == name:
            s["status"] = "sim"
            _save_strategies(data)
            # 同步审批状态
            try:
                from strategy_approval import register_strategy
                register_strategy(name)
                apr = json.load(open(r"D:\quant_framework\strategy_approvals.json","r"))
                apr.setdefault("strategies",{})[name]={"state":"sim_running","state_entered_at":datetime.now().isoformat(),"history":[],"performance":{}}
                json.dump(apr,open(r"D:\quant_framework\strategy_approvals.json","w"),ensure_ascii=False,indent=2)
            except: pass
            return {"success": True, "message": f"{name} 已部署到模拟盘 (10秒后生效)"}
    return {"success": False, "message": "策略不存在"}


def _load_strategies() -> dict:
    try:
        if os.path.exists(STRATEGIES_FILE):
            with open(STRATEGIES_FILE, "r") as f:
                return json.load(f)
    except Exception: pass
    return {"strategies": []}


def _save_strategies(data: dict):
    os.makedirs(os.path.dirname(STRATEGIES_FILE), exist_ok=True)
    with open(STRATEGIES_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
