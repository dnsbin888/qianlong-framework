"""日志自动轮转 — 超过10MB自动归档，保留最近3个备份"""
import os, time

LOG_FILES = [
    r"D:\quant_web\watchdog.log",
    r"D:\quant_web\data\alerts.jsonl",
    r"D:\quant_framework\auto_trade_audit.jsonl",
]


def check_and_rotate():
    for f in LOG_FILES:
        if not os.path.exists(f):
            continue
        size_mb = os.path.getsize(f) / (1024 * 1024)
        if size_mb > 10:
            for i in range(2, 0, -1):
                old = f"{f}.{i}"
                new = f"{f}.{i+1}"
                if os.path.exists(old):
                    if i >= 3:
                        os.remove(old)
                    else:
                        os.replace(old, new)
            backup = f"{f}.1"
            os.replace(f, backup)
            print(f"[LogRotate] {os.path.basename(f)} {size_mb:.1f}MB → 已归档")


def start_bg():
    import threading
    def _loop():
        while True:
            time.sleep(3600)
            try:
                check_and_rotate()
            except: pass
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print("[LogRotate] 日志轮转已启动 (1小时检查)")
