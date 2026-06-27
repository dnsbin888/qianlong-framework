"""进化日志与回滚 — 记录每次参数变更，支持一键回滚"""
import os, json
from datetime import datetime

LOG_FILE = r"D:\quant_web\data\evolution_log.json"
CONFIG_FILE = r"D:\quant_framework\live_trader_config.json"


def log_change(trigger, changes, reason=""):
    """记录一次参数变更"""
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": trigger,
        "changes": changes,
        "reason": reason,
    }
    log = []
    if os.path.exists(LOG_FILE):
        try:
            log = json.load(open(LOG_FILE, "r"))
        except: pass
    log.append(entry)
    json.dump(log, open(LOG_FILE, "w"), ensure_ascii=False, indent=2)
    return len(log) - 1  # 返回索引作为回滚ID


def rollback(entry_id):
    """回滚到指定版本"""
    if not os.path.exists(LOG_FILE):
        return False
    log = json.load(open(LOG_FILE, "r"))
    if entry_id < 0 or entry_id >= len(log):
        return False
    entry = log[entry_id]
    if not os.path.exists(CONFIG_FILE):
        return False
    cfg = json.load(open(CONFIG_FILE, "r"))
    for ch in entry["changes"]:
        if ch["param"] in cfg:
            cfg[ch["param"]] = ch["before"]
    json.dump(cfg, open(CONFIG_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"[EvoLog] 已回滚到版本{entry_id}: {entry['time']}")
    return True


def get_log():
    if os.path.exists(LOG_FILE):
        return json.load(open(LOG_FILE, "r"))
    return []
