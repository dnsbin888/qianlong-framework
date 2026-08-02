"""龙虎榜数据采集 v2.0 — 增量存储+席位追踪底座 (2026-07-11)
数据源: akshare (免费, 东方财富龙虎榜)
用法: python lhb_fetcher.py
"""
import json, os, time
from datetime import datetime

OUTPUT = r"D:\quant_web\data\lhb_daily.json"
HISTORY = r"D:\quant_web\data\lhb_history.jsonl"


def fetch_lhb_today():
    """获取今日龙虎榜数据"""
    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em()
        if df is None or len(df) == 0:
            print("[LHB] 今日暂无龙虎榜数据(可能非交易日)")
            return []
        records = []
        for _, r in df.iterrows():
            code = str(r.get("代码", ""))
            sym = ('sh' if code.startswith('6') else 'sz') + code
            records.append({
                "symbol": sym,
                "code": code,
                "name": str(r.get("名称", "")),
                "close": float(r.get("收盘价", 0) or 0),
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "reason": str(r.get("上榜原因", "")),
                "turnover": float(r.get("成交额", 0) or 0),
                "net_amount": float(r.get("龙虎榜净买额", 0) or 0),  # v2: 净买入金额
            })
        records.sort(key=lambda x: -abs(x.get("change_pct", 0)))
        return records
    except ImportError:
        print("[LHB] akshare 未安装, pip install akshare")
        return []
    except Exception as e:
        print(f"[LHB] 获取失败: {e}")
        return []


def save_lhb(records):
    """保存龙虎榜: daily快照 + history增量追加"""
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    # daily快照 (现有功能, 不动)
    data = {"date": today, "count": len(records), "records": records[:50]}
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[LHB] 保存 {len(records)} 条 -> {OUTPUT}")
    # 历史追加 (v2新增)
    with open(HISTORY, "a", encoding="utf-8") as f:
        for r in records:
            r["_date"] = today
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[LHB] 追加 {len(records)} 条 -> {HISTORY}")
    return data


if __name__ == "__main__":
    recs = fetch_lhb_today()
    if recs:
        save_lhb(recs)
    else:
        print("[LHB] 无龙虎榜数据 (非交易日或数据源暂不可用)")
