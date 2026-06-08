"""快速回测 — 300只股票，生成可用的回测数据供终端展示。"""
import sys, os, numpy as np, pandas as pd
from datetime import datetime
sys.path.insert(0, r"d:\quant_framework\src")
from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals2 import factor_final_pick

DATA_ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
START = datetime(2023, 1, 1)
END = datetime(2025, 6, 1)
MAX_STOCKS = 300
INITIAL_CASH = 1_000_000.0
MAX_POS = 3
POS_PCT = 0.30

print("=" * 60)
print("  Quick Backtest — tdx2_final (Formula 1)")
print("=" * 60)

# Load
print("\n[1] Loading data...")
provider = THSDayDataProvider(DATA_ROOT)
provider.connect()
symbols = provider.scan_symbols()
symbols = [s for s in symbols if s[0] in ("6", "0", "3") and len(s) == 6 and s.isdigit()]
symbols = symbols[:MAX_STOCKS]
print(f"  Using {len(symbols)} stocks")

stock_dfs = {}
all_dates = set()
for sym in symbols:
    data = provider._read_day_file(sym)
    if not data:
        continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
        dt = _date_to_datetime(date_int)
        if dt is None or o <= 0 or c <= 0:
            continue
        records.append({"date": dt, "open": o, "high": h, "low": l,
                        "close": c, "volume": vol})
    if len(records) < 200:
        continue
    df = pd.DataFrame(records).set_index("date")
    stock_dfs[sym] = df
    all_dates.update(df.index)

trading_dates = sorted(d for d in all_dates if START <= d <= END)
print(f"  Loaded {len(stock_dfs)} stocks, {len(trading_dates)} trading days")

# Backtest
print("\n[2] Running backtest...")
cash = INITIAL_CASH
buy_queue = {}  # symbol -> {buy_price, buy_date, cost, volume}
trades = []
equity_record = []

for day_idx, today in enumerate(trading_dates):
    if day_idx % 50 == 0:
        print(f"  {day_idx}/{len(trading_dates)} | "
              f"trades={len(trades)} | cash={cash/10000:.0f}w")

    # ---- Sell (T+1) ----
    for sym, binfo in list(buy_queue.items()):
        df = stock_dfs.get(sym)
        if df is None or today not in df.index:
            continue
        open_p = df.loc[today, "open"]
        sell_p = open_p * 0.999  # slippage
        ret = (sell_p - binfo["buy_price"]) / binfo["buy_price"]
        exit_type = "normal"
        if ret <= -0.03:
            exit_type = "stop_loss"
        elif ret >= 0.05:
            exit_type = "take_profit"
        net_pnl = binfo["cost"] * ret
        cash += binfo["cost"] + net_pnl
        trades.append({
            "symbol": sym,
            "buy_date": binfo["buy_date"],
            "sell_date": today,
            "buy_price": binfo["buy_price"],
            "sell_price": sell_p,
            "return_pct": ret,
            "net_profit": net_pnl,
            "exit_type": exit_type,
            "signal": "tdx2_final",
            "volume": binfo["volume"],
        })
        del buy_queue[sym]

    # ---- Compute signals ----
    today_signals = []
    for sym, df in stock_dfs.items():
        if sym in buy_queue:
            continue
        df_t = df[df.index <= today]
        if len(df_t) < 200:
            continue
        try:
            sig = factor_final_pick(df_t)
            if isinstance(sig, pd.Series) and sig.iloc[-1] > 0:
                today_signals.append((sym, df_t["close"].iloc[-1]))
        except Exception:
            continue

    # ---- Buy ----
    slots = MAX_POS - len(buy_queue)
    for sym, close_p in today_signals[:slots]:
        avail = cash * POS_PCT
        buy_p = close_p * 1.001  # slippage
        vol = int(avail / buy_p / 100) * 100
        if vol < 100 or buy_p * vol > cash:
            continue
        cost = buy_p * vol
        cash -= cost
        buy_queue[sym] = {
            "buy_price": buy_p,
            "buy_date": today,
            "cost": cost,
            "volume": vol,
        }

    # ---- Record equity ----
    mkt_val = sum(b["cost"] for b in buy_queue.values())
    equity_record.append({
        "date": today,
        "equity": cash + mkt_val,
        "cash": cash,
        "market_value": mkt_val,
    })

# Save
print("\n[3] Saving results...")
eq_df = pd.DataFrame(equity_record)
eq_df.to_csv(r"d:\quant_framework\equity_curve.csv", index=False)
print(f"  equity_curve.csv: {len(eq_df)} rows")

tr_df = pd.DataFrame(trades)
tr_df.to_csv(r"d:\quant_framework\trade_log.csv", index=False)
print(f"  trade_log.csv: {len(tr_df)} trades")

# Summary
print("\n[4] Summary")
if len(trades) > 0:
    rets = np.array([t["return_pct"] for t in trades])
    win_rate = (rets > 0).mean()
    total_pnl = sum(t["net_profit"] for t in trades)
    final_eq = equity_record[-1]["equity"] if equity_record else INITIAL_CASH
    total_ret = (final_eq / INITIAL_CASH - 1)

    print(f"  Trades:      {len(trades)}")
    print(f"  Win Rate:    {win_rate:.1%}")
    print(f"  Avg Return:  {rets.mean():+.2%}")
    print(f"  Best:        {rets.max():+.2%}")
    print(f"  Worst:       {rets.min():+.2%}")
    print(f"  Total P&L:   {total_pnl:+,.0f}")
    print(f"  Total Ret:   {total_ret:+.2%}")
    print(f"  Final Eq:    {final_eq:,.0f}")
else:
    print("  WARNING: No trades! Signal is very strict.")
    print("  Try a different signal or broader period.")

print("\nDone!")
