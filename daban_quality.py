"""打板质量评分 v1.1 — 封板质量 0-100 (对标游资打板标准)
数据: 日线可模拟, QMT实盘需 tick 数据 (封单量/涨停时间/炸板次数)
参数治理: 止损/止盈从 trade_config_master.json 读取

封板质量公式:
  涨停时间分 (10:00前=1.0 / 10:30=0.7 / 11:00=0.4 / 午后=0.1)
× 封单占比 (封单/流通盘 ≥3%=1.0 / 2%=0.7 / 1%=0.4)
× 炸板惩罚 (未炸=1.0 / 炸1次=0.7 / 炸2次=0.3)
× 回封加分 (30分钟回封=1.5x)
× 板块联动 (同板块≥3只涨停=1.3x)

日线模拟: 用日线数据近似估计 (量比→封单强度代理, 振幅→炸板代理)
"""
import sys, os, json, numpy as np

def _load_master_params():
    """从 trade_config_master 读取止损/止盈 (参数治理铁律)"""
    try:
        m = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
        sl = m.get("stop_loss", {})
        tp = m.get("take_profit", {})
        return {
            "stop_pct": sl.get("hard", 0.055),
            "take_profit_pct": tp.get("tp1", {}).get("profit_pct", 0.10),
        }
    except Exception:
        return {"stop_pct": 0.055, "take_profit_pct": 0.10}


def limit_up_pct(code):
    """涨停幅度"""
    c = str(code).replace("sh", "").replace("sz", "").replace("bj", "")
    if c.startswith(("30", "688")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def limit_up_price(prev_close, code=""):
    pct = limit_up_pct(code)
    return round(prev_close * (1 + pct), 2)


def score_board_quality(df, code="", sector_bd_count=0):
    """封板质量评分 (日线模拟版)

    日线无法获得: 封单量 / 涨停精确时间 / 炸板次数
    日线代理:
      - 量比 > 3 → 强封
      - 开板→回封 → 炸板回封
      - 一字板 = 最强封
    """
    if df is None or len(df) < 10:
        return 0

    c = df["close"].values
    h = df["high"].values
    l_val = df["low"].values if "low" in df.columns else c
    o = df["open"].values
    v = df["volume"].values
    n = len(c)

    prev_close = c[-2] if n >= 2 else c[-1]
    board_price = limit_up_price(prev_close, code)
    if board_price <= 0:
        return 0

    close = c[-1]
    open_p = o[-1]
    low = l_val[-1]

    # 必须涨停或接近涨停 (<0.5%)
    if abs(close - board_price) / max(board_price, 0.01) > 0.005:
        return 0

    quality = 1.0

    # ① 封板强度 (日线代理: 开盘位置 + 量比)
    # 一字板 = 1.0, 高开>5%封板 = 0.8, 低开封板 = 0.5
    gap = (open_p - prev_close) / max(prev_close, 0.01)
    if gap > 0.09:  # 一字板
        seal_strength = 1.0
    elif gap > 0.05:
        seal_strength = 0.85
    elif gap > 0.02:
        seal_strength = 0.7
    elif gap > 0:
        seal_strength = 0.55
    else:
        seal_strength = 0.4  # 低开封板, 分歧大
    quality *= seal_strength

    # ② 放量质量 (日线代理: 量比)
    avg_vol = np.mean(v[-6:-1]) if n >= 6 else v[-1]
    vol_ratio = v[-1] / max(avg_vol, 1)
    if 1.5 <= vol_ratio <= 3:
        vol_score = 1.0  # 温和放量, 最优
    elif 3 < vol_ratio <= 5:
        vol_score = 0.8  # 放量偏大
    elif vol_ratio > 5:
        vol_score = 0.5  # 巨量, 出货嫌疑
    else:
        vol_score = 0.7  # 缩量封板
    quality *= vol_score

    # ③ 炸板/回封检测 (日线代理: 最低价 vs 涨停价)
    if low < board_price * 0.98:  # 盘中跌超 2% = 炸过板
        quality *= 0.7  # 炸板惩罚
        # 如果最终封住了 = 回封加分
        if abs(close - board_price) < 0.01:
            quality *= 1.3  # 回封加分

    # ④ 板块联动加分
    if sector_bd_count >= 5:
        quality *= 1.3  # 板块多股涨停
    elif sector_bd_count >= 3:
        quality *= 1.15
    elif sector_bd_count >= 1:
        quality *= 1.05

    # ⑤ 封板时间代理 (日线无法知道精确时间，用开盘位置估计)
    # 一字板 = 9:30前封, 高开秒板 = 早盘, 低开尾盘封 = 午后
    if gap > 0.09:
        time_score = 1.0
    elif gap > 0.05:
        time_score = 0.8
    elif gap > 0.02:
        time_score = 0.6
    else:
        time_score = 0.3
    quality *= time_score

    return round(min(100, quality * 100), 1)


def generate_daban_candidates(sd, factor_cache=None):
    """二封回封候选 — 只做炸板后回封 (对齐游资2026: 一封38%胜率不追, 二封52-75%)

    日线检测: 盘中曾炸板(low<涨停价) + 收盘回封(close≈涨停价) = 分歧转一致
    QMT次日监控 → 实时封单确认 → 买入
    """
    candidates = []
    for sym, df in sd.items():
        try:
            if len(df) < 10:
                continue
            if "ST" in sym.upper():
                continue
            c = df["close"].values
            l = df["low"].values
            close = float(c[-1])
            prev_close = float(c[-2]) if len(c) >= 2 else close
            low = float(l[-1])

            # 涨停价
            lim_pct = 0.098 if sym.startswith(('sh60','sz00')) else 0.20
            if sym.startswith(('sh68','sz30')): lim_pct = 0.20
            if sym.startswith(('bj8','bj4')): lim_pct = 0.30
            board_price = round(prev_close * (1 + lim_pct), 2)

            # ══ 二封回封检测: 盘中炸板 + 收盘回封 ══
            is_blown = low < board_price * 0.985  # 盘中曾低于涨停价1.5%
            is_resealed = abs(close - board_price) / max(board_price, 0.01) < 0.005  # 收盘在涨停价
            if not (is_blown and is_resealed):  # 必须二封, 一封跳过
                continue

            score = score_board_quality(df, sym)
            if score < 30:
                continue
            _mp = _load_master_params()
            candidates.append({
                "symbol": sym,
                "score": score,
                "action": "monitor",
                "close": round(close, 2),
                "stop_loss": round(close * (1 - min(_mp["stop_pct"], 0.04)), 2),  # 打板止损更紧
                "take_profit": round(close * (1 + _mp["take_profit_pct"]), 2),
                "reason": f"二封回封 {'炸板' if is_blown else ''}+{'回封' if is_resealed else ''} 质量={score}",
                "hold_days": 1,
                "strategy_id": "daban",
                "strategy_type": "pattern",
            })
        except Exception:
            continue
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:15]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "D:/quant_web")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
    candidates = generate_daban_candidates(sd)
    print(f"打板候选: {len(candidates)} 只")
    for c in candidates[:10]:
        print(f"  {c['symbol']} score={c['score']} {c['reason']}")
