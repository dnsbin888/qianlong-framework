"""V2 策略快速测试 — 缩减股票池和数据量，快速验证策略逻辑。"""
import sys, os
sys.path.insert(0, r"d:\quant_framework\src")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals2 import factor_final_pick, factor_xg_signal
from quant_framework.factors.tdx_signals import factor_qlj

# ── 极简配置 ──
DATA_ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
START = "2024-01-01"
END = "2024-06-30"
MAX_STOCKS = 500  # 只加载前 500 只股票
INITIAL_CASH = 1_000_000.0
print_interval = 100

print("=" * 60)
print("  V2 Quick Test — T+1 Scalp Strategy")
print("=" * 60)

# ── 1. 快速加载数据 ──
print("\n[1/4] Loading data (max 200 stocks)...")
provider = THSDayDataProvider(DATA_ROOT)
provider.connect()

all_syms = provider.scan_symbols()
print(f"  Total .day files: {len(all_syms)}, using first {MAX_STOCKS}")

stock_data = {}
loaded = 0
start_dt = datetime.strptime(START, "%Y-%m-%d")
min_start = start_dt - timedelta(days=400)

for sym in all_syms[:MAX_STOCKS]:
    data = provider._read_day_file(sym)
    if not data:
        continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in data.items():
        dt = _date_to_datetime(date_int)
        if dt is None or dt < min_start or o <= 0 or c <= 0:
            continue
        records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    if len(records) < 300:
        continue
    df = pd.DataFrame(records).sort_values("date").set_index("date")
    stock_data[sym] = df
    loaded += 1
    if loaded % 50 == 0:
        print(f"  Loaded {loaded} stocks...")

print(f"  Valid stocks: {loaded}")

# ── 2. 构建交易日历 ──
print("\n[2/4] Building calendar...")
all_dates = set()
for df in stock_data.values():
    dates = set(df.index[(df.index >= start_dt) & (df.index <= datetime.strptime(END, "%Y-%m-%d"))])
    if len(dates) > len(all_dates):
        all_dates = dates
trading_dates = sorted(all_dates)
print(f"  Trading days: {len(trading_dates)}")

# ── 3. 回测 (V2 逻辑) ──
print(f"\n[3/4] Running V2 backtest...")

# 参数
QUALITY_MIN = 0.55
MAX_HOLD = 5
MAX_POS = 3
STOP_LOSS = -0.05
TAKE_PROFIT_ATR = 3.0
TRAILING_ATR = 1.5
COOLDOWN = 5
SLIPPAGE = 0.001
COMMISSION = 0.0003
STAMP = 0.001
BASE_POS_PCT = 0.30
MOMENTUM_THRESHOLD = 0.02

cash = INITIAL_CASH
holdings = {}   # symbol → {buy_price, volume, cost, buy_date, hold_days, max_price, quality, atr_pct}
trades = []
last_trade = {}
equity_curve = []

# 辅助函数：ATR
def calc_atr_pct(df):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.iloc[-14:].mean()
    return atr / c.iloc[-1] if c.iloc[-1] > 0 else 0.02

# 辅助函数：质量评分
def score_quality(df, raw_signal):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 1. 强度
    s_intensity = 1.0 if raw_signal >= 2 else (0.6 if raw_signal == 1 else 0.3)

    # 2. 趋势
    ma5 = c.iloc[-5:].mean()
    ma10 = c.iloc[-10:].mean()
    ma20 = c.iloc[-20:].mean()
    cur = c.iloc[-1]
    if cur > ma5 > ma10 > ma20:
        s_trend = 0.9
    elif cur > ma5 and cur > ma10:
        s_trend = 0.7
    elif cur > ma20:
        s_trend = 0.5
    elif cur > ma5:
        s_trend = 0.3
    else:
        s_trend = 0.1
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0:
        s_trend = min(1.0, s_trend + 0.1)

    # 3. 量能
    v_cur = v.iloc[-1]
    v_ma5 = v.iloc[-6:-1].mean() if len(v) >= 6 else v.iloc[:-1].mean()
    vr = v_cur / v_ma5 if v_ma5 > 0 else 1.0
    if 1.5 <= vr <= 3.0:
        s_vol = 1.0
    elif 1.2 <= vr < 1.5:
        s_vol = 0.7
    elif vr > 3.0:
        s_vol = 0.6
    elif vr >= 0.8:
        s_vol = 0.4
    else:
        s_vol = 0.2
    if c.iloc[-1] > c.iloc[-2] and vr > 1.0:
        s_vol = min(1.0, s_vol + 0.1)

    # 4. 位置
    h20 = h.iloc[-20:].max()
    l20 = l.iloc[-20:].min()
    pos = (cur - l20) / (h20 - l20) if h20 > l20 else 0.5
    if 0.2 <= pos <= 0.5:
        s_pos = 1.0
    elif 0.5 < pos <= 0.7:
        s_pos = 0.8
    elif 0.7 < pos <= 0.85:
        s_pos = 0.5
    elif pos > 0.85:
        s_pos = 0.2
    else:
        s_pos = 0.6

    total = s_intensity * 0.25 + s_trend * 0.30 + s_vol * 0.25 + s_pos * 0.20
    return {"total": total, "intensity": s_intensity, "trend": s_trend, "volume": s_vol, "position": s_pos, "atr": calc_atr_pct(df)}

# 量能过滤
def check_volume(df):
    v = df["volume"]
    if len(v) < 6:
        return True
    vr = v.iloc[-1] / v.iloc[-6:-1].mean() if v.iloc[-6:-1].mean() > 0 else 1.0
    return vr >= 1.2

# 主循环
total_days = len(trading_dates)
sig_count = 0
sig_pass = 0

for day_idx, today in enumerate(trading_dates):
    if day_idx % 20 == 0:
        pct = day_idx / max(1, total_days) * 100
        print(f"  {today.date()} | {pct:.0f}% | Trades: {len(trades)} | "
              f"Signals: {sig_count}({sig_pass} passed) | Cash: ¥{cash:,.0f} | "
              f"Holding: {len(holdings)}")

    # ── 处理持仓卖出 ──
    for sym, h in list(holdings.items()):
        if h["buy_date"].date() == today.date():
            continue  # T+1 约束

        df = stock_data.get(sym)
        if df is None:
            continue
        df_t = df[df.index == today]
        if df_t.empty:
            continue

        open_p = df_t["open"].iloc[0]
        close_p = df_t["close"].iloc[0]
        high_p = df_t["high"].iloc[0]
        buy_p = h["buy_price"]
        vol = h["volume"]
        atr_pct = h.get("atr_pct", 0.02)
        h["hold_days"] += 1
        if high_p > h.get("max_price", buy_p):
            h["max_price"] = high_p

        ret_pct = (close_p - buy_p) / buy_p if buy_p > 0 else 0
        max_p = h.get("max_price", buy_p)
        atr_price = atr_pct * buy_p

        should_sell = False
        exit_type = "normal"
        sell_price = close_p

        # 硬止损
        if ret_pct <= STOP_LOSS:
            should_sell = True
            exit_type = "stop_loss"
        # 自适应止盈
        elif ret_pct >= max(TAKE_PROFIT_ATR * atr_pct, 0.03):
            should_sell = True
            exit_type = "take_profit"
        # 跟踪止盈
        elif ret_pct > 0 and (close_p - max_p) / max_p < -TRAILING_ATR * atr_pct:
            should_sell = True
            exit_type = "trailing_stop"
        # 超时
        elif h["hold_days"] >= MAX_HOLD:
            should_sell = True
            exit_type = "timeout"
        # T+1 弱势
        elif h["hold_days"] >= 1 and (close_p - open_p) / open_p < MOMENTUM_THRESHOLD if open_p > 0 else True:
            should_sell = True
            exit_type = "t1_weak" if ret_pct < 0.02 else "t1_normal"

        if should_sell:
            sell_p = sell_price * (1 - SLIPPAGE)
            gross = sell_p * vol
            comm = max(gross * COMMISSION, 5.0)
            st = gross * STAMP
            net = gross - comm - st
            profit = net - h["cost"]
            cash += net
            trades.append({
                "symbol": sym, "buy_date": h["buy_date"], "sell_date": today,
                "buy_price": buy_p, "sell_price": sell_p, "volume": vol,
                "return_pct": ret_pct, "net_profit": profit,
                "exit_type": exit_type, "hold_days": h["hold_days"],
                "quality": h.get("quality", 0),
            })
            del holdings[sym]

    # ── 计算信号+买入 ──
    slots = MAX_POS - len(holdings)
    if slots > 0:
        candidates = []
        for sym, df in stock_data.items():
            if sym in holdings:
                continue
            # 冷却期
            if sym in last_trade and (today - last_trade[sym]).days < COOLDOWN:
                continue
            df_ut = df[df.index <= today]
            if len(df_ut) < 300:
                continue
            try:
                sig = factor_xg_signal(df_ut)
                raw = int(sig.iloc[-1]) if not pd.isna(sig.iloc[-1]) else 0
                if raw <= 0:
                    continue
                scores = score_quality(df_ut, raw)
                sig_count += 1
                if scores["total"] < QUALITY_MIN:
                    continue
                if not check_volume(df_ut):
                    continue
                sig_pass += 1
                candidates.append({"symbol": sym, "quality": scores["total"], "close": df_ut["close"].iloc[-1], "atr_pct": scores["atr"]})
            except Exception:
                continue

        candidates.sort(key=lambda x: x["quality"], reverse=True)
        for c in candidates[:slots]:
            buy_p = c["close"] * (1 + SLIPPAGE)
            pos_pct = BASE_POS_PCT
            amt = cash * min(pos_pct, 0.35)
            vol = int(amt / buy_p / 100) * 100
            if vol < 100:
                continue
            cost = buy_p * vol * (1 + COMMISSION)
            if cost > cash:
                continue
            cash -= cost
            holdings[c["symbol"]] = {
                "buy_price": buy_p, "volume": vol, "cost": cost,
                "buy_date": today, "hold_days": 0, "max_price": buy_p,
                "quality": c["quality"], "atr_pct": c["atr_pct"],
            }
            last_trade[c["symbol"]] = today

    # 权益记录
    mv = sum(h["cost"] * (1 + ((stock_data.get(sym, pd.DataFrame())["close"].iloc[-1] - h["buy_price"]) / h["buy_price"])
                             if sym in stock_data else 0) for sym, h in holdings.items())
    equity_curve.append({"date": today, "equity": cash + mv})

# ── 4. 报告 ──
print(f"\n[4/4] ===== V2 Quick Test Results =====")
print(f"  Period: {START} ~ {END}")
print(f"  Stocks tested: {loaded}")
print(f"  Trading days: {total_days}")
print(f"  Raw signals: {sig_count}")
print(f"  Passed quality filter: {sig_pass} ({sig_pass/max(1,sig_count):.0%})")

if not trades:
    print("\n  ⚠ No trades! Signal too rare with this subset.")
    print("  Try: reduce --min-quality, use more stocks, or switch signal.")
else:
    returns = [t["return_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    profits = [t["net_profit"] for t in trades]
    wr = len(wins) / len(returns)

    print(f"\n  ── Trade Summary ──")
    print(f"  Total trades:      {len(trades)}")
    print(f"  Win rate:          {wr:.1%}")
    print(f"  Avg win:           {np.mean(wins):.2%}" if wins else "  Avg win: N/A")
    print(f"  Avg loss:          {np.mean(losses):.2%}" if losses else "  Avg loss: N/A")
    pf = abs(np.mean(wins) / np.mean(losses)) if losses and np.mean(losses) != 0 else float('inf')
    print(f"  Profit factor:     {pf:.2f}")
    print(f"  Best trade:        {max(returns):.2%}")
    print(f"  Worst trade:       {min(returns):.2%}")
    print(f"  Net P&L:           ¥{sum(profits):,.0f}")
    print(f"  Final cash:        ¥{cash:,.0f}")
    total_ret = (cash / INITIAL_CASH) - 1
    print(f"  Total return:      {total_ret:.2%}")

    # Exit types
    from collections import Counter
    exit_dist = Counter(t["exit_type"] for t in trades)
    print(f"\n  ── Exit Types ──")
    for et, cnt in exit_dist.most_common():
        avg_r = np.mean([t["return_pct"] for t in trades if t["exit_type"] == et])
        print(f"  {et:<16s}: {cnt:>4d} (avg: {avg_r:>+7.2%})")

    # Hold days
    hold_days = [t["hold_days"] for t in trades]
    print(f"\n  ── Hold Days ──")
    for d in range(1, max(hold_days) + 1):
        cnt = hold_days.count(d)
        if cnt > 0:
            avg_r = np.mean([t["return_pct"] for t in trades if t["hold_days"] == d])
            print(f"  {d} day(s):  {cnt:>4d} trades (avg: {avg_r:>+7.2%})")

    # Quality impact
    high_q = [t for t in trades if t["quality"] >= 0.7]
    mid_q = [t for t in trades if 0.5 <= t["quality"] < 0.7]
    print(f"\n  ── Quality Impact ──")
    for label, group in [("High(>=0.7)", high_q), ("Mid(0.5-0.7)", mid_q)]:
        if group:
            avg_r = np.mean([t["return_pct"] for t in group])
            wr_g = sum(1 for t in group if t["return_pct"] > 0) / len(group)
            print(f"  {label}: {len(group)} trades  avg={avg_r:>+7.2%}  wr={wr_g:.1%}")

    # Top 5
    print(f"\n  ── Top 5 Trades ──")
    for t in sorted(trades, key=lambda x: x["return_pct"], reverse=True)[:5]:
        print(f"  {t['symbol']}  {t['buy_date'].date()}->{t['sell_date'].date()}  "
              f"{t['hold_days']}d  ret={t['return_pct']:+.2%}  [{t['exit_type']}]  Q={t['quality']:.0%}")

    # Equity
    eq = pd.DataFrame(equity_curve).set_index("date")["equity"]
    peak = eq.expanding().max()
    dd = (eq - peak) / peak
    daily_ret = eq.pct_change().dropna()
    if len(daily_ret) > 1:
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
        print(f"\n  ── Performance ──")
        print(f"  Max drawdown:      {dd.min():.2%}")
        print(f"  Sharpe:            {sharpe:.2f}")
        print(f"  Daily win rate:    {(daily_ret > 0).mean():.1%}")

print("=" * 60)
print("  Quick Test Complete!")
print("=" * 60)
