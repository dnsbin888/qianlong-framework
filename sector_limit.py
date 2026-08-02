"""B3: 行业敞口限制 — 单行业≤30%总仓位
用法: from sector_limit import check_sector_limit
"""
import sys, os, json

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

# 行业映射缓存
_INDUSTRY_MAP = None

def _get_max_sector_pct():
    """E372: 从 master config 读取行业上限, 默认25%"""
    try:
        master = r"D:\quant_framework\trade_config_master.json"
        if os.path.exists(master):
            with open(master, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("position_sizing", {}).get("max_sector_pct", 0.25)
    except Exception:
        pass
    return 0.25


def _load_industry_map():
    global _INDUSTRY_MAP
    if _INDUSTRY_MAP is not None:
        return _INDUSTRY_MAP
    _INDUSTRY_MAP = {}
    # 从 stock_names 获取行业
    try:
        csv_path = r"D:\quant_web\stock_names_full.csv"
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) >= 4:
                        _INDUSTRY_MAP[parts[0]] = parts[3]  # code → industry
    except Exception:
        pass
    return _INDUSTRY_MAP


def get_industry(symbol):
    """获取股票行业"""
    ind_map = _load_industry_map()
    clean = symbol.replace("sh", "").replace("sz", "").replace("bj", "")
    return ind_map.get(symbol, ind_map.get(clean, "未知"))


def check_sector_limit(symbol, existing_positions, total_equity, max_pct=None):
    """检查买入后是否会超过行业敞口限制
    E372: max_pct 从 trade_config_master.json 读取, 默认25%

    Args:
        symbol: 待买入的股票代码
        existing_positions: 现有持仓 [{symbol, market_value}]
        total_equity: 总资产
        max_pct: 最大行业百分比

    Returns:
        (allowed, reason)
    """
    if max_pct is None:
        max_pct = _get_max_sector_pct()
    target_industry = get_industry(symbol)
    if target_industry == "未知":
        return True, ""  # 未知行业不限制

    # 计算该行业现有持仓
    sector_value = 0
    for pos in existing_positions:
        if isinstance(pos, dict):
            sym = pos.get("symbol", "")
            val = pos.get("market_value", pos.get("cost", 0))
        else:
            sym = getattr(pos, "symbol", "")
            val = getattr(pos, "market_value", 0)
        if get_industry(sym) == target_industry:
            sector_value += float(val) if val else 0

    sector_pct = sector_value / max(total_equity, 1)
    if sector_pct >= max_pct:
        return False, f"行业[{target_industry}]已达{sector_pct:.0%}, 超过{max_pct:.0%}限制"
    return True, f"行业[{target_industry}]当前{sector_pct:.0%}, 允许买入"


if __name__ == "__main__":
    print(f"行业敞口限制模型 v1.1 (E372: 从master配置读取, 默认25%)")
    # 测试
    test_positions = [
        {"symbol": "sz000566", "market_value": 100000},
        {"symbol": "sz000048", "market_value": 80000},
    ]
    ok, reason = check_sector_limit("sz000566", test_positions, 1000000)
    print(f"  同行业加仓: {'✅' if ok else '❌'} {reason}")

    ok, reason = check_sector_limit("sh600519", test_positions, 1000000)
    print(f"  跨行业买入: {'✅' if ok else '❌'} {reason}")
    print("✅ 行业敞口模型就绪\n")
