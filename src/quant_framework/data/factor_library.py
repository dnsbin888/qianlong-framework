"""FactorLibrary — 基础量化因子库 (E253)
==========================================

SQLite 因子数据库，支持全市场因子计算、Z-Score 标准化、打分排序。

因子列表 (可扩展):
    - ma5_slope: MA5 斜率
    - ma20_slope: MA20 斜率
    - rsi_14: RSI(14)
    - vol_ratio: 量比 (今日量/5日均量)
    - turnover: 换手率
    - amplitude: 振幅 (high-low)/pre_close
    - ret_5d: 5日收益率
    - ret_20d: 20日收益率
    - vol_20d: 20日波动率
    - max_drawdown_20d: 20日最大回撤

用法::

    lib = FactorLibrary()
    lib.update_daily("2026-06-20")
    scores = lib.get_top_scores("2026-06-20", top_n=50)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger("quant_framework.factors")

_DEFAULT_DB_PATH: str = r"D:\quant_framework\data\factor_db.sqlite"

# ── 因子定义 ──
_FACTOR_LIST: list[str] = [
    "ma5_slope", "ma20_slope", "rsi_14", "vol_ratio", "turnover",
    "amplitude", "ret_5d", "ret_20d", "vol_20d", "max_drawdown_20d",
]

# ── DDL ──
_DDL_FACTORS: str = f"""
CREATE TABLE IF NOT EXISTS factor_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT(10) NOT NULL,
    symbol      TEXT(20) NOT NULL,
    {', '.join(f'{f} REAL DEFAULT 0.0' for f in _FACTOR_LIST)},
    total_score REAL DEFAULT 0.0,
    UNIQUE(trade_date, symbol)
)
"""

_DDL_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_factor_date ON factor_scores(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_factor_symbol ON factor_scores(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_factor_total ON factor_scores(trade_date, total_score DESC)",
]


class FactorLibrary:
    """因子库 — SQLite 存储 + Z-Score 标准化 + 打分排序。

    Args:
        db_path: SQLite 数据库路径
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path: str = db_path
        self._lock: threading.Lock = threading.Lock()
        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.isdir(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(_DDL_FACTORS)
            for idx in _DDL_INDEXES:
                try:
                    conn.execute(idx)
                except Exception:
                    pass
            conn.commit()
            conn.close()
            logger.info(f"因子库已就绪: {self._db_path}")
        except Exception as e:
            logger.error(f"因子库初始化失败: {e}")

    # ═══════════════════════════════════════════════════════
    #  因子计算 (从 CSV/本地数据)
    # ═══════════════════════════════════════════════════════

    def calculate_factors(
        self,
        symbols: list[str],
        trade_date: str,
    ) -> dict[str, dict[str, float]]:
        """计算单日因子值。

        Args:
            symbols: 股票代码列表
            trade_date: 交易日期

        Returns:
            {symbol: {factor: value, ...}}
        """
        import numpy as np
        import pandas as pd

        result: dict[str, dict[str, float]] = {}

        for symbol in symbols:
            try:
                kline = self._get_kline(symbol)
                if kline is None or len(kline) < 25:
                    continue

                closes = kline["close"].values
                highs = kline["high"].values
                lows = kline["low"].values
                volumes = kline["volume"].values

                factors: dict[str, float] = {}

                # MA5 斜率
                ma5 = np.mean(closes[-5:])
                ma5_prev = np.mean(closes[-6:-1])
                factors["ma5_slope"] = round(float((ma5 - ma5_prev) / ma5_prev) if ma5_prev > 0 else 0.0, 6)

                # MA20 斜率
                ma20 = np.mean(closes[-20:])
                ma20_prev = np.mean(closes[-21:-1])
                factors["ma20_slope"] = round(float((ma20 - ma20_prev) / ma20_prev) if ma20_prev > 0 else 0.0, 6)

                # RSI(14)
                factors["rsi_14"] = round(float(self._calc_rsi(closes, 14)), 4)

                # 量比
                vol5_avg = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else volumes[-1]
                factors["vol_ratio"] = round(float(volumes[-1] / vol5_avg) if vol5_avg > 0 else 1.0, 4)

                # 换手率 (若有流通股本则计算，否则用 volume 近似)
                factors["turnover"] = round(float(volumes[-1] / 1e8), 6)  # 近似

                # 振幅
                pre_close = closes[-2] if len(closes) >= 2 else closes[-1]
                factors["amplitude"] = round(float((highs[-1] - lows[-1]) / pre_close) if pre_close > 0 else 0.0, 4)

                # 5日收益率
                factors["ret_5d"] = round(float((closes[-1] - closes[-5]) / closes[-5]) if closes[-5] > 0 else 0.0, 6)

                # 20日收益率
                factors["ret_20d"] = round(float((closes[-1] - closes[-20]) / closes[-20]) if closes[-20] > 0 else 0.0, 6)

                # 20日波动率
                rets = np.diff(closes[-21:]) / closes[-21:-1]
                factors["vol_20d"] = round(float(np.std(rets) * np.sqrt(252)), 6)

                # 20日最大回撤
                cummax = np.maximum.accumulate(closes[-20:])
                dd = (closes[-20:] - cummax) / cummax
                factors["max_drawdown_20d"] = round(float(np.min(dd)), 6)

                result[symbol] = factors

            except Exception:
                continue

        return result

    @staticmethod
    def _calc_rsi(closes, period: int = 14) -> float:
        """计算 RSI。"""
        import numpy as np
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes[-(period + 1):])
        gains = np.sum(deltas[deltas > 0]) / period if np.any(deltas > 0) else 0
        losses = -np.sum(deltas[deltas < 0]) / period if np.any(deltas < 0) else 1e-9
        rs = gains / losses if losses > 0 else 1.0
        return round(float(100 - 100 / (1 + rs)), 2)

    def _get_kline(self, symbol: str):
        """获取日线数据 (从 CSV 或 pickle)。"""
        import pandas as pd
        import os as _os

        # 尝试 CSV
        csv_path = rf"D:\quant_framework\data\market\{symbol}\1d.csv"
        if _os.path.exists(csv_path):
            return pd.read_csv(csv_path)

        # 尝试 pickle/parquet (P0-2: 统一入口)
        try:
            import sys as _sys
            _sys.path.insert(0, r"D:\quant_web")
            from data_loader import load_stock_data_from_cache
            data = load_stock_data_from_cache()
            if data and symbol in data:
                return data[symbol].copy()
        except Exception:
            pass
        pkl_path = r"D:\quant_web\stock_data.pkl"
        if _os.path.exists(pkl_path):
            try:
                import pickle
                df = pickle.load(open(pkl_path, "rb"))
                if symbol in df:
                    return df[symbol]
            except Exception:
                pass
        return None

    # ═══════════════════════════════════════════════════════
    #  Z-Score 标准化
    # ═══════════════════════════════════════════════════════

    def calculate_z_scores(
        self, factor_values: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        """全市场因子 Z-Score 标准化 (剔除极端值)。

        Args:
            factor_values: {symbol: {factor: value, ...}}

        Returns:
            {symbol: {factor_z: z_score, ...}}
        """
        import numpy as np

        if not factor_values:
            return {}

        # 按因子聚合
        factor_arrays: dict[str, list[tuple[str, float]]] = {f: [] for f in _FACTOR_LIST}
        for symbol, factors in factor_values.items():
            for f in _FACTOR_LIST:
                val = factors.get(f, 0.0)
                if val != 0.0 or f == "turnover":  # turnover 可以为 0
                    factor_arrays[f].append((symbol, val))

        result: dict[str, dict[str, float]] = {s: {} for s in factor_values}

        for f in _FACTOR_LIST:
            pairs = factor_arrays[f]
            if len(pairs) < 5:
                continue

            values = np.array([v for _, v in pairs], dtype=float)

            # 剔除极端值 (3 sigma)
            mean = np.mean(values)
            std = np.std(values)
            if std < 1e-9:
                std = 1e-9
            mask = np.abs(values - mean) < 3 * std

            # 对过滤后的数据计算 Z-Score
            filtered_values = values[mask]
            filtered_mean = np.mean(filtered_values)
            filtered_std = np.std(filtered_values)
            if filtered_std < 1e-9:
                filtered_std = 1e-9

            for i, (symbol, _) in enumerate(pairs):
                if i < len(values) and mask[i]:
                    z = (values[i] - filtered_mean) / filtered_std
                    result[symbol][f] = round(float(z), 4)

        return result

    # ═══════════════════════════════════════════════════════
    #  打分排序
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def get_top_scores(
        z_scores: dict[str, dict[str, float]], top_n: int = 50
    ) -> list[dict[str, Any]]:
        """按因子总 Z-Score 降序，返回前 N 名。

        Args:
            z_scores: {symbol: {factor: z_score, ...}}
            top_n: 返回前几名

        Returns:
            [{symbol, total_score, factors}, ...]
        """
        ranked: list[tuple[str, float, dict]] = []
        for symbol, scores in z_scores.items():
            total = sum(scores.values())
            ranked.append((symbol, total, scores))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return [
            {"symbol": s, "total_score": round(t, 4), "factors": f}
            for s, t, f in ranked[:top_n]
        ]

    # ═══════════════════════════════════════════════════════
    #  每日更新
    # ═══════════════════════════════════════════════════════

    def update_daily(self, trade_date: str, symbols: list[str] | None = None) -> int:
        """每日因子库更新 (计算 → 标准化 → 存储)。

        Args:
            trade_date: 交易日期
            symbols: 股票列表 (None=自动获取500只活跃股)

        Returns:
            计算成功的股票数
        """
        if symbols is None:
            symbols = self._get_default_pool()

        logger.info(f"因子库更新开始: {trade_date} ({len(symbols)} 只)")

        # 计算因子原始值
        values = self.calculate_factors(symbols, trade_date)
        if not values:
            logger.warning("因子计算无结果")
            return 0

        # Z-Score 标准化
        z_scores = self.calculate_z_scores(values)

        # 存储到 SQLite
        count = self._save_scores(trade_date, z_scores)
        logger.info(f"因子库更新完成: {count} 只")
        return count

    def _get_default_pool(self) -> list[str]:
        """获取默认股票池 (从 CSV 目录扫描)。"""
        csv_dir = r"D:\quant_framework\data\market"
        if os.path.isdir(csv_dir):
            return [
                d for d in os.listdir(csv_dir)
                if os.path.isdir(os.path.join(csv_dir, d)) and d.isdigit()
            ][:500]
        return ["600000", "000001", "600519"]

    def _save_scores(self, trade_date: str, z_scores: dict[str, dict[str, float]]) -> int:
        """批量写入因子 Z-Score。"""
        if not z_scores:
            return 0

        field_placeholders = ", ".join(f":{f}" for f in _FACTOR_LIST)
        sql = f"""
            INSERT OR REPLACE INTO factor_scores
                (trade_date, symbol, {', '.join(_FACTOR_LIST)}, total_score)
            VALUES (:trade_date, :symbol, {field_placeholders}, :total_score)
        """

        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                count = 0
                for symbol, scores in z_scores.items():
                    total = sum(scores.values())
                    params: dict[str, Any] = {
                        "trade_date": trade_date,
                        "symbol": symbol,
                        "total_score": round(total, 4),
                    }
                    for f in _FACTOR_LIST:
                        params[f] = scores.get(f, 0.0)

                    conn.execute(sql, params)
                    count += 1
                conn.commit()
                return count
            except Exception as e:
                logger.error(f"写入因子库失败: {e}")
                return 0
            finally:
                conn.close()

    def get_scores(self, trade_date: str, top_n: int = 50) -> list[dict[str, Any]]:
        """从数据库读取指定日期的因子得分排名。

        Args:
            trade_date: 交易日期
            top_n: 返回前几名

        Returns:
            [{symbol, total_score, ...}, ...]
        """
        fields = ", ".join(_FACTOR_LIST)
        sql = f"""
            SELECT symbol, total_score, {fields}
            FROM factor_scores
            WHERE trade_date = ?
            ORDER BY total_score DESC
            LIMIT ?
        """

        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (trade_date, top_n)).fetchall()
            return [
                {
                    "symbol": r["symbol"],
                    "total_score": r["total_score"],
                    "factors": {f: r[f] for f in _FACTOR_LIST},
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"读取因子数据失败: {e}")
            return []
        finally:
            conn.close()

    def get_history(
        self, symbol: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """获取单只股票历史因子数据。"""
        fields = ", ".join(_FACTOR_LIST)
        sql = f"""
            SELECT trade_date, {fields}, total_score
            FROM factor_scores
            WHERE symbol = ? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (symbol, start_date, end_date)).fetchall()
            return [
                {
                    "trade_date": r["trade_date"],
                    "total_score": r["total_score"],
                    "factors": {f: r[f] for f in _FACTOR_LIST},
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"读取历史因子失败: {e}")
            return []
        finally:
            conn.close()
