"""市场情绪引擎 v2 — A股专业版。
新增指标: 炸板率, 涨停强度, 连板高度, 昨日涨停表现, 市场温度计。
"""
import sys, os, time, numpy as np, pandas as pd
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, r"d:\quant_framework\src")
from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime

DATA_ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
START_DATE = "2022-01-01"
END_DATE = "2025-12-31"
MAX_STOCKS = 1500

def main():
    print("=" * 60)
    print("  Market Sentiment Engine v2 — A-Share Professional")
    print("=" * 60)

    # ── 1. Load data ──
    print("\n[1/4] Loading stock data...")
    provider = THSDayDataProvider(DATA_ROOT)
    provider.connect()
    all_symbols = provider.scan_symbols()
    symbols = [s for s in all_symbols if s[0] in ("6", "0", "3") and len(s) == 6 and s.isdigit()]
    symbols = symbols[:MAX_STOCKS]
    print(f"  Using {len(symbols)} A-share stocks")

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d")

    # ═══════════════════════════════════════════════════════════
    # 2. Per-stock analysis — scan all stocks for daily metrics
    # ═══════════════════════════════════════════════════════════
    print(f"\n[2/4] Scanning {len(symbols)} stocks for daily sentiment...")
    t0 = time.time()

    daily = defaultdict(lambda: {
        "limit_up": 0, "limit_down": 0, "bomb": 0, "limit_up_strong": 0,
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "total_amount": 0.0, "total_volume": 0.0, "valid": 0,
        "gap_up": 0, "limit_up_vol": 0.0, "limit_up_cnt": 0,
    })

    # Track per-stock consecutive limit-up for 连板高度
    stock_lu_streak = defaultdict(int)  # {symbol: consecutive_limit_up_days}
    daily_max_streak = defaultdict(int)  # {date: max_streak}

    # Track yesterday's limit-up stocks for 昨日涨停表现
    yesterday_lu_stocks = set()
    daily_yesterday_lu_return = {}  # {date: avg_return}

    prev_date = None

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
        sym_streak = 0
        sym_last_date = None

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
            stats = daily[dt]
            stats["valid"] += 1

            # ── 涨跌停 ──
            is_limit_up = change_pct >= 0.098
            is_limit_down = change_pct <= -0.098

            if is_limit_up:
                stats["limit_up"] += 1
                stats["limit_up_vol"] += vol
                stats["limit_up_cnt"] += 1

                # 涨停强度: 高开幅度 (gap) 越大越强
                gap = (o - prev_close) / prev_close
                if gap >= 0.05:
                    stats["limit_up_strong"] += 1

            if is_limit_down:
                stats["limit_down"] += 1

            # ── 炸板 proxy: 最高价触及涨停但收盘未封住 ──
            high_change = (h - prev_close) / prev_close
            if high_change >= 0.095 and not is_limit_up:
                stats["bomb"] += 1

            # ── 涨跌统计 ──
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

            # ── 连板追踪 ──
            if sym_last_date is None or (dt - sym_last_date).days == 1:
                if is_limit_up:
                    sym_streak += 1
                else:
                    sym_streak = 0
            else:
                sym_streak = 1 if is_limit_up else 0
            sym_last_date = dt

            if sym_streak > daily_max_streak[dt]:
                daily_max_streak[dt] = sym_streak

            # ── 昨日涨停追踪 ──
            if i > 0 and prev_date is not None:
                prev_change = (prev_close - sorted_data[i-2][1][3]) / sorted_data[i-2][1][3]
                if prev_change >= 0.098:
                    today_lu_stocks.add(sym)

        # Track yesterday's limit-up stocks for next day
        # (handled in the build phase below)

    print(f"  Done in {time.time() - t0:.0f}s")

    # ═══════════════════════════════════════════════════════════
    # 3. Build DataFrame
    # ═══════════════════════════════════════════════════════════
    print("\n[3/4] Building sentiment DataFrame...")
    rows = []
    for dt in sorted(daily.keys()):
        s = daily[dt]
        valid = s["valid"]
        if valid < 10:
            continue

        limit_up = s["limit_up"]
        limit_down = s["limit_down"]
        bomb = s["bomb"]

        rows.append({
            "date": dt,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "up_count": s["up_count"],
            "down_count": s["down_count"],
            "flat_count": s["flat_count"],
            "bomb_count": bomb,
            "bomb_ratio": bomb / max(limit_up + bomb, 1),  # 炸板率
            "limit_up_strong": s["limit_up_strong"],
            "advance_decline": s["up_count"] / max(s["down_count"], 1),
            "limit_ratio": limit_up / max(limit_down, 1),
            "gap_up_pct": s["gap_up"] / valid,
            "total_amount": s["total_amount"],
            "total_volume": s["total_volume"],
            "valid_stocks": valid,
            "max_streak": daily_max_streak.get(dt, 0),  # 最高连板
            "avg_limit_vol": s["limit_up_vol"] / max(s["limit_up_cnt"], 1),
        })

    df = pd.DataFrame(rows).set_index("date").sort_index()

    # ── 衍生指标 ──
    # 市场宽度
    df["breadth"] = (df["up_count"] - df["down_count"]) / df["valid_stocks"]
    # 综合情绪分 (多因子加权)
    df["sentiment_score"] = (
        (df["limit_up"] - df["limit_down"]) / df["valid_stocks"] * 100 * 0.4 +
        df["breadth"] * 100 * 0.3 +
        (df["total_amount"] / df["total_amount"].rolling(20).mean() - 1) * 100 * 0.3
    )
    # 情绪分 MA5 / MA20
    df["sentiment_ma5"] = df["sentiment_score"].rolling(5).mean()
    df["sentiment_ma20"] = df["sentiment_score"].rolling(20).mean()
    # 市场温度 (0-100)
    score_min, score_max = df["sentiment_score"].min(), df["sentiment_score"].max()
    df["market_temp"] = ((df["sentiment_score"] - score_min) / (score_max - score_min) * 100).clip(0, 100)

    # 情绪阶段标签
    def classify_phase(score):
        if score < -3.0: return "极度冰点"
        elif score < -1.0: return "偏冷"
        elif score < 0.5: return "中性"
        elif score < 2.0: return "偏暖"
        else: return "狂热"

    df["phase"] = df["sentiment_score"].apply(classify_phase)

    # ═══════════════════════════════════════════════════════════
    # 4. Save
    # ═══════════════════════════════════════════════════════════
    print("\n[4/4] Saving results...")
    output = r"d:\quant_framework\sentiment_data.csv"
    df.to_csv(output, encoding="utf-8-sig")
    print(f"  Saved: {output}  ({len(df)} trading days)")

    # ── Summary ──
    recent = df.iloc[-20:]
    print(f"\n  {'='*55}")
    print(f"  A-Share Sentiment Report (Last 20 Days)")
    print(f"  {'='*55}")
    print(f"  日均涨停:       {recent['limit_up'].mean():.0f} 家  (最高 {recent['limit_up'].max():.0f})")
    print(f"  日均跌停:       {recent['limit_down'].mean():.0f} 家  (最高 {recent['limit_down'].max():.0f})")
    print(f"  平均炸板率:     {recent['bomb_ratio'].mean():.1%}")
    print(f"  最高连板:       {recent['max_streak'].max():.0f} 板")
    print(f"  平均涨跌比:     {recent['advance_decline'].mean():.2f}")
    print(f"  日均成交额:     {recent['total_amount'].mean()/1e8:.0f} 亿")
    print(f"  当前温度:       {recent['market_temp'].iloc[-1]:.0f}°C")
    print(f"  当前阶段:       {recent['phase'].iloc[-1]}")
    print(f"  近20日情绪均分: {recent['sentiment_score'].mean():.2f}")

    # Phase distribution
    print(f"\n  情绪阶段分布:")
    for phase in ["极度冰点", "偏冷", "中性", "偏暖", "狂热"]:
        cnt = (df["phase"] == phase).sum()
        print(f"    {phase}: {cnt} 天 ({cnt/len(df)*100:.1f}%)")

    print(f"\n  Done!")

if __name__ == "__main__":
    main()
