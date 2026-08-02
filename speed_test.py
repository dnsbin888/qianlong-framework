"""快速通道测速 — 盘后离线测试核心组件"""
import json, os, time

PLAN_PATH = r"D:\quant_web\data\auto_trade_plan.json"
CFG_PATH  = r"D:\quant_web\data\qmt_trade_config.json"

# ── 1. Plan 读取速度 ──
t0 = time.time()
with open(PLAN_PATH, "r", encoding="utf-8") as f:
    plan = json.load(f)
t_plan = (time.time() - t0) * 1000

# ── 2. ML配置读取速度 ──
t0 = time.time()
with open(CFG_PATH, "r", encoding="utf-8") as f:
    ml_cfg = json.load(f)
t_ml = (time.time() - t0) * 1000

# ── 3. 信号检测速度 (模拟79只股票) ──
stocks = plan.get("stocks", {})
enabled = sum(1 for s in stocks.values() if s.get("enabled"))

bar = {'open': 6.8, 'close': 7.0, 'volume': 5000000}
prev = {'open': 6.5, 'close': 6.5, 'volume': 1000000}

t0 = time.time()
for _ in range(1000):
    for sym, stock in stocks.items():
        # 模拟信号检测
        if bar['open'] > prev['close'] * 1.02 and bar['volume'] > prev['volume'] * 3:
            sig = "竞价抢筹"
        elif bar['close'] > prev['close'] * 1.03 and bar['volume'] > prev['volume'] * 2:
            sig = "盘中突破"
        elif bar['close'] >= round(prev['close'] * 1.10, 2) and bar['volume'] > prev['volume'] * 1.5:
            sig = "打板追封"
        else:
            sig = None

        if sig:
            # 模拟判定
            if stock.get("enabled") and sig in stock.get("signal_types", []):
                pass  # 快速通道
t_signal = (time.time() - t0) * 1000

# ── 4. 旧版: 多层stock.get() + 读ML文件 ──
t0 = time.time()
for _ in range(10000):
    s = stocks.get("sh600530", {})
    ok = (s.get("enabled") and
          "盘中突破" in s.get("signal_types", []) and
          s.get("max_position_pct", 3) > 0)
    if ok:
        pos = s.get("max_position_pct", 3)
        qty = max(100, int(100000 * pos / 100.0 / 7.0 / 100) * 100)
t_old = (time.time() - t0) * 1000 / 10000 * 1000  # μs

# ── 5. 新版: 缓存tuple解包 + frozenset ──
cache = (True, 3, 0, 0, frozenset(["竞价抢筹","打板追封","盘中突破","尾盘急拉"]), 95, 80)
t0 = time.time()
for _ in range(100000):
    enabled, pos_pct, sl, tp, sig_set, bml, mml = cache
    if enabled and "盘中突破" in sig_set and bml >= mml:
        qty = max(100, int(100000 * pos_pct / 100.0 / 7.0 / 100) * 100)
t_new = (time.time() - t0) * 1000 / 100000 * 1000  # μs

# ── 6. 新增: ML文件IO (优化前每信号读一次) ──
t0 = time.time()
for _ in range(1000):
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        ml = json.load(f)
t_io = (time.time() - t0) * 1000 / 1000  # ms/次

# ── 汇总 ──
print("=" * 50)
print("  快速通道测速 v2 (优化前 vs 优化后)")
print("=" * 50)
print(f"  Plan 读取(缓存):      {t_plan:.1f} ms (每3秒)")
print(f"  ML文件IO(已消除):     {t_io:.1f} ms/次 ← 旧版每次信号都读!")
print(f"  信号检测 79只:        {t_signal/1000:.3f} ms")
print(f"  判定+算量 旧版:       {t_old:.1f} μs (多层get+list扫描)")
print(f"  判定+算量 新版:       {t_new:.1f} μs (tuple解包+frozenset)")
print()
print(f"  优化前 handlebar 总延迟:")
print(f"    读ML文件 {t_io:.1f}ms + 检测{t_signal/1000:.3f}ms + 判定{t_old:.0f}μs")
print(f"    = ~{t_io + t_signal/1000:.1f} ms (每只触发信号)")
print()
print(f"  优化后 handlebar 总延迟:")
print(f"    检测 {t_signal/1000:.3f}ms + 判定 {t_new:.0f}μs")
print(f"    = ~{t_signal/1000:.3f} ms")
print()
print(f"  passorder DLL:        <5 ms")
print(f"  总链路(优化后):       <{t_signal/1000 + 5:.1f} ms")
print()
print(f"  自动票: {enabled}/{len(stocks)} 只")
print("=" * 50)
