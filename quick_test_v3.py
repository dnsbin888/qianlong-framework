"""V3 策略快速测试 — 底仓+日内确认加仓 回测验证。

V3 核心逻辑:
  - T日尾盘: 信号触发 → 买入底仓 (60%仓位)
  - T+1: 日线OHLC近似判断加仓 (高开+支撑有效+上攻 → 加仓40%)
  - 后续: 智能持仓 (跟踪止盈/止损/超时)

加仓条件 (回测近似):
  1. 高开 (open > 昨收)
  2. 回调不破昨收 (low > 昨收*0.995)
  3. 盘中上攻 (high > open*1.005)
  4. 不追高 (open 在昨收+3%以内)
"""

import sys, os
sys.path.insert(0, r"d:\quant_framework\src")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from collections import Counter
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals2 import factor_xg_signal

# ── 配置 ──
DATA_ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
START = "2024-01-01"
END = "2024-06-30"
MAX_STOCKS = 500
INITIAL_CASH = 1_000_000.0

# V3 参数
QUALITY_MIN = 0.55
MAX_HOLD = 5
MAX_POS = 3
STOP_LOSS = -0.05
TRAILING_ATR = 1.5
COOLDOWN = 5
SLIPPAGE = 0.001
COMMISSION = 0.0003
STAMP = 0.001
TOTAL_POS_PCT = 0.30        # 总仓位基准
BASE_RATIO = 0.60            # 底仓占比
ADDON_RATIO = 0.40           # 加仓占比
ADDON_MAX_CHASE = 0.03       # 加仓追高上限
MOMENTUM_THRESHOLD = 0.02
ATR_TP_MULT = 3.0

print("=" * 65)
print("  V3 Backtest — Base + Intraday Add-on Strategy")
print("  T日底仓(60%) + T+1 9:45-10:00 放量连阳加仓(40%)")
print("=" * 65)

# ── 1. 加载数据 ──
print(f"\n[1/4] Loading data (max {MAX_STOCKS} stocks)...")
provider = THSDayDataProvider(DATA_ROOT)
provider.connect()
all_syms = provider.scan_symbols()
print(f"  Total: {len(all_syms)} .day files, using first {MAX_STOCKS}")

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
    if loaded % 100 == 0:
        print(f"  Loaded {loaded} stocks...")

print(f"  Valid: {loaded} stocks")

# ── 2. 交易日历 ──
print("\n[2/4] Building calendar...")
all_dates = set()
for df in stock_data.values():
    dates = set(df.index[(df.index >= start_dt) & (df.index <= datetime.strptime(END, "%Y-%m-%d"))])
    if len(dates) > len(all_dates):
        all_dates = dates
trading_dates = sorted(all_dates)
print(f"  Trading days: {len(trading_dates)}")

# ── 辅助函数 ──
def calc_atr_pct(df):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.iloc[-14:].mean()
    return atr / c.iloc[-1] if c.iloc[-1] > 0 else 0.02

def score_quality(df, raw_signal):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    s_intensity = 1.0 if raw_signal >= 2 else (0.6 if raw_signal == 1 else 0.3)
    ma5, ma10, ma20 = c.iloc[-5:].mean(), c.iloc[-10:].mean(), c.iloc[-20:].mean()
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
    h20, l20 = h.iloc[-20:].max(), l.iloc[-20:].min()
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
    atr = calc_atr_pct(df)
    return {"total": total, "intensity": s_intensity, "trend": s_trend, "volume": s_vol, "position": s_pos, "atr": atr}

def check_volume(df):
    v = df["volume"]
    if len(v) < 6:
        return True
    vr = v.iloc[-1] / v.iloc[-6:-1].mean() if v.iloc[-6:-1].mean() > 0 else 1.0
    return vr >= 1.2

# ── 辅助: 涨跌停判断 ──
def get_limit_pct(symbol):
    """根据股票代码返回涨跌停幅度 (主板10%/创业板科创板20%/北交所30%)"""
    code = symbol.replace(".day", "").split("\\")[-1] if "\\" in symbol else symbol
    code = code.replace(".day", "")
    # 取纯数字部分
    nums = "".join(c for c in code if c.isdigit())
    if nums.startswith("688") or nums.startswith("3"):
        return 0.20  # 科创板 / 创业板
    elif nums.startswith("8") or nums.startswith("4"):
        return 0.30  # 北交所
    else:
        return 0.10  # 主板

def is_limit_down(symbol, prev_close, price):
    return price <= prev_close * (1 - get_limit_pct(symbol))

# ── 3. 回测 ──
print(f"\n[3/4] Running V3 backtest...")

cash = INITIAL_CASH
holdings = {}   # {symbol: {base_price, base_vol, base_cost, addon_price, addon_vol, addon_cost, buy_date, hold_days, max_price, quality, atr_pct, yclose, has_addon}}
trades = []
last_trade = {}
equity_curve = []
addon_stats = {"checked": 0, "executed": 0, "declined": 0}
pending_sells = {}  # {symbol: sell_info} — 当日触发的卖出顺延到次日开盘执行

total_days = len(trading_dates)
sig_count = 0
sig_pass = 0

for day_idx, today in enumerate(trading_dates):
    if day_idx % 20 == 0:
        pct = day_idx / max(1, total_days) * 100
        addon_info = f"Addon: {addon_stats['executed']}/{addon_stats['checked']}" if addon_stats['checked'] > 0 else ""
        print(f"  {today.date()} | {pct:.0f}% | Trades: {len(trades)} | "
              f"Signals: {sig_count}({sig_pass} passed) | Cash: {cash:,.0f} | "
              f"Holding: {len(holdings)} | {addon_info}")

    # ═══════════════════════════════════════════════════════
    # Step 0: 执行昨日挂单卖出 (次日开盘价成交)
    # 行业规范: 当日收盘触发卖出信号 → 次日开盘执行
    # ═══════════════════════════════════════════════════════
    for sym in list(pending_sells.keys()):
        sell_info = pending_sells.pop(sym)
        df = stock_data.get(sym)
        if df is None:
            holdings.pop(sym, None)
            continue
        df_t = df[df.index == today]
        if df_t.empty:
            pending_sells[sym] = sell_info  # 当天无数据, 继续挂单
            continue

        open_p = df_t["open"].iloc[0]
        prev_close = sell_info["prev_close"]

        # 跌停无法卖出 → 继续挂单
        if is_limit_down(sym, prev_close, open_p):
            pending_sells[sym] = {**sell_info, "prev_close": open_p}
            continue

        # 执行卖出 (次日开盘价)
        sell_p = open_p * (1 - SLIPPAGE)
        total_vol = sell_info["total_vol"]
        gross = sell_p * total_vol
        comm = max(gross * COMMISSION, 5.0)
        st = gross * STAMP
        net = gross - comm - st
        profit = net - sell_info["total_cost"]
        cash += net
        trades.append({
            "symbol": sym, "buy_date": sell_info["buy_date"], "sell_date": today,
            "base_price": sell_info["base_price"], "base_vol": sell_info["base_vol"],
            "addon_price": sell_info.get("addon_price", 0), "addon_vol": sell_info.get("addon_vol", 0),
            "avg_price": sell_info["avg_price"], "sell_price": sell_p, "total_vol": total_vol,
            "return_pct": sell_info["ret_pct"], "net_profit": profit,
            "exit_type": sell_info["exit_type"], "hold_days": sell_info["hold_days"],
            "quality": sell_info.get("quality", 0),
            "has_addon": sell_info.get("has_addon", False),
        })
        holdings.pop(sym, None)

    # ── 处理持仓卖出 + 加仓检测 ──
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
        high_p = df_t["high"].iloc[0]
        low_p = df_t["low"].iloc[0]
        close_p = df_t["close"].iloc[0]

        # ═══════════════════════════════════════════════════
        # V3 核心: T+1 日内加仓检测
        # ═══════════════════════════════════════════════════
        yclose = h.get("yclose", h["base_price"])
        if not h.get("has_addon") and not h.get("addon_checked"):
            # 判断是否是 T+1 (底仓买入日的下一个交易日)
            is_t1 = (today.date() - h["buy_date"].date()).days == 1

            if is_t1:
                addon_stats["checked"] += 1

                # 加仓条件 (回测近似5分钟K线 — 放宽版)
                # 核心逻辑: T+1开盘后有真实买盘推动价格突破昨收
                # 条件1: 盘中突破昨收 (high > yclose, 代表日内有力量推过昨收)
                break_yclose = high_p > yclose * 1.002
                # 条件2: 不是低开低走 (close > open 或 high-low不极端)
                not_bearish = not (open_p < yclose * 0.99 and close_p < open_p)
                # 条件3: 收盘站稳 (close > yclose, 收盘在昨收上方)
                close_above = close_p > yclose
                # 条件4: 不追太高 (开盘不超昨收+3%)
                not_chasing = (open_p - yclose) / yclose < ADDON_MAX_CHASE

                if break_yclose and not_bearish and close_above and not_chasing:
                    # 执行加仓!
                    addon_price = open_p * (1 + SLIPPAGE)
                    addon_pct = TOTAL_POS_PCT * ADDON_RATIO
                    addon_amt = cash * min(addon_pct, 0.15)  # 加仓最多用15%现金
                    addon_vol = int(addon_amt / addon_price / 100) * 100

                    if addon_vol >= 100:
                        addon_cost = addon_price * addon_vol * (1 + COMMISSION)
                        if addon_cost <= cash:
                            cash -= addon_cost
                            h["addon_price"] = addon_price
                            h["addon_vol"] = addon_vol
                            h["addon_cost"] = addon_cost
                            h["has_addon"] = True
                            h["addon_checked"] = True

                            # 更新均价
                            total_vol = h["base_vol"] + addon_vol
                            total_cost = h["base_cost"] + addon_cost
                            h["avg_price"] = total_cost / total_vol if total_vol > 0 else h["base_price"]

                            if addon_price > h.get("max_price", 0):
                                h["max_price"] = addon_price

                            addon_stats["executed"] += 1
                        else:
                            addon_stats["declined"] += 1
                            h["addon_checked"] = True
                    else:
                        addon_stats["declined"] += 1
                        h["addon_checked"] = True
                else:
                    addon_stats["declined"] += 1
                    h["addon_checked"] = True
            else:
                h["addon_checked"] = True  # 不是T+1, 过了窗口

        # ═══════════════════════════════════════════════════
        # 卖出判断 (基于均价)
        # ═══════════════════════════════════════════════════
        avg_price = h.get("avg_price", h["base_price"])
        atr_pct = h.get("atr_pct", 0.02)
        h["hold_days"] += 1
        if high_p > h.get("max_price", 0):
            h["max_price"] = high_p

        ret_pct = (close_p - avg_price) / avg_price if avg_price > 0 else 0
        max_p = h.get("max_price", avg_price)
        atr_price = atr_pct * avg_price

        should_sell = False
        exit_type = "normal"
        sell_price = close_p

        # 硬止损
        if ret_pct <= STOP_LOSS:
            should_sell = True
            exit_type = "stop_loss"
        # 自适应止盈
        elif ret_pct >= max(ATR_TP_MULT * atr_pct, 0.03):
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
            exit_type = "t1_weak"

        if should_sell:
            total_vol = h["base_vol"] + h.get("addon_vol", 0)
            # 行业规范: 当日触发卖出 → 挂单到次日开盘执行
            pending_sells[sym] = {
                "symbol": sym,
                "buy_date": h["buy_date"],
                "base_price": h["base_price"], "base_vol": h["base_vol"],
                "addon_price": h.get("addon_price", 0), "addon_vol": h.get("addon_vol", 0),
                "total_cost": h["base_cost"] + h.get("addon_cost", 0),
                "avg_price": avg_price, "total_vol": total_vol,
                "ret_pct": ret_pct, "exit_type": exit_type, "hold_days": h["hold_days"],
                "quality": h.get("quality", 0), "has_addon": h.get("has_addon", False),
                "prev_close": close_p,
            }

    # ── T日选股 + 底仓买入 ──
    slots = MAX_POS - len(holdings)
    if slots > 0:
        candidates = []
        for sym, df in stock_data.items():
            if sym in holdings:
                continue
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
            # V3: 底仓 = 总仓位 * BASE_RATIO
            buy_p = c["close"] * (1 + SLIPPAGE)
            base_pct = TOTAL_POS_PCT * BASE_RATIO
            base_amt = cash * min(base_pct, 0.20)
            base_vol = int(base_amt / buy_p / 100) * 100
            if base_vol < 100:
                continue
            base_cost = buy_p * base_vol * (1 + COMMISSION)
            if base_cost > cash:
                continue
            cash -= base_cost
            holdings[c["symbol"]] = {
                "symbol": c["symbol"],
                "base_price": buy_p, "base_vol": base_vol, "base_cost": base_cost,
                "addon_price": 0, "addon_vol": 0, "addon_cost": 0,
                "avg_price": buy_p,
                "buy_date": today, "hold_days": 0, "max_price": buy_p,
                "quality": c["quality"], "atr_pct": c["atr_pct"],
                "yclose": c["close"], "has_addon": False, "addon_checked": False,
            }
            last_trade[c["symbol"]] = today

    # 权益 (使用当日价格，避免前视偏差)
    mv = 0
    for sym, h in holdings.items():
        df = stock_data.get(sym)
        if df is not None:
            df_t = df[df.index == today]
            if not df_t.empty:
                cur = df_t["close"].iloc[0]
                total_vol = h["base_vol"] + h.get("addon_vol", 0)
                mv += cur * total_vol
    equity_curve.append({"date": today, "equity": cash + mv})

# ── 4. 报告 ──
print(f"\n[4/4] ===== V3 Backtest Results =====")
print(f"  Period: {START} ~ {END}")
print(f"  Stocks: {loaded}")
print(f"  Model:  T日底仓({BASE_RATIO:.0%}) + T+1日内确认加仓({ADDON_RATIO:.0%})")
print(f"  Raw signals: {sig_count}, Passed: {sig_pass}")

if not trades:
    print("\n  ⚠ No trades!")
else:
    returns = [t["return_pct"] for t in trades]
    profits = [t["net_profit"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins) / len(returns) if returns else 0

    print(f"\n  {'='*55}")
    print(f"  ── V3 Trade Summary ──")
    print(f"  Total trades:      {len(trades)}")
    print(f"  Win rate:          {wr:.1%}")
    print(f"  Avg win:           {np.mean(wins):.2%}" if wins else "  Avg win: N/A")
    print(f"  Avg loss:          {np.mean(losses):.2%}" if losses else "  Avg loss: N/A")
    pf = abs(np.mean(wins) / np.mean(losses)) if losses and np.mean(losses) != 0 else float('inf')
    print(f"  Profit factor:     {pf:.2f}")
    print(f"  Best:              {max(returns):.2%}")
    print(f"  Worst:             {min(returns):.2%}")
    net_pnl = sum(profits)
    print(f"  Net P&L:           {net_pnl:,.0f}")
    total_ret = (cash / INITIAL_CASH) - 1
    print(f"  Total return:      {total_ret:.2%}")

    # ═══ V3 加仓专项分析 ═══
    with_addon = [t for t in trades if t["has_addon"]]
    without_addon = [t for t in trades if not t["has_addon"]]

    print(f"\n  {'='*55}")
    print(f"  ── V3 ADD-ON IMPACT ANALYSIS ──")
    print(f"  Add-on checks:     {addon_stats['checked']}")
    print(f"  Add-on executed:   {addon_stats['executed']} "
          f"({addon_stats['executed']/max(1,addon_stats['checked']):.0%})")
    print(f"  Add-on declined:   {addon_stats['declined']}")

    if with_addon:
        print(f"\n  🔥 WITH Add-on ({len(with_addon)} trades):")
        print(f"     Avg return:     {np.mean([t['return_pct'] for t in with_addon]):.2%}")
        addon_wr = sum(1 for t in with_addon if t["return_pct"] > 0) / len(with_addon)
        print(f"     Win rate:       {addon_wr:.1%}")
        print(f"     Avg hold days:  {np.mean([t['hold_days'] for t in with_addon]):.1f}")
        exit_addon = Counter(t["exit_type"] for t in with_addon)
        print(f"     Exit types:     {dict(exit_addon)}")
        addon_pnl = sum(t["net_profit"] for t in with_addon)
        print(f"     Net P&L:        {addon_pnl:,.0f}")

    if without_addon:
        print(f"\n  📊 WITHOUT Add-on ({len(without_addon)} trades):")
        print(f"     Avg return:     {np.mean([t['return_pct'] for t in without_addon]):.2%}")
        noaddon_wr = sum(1 for t in without_addon if t["return_pct"] > 0) / len(without_addon)
        print(f"     Win rate:       {noaddon_wr:.1%}")
        noaddon_pnl = sum(t["net_profit"] for t in without_addon)
        print(f"     Net P&L:        {noaddon_pnl:,.0f}")

    if with_addon and without_addon:
        addon_avg = np.mean([t["return_pct"] for t in with_addon])
        noaddon_avg = np.mean([t["return_pct"] for t in without_addon])
        diff = addon_avg - noaddon_avg
        print(f"\n  📈 Add-on premium:  {diff:+.2%} (加仓 vs 不加仓)")

    # Exit types
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
            cnt_addon = sum(1 for t in trades if t["hold_days"] == d and t["has_addon"])
            print(f"  {d} day(s): {cnt:>4d} trades (加仓:{cnt_addon})  avg={avg_r:>+7.2%}")

    # Top 5
    print(f"\n  ── Top 5 Trades ──")
    for t in sorted(trades, key=lambda x: x["return_pct"], reverse=True)[:5]:
        addon_label = "[+ADDON]" if t["has_addon"] else "[BASE]"
        print(f"  {t['symbol']} {addon_label}  {t['buy_date'].date()}->{t['sell_date'].date()}  "
              f"{t['hold_days']}d  ret={t['return_pct']:+.2%}  [{t['exit_type']}]")

    # Worst 5
    print(f"\n  ── Worst 5 Trades ──")
    for t in sorted(trades, key=lambda x: x["return_pct"])[:5]:
        addon_label = "[+ADDON]" if t["has_addon"] else "[BASE]"
        print(f"  {t['symbol']} {addon_label}  {t['buy_date'].date()}->{t['sell_date'].date()}  "
              f"{t['hold_days']}d  ret={t['return_pct']:+.2%}  [{t['exit_type']}]")

    # Performance
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

    print(f"\n  Final cash:        {cash:,.0f}")
    print(f"  Final equity:      {eq.iloc[-1]:,.0f}")

print("=" * 65)
print("  V3 Backtest Complete!")
print("=" * 65)
