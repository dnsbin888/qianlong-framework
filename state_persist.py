"""系统状态持久化 — 重启不丢配置"""
import json, os
from datetime import datetime

STATE_FILE = r"D:\quant_web\data\state_persist.json"


def load():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, "r"))
        except: pass
    return {"paper_auto_enabled": True, "live_auto_enabled": True, "circuit_breaker": False}


def save(key, value):
    state = load()
    state[key] = value
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"), ensure_ascii=False, indent=2)
