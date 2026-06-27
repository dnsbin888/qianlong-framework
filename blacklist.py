"""黑白名单管理 — 永不买入的问题股黑名单

存储: D:\quant_framework\blacklist.json
集成: 下单前检查，黑名单中的股票自动跳过
"""

import json, os

BLACKLIST_FILE = r"D:\quant_framework\blacklist.json"


def load() -> set:
    """加载黑名单"""
    if not os.path.exists(BLACKLIST_FILE):
        return set()
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("symbols", []))
    except Exception:
        return set()


def save(symbols: set):
    """保存黑名单"""
    os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
    with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "symbols": sorted(list(symbols)),
            "updated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False)


def add(symbol: str) -> bool:
    """添加到黑名单"""
    symbol = symbol.replace("sh", "").replace("sz", "").replace("bj", "").upper()
    black = load()
    if symbol in black:
        return False
    black.add(symbol)
    save(black)
    print(f"[Blacklist] 已拉黑: {symbol}")
    return True


def remove(symbol: str) -> bool:
    """从黑名单移除"""
    symbol = symbol.replace("sh", "").replace("sz", "").replace("bj", "").upper()
    black = load()
    if symbol not in black:
        return False
    black.discard(symbol)
    save(black)
    print(f"[Blacklist] 已移除: {symbol}")
    return True


def is_blocked(symbol: str) -> bool:
    """检查是否在黑名单中"""
    code = symbol.replace("sh", "").replace("sz", "").replace("bj", "").upper()
    return code in load()


def list_all() -> list:
    """列出所有黑名单"""
    return sorted(list(load()))
