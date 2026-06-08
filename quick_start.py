"""
量化小白快速上手脚本 — 一键下载数据 + 回测 + 看结果
运行: python quick_start.py                          (默认: 600000 浦发银行)
      python quick_start.py 600519                   (指定: 贵州茅台)
      python quick_start.py 002415 20200101          (指定股票+起始日期)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# ============================================================
# 第一步：解析参数
# ============================================================
STOCKS = {
    "600000": "浦发银行",
    "600036": "招商银行",
    "601318": "中国平安",
    "000858": "五粮液",
    "002415": "海康威视",
    "600519": "贵州茅台",
    "300750": "宁德时代",
}

symbol = sys.argv[1].strip() if len(sys.argv) > 1 else "600000"
start_date = sys.argv[2].strip() if len(sys.argv) > 2 else "20230101"
end_date = sys.argv[3].strip() if len(sys.argv) > 3 else datetime.now().strftime("%Y%m%d")
name = STOCKS.get(symbol, symbol)

print("=" * 55)
print("  量化策略快速回测 — 小白入门版")
print("=" * 55)

# 如果参数不对，显示帮助
if symbol == "--help" or symbol == "-h":
    print("\n用法: python quick_start.py [股票代码] [开始日期] [结束日期]")
    print("示例: python quick_start.py 600519 20200101 20250601\n")
    print("可选股票:")
    for code, name in STOCKS.items():
        print(f"  {code}  {name}")
    sys.exit(0)

print(f"\n股票: {symbol} {name}")
print(f"区间: {start_date} ~ {end_date}")
print(f"\n正在下载数据...")

# ============================================================
# 第二步：下载真实行情数据
# ============================================================
try:
    # 两个接口尝试
    prefix = "sh" if symbol.startswith("6") else "sz"
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq"
        )
        # 中文列名 → 英文
        col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
                    "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_change"}
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    except Exception:
        # 备用接口
        df = ak.stock_zh_a_daily(
            symbol=f"{prefix}{symbol}", start_date=start_date, end_date=end_date, adjust="qfq"
        )

    if df is None or df.empty:
        print("未获取到数据，请检查股票代码是否正确")
        sys.exit(1)

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    print(f"已获取 {len(df)} 条日线数据 ({df.index[0].date()} ~ {df.index[-1].date()})")

except Exception as e:
    print(f"下载失败: {e}")
    print("提示: 确保网络正常，股票代码正确")
    sys.exit(1)

# ============================================================
# 第三步：计算 MACD 指标
# ============================================================
def compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist

df["dif"], df["dea"], df["hist"] = compute_macd(df["close"])

# ============================================================
# 第四步：生成交易信号（MACD 金叉买入 / 死叉卖出）
# ============================================================
df["signal"] = 0

# 金叉: DIF 上穿 DEA
golden_cross = (df["dif"] > df["dea"]) & (df["dif"].shift(1) <= df["dea"].shift(1))
df.loc[golden_cross, "signal"] = 1

# 死叉: DIF 下穿 DEA
death_cross = (df["dif"] < df["dea"]) & (df["dif"].shift(1) >= df["dea"].shift(1))
df.loc[death_cross, "signal"] = -1

# ============================================================
# 第五步：模拟交易
# ============================================================
initial_cash = 100000  # 10万本金
cash = initial_cash
shares = 0
trades = []

for i in range(1, len(df)):
    date = df.index[i]
    price = float(df["close"].iloc[i])
    sig = int(df["signal"].iloc[i])

    # 买入信号
    if sig == 1 and cash >= price * 100:
        buy_shares = int(cash * 0.95 / price / 100) * 100  # 95%仓位，整手
        if buy_shares >= 100:
            cost = buy_shares * price * 1.0003  # 万三佣金
            cash -= cost
            shares += buy_shares
            trades.append({"date": date, "type": "买入", "price": price,
                           "shares": buy_shares, "cost": cost, "cash": cash,
                           "value": cash + shares * price})

    # 卖出信号
    elif sig == -1 and shares >= 100:
        sell_shares = shares
        revenue = sell_shares * price * (1 - 0.0003 - 0.001)  # 佣金+印花税
        cash += revenue
        trades.append({"date": date, "type": "卖出", "price": price,
                       "shares": sell_shares, "revenue": revenue, "cash": cash,
                       "value": cash + 0})
        shares = 0

# 最后一天强制清仓
if shares >= 100:
    last_price = float(df["close"].iloc[-1])
    revenue = shares * last_price * (1 - 0.0003 - 0.001)
    cash += revenue
    trades.append({"date": df.index[-1], "type": "清仓", "price": last_price,
                   "shares": shares, "revenue": revenue, "cash": cash,
                   "value": cash})
    shares = 0

final_value = cash + shares * float(df["close"].iloc[-1])

# ============================================================
# 第六步：输出结果
# ============================================================
print("\n" + "=" * 55)
print("  回测结果")
print("=" * 55)

if trades:
    trades_df = pd.DataFrame(trades)
    buys = trades_df[trades_df["type"] == "买入"]
    sells = trades_df[trades_df["type"].isin(["卖出", "清仓"])]

    # 配对计算每笔盈亏
    profits = []
    for i in range(min(len(buys), len(sells))):
        buy_price = buys.iloc[i]["price"]
        sell_price = sells.iloc[i]["price"]
        buy_shares = buys.iloc[i]["shares"]
        ret = (sell_price / buy_price - 1) * 100
        profits.append(ret)

    win_count = sum(1 for p in profits if p > 0)
    total_return = (final_value / initial_cash - 1) * 100

    print(f"  初始资金:     {initial_cash:>12,} 元")
    print(f"  最终资金:     {final_value:>12,.0f} 元")
    print(f"  总收益率:     {total_return:>+12.2f}%")
    print(f"  交易次数:     {len(trades):>12} 笔")
    if profits:
        print(f"  盈利次数:     {win_count:>12} 笔")
        print(f"  胜率:         {win_count/len(profits)*100:>12.1f}%")
        print(f"  平均单笔收益: {np.mean(profits):>+12.2f}%")
        print(f"  最大单笔收益: {np.max(profits):>+12.2f}%")
        print(f"  最大单笔亏损: {np.min(profits):>+12.2f}%")

    # 与买入持有对比
    buy_hold_return = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1) * 100
    print(f"\n  买入持有收益: {buy_hold_return:>+12.2f}% (不做任何交易)")
    print(f"  策略超额收益: {total_return - buy_hold_return:>+12.2f}%")

    print(f"\n  交易明细:")
    for t in trades:
        action = t["type"]
        p = t["price"]
        s = t["shares"]
        d = str(t["date"])[:10]
        print(f"    {d}  {action:4s}  {p:.2f}元 × {s}股")

else:
    print("  未产生任何交易信号")

# ============================================================
# 第七步：保存结果
# ============================================================
os.makedirs("./data/market", exist_ok=True)
df.to_csv(f"./data/market/{symbol}_1d.csv")
print(f"\n数据已保存: ./data/market/{symbol}_1d.csv")
print("=" * 55)
