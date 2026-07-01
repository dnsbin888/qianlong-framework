"""V1-5: 全市场因子重建 (蓝图 v3.0)

方法: 在全市场8963只DataFrame上计算三策略因子 → Spearman截面IC
解决: factors.db选择性偏差 (3.2%信号股 vs 全市场)
"""

import sys, os, json, gzip, pickle, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")

# QMT桥接 (模块级导入, 避免每次因子计算都import)
try:
    from qmt_factor_bridge import compute_qmt_factors as _qmt_factors, compute_qmt_composite as _qmt_composite
except ImportError:
    _qmt_factors, _qmt_composite = None, None

CONF = {
    "stock_data": r"D:\quant_web\stock_data.parquet",
    "fallback": r"D:\quant_web\stock_data.pkl.gz",
    "fallback2": r"D:\quant_web\stock_data.pkl",
    "output": r"D:\quant_framework\full_market_ic_report.json",
    "sample": 500,
    "days": 90,
    "ic_days": 90,  # P7-5: IC专用加载天数 (Web用60天, IC用90天)
    "windows": [1, 3, 5, 7, 10, 12, 15, 20],
    "min_days": 20,  # 修复: 60天窗口只有33个交易日, 原60导致全部跳过
}


def load_data(keep_days: int = None) -> dict[str, pd.DataFrame]:
    # P7-5: IC脚本独立加载更多数据 (Web 60天, IC 90天)
    if keep_days is None:
        keep_days = getattr(CONF, 'ic_days', 90)
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_cache
    p = CONF["stock_data"]
    if not os.path.exists(p):
        p = CONF.get("fallback", "") or CONF.get("fallback2", "")
    if os.path.exists(p):
        sd = load_stock_data_cache(p, keep_days=keep_days)
    else:
        sd = None
    if sd:
        sd = {k: v for k, v in sd.items() if not k.startswith(('sh000','sz399','bj'))}
        print(f"[V1-5] 过滤指数后: {len(sd)}只A股 ({keep_days}天)")
    return sd


def _factor_chase(df: pd.DataFrame) -> float | None:
    """追涨因子: 动量+量比+趋势 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values[-20:]
    v = df["volume"].values[-20:]
    if c[-1] <= 0:
        return None
    mom = (c[-1] - c[-6]) / max(c[-6], 0.01) if len(c) >= 6 else 0
    vm = np.mean(v)
    vol_r = v[-1] / max(vm, 1)
    ma5 = np.mean(c[-5:]) if len(c) >= 5 else c[-1]
    ma20 = np.mean(c)
    return min(40, max(0, mom * 400)) + min(30, max(0, (vol_r - 1) * 60)) + min(30, max(0, (ma5 / max(ma20, 0.01) - 1) * 500))


def _factor_low(df: pd.DataFrame) -> float | None:
    """低吸因子v4: 极简版 — 先验证IC, 再逐步收紧"""
    if len(df) < 20:
        return None
    c = df["close"].values
    v = df["volume"].values
    n = len(c)
    if c[-1] <= 0:
        return None

def _factor_low(df: pd.DataFrame) -> float | None:
    """低吸因子v5: 5日跌幅+当日反弹, 去掉type filter (对全A有效)"""
    if len(df) < 10:
        return None
    c = df["close"].values
    n = len(c)
    if c[-1] <= 0: return None

    chg5 = (c[-1] - c[-6]) / max(c[-6], 0.01) if n >= 6 else 0
    # 只要5日跌幅即可 (不要求当日反弹——反弹=加分项, 不是必要条件)
    if chg5 > 0: return None  # 前5日涨→不是低吸信号

    # 简单评分: 跌幅越大分越高
    return round(abs(chg5) * 100, 1)


# ═══ 旧体系对照因子 (全市场重新计算) ═══

def _factor_power_old(df: pd.DataFrame) -> float | None:
    """旧体系: power_score(最强评级) — 15子因子加权综合 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values[-60:] if len(df) >= 60 else df["close"].values
    v = df["volume"].values[-60:] if len(df) >= 60 else df["volume"].values
    h = df["high"].values[-60:] if len(df) >= 60 else c
    l = df["low"].values[-60:] if len(df) >= 60 else c
    if c[-1] <= 0:
        return None
    # 趋势
    ma20 = np.mean(c[-20:]) if len(c) >= 20 else c[-1]
    ma60 = np.mean(c[-60:]) if len(c) >= 60 else c[-1]
    trend = min(99, max(15, (c[-1] / max(ma20, 0.01) - 1) * 500 + 50))
    # 动量
    mom_5d = (c[-1] - c[-6]) / max(c[-6], 0.01) if len(c) >= 6 else 0
    momentum = min(99, max(15, mom_5d * 400 + 50))
    # 量比
    vol_ma = np.mean(v[-20:]) if len(v) >= 20 else v[-1]
    vol_ratio = v[-1] / max(vol_ma, 1)
    volume_score = min(99, max(15, (vol_ratio - 1) * 60 + 50))
    # RSI
    rets_arr = np.diff(c[-15:]) if len(c) >= 15 else np.zeros(14)
    rets = rets_arr[-14:] if len(rets_arr) >= 14 else rets_arr
    gains = np.mean(np.maximum(rets, 0))
    losses = np.mean(np.abs(np.minimum(rets, 0))) + 1e-9
    rsi_val = 100 - 100 / (1 + gains / losses)
    rsi_score = min(99, max(15, (50 - rsi_val) * 1.5 + 50))
    # ATR
    tr = np.maximum(h[-14:] - l[-14:], np.abs(c[-14:] - np.roll(c[-14:], 1))) if len(c) >= 15 else np.ones(14)
    atr = np.mean(tr) / max(c[-1], 0.01) if len(tr) >= 14 else 0.02
    atr_score = min(99, max(15, 50 - atr * 200))
    # MA多头 (牛线)
    ma5 = np.mean(c[-5:]) if len(c) >= 5 else c[-1]
    ma_bull = 80 if c[-1] > ma5 > ma20 else 40
    # 加权
    power = (trend * 1.5 + momentum * 1.2 + volume_score * 1.3 + rsi_score * 0.7 +
             atr_score * 0.7 + ma_bull * 1.5) / 7.0
    return min(99, max(15, power))


def _factor_bull_old(df: pd.DataFrame) -> float | None:
    """旧体系: bull_line(牛线突破) — MA5>MA20>MA60 + 放量 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values
    v = df["volume"].values
    if c[-1] <= 0:
        return None
    ma5 = np.mean(c[-5:]) if len(c) >= 5 else c[-1]
    ma20 = np.mean(c[-20:]) if len(c) >= 20 else c[-1]
    ma60 = np.mean(c[-60:]) if len(c) >= 60 else c[-1]
    score = 50.0
    if c[-1] > ma5 > ma20:
        score += 20  # 短期多头
    if ma20 > ma60:
        score += 15  # 中期多头
    vol_ratio = v[-1] / max(np.mean(v[-20:]), 1) if len(v) >= 20 else 1
    if vol_ratio > 1.2:
        score += 15  # 放量突破
    return min(100, score)


def _factor_trend_old(df: pd.DataFrame) -> float | None:
    """旧体系: trend_score — MA排列强度 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values
    if c[-1] <= 0:
        return None
    ma5 = np.mean(c[-5:]) if len(c) >= 5 else c[-1]
    ma10 = np.mean(c[-10:]) if len(c) >= 10 else c[-1]
    ma20 = np.mean(c[-20:]) if len(c) >= 20 else c[-1]
    ma60 = np.mean(c[-60:]) if len(c) >= 60 else c[-1]
    # MA排列: 5>10>20>60 = 满分
    s = 50
    if c[-1] > ma5: s += 10
    if ma5 > ma10: s += 10
    if ma10 > ma20: s += 10
    if ma20 > ma60: s += 10
    if c[-1] > ma60: s += 10
    return min(100, s)


def _factor_mom_old(df: pd.DataFrame) -> float | None:
    """旧体系: momentum_score — 多周期动量 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values
    if c[-1] <= 0:
        return None
    mom1 = (c[-1] - c[-2]) / max(c[-2], 0.01) if len(c) >= 2 else 0
    mom5 = (c[-1] - c[-6]) / max(c[-6], 0.01) if len(c) >= 6 else 0
    mom10 = (c[-1] - c[-11]) / max(c[-11], 0.01) if len(c) >= 11 else 0
    mom20 = (c[-1] - c[-21]) / max(c[-21], 0.01) if len(c) >= 21 else 0
    return min(100, max(0, 50 + mom1 * 400 + mom5 * 200 + mom10 * 100 + mom20 * 50))


def _factor_fund(df: pd.DataFrame) -> float | None:
    """S1-6: Westock资金面因子 V2(反转) — 买低背离=机构吸筹 0-100

    V1-5发现: 原始方向IC=-0.063, 高流向分=机构出货。
    反转后: 低流向分→高因子分, 预期IC≈+0.063。
    """
    if len(df) < 20:
        return None
    c = df["close"].values[-20:]
    v = df["volume"].values[-20:]
    if c[-1] <= 0:
        return None
    rets = np.diff(c) / np.maximum(c[:-1], 0.01)
    vol_chg = np.diff(v) / np.maximum(v[:-1], 1)
    # 原始流向: 价升量增=正, 价跌量增=负
    flow_raw = np.sum(np.sign(rets[-5:]) * np.sign(vol_chg[-5:]) * np.abs(rets[-5:]) * np.abs(vol_chg[-5:]))
    # V2反转: 低flow=吸筹→高分
    flow_score = min(50, max(0, 25 - flow_raw * 100))
    # 大单方向反转: 跌日放量(吸筹)>涨日放量(出货)
    up_days = rets[-10:] > 0
    dn_days = rets[-10:] < 0
    up_vol = np.mean(vol_chg[-10:][up_days]) if up_days.any() else 0
    dn_vol = np.mean(vol_chg[-10:][dn_days]) if dn_days.any() else 0
    big_order = min(50, max(0, 25 + (dn_vol - up_vol) * 50))
    return flow_score + big_order


def _factor_chip(df: pd.DataFrame) -> float | None:
    """S1-6: Westock筹码面因子 — 获利盘+集中度 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values[-60:] if len(df) >= 60 else df["close"].values
    if c[-1] <= 0:
        return None
    # 获利盘比例 (简化: 价格在60日区间中的位置)
    h60, l60 = np.max(c), np.min(c)
    profit_ratio = (c[-1] - l60) / max(h60 - l60, 0.01)
    profit_score = min(40, max(0, profit_ratio * 40))
    # 筹码集中度 (简化: 成交量变异系数 越低越集中)
    v60 = df["volume"].values[-60:] if len(df) >= 60 else df["volume"].values
    vol_cv = np.std(v60) / max(np.mean(v60), 1)
    conc_score = min(30, max(0, (1 - vol_cv) * 30))
    # 换手率稳定性
    turnover_std = np.std(v60[-20:] / max(np.mean(v60[-20:]), 1))
    stab_score = min(30, max(0, (1 - turnover_std) * 30))
    return profit_score + conc_score + stab_score


def _factor_def(df: pd.DataFrame) -> float | None:
    """防守因子: 低波+稳收益+趋势 0-100"""
    if len(df) < 20:
        return None
    c = df["close"].values[-20:]
    if c[-1] <= 0:
        return None
    rets = np.diff(c) / np.maximum(c[:-1], 0.01)
    vol = float(np.std(rets) * np.sqrt(252)) if len(rets) >= 20 else 0.5
    t7 = (c[-1] - c[-8]) / max(c[-8], 0.01) if len(c) >= 8 else 0
    ma20 = np.mean(c)
    return min(40, max(0, (0.50 - vol) * 80)) + min(30, max(0, t7 * 300)) + min(30, max(0, (c[-1] / max(ma20, 0.01) - 1) * 500))


def _factor_qmt_composite(df: pd.DataFrame) -> float | None:
    """QMT统一复合因子"""
    if _qmt_factors is None:
        return None
    f = _qmt_factors(df)
    return _qmt_composite(f) if f else None


def _spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    ok = ~(np.isnan(x) | np.isnan(y))
    if ok.sum() < 30:
        return np.nan
    try:
        from scipy.stats import spearmanr
        ic, _ = spearmanr(x[ok], y[ok])
        return float(ic)
    except ImportError:
        from scipy.stats import rankdata
        return float(np.corrcoef(rankdata(x[ok]), rankdata(y[ok]))[0, 1])


def scan_dates(data: dict[str, pd.DataFrame], days: int) -> list[str]:
    """找出最近N个有足够覆盖的交易日。"""
    # 取第一只股票的最后 days*2 个日期作为候选
    first_df = next(iter(data.values()))
    if not isinstance(first_df, pd.DataFrame):
        return []
    all_dates = sorted(set(str(ts)[:10] for ts in first_df.index))
    return all_dates[-days:] if len(all_dates) >= days else all_dates


def run(days=None, sample=None):
    days = days or CONF["days"]
    sample = sample or CONF["sample"]
    windows = CONF["windows"]

    print(f"[V1-5] 全市场IC: {days}天 x {sample}只")
    data = load_data()
    all_syms = list(data.keys())
    print(f"[V1-5] 股票: {len(all_syms)}只")

    dates = scan_dates(data, days)
    print(f"[V1-5] 交易日: {len(dates)}天 ({dates[0]}~{dates[-1]})")

    # Phase A: 从 FactorRegistry 自动读取因子列表
    try:
        from factor_registry import get_all_compute_fns
        factors = get_all_compute_fns()
        if not factors:
            raise ImportError("Registry empty")
    except ImportError:
        # 降级: 硬编码
        factors = {
            "trend_score": _factor_trend_old,
            "chase_v2": _factor_chase, "chip_v2": _factor_chip,
            "defensive_v2": _factor_def, "bull_line": _factor_bull_old,
            "momentum_score": _factor_mom_old, "qmt_composite": _factor_qmt_composite,
            "power_score": _factor_power_old,
        }
    results = {n: {w: [] for w in windows} for n in factors}

    for di, date_str in enumerate(dates):
        np.random.seed(di)
        pool = []
        for s in all_syms[:sample*3]:
            if s in data:
                try:
                    data[s].index.get_loc(pd.Timestamp(date_str))
                    pool.append(s)
                    if len(pool) >= sample:
                        break
                except KeyError:
                    continue
        if len(pool) < 30:
            continue
        if di % 10 == 0 or di < 3:
            print(f"  [{di}/{len(dates)}] {date_str} pool={len(pool)}")

        fvals = {n: [] for n in factors}
        fwds = {w: [] for w in windows}
        valid = 0

        for sym in pool:
            df = data[sym]
            try:
                idx = df.index.get_loc(pd.Timestamp(date_str))
            except KeyError:
                # 降级: 字符串匹配
                mask = [str(ts)[:10] == date_str for ts in df.index]
                if not any(mask):
                    continue
                idx = mask.index(True)
            if idx < CONF["min_days"]:
                continue
            past = df.iloc[max(0, idx - 60): idx + 1]
            if len(past) < 20:
                continue

            cur_close = float(df.iloc[idx]["close"])
            if cur_close <= 0:
                continue

            # 先算因子值, 有至少一个有效值才加入fwds
            stock_fvals = {}
            for n, fn in factors.items():
                val = fn(past)
                if val is not None:
                    stock_fvals[n] = val

            if not stock_fvals:  # 所有因子都无效 → 跳过此股票
                continue

            for n, val in stock_fvals.items():
                fvals[n].append(val)

            for w in windows:
                if isinstance(idx, int) and idx + w >= len(df):
                    fwds[w].append(np.nan)
                elif isinstance(idx, int):
                    fwd_c = float(df.iloc[idx + w]["close"])
                    fwds[w].append((fwd_c - cur_close) / max(cur_close, 0.01) if fwd_c > 0 else np.nan)
                else:
                    fwds[w].append(np.nan)

            valid += 1

        if valid < 30:
            continue

        for n in factors:
            fv = np.array(fvals[n])
            fv_len = len(fv)
            for w in windows:
                fd = np.array(fwds[w][:fv_len])  # 对齐长度
                ic = _spearman_ic(fv, fd)
                if not np.isnan(ic):
                    results[n][w].append(ic)

    # Summary
    summary = {}
    for n in factors:
        summary[n] = {}
        for w in windows:
            vv = results[n][w]
            summary[n][f"IC_{w}d"] = round(float(np.mean(vv)), 4) if len(vv) >= 5 else None
            summary[n][f"IC_{w}d_n"] = len(vv)

    report = {
        "version": "V1-5", "generated_at": datetime.now().isoformat(),
        "method": "Spearman cross-sectional on full market",
        "sample": sample, "total": len(all_syms), "days": len(dates),
        "factors": summary,
        "fixes": "DataFrame-native, proper cross-section, no selection bias",
    }
    with open(CONF["output"], "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[V1-5] 报告: {CONF['output']}")

    # Phase B: 分层回测 (Top 20% vs Bottom 20%)
    print("\n[V1-5] 分层回测 (Top 20% vs Bottom 20%, 5日):")
    layered = {}
    for n in factors:
        all_returns = []
        for di, date_str in enumerate(dates[-20:]):  # 最近20天
            # 重新采样并计算因子值
            np.random.seed(di)
            pool = []
            for s in list(data.keys())[:sample*2]:
                if s in data:
                    try: data[s].index.get_loc(pd.Timestamp(date_str)); pool.append(s)
                    except KeyError: continue
                if len(pool) >= sample: break
            if len(pool) < 50: continue
            scores, fwds_5 = [], []
            fn = factors[n]
            for sym in pool:
                df = data[sym]
                try:
                    idx = df.index.get_loc(pd.Timestamp(date_str))
                except KeyError: continue
                if idx < 60: continue
                past = df.iloc[max(0,idx-60):idx+1]
                if len(past) < 20: continue
                val = fn(past)
                if val is None: continue
                if idx + 5 >= len(df): continue
                fwd = (float(df.iloc[idx+5]["close"]) - float(df.iloc[idx]["close"])) / max(float(df.iloc[idx]["close"]), 0.01)
                scores.append(val); fwds_5.append(fwd)
            if len(scores) < 50: continue
            cutoff = int(len(scores) * 0.2)
            idx_sorted = np.argsort(scores)
            top_ret = np.mean([fwds_5[i] for i in idx_sorted[-cutoff:]])
            bot_ret = np.mean([fwds_5[i] for i in idx_sorted[:cutoff]])
            all_returns.append((top_ret, bot_ret, top_ret - bot_ret))

        if len(all_returns) >= 5:
            avg_top = np.mean([r[0] for r in all_returns])
            avg_bot = np.mean([r[1] for r in all_returns])
            spread = np.mean([r[2] for r in all_returns])
            ok = "✅" if spread > 0 else "❌"
            layered[n] = {"top_20pct": round(avg_top*100, 2), "bottom_20pct": round(avg_bot*100, 2),
                          "spread": round(spread*100, 2), "pass": spread > 0}
            print(f"  {ok} {n:<25s} Top={avg_top*100:+.1f}% Bottom={avg_bot*100:+.1f}% 多空={spread*100:+.1f}%")
        else:
            layered[n] = {"error": "insufficient data"}
    report["layered_backtest"] = layered

    # Phase A: 自动更新 FactorRegistry
    try:
        from factor_registry import update_all_ic_from_report
        update_all_ic_from_report(CONF["output"])
        print("[V1-5] FactorRegistry 已自动更新")
    except Exception:
        pass

    # Factor Health: 自动健康检查
    try:
        from factor_health import run_health_check
        health = run_health_check()
        s = health.get("summary", {})
        print(f"[V1-5] 健康检查: {s.get('healthy',0)}健康 {s.get('watch',0)}观察 {s.get('danger',0)}危险")
    except Exception:
        pass

    for n in factors:
        ic5 = summary[n].get("IC_5d")
        label = "有效" if ic5 and abs(ic5) > 0.02 else ("弱" if ic5 and abs(ic5) > 0.01 else "无效/不足")
        print(f"  {n}: IC(5d)={ic5} {label}")

    # Phase 7: ICIR + 衰减检测
    print("\n[V1-5] ICIR + 衰减检测:")
    icir_report = {}
    for n in factors:
        ic_5d_list = results[n].get(5, [])
        if len(ic_5d_list) >= 5:
            ic_arr = np.array(ic_5d_list)
            ic_mean = float(np.mean(ic_arr))
            ic_std = float(np.std(ic_arr))
            icir = round(ic_mean / ic_std, 3) if ic_std > 1e-8 else 0
            # 衰减检测: 线性回归IC趋势
            if len(ic_arr) >= 10:
                from scipy.stats import linregress
                slope, _, _, _, _ = linregress(range(len(ic_arr)), ic_arr)
                decay_rate = round(float(slope * 100), 4)
            else:
                decay_rate = 0
            icir_report[n] = {"icir": icir, "decay_rate": decay_rate, "n_obs": len(ic_5d_list)}
            status = "✅" if abs(icir) > 0.3 else ("⚠" if abs(icir) > 0.2 else "❌")
            print(f"  {status} {n:<25s} ICIR={icir:.3f} decay={decay_rate:+.4f}/obs N={len(ic_5d_list)}")
        else:
            icir_report[n] = {"icir": 0, "decay_rate": 0, "n_obs": 0}
    report["icir"] = icir_report

    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=CONF["days"])
    p.add_argument("--sample", type=int, default=CONF["sample"])
    args = p.parse_args()
    run(days=args.days, sample=args.sample)
