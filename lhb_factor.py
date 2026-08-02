"""P2-02: LHB Factor v1.0 — akshare stock_lhb_detail_em"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

CACHE_PATH = r"D:\quant_framework\data\lhb_cache.json"


def fetch_lhb_data():
    if os.path.exists(CACHE_PATH):
        mtime = os.path.getmtime(CACHE_PATH)
        age_h = (pd.Timestamp.now().timestamp() - mtime) / 3600
        if age_h < 1:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)

    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em()
        if df is None or len(df) == 0:
            return _fallback()

        # 列名映射 (中文→英文)
        col_map = {}
        for c in df.columns:
            if c in ('code',): col_map[c] = 'code'
        if 'code' not in df.columns and 'code' not in col_map:
            for c in df.columns:
                if 'code' in c.lower() or c == 'code':
                    col_map[c] = 'code'
                    break

        result = {}
        for _, row in df.iterrows():
            # 用位置或名称获取代码
            code = None
            for c in df.columns:
                val = str(row.get(c, ""))
                if len(val) == 6 and val.isdigit():
                    code = val
                    break
            if not code:
                continue

            prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
            sym = prefix + code

            net = 0.0
            buy = 0.0
            for c in df.columns:
                val = row.get(c, 0)
                if val is None:
                    continue
                cn = str(c)
                fv = float(val) if val != '' else 0.0
                if '净买' in cn:
                    net += fv
                elif '买入' in cn and '卖出' not in cn:
                    buy += fv

            if sym not in result:
                result[sym] = {"total_net": 0.0, "total_buy": 0.0, "count": 0}
            result[sym]["total_net"] += net
            result[sym]["total_buy"] += buy
            result[sym]["count"] += 1

        for sym, info in result.items():
            intensity = info["total_net"] / max(info["total_buy"], 1)
            count_bonus = min(30, info["count"] * 10)
            info["score"] = round(min(100, max(0, 50 + intensity * 40 + count_bonus)), 1)

        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[LHB] {len(result)} stocks cached")
        return result

    except Exception as e:
        print(f"[LHB] fetch failed: {e}")
        return _fallback()


def _fallback():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def compute_lhb_score(symbol, lhb_cache=None):
    if lhb_cache is None:
        lhb_cache = fetch_lhb_data()
    info = lhb_cache.get(symbol, {})
    return info.get("score", 50) if info else 50


if __name__ == "__main__":
    print("=" * 50)
    print("  LHB Factor v1.0")
    print("=" * 50)
    lhb = fetch_lhb_data()
    print(f"  Total: {len(lhb)} stocks")
    if lhb:
        ranked = sorted(lhb.items(), key=lambda x: -x[1].get("score", 0))
        print("  Top 10:")
        for sym, info in ranked[:10]:
            print(f"    {sym}: net={info['total_net']/1e4:.0f}w cnt={info['count']} score={info['score']:.0f}")
    print("  Done")
