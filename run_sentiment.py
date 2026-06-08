"""市场情绪指标计算 — 从通达信日线统计每日情绪。

输出: sentiment_data.csv
  - date: 交易日
  - limit_up: 涨停家数
  - limit_down: 跌停家数
  - up_count/down_count: 上涨/下跌家数
  - advance_decline: 涨跌比
  - total_amount: 全市场成交额
  - sentiment_score: 综合情绪分 (正值=乐观, 负值=悲观)
"""

import sys
import os
import time
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, r"d:\quant_framework\src")
from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime

DATA_ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
START_DATE = "2022-01-01"
END_DATE = "2025-12-31"
MAX_STOCKS = 1500  # Limit for speed

def main():
    print("=" * 60)
    print("  Market Sentiment Engine")
    print("=" * 60)
    print(f"  Data: {DATA_ROOT}")
    print(f"  Period: {START_DATE} -> {END_DATE}")

    # Load
    print("\n[1/3] Loading stock data...")
    provider = THSDayDataProvider(DATA_ROOT)
    provider.connect()
    all_symbols = provider.scan_symbols()
    symbols = [s for s in all_symbols if s[0] in ("6", "0", "3") and len(s) == 6 and s.isdigit()]
    symbols = symbols[:MAX_STOCKS]
    print(f"  Using {len(symbols)} A-share stocks")

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d")

    # Collect daily records
    print("\n[2/3] Computing daily sentiment...")
    t0 = time.time()
    daily_stats = defaultdict(lambda: {
        "limit_up": 0, "limit_down": 0,
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "total_amount": 0.0, "total_volume": 0.0, "valid": 0,
        "gap_up": 0,
    })

    for si, sym in enumerate(symbols):
        if si % 300 == 0:
            elapsed = time.time() - t0
            rate = (si + 1) / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - si) / rate if rate > 0 else 0
            print(f"  {si}/{len(symbols)} ({si/len(symbols)*100:.0f}%) rate={rate:.1f}/s ETA={eta:.0f}s")

        data_obj = provider._read_day_file(sym)
        if not data_obj or len(data_obj) < 200:
            continue

        sorted_data = sorted(data_obj.items())
        for i in range(1, len(sorted_data)):
            date_int, (o, h, l, c, amt, vol) = sorted_data[i]
            dt = _date_to_datetime(date_int)
            if dt is None or dt < start_dt or dt > end_dt:
                continue
            if o <= 0 or c <= 0:
                continue

            _, prev = sorted_data[i - 1]
            prev_close = prev[3]
            if prev_close <= 0:
                continue

            change_pct = (c - prev_close) / prev_close
            stats = daily_stats[dt]
            stats["valid"] += 1

            if change_pct >= 0.098:
                stats["limit_up"] += 1
            elif change_pct <= -0.098:
                stats["limit_down"] += 1

            if change_pct > 0:
                stats["up_count"] += 1
            elif change_pct < 0:
                stats["down_count"] += 1
            else:
                stats["flat_count"] += 1

            if o > prev_close:
                stats["gap_up"] += 1

            stats["total_amount"] += amt if amt > 0 else 0
            stats["total_volume"] += vol if vol > 0 else 0

    print(f"  Done in {time.time() - t0:.0f}s")

    # Build DataFrame
    print("\n[3/3] Building sentiment DataFrame...")
    rows = []
    for dt in sorted(daily_stats.keys()):
        s = daily_stats[dt]
        valid = s["valid"]
        if valid < 10:
            continue
        rows.append({
            "date": dt,
            "limit_up": s["limit_up"],
            "limit_down": s["limit_down"],
            "up_count": s["up_count"],
            "down_count": s["down_count"],
            "flat_count": s["flat_count"],
            "advance_decline": s["up_count"] / max(s["down_count"], 1),
            "limit_ratio": s["limit_up"] / max(s["limit_down"], 1),
            "gap_up_pct": s["gap_up"] / valid,
            "total_amount": s["total_amount"],
            "total_volume": s["total_volume"],
            "valid_stocks": valid,
        })

    df = pd.DataFrame(rows).set_index("date").sort_index()

    # Sentiment score: normalized composite
    df["sentiment_score"] = (
        (df["limit_up"] - df["limit_down"]) / df["valid_stocks"] * 100
    )
    # Market breadth
    df["breadth"] = (df["up_count"] - df["down_count"]) / df["valid_stocks"]

    # Save
    output = r"d:\quant_framework\sentiment_data.csv"
    df.to_csv(output, encoding="utf-8-sig")
    print(f"  Saved: {output}")
    print(f"  Trading days: {len(df)}")

    # Stats
    print(f"\n  --- Sentiment Summary ---")
    print(f"  Avg daily limit-up:   {df['limit_up'].mean():.0f}")
    print(f"  Max daily limit-up:   {df['limit_up'].max():.0f}")
    print(f"  Avg daily limit-down: {df['limit_down'].mean():.0f}")
    print(f"  Max daily limit-down: {df['limit_down'].max():.0f}")
    print(f"  Avg advance/decline:  {df['advance_decline'].mean():.2f}")
    print(f"  Avg breadth:          {df['breadth'].mean():.3f}")
    print(f"  Market >50% up days:  {(df['breadth'] > 0).mean():.1%}")

    # Emotion phases
    ice_days = (df["limit_up"] <= 15) & (df["limit_down"] > df["limit_up"])
    hot_days = (df["limit_up"] >= 80)
    print(f"  Ice days (<15 limit-up):  {ice_days.sum()} ({(ice_days).mean():.1%})")
    print(f"  Hot days (>80 limit-up):  {hot_days.sum()} ({(hot_days).mean():.1%})")
    print()

    return df


if __name__ == "__main__":
    main()
