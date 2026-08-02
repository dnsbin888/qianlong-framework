"""出场自动化监控 — 同花顺内运行 (E371精简版)
  盘中自动止盈止损, 每5秒检查一次。
  核心逻辑已由 RuleEngine 覆盖, 此脚本作为同花顺内独立备份。
"""
import time, datetime, json, os

CONFIG = {
    "stop_half": -0.03, "stop_full": -0.055,
    "tp_trail_peak": 0.07, "tp_trail_drop": -0.015,
    "tp_sell_pct": 0.50, "check_interval": 5,
    "position_file": "auto_exit_positions.json",
}


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")


def main():
    print(f"[{now_str()}] 出场监控启动")
    try:
        from ths_api import get_positions, sell
        while True:
            try:
                positions = get_positions()
                for pos in positions:
                    pnl = pos.get("profit_pct", 0)
                    sym = pos.get("symbol", "")
                    qty = pos.get("qty", 0)
                    if pnl <= CONFIG["stop_full"]:
                        print(f"[{now_str()}] {sym} 硬止损 -{abs(pnl)*100:.1f}%")
                        sell(sym, qty)
                    elif pnl <= CONFIG["stop_half"]:
                        half = max(100, qty // 2)
                        print(f"[{now_str()}] {sym} 软止损 -{abs(pnl)*100:.1f}% 卖半")
                        sell(sym, half)
            except Exception as e:
                print(f"[{now_str()}] 检查异常: {e}")
            time.sleep(CONFIG["check_interval"])
    except ImportError:
        print("请在 同花顺 → 工具 → 脚本 中运行此文件")
    except KeyboardInterrupt:
        print(f"[{now_str()}] 监控已停止")


if __name__ == "__main__":
    main()
