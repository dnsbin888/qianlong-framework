"""P2-01: 北向资金因子 v1.0
对标: Wind北向资金监控 / 聚宽北上资金因子
数据源: akshare stock_hsgt_hold_stock_em (沪深港通持股汇总)
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

CACHE_PATH = r"D:\quant_framework\data\northbound_cache.json"


def fetch_northbound_holdings():
    """获取沪深港通个股持仓汇总 (缓存24h)"""
    if os.path.exists(CACHE_PATH):
        mtime = os.path.getmtime(CACHE_PATH)
        age_h = (pd.Timestamp.now().timestamp() - mtime) / 3600
        if age_h < 24:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)

    try:
        import akshare as ak
        df = ak.stock_hsgt_hold_stock_em()
        if df is None or len(df) == 0:
            return _load_cache_fallback()

        result = {}
        for _, row in df.iterrows():
            code = str(row.get("code", "")).strip()
            if len(code) != 6:
                continue
            prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
            sym = prefix + code

            hold_pct = float(row.get("hold_ratio", 0) or 0)
            # 因子分: 北向持仓占比映射到0-100
            score = min(100, max(0, hold_pct * 10 + 30))

            result[sym] = {
                "hold_pct": round(hold_pct, 3),
                "factor_score": round(score, 1),
            }

        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[Northbound] {len(result)} stocks cached")
        return result

    except Exception as e:
        print(f"[Northbound] fetch failed: {e}")
        return _load_cache_fallback()


def _load_cache_fallback():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def compute_northbound_score(symbol, holdings_cache=None):
    if holdings_cache is None:
        holdings_cache = fetch_northbound_holdings()
    info = holdings_cache.get(symbol, {})
    return info.get("factor_score", 50) if info else 50


def compute_northbound_factor(stock_data, factor_cache=None):
    holdings = fetch_northbound_holdings()
    if not holdings:
        return {}
    result = {}
    for sym in stock_data:
        score = compute_northbound_score(sym, holdings)
        if score != 50:
            result[sym] = score
    print(f"[Northbound] factor: {len(result)} stocks")
    return result


if __name__ == "__main__":
    print("=" * 50)
    print("  Northbound Factor v1.0")
    print("=" * 50)
    holdings = fetch_northbound_holdings()
    print(f"  Total: {len(holdings)} stocks")
    if holdings:
        ranked = sorted(holdings.items(), key=lambda x: -x[1].get("factor_score", 0))
        print("  Top 10:")
        for sym, info in ranked[:10]:
            print(f"    {sym}: hold={info['hold_pct']:.1f}% score={info['factor_score']:.0f}")
    print("\n  Done")
