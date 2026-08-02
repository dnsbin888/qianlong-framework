"""潜龙股票桥接 — 一键联动同花顺+通达信"""
import sys, os, json
from datetime import datetime

code = sys.argv[1] if len(sys.argv) > 1 else ""
code = code.replace("qlstock://", "").replace("/", "").strip()
if not code: sys.exit(0)

# 复制到剪贴板 (联动精灵不参与, 避免窗口操控)
try:
    import pyperclip; pyperclip.copy(code)
except: pass
