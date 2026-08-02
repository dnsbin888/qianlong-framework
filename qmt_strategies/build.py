"""QMT Strategy Builder - pure binary concat modules"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, "qmt_full_strategy.py")

# 拼接顺序: common → 各策略模块
MODULES = [
    "common.py",
    "strategy_chase.py", "strategy_breakthrough.py",
    "strategy_auction.py", "strategy_tail.py",
    "strategy_continuation.py", "strategy_floor.py",
    "init_body.py", "signal_body.py", "helper_body.py",
]

def build():
    parts = []
    for mod in MODULES:
        path = os.path.join(BASE, mod)
        if not os.path.exists(path):
            print(f"[Build] SKIP: {mod}")
            continue
        with open(path, "rb") as f:
            data = f.read()
        # 去除非首个的 encoding header
        if len(parts) > 0:
            data = data.replace(b"#encoding:gbk\r\n", b"").replace(b"#encoding:gbk\n", b"")
        # 去掉模块间 import 行
        for skip in [b"from common import *\r\n", b"from common import *\n",
                     b"from init_body import *\r\n", b"from init_body import *\n",
                     b"from wts_body import *\r\n", b"from wts_body import *\n",
                     b"from daban_body import *\r\n", b"from daban_body import *\n",
                     b"from tdx_hb_body import *\r\n", b"from tdx_hb_body import *\n",
                     b"from signals_body import *\r\n", b"from signals_body import *\n",
                     b"from callbacks_body import *\r\n", b"from callbacks_body import *\n"]:
            data = data.replace(skip, b"")
        parts.append(data)

    result = b"\n".join(parts)
    with open(OUTPUT, "wb") as f:
        f.write(result)

    with open(OUTPUT, "rb") as f:
        lines = len(f.readlines())
    print(f"[Build] Done: {OUTPUT}")
    print(f"        {lines} lines, {os.path.getsize(OUTPUT)/1024:.1f}KB")
    # verify no broken imports
    content = open(OUTPUT, "r", encoding="gbk", errors="replace").read()
    if "from _body import" in content:
        print("        [!] import still present - may fail in QMT")

if __name__ == "__main__":
    build()
