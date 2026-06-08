"""一键同步：量化信号 → 打板键盘Table.txt + 同花顺自动下单 + 操作清单"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, json, os, shutil, numpy as np
from datetime import datetime
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
NAMES_FILE = r"d:\quant_framework\stock_names.json"

# ═══════════════════ 打板键盘路径 ═══════════════════
KEYBOARD_DIRS = [
    r"d:\通信达技术指标\1键盘管理软件\24键专业版 ID条件单",
    r"d:\通信达技术指标\1键盘管理软件\24键64专业版7",
    r"d:\1键盘\24键专业版 ID条件单",
]

# ═══════════════════ 加载数据 ═══════════════════
with open(CACHE, "rb") as f: data = pickle.load(f)
names = {}
if os.path.exists(NAMES_FILE):
    with open(NAMES_FILE, "r", encoding="utf-8") as f: names = json.load(f)

latest = max(d for sd in data.values() for d in sd["dates"] if len(str(d))==8 and d<=20260601)
date_str = f"{str(latest)[:4]}-{str(latest)[4:6]}-{str(latest)[6:8]}"

# ═══════════════════ 扫描信号 ═══════════════════
# 最优参数(回测): P15-100 + T>2e8 + VR>2.5  PF=8.74
# 当前缓存用宽松版确保出信号
MIN_PRICE, MAX_PRICE, MIN_TURNOVER, MIN_VOL_RATIO, TB_MIN, MIN_SCORE = 10, 100, 5e7, 1.5, 0.5, 60

signals = []
for sym, sd in data.items():
    if latest not in sd["dates"]: continue
    i = sd["dates"].index(latest)
    if i < 250: continue
    p = sd["close"][i]
    if p < MIN_PRICE or p > MAX_PRICE: continue
    vs = sd["volume"][max(0,i-20):i+1]
    avg_v = float(np.mean(vs[:-1])) if len(vs) > 1 else 0  # avg of previous days
    today_v = vs[-1] if len(vs) > 0 else 0
    if avg_v * p < MIN_TURNOVER: continue
    if len(vs) >= 5 and today_v < avg_v * MIN_VOL_RATIO: continue

    fv = sd["factors"]
    def g(n): arr = fv.get(n,[]); return float(arr[i]) if arr is not None and i<len(arr) and not np.isnan(float(arr[i])) else 0
    tb = g("trend_bottom")
    if tb < TB_MIN: continue
    add = g("add_position"); bp = g("bull_position")
    score = tb*60 + add*40 + bp*20
    if score < MIN_SCORE: continue

    atr_v = 0.01
    if i >= 14:
        h=np.array(sd["high"][i-14:i+1]); l=np.array(sd["low"][i-14:i+1])
        c_=np.array([sd["close"][i-15]]+sd["close"][i-14:i])
        tr=np.maximum(h-l,np.maximum(np.abs(h-c_),np.abs(l-c_)))
        atr_v=float(np.mean(tr))

    # 确定市场前缀 (6开头=SH, 0/3开头=SZ)
    prefix = "SH" if sym[0] in "56" else "SZ"
    signals.append(dict(sym=sym, code=f"{prefix}{sym}", name=names.get(sym,""),
                        price=p, score=score, tb=tb, atr=atr_v,
                        stop=round(p*.92,2), tp1=round(p*1.05,2), tp2=round(p*1.10,2),
                        buy_zone=round(p-atr_v*2,2)))

signals.sort(key=lambda x: x["score"], reverse=True)

# ═══════════════════ 1. 覆写 Table.txt ═══════════════════
top_n = min(len(signals), 100)
table_content = "代码\n\n" + "\n\n".join(s["code"] for s in signals[:top_n]) + "\n\n"

synced = 0
for kbd_dir in KEYBOARD_DIRS:
    if os.path.isdir(kbd_dir):
        table_path = os.path.join(kbd_dir, "Table.txt")
        # 备份旧文件
        backup = os.path.join(kbd_dir, f"Table.{date_str}.bak")
        if os.path.exists(table_path):
            shutil.copy(table_path, backup)
        # 写入新候选池
        with open(table_path, "w", encoding="gbk") as f:
            f.write(table_content)
        print(f"[1] 打板键盘同步: {table_path} ({top_n}只)")
        synced += 1

# ═══════════════════ 2. 生成自动下单指令 ═══════════════════
auto_orders = []
for s in signals[:10]:
    auto_orders.append({
        "symbol": s["sym"],
        "action": "buy",
        "price": "zxjg",
        "volume": max(int(200000 / s["price"] / 100) * 100, 100),
        "reason": f"TB={s['tb']:.2f} Score={s['score']:.0f}",
        "stop_loss": s["stop"],
        "take_profit": s["tp1"],
    })

order_file = r"d:\quant_framework\live_orders.json"
with open(order_file, "w", encoding="utf-8") as f:
    json.dump({"orders": auto_orders, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
print(f"[2] 自动下单指令: {order_file} ({len(auto_orders)}条)")

# ═══════════════════ 3. 打印操作清单 ═══════════════════
print(f"\n{'='*60}")
print(f"  同步完成! 打板键盘已加载 {top_n} 只候选")
print(f"  数据日期: {date_str}")
print(f"{'='*60}")
print(f"\n  Top 10 信号:")
print(f"  {'代码':<10} {'名称':<10} {'现价':>7} {'评分':>6} {'买入区':>8} {'止损':>8}")
for s in signals[:10]:
    print(f"  {s['sym']:<10} {s['name'][:10]:<10} {s['price']:>7.2f} {s['score']:>6.0f} {s['buy_zone']:>8.2f} {s['stop']:>8.2f}")

print(f"\n  Key mapping:")
print(f"    K11=单个涨停买入  K13=批量涨停买入  K15=涨停-1分买")
print(f"    K21=炸板即卖      K23=止盈止损     K24=回落卖出")
print(f"    K41=全仓买入      K42=1/2仓        K43=1/3仓")
print(f"    K51=现价1/4仓     K52=1/5仓       K53=1/10仓")
print(f"\n  明天打开打板键盘 → 候选池已就绪 → 盯着敲键盘!")
