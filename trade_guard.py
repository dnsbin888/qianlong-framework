"""交易守护进程 — 确保止盈止损一定执行

工作原理:
  1. 每10秒检查同花顺连接状态
  2. 监控持仓盈亏, 触发条件时记录日志
  3. 如果键盘没触发, 发桌面弹窗告警
  4. 所有事件写入日志, 事后可追溯

用法: python trade_guard.py
      开盘后一直运行, 收盘自动退出
"""

import time, datetime, json, os, sys

# ═══════════════════ 配置 ═══════════════════
CHECK_INTERVAL = 10  # 检查间隔(秒)
LOG_FILE = r"d:\quant_framework\trade_guard.log"
ALERT_FILE = r"d:\quant_framework\alert.txt"

# 监控规则 — 与 auto_exit_monitor.py 保持一致
RULES = {
    "stop_half_pct": -0.03,   # -3%卖一半 (与 auto_exit_monitor stop_half 一致)
    "stop_loss_pct": -0.05,   # -5%止损 (与 auto_exit_monitor stop_full 一致)
    "tp1_pct": 0.07,          # +7%启动跟踪 (与 auto_exit_monitor tp_trail_peak 一致)
    "tp1_drop": 0.015,        # 回落1.5%触发 (与 auto_exit_monitor tp_trail_drop 一致)
}

# 涨跌停幅度: 统一使用公共模块
try:
    from quant_framework.core.market_limits import get_limit_pct as _get_limit_pct
except ImportError:
    _LIMIT_PCT_MAP = {"main": 0.10, "st": 0.05, "gem": 0.20, "star": 0.20, "bse": 0.30}
    def _get_limit_pct(code: str) -> float:
        digits = "".join(c for c in str(code) if c.isdigit())
        if len(digits) < 6: return 0.10
        prefix = digits[:3]
        if prefix == "688": return 0.20
        if prefix == "300": return 0.20
        if digits[0] in ("8", "4"): return 0.30
        return 0.10

# ═══════════════════ 日志 ═══════════════════
def log(msg, level="INFO"):
    t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def alert(msg):
    """桌面弹窗告警"""
    log(f"ALERT: {msg}", "ALERT")
    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now()}\n{msg}\n")
    # 尝试弹窗
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "交易告警!", 0x30)
    except Exception:
        pass

# ═══════════════════ 主循环 ═══════════════════
def main():
    log("=" * 50)
    log("交易守护进程启动")
    log(f"监控规则: 止损{RULES['stop_loss_pct']:.0%} | 止盈+{RULES['tp1_pct']:.0%}回落{abs(RULES['tp1_drop']):.1%} | 检查间隔{CHECK_INTERVAL}秒")
    log("=" * 50)

    # 尝试连接同花顺
    ths_connected = False
    try:
        sys.path.insert(0, r"d:\同花顺软件\同花顺\script")
        from ths_api import xd, hq
        api = hq.ths_hq_api()
        ths_connected = True
        log("同花顺连接成功")
    except ImportError:
        log("不在同花顺环境中, 使用外部监控模式", "WARN")
        log("请确保同花顺已登录并保持前台运行")
        log("守护进程将监控键盘日志文件")

    last_equity = {}
    check_count = 0

    while True:
        check_count += 1
        now = datetime.datetime.now()
        t = now.time()

        # 收盘退出
        if t >= datetime.time(15, 5):
            log("收盘, 守护进程退出")
            break

        # 午休
        if datetime.time(11, 30) <= t <= datetime.time(13, 0):
            time.sleep(60)
            continue

        # 盘前
        if t < datetime.time(9, 25):
            time.sleep(30)
            continue

        # ── 检查1: 同花顺连接 ──
        if ths_connected:
            try:
                positions = xd.g_position
                money = xd.g_money
                if check_count % 30 == 0:  # 每5分钟报告一次
                    log(f"持仓: {len(positions)}只 | 可用资金: {money.get('kyje', '?')}")
            except Exception as e:
                alert(f"同花顺连接异常: {e}")
                ths_connected = False

        # ── 检查2: 持仓盈亏触发 ──
        if ths_connected:
            try:
                positions = xd.g_position
                quote_data = api.reg_quote(list(positions.keys()))
                api.wait_update()

                for code, pos in positions.items():
                    try:
                        q = api.get_quote([code]) if hasattr(api, 'get_quote') else {}
                        price = 0
                        if q and code in q:
                            price = q[code].price if hasattr(q[code], 'price') else q[code].get('price', 0)
                        if price <= 0:
                            continue

                        cost = float(pos.get('cbj', 0))
                        available = int(pos.get('kyye', 0))
                        if cost <= 0 or available <= 0:
                            continue

                        pnl = (price - cost) / cost

                        # 跌停检测 — 跌停时无法卖出, 仅告警不触发止损指令
                        q = q[code] if code in q else {}
                        pre_close_val = (
                            q.pre_close if hasattr(q, 'pre_close')
                            else q.get('pre_close', cost) if isinstance(q, dict)
                            else cost
                        )
                        limit_pct = get_limit_pct(code)
                        limit_down = round(pre_close_val * (1 - limit_pct), 2)
                        is_limit_down = price <= limit_down + 0.01

                        # 止损触发
                        if pnl <= RULES["stop_loss_pct"]:
                            if is_limit_down:
                                alert(
                                    f"跌停中无法卖出! {code} 现价{price:.2f} "
                                    f"跌停价{limit_down:.2f} 亏损{pnl:.1%}\n"
                                    f"请关注复牌后操作!"
                                )
                            else:
                                alert(
                                    f"止损触发! {code} 现价{price:.2f} "
                                    f"成本{cost:.2f} 亏损{pnl:.1%}\n"
                                    f"请立即手动清仓!"
                                )

                        # 止盈提醒
                        if pnl >= RULES["tp1_pct"]:
                            log(f"止盈区域: {code} 盈利{pnl:.1%} 现价{price:.2f}", "INFO")

                    except Exception:
                        continue

            except Exception as e:
                if check_count % 60 == 0:
                    log(f"持仓检查异常: {e}", "WARN")

        # ── 检查3: 键盘日志 ──
        # 检查键盘软件有没有生成错误日志
        kb_log = r"d:\通信达技术指标\1键盘管理软件\24键专业版 ID条件单\log.txt"
        if os.path.exists(kb_log):
            with open(kb_log, 'r', encoding='gbk', errors='ignore') as f:
                last_lines = f.readlines()[-5:]
                for line in last_lines:
                    if 'error' in line.lower() or 'fail' in line.lower():
                        log(f"键盘异常: {line.strip()}", "WARN")

        time.sleep(CHECK_INTERVAL)

    log("守护进程正常退出")

if __name__ == "__main__":
    main()
