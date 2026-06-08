r"""实时信号扫描 — 扫描当前全市场, 输出活跃的TDX买入信号。

基于因子缓存，扫描最新交易日，按评分排序输出买入候选。

用法:
  python scan_signals.py              # 默认: 全部2000只, 输出Top30
  python scan_signals.py --top 50     # 输出Top50
  python scan_signals.py --min-price 10 --min-vol 50000000  # 价格>10+日成交>5000万
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, argparse, numpy as np
import warnings; warnings.filterwarnings("ignore")

from quant_framework.factors.factor_utils import get_factor_for_date, has_year_sliced_factors

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"

parser = argparse.ArgumentParser(description="Scan current market for TDX buy signals")
parser.add_argument("--top", type=int, default=30, help="Show top N signals")
parser.add_argument("--min-price", type=float, default=5.0, help="Minimum price filter")
parser.add_argument("--min-vol", type=float, default=50_000_000, help="Minimum daily turnover (CNY)")
parser.add_argument("--tb-threshold", type=float, default=0.5, help="Trend bottom threshold")
parser.add_argument("--all", action="store_true", help="Show all signals (no top-N limit)")
args = parser.parse_args()

print("=" * 70)
print("  TDX Signal Scanner — Current Market Buy Candidates")
print("=" * 70)
print(f"  Filters: price>={args.min_price}, turnover>={args.min_vol/1e4:.0f}万, tb>={args.tb_threshold}")
print()

# ===== Load =====
with open(CACHE, "rb") as f:
    data = pickle.load(f)

# ===== Find latest trading day =====
latest_date = 0
for sd in data.values():
    for d in sd["dates"]:
        if len(str(d)) == 8 and d > latest_date and d <= 20260601:
            latest_date = d

print(f"  Latest trading day: {str(latest_date)[:4]}-{str(latest_date)[4:6]}-{str(latest_date)[6:8]}")
print(f"  Stock universe: {len(data)} stocks")
print()

# ===== Scan =====
signals = []
n_price_filtered = 0
n_vol_filtered = 0
n_limit_up = 0
n_tb_filtered = 0
n_no_data = 0

for sym, sd in data.items():
    if latest_date not in sd["dates"]:
        n_no_data += 1
        continue

    i = sd["dates"].index(latest_date)
    if i < 250:
        n_no_data += 1
        continue

    price = sd["close"][i]

    # Price filter
    if price < args.min_price:
        n_price_filtered += 1
        continue

    # Volume filter
    vols = sd["volume"][max(0, i-20):i+1]
    if len(vols) >= 5 and np.mean(vols) * price < args.min_vol:
        n_vol_filtered += 1
        continue

    # Limit-up check
    if i >= 1:
        prev_c = sd["close"][i-1]
        if prev_c > 0 and price >= round(prev_c * 1.10, 2) - 0.01:
            n_limit_up += 1
            continue
        if sd["open"][i] == sd["high"][i] == price:
            n_limit_up += 1
            continue

    # Factor values — P0-因子-01: 按日期取对应年份切片因子，杜绝未来函数
    _fdef = lambda name, d=0.0: get_factor_for_date(sd, latest_date, name, default=d)
    tb = _fdef("trend_bottom")
    add = int(_fdef("add_position"))
    bp = _fdef("bull_position")

    if tb < args.tb_threshold:
        n_tb_filtered += 1
        continue

    # Compute ATR
    atr = 0.01
    if i >= 14:
        h_arr = np.array(sd["high"][i-14:i+1])
        l_arr = np.array(sd["low"][i-14:i+1])
        c_arr = np.array([sd["close"][i-15]] + sd["close"][i-14:i])
        tr = np.maximum(h_arr - l_arr, np.maximum(np.abs(h_arr - c_arr), np.abs(l_arr - c_arr)))
        atr = float(np.mean(tr))

    # Score
    score = tb * 60 + add * 40 + bp * 20

    # ATR-based position size
    risk_amt = 1_000_000 * 0.02
    stop_d = atr * 2.0
    shares = int(risk_amt / stop_d / price / 100) * 100 if stop_d > 0 else 0

    # Daily change
    chg_pct = (price - sd["close"][i-1]) / sd["close"][i-1] * 100 if i >= 1 and sd["close"][i-1] > 0 else 0

    # Avg volume (20d)
    avg_vol = np.mean(sd["volume"][max(0,i-20):i+1])
    turnover_amt = avg_vol * price

    signals.append({
        "symbol": sym,
        "price": price,
        "chg%": chg_pct,
        "score": score,
        "tb": tb,
        "add": int(add),
        "bp": bp,
        "atr": atr,
        "atr%": atr / price * 100 if price > 0 else 0,
        "shares": shares,
        "amount": shares * price,
        "turnover_w": turnover_amt / 1e4,
        "trend": "UP" if bp > 0 else "DN",
    })

# Sort by score
signals.sort(key=lambda x: x["score"], reverse=True)

# ===== Output =====
print(f"  Filtered: price={n_price_filtered}, volume={n_vol_filtered}, "
      f"limit_up={n_limit_up}, tb={n_tb_filtered}, no_data={n_no_data}")
print(f"  Signals found: {len(signals)}")
print()

if not signals:
    print("  No signals found matching criteria. Try relaxing filters.")
    sys.exit(0)

# Table header
print(f"{'Rank':<5} {'Symbol':<10} {'Price':>8} {'Chg%':>7} {'Score':>7} "
      f"{'TB':>6} {'Add':>4} {'Trend':>5} {'ATR%':>6} {'Turnover(万)':>13} {'PosSize(股)':>12}")
print(f"{'-'*5} {'-'*10} {'-'*8} {'-'*7} {'-'*7} {'-'*6} {'-'*4} {'-'*5} {'-'*6} {'-'*13} {'-'*12}")

top_n = len(signals) if args.all else min(args.top, len(signals))
for rank, s in enumerate(signals[:top_n], 1):
    print(f"{rank:<5} {s['symbol']:<10} {s['price']:>8.2f} {s['chg%']:>+6.2f}% {s['score']:>7.1f} "
          f"{s['tb']:>6.3f} {s['add']:>4} {s['trend']:>5} {s['atr%']:>5.1f}% "
          f"{s['turnover_w']:>12.0f}万 {s['shares']:>12}")

# Summary stats
scores = [s["score"] for s in signals]
print(f"\n  Score range: {min(scores):.1f} ~ {max(scores):.1f}, mean={np.mean(scores):.1f}")
print(f"  Strongest: {signals[0]['symbol']} (score={signals[0]['score']:.0f}, "
      f"TB={signals[0]['tb']:.3f}, price={signals[0]['price']:.2f})")

# Recommended watchlist
print(f"\n  === Recommended Buy Candidates (Top 5) ===")
for s in signals[:5]:
    print(f"  {s['symbol']:<10}  Price={s['price']:.2f}  TB={s['tb']:.3f}  Add={'YES' if s['add'] else 'no'}  "
          f"ATR={s['atr']:.3f}({s['atr%']:.1f}%)  Pos={s['shares']}shares  "
          f"Risk={(s['shares'] * s['atr'] * 2.0):.0f}CNY")
    # Stop levels
    print(f"             Stop: -8%={s['price']*0.92:.2f}  ATR={s['price']-s['atr']*2.0:.2f}  "
          f"TP1(+5%)={s['price']*1.05:.2f}  TP2(+10%)={s['price']*1.10:.2f}")

print(f"\n  Done! {len(signals)} buy candidates found.")
