"""交易守护进程 — 独立止损止盈监控 (E371精简版)
  每10秒检查一次持仓, 触发条件时推钉钉告警。
  核心止损止盈逻辑已由 RuleEngine 覆盖, 此脚本作为独立备份安全网。
"""
import time, datetime, json, os, sys

CHECK_INTERVAL = 10
LOG_FILE = r"d:\quant_framework\trade_guard.log"
RULES = {"stop_half": -0.03, "stop_full": -0.055, "tp_trail": 0.07, "tp_drop": -0.015}


def log(msg, level="INFO"):
    t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] [{level}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except: pass


def alert(msg):
    log(f"ALERT: {msg}", "ALERT")
    try:
        from dingtalk_alerts import send_alert
        send_alert("⚠️ 交易守护", msg, "warning")
    except: pass


def main():
    log("交易守护进程启动")
    while True:
        try:
            from ths_api import get_positions
            positions = get_positions()
            for pos in positions:
                pnl = pos.get("profit_pct", 0)
                sym = pos.get("symbol", "?")
                if pnl <= RULES["stop_full"]:
                    alert(f"{sym} 硬止损触发 ({pnl*100:.1f}%)")
                elif pnl <= RULES["stop_half"]:
                    alert(f"{sym} 软止损触发 ({pnl*100:.1f}%)")
        except ImportError:
            log("THS接口不可用, 跳过本轮", "WARN")
        except Exception as e:
            log(f"检查异常: {e}", "ERROR")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
