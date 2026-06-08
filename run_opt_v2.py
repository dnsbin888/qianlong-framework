r"""优化V2 — 多信号共振 + 大盘择时 + 宽ATR止损 + 分批止盈"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, time, random, numpy as np
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"

# ===== 测试三组配置 =====
CONFIGS = [
    # 核心假设: 策略在大市值+高流动性股票上更有效
    ("日成交>1亿+价>10", 0.5, False, False, False, 2.0, -0.08, 10, 5, 100_000_000),
    ("日成交>5000万+价>5",0.5, False, False, False, 2.0, -0.08, 10, 5, 50_000_000),
    ("日成交>2000万",     0.5, False, False, False, 2.0, -0.08, 10, 5, 20_000_000),
]

N_SAMPLE = 2000  # 全量验证

print("=" * 65)
print("  Optimization V2: Multi-Signal + Market Timing + Wide ATR")
print("=" * 65)

# ===== Load =====
with open(CACHE, "rb") as f: raw = pickle.load(f)
random.seed(42); keys = random.sample(list(raw.keys()), min(N_SAMPLE, len(raw)))
data = {k: raw[k] for k in keys}

# Build date index
all_dates = set()
stock_idx = {}
for sym, sd in data.items():
    idx_map = {d: i for i, d in enumerate(sd["dates"]) if 20200101 <= d <= 20260101}
    stock_idx[sym] = idx_map
    all_dates.update(idx_map.keys())
trading_dates = sorted(all_dates)

# ===== Load market index for timing =====
print("  Loading index data...")
provider = None
index_closes = {}
try:
    from quant_framework.data.providers.ths_day import THSDayDataProvider
    provider = THSDayDataProvider()
    provider.connect()
    for sym in ["999999", "1A0001", "000001"]:
        idx_data = provider._read_day_file(sym)
        if idx_data and len(idx_data) > 500:
            for d, (o, h, l, c, amt, vol) in idx_data.items():
                index_closes[d] = c
            print(f"  Index: {sym} ({len(idx_data)} records)")
            break
except Exception:
    print("  No index data, skipping market filter")
    index_closes = {}

# ===== Helpers =====
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
    if curr_c >= round(prev_c * 1.10, 2) - 0.01: return True
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
    c_arr = np.array([sd["close"][i-15]] + sd["close"][i-14:i])
    tr = np.maximum(h_arr - l_arr, np.maximum(np.abs(h_arr - c_arr), np.abs(l_arr - c_arr)))
    return float(np.mean(tr))

def get_ma(sym, di, p):
    sd = data[sym]; i = stock_idx[sym].get(di)
    if i is None or i < p: return None
    return float(np.mean(sd["close"][i-p+1:i+1]))

def is_bear_market(di):
    """MA60 趋势判断: 指数 < MA60 = 熊市, 不做多"""
    if not index_closes: return False
    dates = sorted(index_closes.keys())
    prev = [d for d in dates if d <= di]
    if len(prev) < 120: return False
    closes = [index_closes[d] for d in prev[-120:]]
    ma60 = np.mean(closes[-60:])
    return closes[-1] < ma60

# ===== Run each config =====
results = []
for name, tb_th, req_add, req_mf, market_f, atr_m, hard_s, time_d, max_p, min_vol in CONFIGS:
    t0 = time.time()
    cash = 1_000_000
    holdings = {}
    trades = []
    n_bear_skipped = 0

    for di, date_int in enumerate(trading_dates):
        # Market timing: skip entries in bear market
        bear = market_f and is_bear_market(date_int)

        # === EXITS ===
        rm = []
        for sym, h in list(holdings.items()):
            p = get_price(sym, date_int)
            if p is None: continue
            hi = get_high(sym, date_int) or p
            if hi > h["highest"]: h["highest"] = hi
            pnl = (p - h["entry_p"]) / h["entry_p"]
            held = sum(1 for d in data[sym]["dates"] if h["entry_d"] <= d <= date_int)

            exit_r, exit_s = "", 0
            if pnl <= hard_s: exit_r, exit_s = "硬止损", h["remain"]
            elif held >= time_d and pnl < 0.01: exit_r, exit_s = f"时间{held}日", h["remain"]
            else:
                atr = get_atr(sym, date_int)
                if atr and p <= h["highest"] - atr * atr_m:
                    exit_r, exit_s = "ATR追踪", h["remain"]

            if exit_s >= 100:
                cost_b = h["cost"] * (exit_s / h["shares"])
                cash += exit_s * p * 0.9987
                trades.append(dict(pnl=exit_s*p*0.9987-cost_b, pnl_pct=(exit_s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))
                rm.append(sym)
                continue

            # TP tiers
            if not h["tp1"] and pnl >= 0.05:
                s = int(h["shares"] * 0.33 / 100) * 100
                if s >= 100 and s <= h["remain"]:
                    h["tp1"] = True; h["remain"] -= s
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))
            if not h["tp2"] and h.get("tp1") and pnl >= 0.10:
                s = int(h["shares"] * 0.33 / 100) * 100
                if s >= 100 and s <= h["remain"]:
                    h["tp2"] = True; h["remain"] -= s
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))
            if h.get("tp2") and h["remain"] >= 100:
                ma = get_ma(sym, date_int, 5)
                if ma and p < ma:
                    s = h["remain"]
                    cost_b = h["cost"] * (s / h["shares"])
                    cash += s * p * 0.9987
                    trades.append(dict(pnl=s*p*0.9987-cost_b, pnl_pct=(s*p*0.9987-cost_b)/cost_b if cost_b>0 else 0))
                    rm.append(sym)

        for sym in rm: holdings.pop(sym, None)

        # === ENTRIES (skip in bear market) ===
        if bear:
            n_bear_skipped += 1
            continue

        if len(holdings) < max_p and cash > 50000:
            sigs = []
            for sym in data:
                if sym in holdings: continue
                i = stock_idx[sym].get(date_int)
                if i is None or i < 250: continue
                p = data[sym]["close"][i]
                if p < 3.0: continue
                # 流动性+价格过滤
                if min_vol > 0:
                    vols = data[sym]["volume"][max(0,i-20):i+1]
                    if len(vols) >= 5 and np.mean(vols) * p < min_vol:
                        continue
                    # 价格门槛 (日成交>5000万时价格至少5元, >1亿时至少10元)
                    if min_vol >= 100_000_000 and p < 10: continue
                    if min_vol >= 50_000_000 and p < 5: continue
                if is_limit_up(sym, date_int): continue

                # Multi-signal check
                tb = get_factor(sym, date_int, "trend_bottom")
                if tb < tb_th: continue
                if req_add:
                    add = get_factor(sym, date_int, "add_position")
                    if add < 0.5: continue  # 需要加仓信号共振
                if req_mf:
                    bp = get_factor(sym, date_int, "bull_position")
                    if bp > 0: continue  # 牛线上方不做(等回调)

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
                                    tp1=False, tp2=False, remain=shares, add_n=0, entry_d=date_int)

    # === Stats ===
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
    results.append((name, len(trades), wr, aw, al, pf, tp, n_bear_skipped, elapsed))
    flags = []
    if req_add: flags.append("共振")
    if market_f: flags.append("择时")
    if atr_m >= 3: flags.append("宽ATR")
    flag_str = "+".join(flags) if flags else "baseline"
    print(f"  {name:<16} T:{len(trades):>4}  WR:{wr:>5.1%}  AW:{aw:>5.1%}  AL:{al:>5.1%}  "
          f"PF:{pf:>5.2f}  P&L:{tp:>+10,.0f}  SkipBear:{n_bear_skipped}  {elapsed:.0f}s")

# ===== Ranking =====
print(f"\n{'='*65}")
print(f"  RANKING ({len(data)} stocks, {len(trading_dates)} days)")
print(f"{'='*65}")
print(f"  {'Config':<18} {'Trades':>5} {'WR':>6} {'AW':>6} {'AL':>6} {'PF':>5} {'P&L':>10} {'Skipped':>8}")
print(f"  {'-'*18} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*5} {'-'*10} {'-'*8}")
for (name, nt, wr, aw, al, pf, tp, skip, et) in sorted(results, key=lambda x: (-x[5], -x[2])):
    print(f"  {name:<18} {nt:>5} {wr:>5.1%} {aw:>5.1%} {al:>5.1%} {pf:>5.2f} {tp:>+10,.0f} {skip:>8}")

best = sorted(results, key=lambda x: (-x[5], -x[2]))[0]
print(f"\n  => Best: {best[0]} PF={best[5]:.2f} WR={best[2]:.1%} P&L={best[6]:,.0f}")
print("  Done!")
