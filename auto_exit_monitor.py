"""出场自动化监控 — 同花顺环境内运行, 盘中自动止盈止损

功能:
  1. 实时从同花顺读取持仓 (xd.g_position)
  2. 对每只持仓逐笔跟踪入场价、最高价
  3. 触发条件自动下单:
     -3% → 卖一半 (xd.cmd sell xxxx 最新价 -cw 1/2)
     -5% → 全清
     +7%回落1.5% → 卖一半
     涨停 → 不卖
  4. 每5秒检查一次

用法:
  在同花顺内: 工具 → 脚本 → 打开此文件 → 运行
  或放在 d:\同花顺软件\同花顺\script\ 目录下
"""

import time, datetime, json, os
from collections import defaultdict

# 涨跌停幅度: 优先从公共模块导入, 降级到内联版本
# 公共模块: quant_framework.core.market_limits.get_limit_pct
try:
    from quant_framework.core.market_limits import get_limit_pct as _get_limit_pct
except ImportError:
    # 内联 fallback — 与 market_limits.py 保持一致
    _LIMIT_PCT_MAP = {"main": 0.10, "st": 0.05, "gem": 0.20, "star": 0.20, "bse": 0.30}
    def _get_limit_pct(code: str) -> float:
        digits = "".join(c for c in str(code) if c.isdigit())
        if len(digits) < 6: return 0.10
        prefix = digits[:3]
        if prefix == "688": return 0.20
        if prefix == "300": return 0.20
        if digits[0] in ("8", "4"): return 0.30
        return 0.10
CONFIG = {
    "stop_half": -0.03,      # -3% 卖一半
    "stop_full": -0.05,      # -5% 全清
    "tp_trail_peak": 0.07,   # +7% 启动跟踪
    "tp_trail_drop": -0.015, # 回落1.5% 触发
    "tp_sell_pct": 0.50,     # 卖一半
    "check_interval": 5,     # 5秒检查一次
    "position_file": "auto_exit_positions.json",  # 持仓记录
}

# ═══════════════════ 初始化 ═══════════════════
from ths_api import *

print("=" * 55)
print("  出场自动化监控")
print(f"  {datetime.datetime.now().strftime('%H:%M:%S')} 启动")
print("=" * 55)
print(f"  规则:")
print(f"    跌3% → 自动卖一半")
print(f"    跌5% → 自动全清")
print(f"    涨7%回落1.5% → 自动卖一半")
print(f"    涨停 → 持有不卖")
print(f"  每{CONFIG['check_interval']}秒检查\n")

# 加载持仓记录 (记录每只的入场价和最高价)
positions = {}
if os.path.exists(CONFIG["position_file"]):
    with open(CONFIG["position_file"], "r", encoding="utf-8") as f:
        positions = json.load(f)

api = hq.ths_hq_api()

def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")

def execute_sell(code, price, pct, reason):
    """执行卖出: -cw 表示卖出仓位比例"""
    cmd = f"sell {code} zxjg -cw {pct} -notip"
    try:
        xd.cmd(cmd)
        print(f"  [{now_str()}] {reason}: {code} @{price:.2f} {pct*100:.0f}%仓位")
        return True
    except Exception as e:
        print(f"  [{now_str()}] 卖出失败 {code}: {e}")
        return False

def in_session():
    t = datetime.datetime.now().time()
    return (datetime.time(9, 25) <= t <= datetime.time(11, 30)) or \
           (datetime.time(13, 0) <= t <= datetime.time(14, 57))

# 等待开盘
print("  等待开盘...")
while not in_session():
    time.sleep(10)

print(f"  [{now_str()}] 开盘! 开始监控\n")

# ═══════════════════ 主循环 ═══════════════════
last_log = 0

while True:
    if not in_session():
        if datetime.datetime.now().hour >= 15:
            print(f"\n  [{now_str()}] 收盘, 监控结束")
            break
        time.sleep(30)
        continue

    time.sleep(CONFIG["check_interval"])

    try:
        # 读取当前持仓
        current_positions = xd.g_position
        # 订阅所有持仓的实时行情
        held_codes = list(current_positions.keys())
    except Exception as e:
        if time.time() - last_log > 60:
            print(f"  [{now_str()}] 读取持仓异常: {e}")
            last_log = time.time()
        continue

    if not held_codes:
        if time.time() - last_log > 300:
            print(f"  [{now_str()}] 无持仓, 等待中...")
            last_log = time.time()
        continue

    # 确保订阅
    try:
        api.reg_quote(held_codes)
        api.wait_update()
    except Exception:
        pass

    # 逐只检查
    for code in held_codes:
        try:
            pos = current_positions[code]
            available = int(pos.get("kyye", 0))
            total = int(pos.get("gpye", 0))
            cost = float(pos.get("cbj", 0))
            if available <= 0 or total <= 0 or cost <= 0:
                continue

            # 实时价格
            quote_data = api.get_quote([code]) if hasattr(api, 'get_quote') else {}
            q = quote_data.get(code)
            if q is None:
                continue
            price = q.price if hasattr(q, 'price') else q.get('price', 0)
            high = q.high if hasattr(q, 'high') else q.get('high', price)
            if price <= 0:
                continue

            # 涨停? 跌停? — 按板块自动判断幅度
            pre_close = q.pre_close if hasattr(q, 'pre_close') else q.get('pre_close', cost)
            limit_pct = _get_limit_pct(code)
            if pre_close > 0:
                limit_up = round(pre_close * (1 + limit_pct), 2)
                limit_down = round(pre_close * (1 - limit_pct), 2)
            else:
                limit_up, limit_down = 999, 0

            # 涨停 — 持有不卖
            if price >= limit_up - 0.01:
                continue

            # 跌停 — 无法卖出, 跳过止损避免废单
            if price <= limit_down + 0.01:
                continue

        except (KeyError, TypeError, ValueError) as e:
            continue

        # 载入/更新持仓记录
        if code not in positions:
            # 新持仓: 记录入场价
            positions[code] = {
                "entry_price": cost,
                "highest": price,
                "half_sold": False,
                "total_shares": total,
            }
        else:
            p = positions[code]
            if high > p["highest"]:
                p["highest"] = high

            pnl = (price - p["entry_price"]) / p["entry_price"]
            peak_pnl = (p["highest"] - p["entry_price"]) / p["entry_price"]

            # ── -5% 全清 (优先级最高) ──
            if pnl <= CONFIG["stop_full"] and available > 0:
                execute_sell(code, price, 1.0, f"止损{pnl:.0%}")
                positions.pop(code, None)
                continue

            # ── -3% 卖一半 (只触发一次) ──
            if not p["half_sold"] and pnl <= CONFIG["stop_half"] and available > 0:
                execute_sell(code, price, 0.5, f"止损减半{pnl:.0%}")
                p["half_sold"] = True
                continue

            # ── +7% 回落 1.5% 卖一半 ──
            if peak_pnl >= CONFIG["tp_trail_peak"]:
                drop_from_peak = (price - p["highest"]) / p["entry_price"]
                if drop_from_peak <= CONFIG["tp_trail_drop"]:
                    if not p["half_sold"] and available > 0:
                        execute_sell(code, price, CONFIG["tp_sell_pct"],
                                    f"止盈{peak_pnl:.0%}回落")
                        p["half_sold"] = True

    # 定期保存状态
    if time.time() - last_log > 300:
        with open(CONFIG["position_file"], "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False)
        active = len(positions)
        half_sold = sum(1 for p in positions.values() if p.get("half_sold"))
        print(f"  [{now_str()}] 持仓{active}只 (已减半{half_sold})")
        last_log = time.time()

# 收盘保存
with open(CONFIG["position_file"], "w", encoding="utf-8") as f:
    json.dump(positions, f, ensure_ascii=False)
print(f"\n  今日监控结束, 状态已保存")
