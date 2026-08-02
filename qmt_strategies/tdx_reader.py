"""TDX信号读取器 — QMT端读 tdx_pool_stocks.json
B路径: 通达信原生跑公式 → tpool XML → tdx_signal_watcher解析 → JSON
      → QMT读JSON → 查候选池 → 快速通道 passorder
"""
import json, os

TDX_STOCKS_FILE = r"d:\quant_framework\tdx_pool_stocks.json"


def load_tdx_signals():
    """读取TDX最新选股结果

    Returns:
        {公式名: {count: N, stocks: ["sh600519", "sz000858"], updated: "14:30:00"}}
    """
    if not os.path.exists(TDX_STOCKS_FILE):
        return {}
    try:
        with open(TDX_STOCKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_tdx_stock_set():
    """获取TDX选出的所有股票集合 (去重)"""
    data = load_tdx_signals()
    stocks = set()
    for info in data.values():
        for s in info.get("stocks", []):
            stocks.add(s)
    return stocks


def is_tdx_pick(symbol):
    """检查某只股票是否被TDX选中"""
    return symbol in get_tdx_stock_set()
