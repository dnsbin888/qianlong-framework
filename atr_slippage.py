"""B2: ATR 动态滑点模型 — 小票滑点多, 大票滑点少
用法: from atr_slippage import estimate_slippage
"""
import numpy as np


def compute_atr(df, period=14):
    """计算 Average True Range"""
    close = df["close"].values
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
    atr = np.zeros_like(tr)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr[-1]


def compute_avg_dollar_volume(df, window=20):
    """计算日均成交额"""
    volume = df["volume"].values
    close = df["close"].values
    if len(volume) > window:
        volume = volume[-window:]
        close = close[-window:]
    return np.mean(volume * close)


def estimate_slippage(df, side="buy", position_pct=0.10, total_capital=1_000_000):
    """ATR动态滑点估算

    Args:
        df: OHLCV DataFrame
        side: "buy" or "sell"
        position_pct: 仓位比例 (0-1)
        total_capital: 总资金

    Returns:
        slippage_pct: 滑点百分比 (0-1)
    """
    if df is None or len(df) < 14:
        return 0.0015  # 默认 0.15%

    close = float(df["close"].values[-1])
    if close <= 0:
        return 0.0015

    atr = compute_atr(df)
    adv = compute_avg_dollar_volume(df)

    # 行业标准: slippage(bps) = base + impact * sqrt(order_value / ADV)
    order_value = total_capital * position_pct
    if adv > 0 and order_value > 0:
        impact_ratio = np.sqrt(order_value / adv)
        # A股放大系数 (散户多, 流动性差)
        slippage_bps = 15 + 150 * impact_ratio + 50 * (atr / close)
    else:
        slippage_bps = 25  # 默认 0.25%

    # 方向: 买方多付, 卖方少收
    if side == "buy":
        slippage_bps *= 1.15
    else:
        slippage_bps *= 0.95

    slippage = slippage_bps / 10000  # bps → 小数
    slippage = max(0.0005, min(0.02, slippage))  # 钳制 0.05%-2%

    return round(float(slippage), 4)


def apply_slippage(price, df, side="buy", position_pct=0.10, total_capital=1_000_000):
    """应用滑点到价格"""
    slip = estimate_slippage(df, side, position_pct, total_capital)
    if side == "buy":
        return round(price * (1 + slip), 3)
    else:
        return round(price * (1 - slip), 3)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
    if sd:
        print(f"ATR 滑点模型测试 ({len(sd)}只股票):\n")
        samples = [
            ("sh600519", "茅台(大票)"),
            ("sh601288", "农行(大盘)"),
            ("sz300522", "世名(小票)"),
            ("sz000566", "海南海药(中盘)"),
        ]
        for sym, name in samples:
            df = sd.get(sym)
            if df is not None:
                slip_buy = estimate_slippage(df, "buy", 0.10, 1_000_000)
                slip_sell = estimate_slippage(df, "sell", 0.10, 1_000_000)
                close = float(df["close"].values[-1])
                actual_buy = apply_slippage(close, df, "buy", 0.10, 1_000_000)
                actual_sell = apply_slippage(close, df, "sell", 0.10, 1_000_000)
                print(f"  {name:20s} ¥{close:8.2f}  买滑点={slip_buy:.3%} (¥{actual_buy:.2f})  卖滑点={slip_sell:.3%} (¥{actual_sell:.2f})")
    print(f"\n✅ ATR 滑点模型就绪\n")
