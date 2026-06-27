"""每日自动对账 (蓝图 v3.0 O2-1)

比较 RuleEngine/Broker 持仓 vs SQLite 持仓记录, 检测漂移。
输出: 对账报告 + 漂移告警
"""

import os, sys, json, logging
from datetime import datetime

logger = logging.getLogger(__name__)

SQLITE_DB = r"D:\quant_web\data\ml\trades.db"
PAPER_ACCOUNT = r"D:\quant_framework\paper_account.json"
POSITION_TRACK = r"D:\quant_framework\live_positions_track.json"
RECON_LOG = r"D:\quant_framework\reconciliation.log"


def get_sqlite_positions() -> dict:
    """从SQLite读取最新持仓。"""
    try:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()
        cur.execute("SELECT symbol, qty, avg_cost FROM positions WHERE status='open'")
        rows = cur.fetchall()
        conn.close()
        return {r[0]: {"qty": r[1], "avg_cost": r[2]} for r in rows}
    except Exception as e:
        logger.warning(f"SQLite持仓读取失败: {e}")
        return {}


def get_paper_positions() -> dict:
    """从模拟盘读取当前持仓。"""
    try:
        if os.path.exists(PAPER_ACCOUNT):
            with open(PAPER_ACCOUNT, "r") as f:
                data = json.load(f)
            return {k: {"qty": v.get("qty", 0), "avg_cost": v.get("avg_cost", 0)}
                    for k, v in data.get("positions", {}).items()}
    except Exception as e:
        logger.warning(f"Paper持仓读取失败: {e}")
    return {}


def get_track_positions() -> dict:
    """从跟踪文件读取持仓。"""
    try:
        if os.path.exists(POSITION_TRACK):
            with open(POSITION_TRACK, "r") as f:
                data = json.load(f)
            return {k: {"qty": v.get("qty", 0), "avg_cost": v.get("avg_cost", 0)}
                    for k, v in data.items()}
    except Exception as e:
        logger.warning(f"跟踪文件读取失败: {e}")
    return {}


def reconcile() -> dict:
    """执行对账, 返回差异报告。"""
    paper = get_paper_positions()
    track = get_track_positions()
    sqlite = get_sqlite_positions()

    all_syms = set(list(paper.keys()) + list(track.keys()) + list(sqlite.keys()))
    diffs = []
    for sym in all_syms:
        p = paper.get(sym, {})
        t = track.get(sym, {})
        s = sqlite.get(sym, {})
        qtys = {p.get("qty", 0), t.get("qty", 0), s.get("qty", 0)}
        if len(qtys) > 1:
            diffs.append({
                "symbol": sym,
                "paper_qty": p.get("qty", 0),
                "track_qty": t.get("qty", 0),
                "sqlite_qty": s.get("qty", 0),
                "drift": max(qtys) - min(qtys),
            })

    report = {
        "time": datetime.now().isoformat(),
        "paper_count": len(paper),
        "track_count": len(track),
        "sqlite_count": len(sqlite),
        "differences": len(diffs),
        "details": diffs,
    }

    # 漂移告警
    if diffs:
        logger.warning(f"[Recon] {len(diffs)}只股票持仓不一致!")
        for d in diffs:
            logger.warning(f"  {d['symbol']}: paper={d['paper_qty']} track={d['track_qty']} sqlite={d['sqlite_qty']}")

    # 写日志
    with open(RECON_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    r = reconcile()
    status = "⚠️ 漂移" if r["differences"] > 0 else "✅ 一致"
    print(f"[Recon] {status} | paper:{r['paper_count']} track:{r['track_count']} sqlite:{r['sqlite_count']}")
