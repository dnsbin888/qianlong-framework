"""潜龙股票桥接 — 一键联动同花顺+通达信"""
import sys, os, json
from datetime import datetime

code = sys.argv[1] if len(sys.argv) > 1 else ""
code = code.replace("qlstock://", "").replace("/", "").strip()
if not code: sys.exit(0)

# 写入桥接文件 (联动精灵监控)
data = {"symbol": code, "action": "view", "targets": ["同花顺","通达信"], "timestamp": datetime.now().strftime("%H:%M:%S")}
with open(r"d:\quant_framework\bridge_stock.json", "w", encoding="utf-8") as f:
    json.dump(data, f)

# 复制到剪贴板
try:
    import pyperclip; pyperclip.copy(code)
except: pass

# 激活同花顺
try:
    import pygetwindow as gw
    for kw in ["同花顺", "网上股票交易系统"]:
        for w in gw.getWindowsWithTitle(kw):
            w.activate()
            break
except: pass
