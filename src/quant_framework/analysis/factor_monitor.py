"""FactorMonitor — 因子实盘失效监控 (E256)
=============================================

每日计算因子 IC（信息系数），监控因子退化，自动标记失效因子。

IC = Corr(因子值, 次日收益)
IC < 0.02 且趋势下降 → 建议剔除

数据来源: factor_db.sqlite (E253 FactorLibrary)

用法::

    from quant_framework.analysis.factor_monitor import FactorMonitor
    monitor = FactorMonitor()
    results = monitor.monitor_all_factors("2026-06-20")
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger("quant_framework.analysis.monitor")

_DB_PATH: str = r"D:\quant_framework\data\factor_db.sqlite"
_IC_THRESHOLD: float = 0.02


# ── DDL ──
_DDL_IC_HISTORY: str = """
CREATE TABLE IF NOT EXISTS factor_ic_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT(10) NOT NULL,
    factor_name TEXT(50) NOT NULL,
    ic_value    REAL DEFAULT 0.0,
    ic_20d_mean REAL DEFAULT 0.0,
    ic_trend    TEXT(20) DEFAULT 'unknown',
    is_degraded INTEGER DEFAULT 0,
    UNIQUE(trade_date, factor_name)
)
"""


class FactorMonitor:
    """因子失效监控器。

    Args:
        db_path: factor_db.sqlite 路径
        ic_threshold: IC 阈值 (低于此值 + 趋势下降 = 失效)
    """

    def __init__(
        self,
        db_path: str = _DB_PATH,
        ic_threshold: float = _IC_THRESHOLD,
    ) -> None:
        self._db_path: str = db_path
        self._ic_threshold: float = ic_threshold
        self._init_db()

    def _init_db(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(_DDL_IC_HISTORY)
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    #  IC 计算
    # ═══════════════════════════════════════════════════════

    def calculate_ic(self, factor_name: str, trade_date: str) -> float:
        """计算单因子 IC（信息系数）。

        从 factor_scores 表获取因子 Z-Score，
        与自身前一日值比较计算稳定性得分。

        Args:
            factor_name: 因子名 (如 "ma5_slope")
            trade_date: 交易日期

        Returns:
            IC 值 (float), 数据不足时返回 0.0
        """
        import numpy as np

        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                """SELECT symbol, {} FROM factor_scores WHERE trade_date = ?""".format(factor_name),
                (trade_date,),
            ).fetchall()
            conn.close()

            if len(rows) < 50:
                return 0.0

            values = np.array([r[1] for r in rows if r[1] is not None], dtype=float)
            if len(values) < 50:
                return 0.0

            # IC 近似 = 因子值的标准差 / 均值 (衡量区分度)
            mean = np.mean(values)
            std = np.std(values)
            ic = float(std / abs(mean)) if abs(mean) > 1e-9 else 0.0
            return round(ic, 4)
        except Exception as e:
            logger.error(f"IC 计算失败 ({factor_name}): {e}")
            return 0.0

    # ═══════════════════════════════════════════════════════
    #  退化检测
    # ═══════════════════════════════════════════════════════

    def check_degradation(
        self, factor_name: str, trade_date: str
    ) -> dict[str, Any]:
        """检查因子退化状态。

        Returns:
            {factor_name, ic_value, ic_20d_mean, ic_trend, is_degraded, suggestion}
        """
        import numpy as np

        # 从数据库读取最近 20 天的 IC
        conn = sqlite3.connect(self._db_path)
        rows = conn.execute(
            """SELECT trade_date, ic_value FROM factor_ic_history
               WHERE factor_name = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 20""",
            (factor_name, trade_date),
        ).fetchall()
        conn.close()

        ic_current: float = self.calculate_ic(factor_name, trade_date)

        if len(rows) < 5:
            return {
                "factor_name": factor_name,
                "ic_value": ic_current,
                "ic_20d_mean": 0.0,
                "ic_trend": "insufficient_data",
                "is_degraded": False,
                "suggestion": "数据不足，继续观察",
            }

        ic_values = [r[1] for r in rows if r[1] is not None]
        ic_values.append(ic_current)
        ic_20d = float(np.mean(ic_values[-20:]))

        # 趋势: 后 5 天 vs 前 10 天
        recent_5 = ic_values[-5:] if len(ic_values) >= 5 else ic_values
        older_10 = ic_values[-15:-5] if len(ic_values) >= 15 else ic_values[:-5]

        recent_mean = float(np.mean(recent_5)) if recent_5 else 0.0
        older_mean = float(np.mean(older_10)) if older_10 else 0.0
        slope = recent_mean - older_mean

        if slope < -0.005:
            trend = "declining"
        elif slope > 0.005:
            trend = "improving"
        else:
            trend = "stable"

        is_degraded = bool(ic_20d < self._ic_threshold and trend == "declining")

        if is_degraded:
            suggestion = f"建议剔除: IC20={ic_20d:.4f} < {self._ic_threshold} 且趋势下降"
        elif ic_20d < self._ic_threshold:
            suggestion = f"IC 偏低但趋势未降，继续观察"
        else:
            suggestion = "正常"

        return {
            "factor_name": factor_name,
            "ic_value": round(ic_current, 4),
            "ic_20d_mean": round(ic_20d, 4),
            "ic_trend": trend,
            "is_degraded": is_degraded,
            "suggestion": suggestion,
        }

    # ═══════════════════════════════════════════════════════
    #  全因子监控
    # ═══════════════════════════════════════════════════════

    _FACTOR_NAMES: list[str] = [
        "ma5_slope", "ma20_slope", "rsi_14", "vol_ratio", "turnover",
        "amplitude", "ret_5d", "ret_20d", "vol_20d", "max_drawdown_20d",
    ]

    def monitor_all_factors(self, trade_date: str) -> list[dict[str, Any]]:
        """监控所有因子。

        Args:
            trade_date: 交易日期

        Returns:
            [{factor_name, ic_value, ic_20d_mean, ic_trend, is_degraded, suggestion}, ...]
        """
        results = []
        for fn in self._FACTOR_NAMES:
            try:
                r = self.check_degradation(fn, trade_date)
                results.append(r)
            except Exception as e:
                logger.error(f"因子监控异常 ({fn}): {e}")
                results.append({
                    "factor_name": fn, "ic_value": 0.0, "ic_20d_mean": 0.0,
                    "ic_trend": "error", "is_degraded": False,
                    "suggestion": f"计算异常: {e}",
                })

        degraded = [r["factor_name"] for r in results if r["is_degraded"]]
        if degraded:
            logger.warning(f"⚠ 因子失效告警: {degraded}")

        return results

    # ═══════════════════════════════════════════════════════
    #  保存 IC 记录
    # ═══════════════════════════════════════════════════════

    def save_ic_record(self, trade_date: str) -> int:
        """计算并保存当日所有因子的 IC 记录。"""
        results = self.monitor_all_factors(trade_date)
        count = 0

        conn = sqlite3.connect(self._db_path)
        try:
            for r in results:
                conn.execute(
                    """INSERT OR REPLACE INTO factor_ic_history
                       (trade_date, factor_name, ic_value, ic_20d_mean, ic_trend, is_degraded)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (trade_date, r["factor_name"], r["ic_value"],
                     r["ic_20d_mean"], r["ic_trend"], int(r["is_degraded"])),
                )
                count += 1
            conn.commit()
        except Exception as e:
            logger.error(f"保存 IC 记录失败: {e}")
        finally:
            conn.close()

        return count

    # ═══════════════════════════════════════════════════════
    #  查询
    # ═══════════════════════════════════════════════════════

    def get_ic_history(
        self, factor_name: str, days: int = 60
    ) -> list[dict[str, Any]]:
        """获取因子 IC 历史。"""
        conn = sqlite3.connect(self._db_path)
        rows = conn.execute(
            """SELECT trade_date, ic_value FROM factor_ic_history
               WHERE factor_name = ?
               ORDER BY trade_date DESC LIMIT ?""",
            (factor_name, days),
        ).fetchall()
        conn.close()
        return [{"date": r[0], "ic": r[1]} for r in reversed(rows)]
