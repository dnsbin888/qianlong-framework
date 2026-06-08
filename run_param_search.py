"""参数扫描 — 300只股票快速对比多组参数，找到最优配置后再全量跑"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, time, random, numpy as np
import warnings; warnings.filterwarnings("ignore")

from quant_framework.factors.factor_utils import get_factor_for_date

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 300  # 少量快速测试

# ===== 参数网格 =====
PARAM_GRID = [
    # (name, time_days, hard_stop, tb_threshold, atr_mult, tp1, tp2, max_pos)
    # 基准 (当前配置)
    ("baseline",       5, -0.05, 0.3, 2.0, 0.05, 0.10, 5),
    # 时间止损放宽
    ("T10",           10, -0.05, 0.3, 2.0, 0.05, 0.10, 5),
    ("T15",           15, -0.05, 0.3, 2.0, 0.05, 0.10, 5),
    # 硬止损放宽
    ("H-8%",           5, -0.08, 0.3, 2.0, 0.05, 0.10, 5),
    ("H-10%",          5, -0.10, 0.3, 2.0, 0.05, 0.10, 5),
    # 入场阈值提高
    ("TB0.5",          5, -0.05, 0.5, 2.0, 0.05, 0.10, 5),
    ("TB0.7",          5, -0.05, 0.7, 2.0, 0.05, 0.10, 5),
    # 组合优化
    ("opt1",          10, -0.08, 0.5, 2.0, 0.05, 0.10, 5),  # T10+H-8%+TB0.5
    ("opt2",          15, -0.08, 0.5, 2.5, 0.05, 0.10, 5),  # T15+H-8%+TB0.5+ATR2.5
    ("opt3",          10, -0.08, 0.7, 2.0, 0.03, 0.08, 3),  # 收紧入场+早止盈+少持仓
    ("opt4",          20, -0.10, 0.5, 3.0, 0.08, 0.15, 5),  # 宽止损+大止盈+长持有
]

print("=" * 65)
print("  Parameter Grid Search")
print(f"  {len(PARAM_GRID)} configs × {N_SAMPLE} stocks")
print("=" * 65)

# ===== Load data =====
with open(CACHE, "rb") as f:
    raw = pickle.load(f)
random.seed(42); keys = random.sample(list(raw.keys()), min(N_SAMPLE, len(raw)))
data = {k: raw[k] for k in keys}

# Build date index
all_dates = set()
stock_idx = {}
for sym, sd in data.items():
    idx_map = {d: i for i, d in enumerate(sd["dates"]) if 20200101 <= d <= 20260101}
    stock_idx[sym] = idx_map
    all_dates.update(idx_map.keys())
trading_dates = sorted(all_dates)[:500]  # First 500 days (~2 years)

# ===== Helper functions (shared) =====
def get_price(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    return sd["close"][i] if i is not None and i < len(sd["close"]) else None

def get_high(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    return sd["high"][i] if i is not None and i < len(sd.get("high",[])) else None

def is_limit_up(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < 1: return False
    prev_c = sd["close"][i-1]; curr_c = sd["close"][i]
    if prev_c <= 0: return False
    limit_up_p = round(prev_c * 1.10, 2)
    if curr_c >= limit_up_p - 0.01: return True
    if sd["open"][i] == sd["high"][i] == curr_c: return True
    return False

def get_factor(sym, di, fname):
    """P0-因子-01: 按日期取对应年份切片因子，杜绝未来函数"""
    sd = data[sym]
    return get_factor_for_date(sd, di, fname, default=0.0)

def get_atr(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < 14: return None
    h_arr = np.array(sd["high"][i-14:i+1]); l_arr = np.array(sd["low"][i-14:i+1])
    c_arr = np.array([sd["close"][i-15]] + sd["close"][i-14:i])
    tr = np.maximum(h_arr - l_arr, np.maximum(np.abs(h_arr - c_arr), np.abs(l_arr - c_arr)))
    return float(np.mean(tr))

def get_ma(sym, di, p):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < p: return None
    return float(np.mean(sd["close"][i-p+1:i+1]))

# ===== Run each config =====
results = []
for name, time_d, hard_s, tb_th, atr_m, tp1, tp2, max_p in PARAM_GRID:
    t0 = time.time()

    cash = 1_000_000
    holdings = {}  # sym→{entry_p, shares, cost, highest, tp1_done, tp2_done, remain, add_n, last_add, entry_d}
    trades = []

    for di, date_int in enumerate(trading_dates):
        # === EXITS ===
        rm = []
        for sym, h in list(holdings.items()):
            p = get_price(sym, date_int)
            if p is None: continue
            hi = get_high(sym, date_int) or p
            if hi > h["highest"]: h["highest"] = hi
            pnl = (p - h["entry_p"]) / h["entry_p"]
            held = sum(1 for d in data[sym]["dates"] if h["entry_d"] <= d <= date_int)

            exit_r = ""; exit_s = 0

            if pnl <= hard_s: exit_r, exit_s = f"硬止损", h["remain"]
            elif held >= time_d and pnl < 0.01: exit_r, exit_s = f"时间{held}日", h["remain"]
            else:
                atr = get_atr(sym, date_int)
                if atr and p <= h["highest"] - atr * atr_m:
                    exit_r, exit_s = f"ATR追踪", h["remain"]

            if exit_s >= 100:
                cost_b = h["cost"] * (exit_s / h["shares"])
                proceeds = exit_s * p * 0.9987  # comm+tax
                cash += proceeds
                trades.append(dict(pnl=proceeds-cost_b, pnl_pct=(proceeds-cost_b)/cost_b if cost_b>0 else 0))
                rm.append(sym)
                continue

            if not h["tp1_done"] and pnl >= tp1:
                s = int(h["shares"] * 0.33 / 100) * 100
                if s >= 100 and s <= h["remain"]:
                    h["tp1_done"] = True; h["remain"] -= s
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))

            if not h["tp2_done"] and h.get("tp1_done") and pnl >= tp2:
                s = int(h["shares"] * 0.33 / 100) * 100
                if s >= 100 and s <= h["remain"]:
                    h["tp2_done"] = True; h["remain"] -= s
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))

            if h.get("tp2_done") and h["remain"] >= 100:
                ma = get_ma(sym, date_int, 5)
                if ma and p < ma:
                    s = h["remain"]
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))
                    rm.append(sym)

        for sym in rm: holdings.pop(sym, None)

        # === ENTRIES ===
        if len(holdings) < max_p and cash > 50000:
            sigs = []
            for sym in data:
                if sym in holdings: continue
                i = stock_idx[sym].get(date_int)
                if i is None or i < 250: continue
                p = data[sym]["close"][i]
                if p < 3.0: continue
                if is_limit_up(sym, date_int): continue

                tb = get_factor(sym, date_int, "trend_bottom")
                if tb < tb_th: continue

                add = get_factor(sym, date_int, "add_position")
                bp = get_factor(sym, date_int, "bull_position")
                score = tb * 60 + add * 40 + bp * 20
                atr = get_atr(sym, date_int) or 0.01
                sigs.append((sym, score, p, atr))
            sigs.sort(key=lambda x: x[1], reverse=True)

            for sym, score, price, atr in sigs[:max_p - len(holdings)]:
                risk_amt = cash * 0.02
                stop_d = atr * atr_m
                if stop_d <= 0: continue
                shares = int(risk_amt / stop_d / price / 100) * 100
                shares = min(shares, int(cash * 0.20 / price / 100) * 100)
                if shares < 100: continue
                cost = shares * price * 1.0003
                if cost > cash * 0.8: continue
                cash -= cost
                holdings[sym] = dict(entry_p=price, shares=shares, cost=cost, highest=price,
                                    tp1_done=False, tp2_done=False, remain=shares,
                                    add_n=0, last_add=0, entry_d=date_int)

    # ===== Summarize =====
    if trades:
        w = [t for t in trades if t["pnl"] > 0]
        l_ = [t for t in trades if t["pnl"] <= 0]
        wr = len(w) / len(trades)
        aw = np.mean([t["pnl_pct"] for t in w]) if w else 0
        al = np.mean([t["pnl_pct"] for t in l_]) if l_ else 0
        pf = abs(sum(t["pnl"] for t in w) / sum(t["pnl"] for t in l_)) if l_ else float("inf")
        tp = sum(t["pnl"] for t in trades)
    else:
        wr = aw = al = pf = tp = 0

    elapsed = time.time() - t0
    results.append((name, len(trades), wr, aw, al, pf, tp, elapsed))
    print(f"  {name:<12} Trades:{len(trades):>5}  WR:{wr:>5.1%}  "
          f"AvgW:{aw:>5.1%}  AvgL:{al:>5.1%}  PF:{pf:>6.2f}  P&L:{tp:>+10,.0f}  {elapsed:.0f}s")

# ===== Final ranking =====
print(f"\n{'=' * 65}")
print(f"  RANKING by Profit Factor")
print(f"{'=' * 65}")
print(f"  {'Config':<12} {'Trades':>6} {'WinRate':>8} {'AvgWin':>7} {'AvgLoss':>7} {'PF':>6} {'TotalP&L':>12} {'Time':>6}")
print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*7} {'-'*7} {'-'*6} {'-'*12} {'-'*6}")
for name, nt, wr, aw, al, pf, tp, et in sorted(results, key=lambda x: -x[5]):
    print(f"  {name:<12} {nt:>6} {wr:>7.1%} {aw:>6.1%} {al:>6.1%} {pf:>6.2f} {tp:>+12,.0f} {et:>5.0f}s")

# Best recommendation
best = sorted(results, key=lambda x: (-x[5], -x[2]))[0]
print(f"\n  => Best: {best[0]} (PF={best[5]:.2f}, WR={best[2]:.1%}, P&L={best[6]:,.0f})")
print("  Done!")
