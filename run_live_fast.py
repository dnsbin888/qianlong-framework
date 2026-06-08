"""实盘策略快速验证 — 缓存因子 + 涨停过滤 + 完整交易体系"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, time, random, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
CFG = dict(initial_cash=1_000_000, max_pos=5, risk=0.02, atr_m=2.0, hard=-0.08,
           time_days=10, tp1=0.05, s1=0.33, tp2=0.10, s2=0.33, trail=5,
           add_pct=0.03, add_sz=0.50, add_cd=5, comm=0.0003, tax=0.001,
           filt_limit_up=True, min_vol_r=0.8, n_sample=99999, tb_threshold=0.5)

# ===== Load =====
print("=" * 60)
print("  Live Strategy: 涨停过滤 + ATR仓位 + 分批止盈 + 移动止损")
print("=" * 60)

with open(CACHE, "rb") as f:
    raw = pickle.load(f)
random.seed(42); keys = random.sample(list(raw.keys()), min(CFG["n_sample"], len(raw)))
data = {k: raw[k] for k in keys}
print(f"  Stocks: {len(data)}")

# Build date-indexed arrays for fast access
# dates_map[date_int] → [(sym, close, high, open, vol, ...)]
# Pre-compute all signal scores
print("  Pre-computing signals...")
t0 = time.time()
all_dates = set()
stock_idx = {}  # sym → {date_int→index}
for sym, sd in data.items():
    idx_map = {d: i for i, d in enumerate(sd["dates"]) if 20200101 <= d <= 20260101}
    stock_idx[sym] = idx_map
    all_dates.update(idx_map.keys())

trading_dates = sorted(all_dates)
print(f"  {len(trading_dates)} trading days, {time.time()-t0:.0f}s prep")

# ===== Helpers =====
def get_price(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    return sd["close"][i] if i is not None and i < len(sd["close"]) else None

def get_high(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    return sd["high"][i] if i is not None and i < len(sd.get("high",[])) else None

def get_vol(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    return sd["volume"][i] if i is not None and i < len(sd.get("volume",[])) else 0

def is_limit_up(sym, di):
    """涨停过滤: 涨停板±0.01 或 一字板 → 买不到"""
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < 1: return False
    prev_c = sd["close"][i-1]; curr_c = sd["close"][i]
    if prev_c <= 0: return False
    limit_up_p = round(prev_c * 1.10, 2)
    if curr_c >= limit_up_p - 0.01: return True
    # 一字板: open==high==close
    if sd["open"][i] == sd["high"][i] == curr_c: return True
    return False

def get_factor(sym, di, fname):
    """P0-因子-01: 按日期取对应年份切片因子，杜绝未来函数"""
    from quant_framework.factors.factor_utils import get_factor_for_date
    sd = data[sym]
    return get_factor_for_date(sd, di, fname, default=0.0)

def get_atr(sym, di):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < 14: return None
    h_arr = np.array(sd["high"][i-14:i+1]); l_arr = np.array(sd["low"][i-14:i+1])
    c_arr = np.array([sd["close"][i-15]] + sd["close"][i-14:i]) if i >= 15 else np.array([sd["close"][i-1]] + sd["close"][i-13:i])
    tr = np.maximum(h_arr - l_arr, np.maximum(np.abs(h_arr - c_arr), np.abs(l_arr - c_arr)))
    return float(np.mean(tr))

def get_ma(sym, di, p):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < p: return None
    return float(np.mean(sd["close"][i-p+1:i+1]))

# ===== Scan signals =====
def scan(date_int):
    results = []
    for sym in data:
        if sym in holdings: continue
        i = stock_idx[sym].get(date_int)
        if i is None or i < 250: continue
        price = data[sym]["close"][i]
        if price < 3.0: continue

        # 涨停过滤
        if CFG["filt_limit_up"] and is_limit_up(sym, date_int):
            continue

        # 量能过滤 (skip if no volume in cache)
        vol_ok = True
        if "volume" in data[sym] and i >= 5:
            vol_arr = data[sym]["volume"]
            if len(vol_arr) > i:
                avg_v = np.mean(vol_arr[i-5:i])
                if vol_arr[i] < avg_v * CFG["min_vol_r"]:
                    vol_ok = False
        if not vol_ok:
            continue

        tb = get_factor(sym, date_int, "trend_bottom")
        if tb < CFG["tb_threshold"]: continue  # 核心信号不够强

        add = get_factor(sym, date_int, "add_position")
        bp = get_factor(sym, date_int, "bull_position")
        score = tb * 60 + add * 40 + bp * 20
        atr = get_atr(sym, date_int) or 0.01

        results.append((sym, score, price, atr))
    results.sort(key=lambda x: x[1], reverse=True)
    return results

# ===== Trading loop =====
cash = CFG["initial_cash"]
holdings = {}  # sym→{entry_p, shares, cost, highest, tp1, tp2, remain, add_n, last_add, entry_d}
trades = []

t0 = time.time()
for di, date_int in enumerate(trading_dates):
    if di % 300 == 0:
        mkt = sum(h["remain"] * (get_price(s, date_int) or 0) for s, h in holdings.items())
        elapsed = max(time.time()-t0, 0.1)
        eta = (len(trading_dates)-di)/(di+1)*elapsed if di else 0
        print(f"  {di}/{len(trading_dates)} Trades:{len(trades)} Equity:{cash+mkt:,.0f} ETA:{eta:.0f}s")

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

        # Hard stop
        if pnl <= CFG["hard"]:
            exit_r, exit_s = f"硬止损{pnl:.1%}", h["remain"]
        # Time stop
        elif held >= CFG["time_days"] and pnl < 0.01:
            exit_r, exit_s = f"时间{held}日", h["remain"]
        # ATR trailing
        else:
            atr = get_atr(sym, date_int)
            if atr and p <= h["highest"] - atr * CFG["atr_m"]:
                exit_r, exit_s = f"ATR追踪{pnl:.1%}", h["remain"]

        if exit_s >= 100:
            cost_b = h["cost"] * (exit_s / h["shares"])
            proceeds = exit_s * p * (1 - CFG["comm"] - CFG["tax"])
            cash += proceeds
            trades.append(dict(date=date_int, sym=sym, b_p=h["entry_p"], s_p=p,
                               shares=exit_s, pnl=proceeds-cost_b,
                               pnl_pct=(proceeds-cost_b)/cost_b if cost_b>0 else 0,
                               reason=exit_r))
            rm.append(sym)
            continue

        # TP Tier 1
        if not h["tp1"] and pnl >= CFG["tp1"]:
            s = int(h["shares"] * CFG["s1"] / 100) * 100
            if s >= 100 and s <= h["remain"]:
                h["tp1"] = True; h["remain"] -= s
                cost_b = h["cost"] * (s / h["shares"])
                proceeds = s * p * (1 - CFG["comm"] - CFG["tax"])
                cash += proceeds
                trades.append(dict(date=date_int, sym=sym, b_p=h["entry_p"], s_p=p,
                                   shares=s, pnl=proceeds-cost_b,
                                   pnl_pct=(proceeds-cost_b)/cost_b if cost_b>0 else 0,
                                   reason=f"止盈T1 +{pnl:.1%}"))
        # TP Tier 2
        if not h["tp2"] and h["tp1"] and pnl >= CFG["tp2"]:
            s = int(h["shares"] * CFG["s2"] / 100) * 100
            if s >= 100 and s <= h["remain"]:
                h["tp2"] = True; h["remain"] -= s
                cost_b = h["cost"] * (s / h["shares"])
                proceeds = s * p * (1 - CFG["comm"] - CFG["tax"])
                cash += proceeds
                trades.append(dict(date=date_int, sym=sym, b_p=h["entry_p"], s_p=p,
                                   shares=s, pnl=proceeds-cost_b,
                                   pnl_pct=(proceeds-cost_b)/cost_b if cost_b>0 else 0,
                                   reason=f"止盈T2 +{pnl:.1%}"))
        # Trailing MA
        if h["tp2"] and h["remain"] >= 100:
            ma = get_ma(sym, date_int, CFG["trail"])
            if ma and p < ma:
                s = h["remain"]
                cost_b = h["cost"] * (s / h["shares"])
                proceeds = s * p * (1 - CFG["comm"] - CFG["tax"])
                cash += proceeds
                trades.append(dict(date=date_int, sym=sym, b_p=h["entry_p"], s_p=p,
                                   shares=s, pnl=proceeds-cost_b,
                                   pnl_pct=(proceeds-cost_b)/cost_b if cost_b>0 else 0,
                                   reason=f"MA{CFG['trail']}出场{pnl:.1%}"))
                rm.append(sym)

    for sym in rm:
        holdings.pop(sym, None)

    # === ENTRIES ===
    if len(holdings) < CFG["max_pos"] and cash > CFG["initial_cash"] * 0.05:
        sigs = scan(date_int)
        for sym, score, price, atr in sigs[:CFG["max_pos"] - len(holdings)]:
            risk_amt = cash * CFG["risk"]
            stop_d = atr * CFG["atr_m"]
            if stop_d <= 0: continue
            shares = int(risk_amt / stop_d / price / 100) * 100
            shares = min(shares, int(cash * 0.20 / price / 100) * 100)
            if shares < 100: continue
            cost = shares * price * (1 + CFG["comm"])
            if cost > cash * 0.8: continue
            cash -= cost
            holdings[sym] = dict(entry_p=price, shares=shares, cost=cost, highest=price,
                                tp1=False, tp2=False, remain=shares, add_n=0, last_add=0, entry_d=date_int)

    # === ADD ===
    for sym, h in list(holdings.items()):
        if h["add_n"] >= 2: continue
        p = get_price(sym, date_int)
        if p is None: continue
        pnl = (p - h["entry_p"]) / h["entry_p"]
        days_since = sum(1 for d in data[sym]["dates"] if max(h["last_add"], h["entry_d"]) < d <= date_int)
        if pnl > CFG["add_pct"] and days_since >= CFG["add_cd"]:
            add_s = int(h["shares"] * CFG["add_sz"] / 100) * 100
            if add_s >= 100:
                add_cost = add_s * p * (1 + CFG["comm"])
                if add_cost < cash * 0.3:
                    cash -= add_cost
                    h["shares"] += add_s; h["remain"] += add_s
                    h["cost"] += add_cost; h["entry_p"] = h["cost"] / h["shares"]
                    h["add_n"] += 1; h["last_add"] = date_int

# ===== Report =====
print(f"\n{'='*60}")
print(f"  RESULTS ({len(data)} stocks, {len(trading_dates)} days)")
print(f"{'='*60}")

if not trades:
    print("  No trades executed!")
    sys.exit(1)

w = [t for t in trades if t["pnl"] > 0]
l_ = [t for t in trades if t["pnl"] <= 0]
wr = len(w) / len(trades) if trades else 0
aw = np.mean([t["pnl_pct"] for t in w]) if w else 0
al = np.mean([t["pnl_pct"] for t in l_]) if l_ else 0
pf = abs(sum(t["pnl"] for t in w) / sum(t["pnl"] for t in l_)) if l_ else float("inf")
tp = sum(t["pnl"] for t in trades)

# By exit reason
rs = defaultdict(lambda: [0, 0.0])  # count, total_pnl
for t in trades:
    k = t["reason"][:2]
    rs[k][0] += 1; rs[k][1] += t["pnl"]

print(f"  Total Trades:  {len(trades):>6}")
print(f"  Win Rate:      {wr:>6.1%}  ({len(w)}W / {len(l_)}L)")
print(f"  Avg Win:       {aw:>6.2%}")
print(f"  Avg Loss:      {al:>6.2%}")
print(f"  Profit Factor: {pf:>6.2f}")
print(f"  Total P&L:     {tp:>+6,.0f}")
print(f"  Final Cash:    {cash:>6,.0f}")
print(f"  Holdings:      {len(holdings):>6}")
print()

print(f"  Exit Breakdown:")
for k, (c, pnl) in sorted(rs.items(), key=lambda x: -x[1][0]):
    print(f"    {k}... : {c:>5} trades, P&L={pnl:>+10,.0f}")

# Worst/Best
st = sorted(trades, key=lambda t: t["pnl_pct"])
w3 = [(t["sym"], t["pnl_pct"]) for t in st[:3]]
b3 = [(t["sym"], t["pnl_pct"]) for t in st[-3:]]
print(f"\n  Worst 3: {w3}")
print(f"  Best 3:  {b3}")
print("\n  Done!")
