"""
sqlite_persistence.py — SQLite 持久化层 (v1.0)
==============================================

基于 Python 内置 ``sqlite3`` 模块的双写持久化服务。

设计原则 (宪法合规):
    - 零外部依赖: 只使用内置 sqlite3，不依赖 SQLAlchemy
    - 降级保底: 所有写入操作 try...except，失败静默返回 False
    - 线程安全: ``check_same_thread=False`` 支持多线程
    - 日志规范: 使用 logging 而非 print

Usage::

    from quant_framework.data.sqlite_persistence import get_db_service

    svc = get_db_service()
    svc.save_performance([
        {"date": "2026-06-19", "live_value": 33895, "live_pnl": 100,
         "paper_value": 1000000, "paper_pnl": 500, "paper_return": 0.05,
         "benchmark_value": 4500}
    ])

    records = svc.get_performance_history(days=365)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger("sqlite_persistence")

# ── 环境自检: SQLAlchemy 是否可用 (非硬依赖，仅提示) ──
try:
    import sqlalchemy  # noqa: F401
    _SQLALCHEMY_AVAILABLE = True
except ImportError:
    _SQLALCHEMY_AVAILABLE = False
    logger.warning(
        "SQLAlchemy 未安装 (不影响 sqlite_persistence 运行). "
        "如需 ORM 功能请执行: pip install sqlalchemy>=2.0"
    )

# ── 默认数据库路径 ──
_DEFAULT_DB_PATH: str = r"D:\quant_web\quant_engine.db"

# ── DDL: 日频净值表 ──
_DDL_DAILY_PERFORMANCE: str = """
CREATE TABLE IF NOT EXISTS daily_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT(10) UNIQUE NOT NULL,
    live_value      REAL DEFAULT 0.0,
    live_pnl        REAL DEFAULT 0.0,
    paper_value     REAL DEFAULT 0.0,
    paper_pnl       REAL DEFAULT 0.0,
    paper_return    REAL DEFAULT 0.0,
    benchmark_value REAL DEFAULT 0.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# ── DDL: 交易订单表 ──
_DDL_TRADE_ORDERS: str = """
CREATE TABLE IF NOT EXISTS trade_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT(50) UNIQUE NOT NULL,
    symbol      TEXT(20) NOT NULL,
    direction   TEXT(10) NOT NULL,
    volume      REAL DEFAULT 0.0,
    price       REAL DEFAULT 0.0,
    status      TEXT(20) DEFAULT 'pending',
    fill_status TEXT(20) DEFAULT 'submitted',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# ── DDL: 信号日志表 ──
_DDL_SIGNAL_LOG: str = """
CREATE TABLE IF NOT EXISTS signal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT(20) NOT NULL,
    signal_type TEXT(10) NOT NULL,
    price       REAL DEFAULT 0.0,
    params_used TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# ── DDL: 日度归因表 (E243) ──
_DDL_DAILY_ATTRIBUTION: str = """
CREATE TABLE IF NOT EXISTS daily_attribution (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT(10) UNIQUE NOT NULL,
    strategy_name   TEXT(50) DEFAULT 'ma_cross',
    total_trades    INTEGER DEFAULT 0,
    winning_trades  INTEGER DEFAULT 0,
    losing_trades   INTEGER DEFAULT 0,
    total_pnl       REAL DEFAULT 0.0,
    avg_win         REAL DEFAULT 0.0,
    avg_loss        REAL DEFAULT 0.0,
    win_rate        REAL DEFAULT 0.0,
    unsettled_count INTEGER DEFAULT 0,
    pairs_json      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# ── DDL: 索引 ──
_DDL_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_daily_perf_date ON daily_performance(date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_orders_symbol ON trade_orders(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_trade_orders_order_id ON trade_orders(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_signal_log_symbol ON signal_log(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_signal_log_created ON signal_log(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_daily_attr_date ON daily_attribution(trade_date DESC)",
]


class DBService:
    """SQLite 数据访问层 — 线程安全，异常降级。

    所有写入方法失败时返回 ``False`` 并记录 ``logging.error``，
    绝不向上层抛异常 (宪法 2.2 / 5.3)。
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path: str = db_path
        self._lock: threading.Lock = threading.Lock()
        self._engine_initialized: bool = False
        self.init_db()

    # ── 初始化 ──────────────────────────────────────────

    def init_db(self) -> None:
        """创建数据库文件和表结构 (幂等操作)。"""
        try:
            # 确保目录存在
            db_dir: str = os.path.dirname(self._db_path)
            if db_dir and not os.path.isdir(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            conn: sqlite3.Connection = self._get_connection()
            cursor: sqlite3.Cursor = conn.cursor()
            cursor.execute(_DDL_DAILY_PERFORMANCE)
            cursor.execute(_DDL_TRADE_ORDERS)
            cursor.execute(_DDL_SIGNAL_LOG)
            cursor.execute(_DDL_DAILY_ATTRIBUTION)
            # E250 P0-3: 迁移已有 trade_orders 表 (加 fill_status 列)
            try:
                cursor.execute("ALTER TABLE trade_orders ADD COLUMN fill_status TEXT(20) DEFAULT 'submitted'")
            except Exception:
                pass  # 列已存在
            for idx_ddl in _DDL_INDEXES:
                try:
                    cursor.execute(idx_ddl)
                except Exception:
                    pass
            conn.commit()
            conn.close()
            self._engine_initialized = True
            logger.info(f"SQLite 数据库已就绪: {self._db_path}")
        except Exception as e:
            logger.error(f"SQLite 初始化失败: {e}")
            self._engine_initialized = False

    # ── 净值记录 ────────────────────────────────────────

    def save_performance(self, records: list[dict[str, Any]]) -> bool:
        """保存日频净值记录 (upsert: 同日期覆盖)。

        宪法 2.2 / 5.3: 所有异常在内部静默消化，返回 False。

        Args:
            records: 日频净值字典列表，字段:
                date, live_value, live_pnl, paper_value,
                paper_pnl, paper_return, benchmark_value

        Returns:
            True 成功，False 失败 (降级，不抛异常)
        """
        if not records:
            return True

        if not self._engine_initialized:
            logger.warning("SQLite 引擎未初始化，跳过写入")
            return False

        sql: str = """
            INSERT OR REPLACE INTO daily_performance
                (date, live_value, live_pnl, paper_value, paper_pnl,
                 paper_return, benchmark_value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                cursor: sqlite3.Cursor = conn.cursor()
                for rec in records:
                    cursor.execute(sql, (
                        _safe_str(rec, "date"),
                        _safe_float(rec, "live_value"),
                        _safe_float(rec, "live_pnl"),
                        _safe_float(rec, "paper_value"),
                        _safe_float(rec, "paper_pnl"),
                        _safe_float(rec, "paper_return"),
                        _safe_float(rec, "benchmark_value"),
                    ))
                conn.commit()
                logger.info(f"SQLite 双写成功: {len(records)} 条净值记录")
                return True
            except Exception as e:
                logger.error(f"SQLite 写入 daily_performance 失败: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def get_performance_history(self, days: int = 730) -> list[dict[str, Any]]:
        """读取最近 N 天的净值记录。

        Args:
            days: 回溯天数，默认 730 (约2年)

        Returns:
            净值记录列表 (date 升序)
        """
        if not self._engine_initialized:
            logger.warning("SQLite 引擎未初始化，返回空列表")
            return []

        sql: str = """
            SELECT date, live_value, live_pnl, paper_value, paper_pnl,
                   paper_return, benchmark_value
            FROM daily_performance
            ORDER BY date DESC
            LIMIT ?
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, (days,))
                rows: list[tuple] = cursor.fetchall()
                records: list[dict[str, Any]] = []
                for row in reversed(rows):  # date 升序
                    records.append({
                        "date": row[0],
                        "live_value": row[1],
                        "live_pnl": row[2],
                        "paper_value": row[3],
                        "paper_pnl": row[4],
                        "paper_return": row[5],
                        "benchmark_value": row[6],
                    })
                return records
            except Exception as e:
                logger.error(f"SQLite 读取 daily_performance 失败: {e}")
                return []
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # ── 交易订单 ────────────────────────────────────────

    def save_trade(self, order: dict[str, Any]) -> bool:
        """保存交易订单 (upsert: 同 order_id 覆盖)。

        Args:
            order: 包含 order_id, symbol, direction,
                   volume, price, status 的字典

        Returns:
            True 成功，False 失败
        """
        if not self._engine_initialized:
            logger.warning("SQLite 引擎未初始化，跳过写入")
            return False

        sql: str = """
            INSERT OR REPLACE INTO trade_orders
                (order_id, symbol, direction, volume, price, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, (
                    _safe_str(order, "order_id"),
                    _safe_str(order, "symbol"),
                    _safe_str(order, "direction"),
                    _safe_float(order, "volume"),
                    _safe_float(order, "price"),
                    _safe_str(order, "status", default="pending"),
                ))
                conn.commit()
                logger.info(f"SQLite 交易记录已保存: {order.get('order_id')}")
                return True
            except Exception as e:
                logger.error(f"SQLite 写入 trade_orders 失败: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def update_trade_fill_status(self, order_id: str, fill_status: str) -> bool:
        """更新订单成交状态 (E250 P0-3).

        Args:
            order_id: QMT 委托序号
            fill_status: "filled" / "partial" / "rejected" / "cancelled"

        Returns:
            True 成功, False 失败
        """
        if not self._engine_initialized:
            return False

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.execute(
                    "UPDATE trade_orders SET fill_status = ? WHERE order_id = ?",
                    (fill_status, order_id),
                )
                conn.commit()
                logger.info(f"订单状态更新: {order_id} → {fill_status}")
                return True
            except Exception as e:
                logger.error(f"更新 fill_status 失败: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def save_signal_log(
        self,
        symbol: str,
        signal_type: str,
        price: float,
        params_used: dict[str, Any],
    ) -> bool:
        """记录盘中触发的模拟交易信号 (E233).

        Args:
            symbol: 股票代码
            signal_type: "buy" 或 "sell"
            price: 触发价格
            params_used: 策略参数 (如 {"fast_period": 5, "slow_period": 20})

        Returns:
            True 写入成功, False 失败 (降级不抛异常)
        """
        import json as _json

        if not self._engine_initialized:
            return False

        sql: str = """
            INSERT INTO signal_log (symbol, signal_type, price, params_used)
            VALUES (?, ?, ?, ?)
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.execute(sql, (
                    symbol,
                    signal_type,
                    price,
                    _json.dumps(params_used, ensure_ascii=False),
                ))
                conn.commit()
                logger.info(f"信号已记录: {symbol} {signal_type} @ {price}")
                return True
            except Exception as e:
                logger.error(f"信号记录失败: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def get_trades(self, symbol: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """读取最近的交易订单。

        Args:
            symbol: 过滤股票代码 (空字符串 = 全部)
            limit: 最大返回条数

        Returns:
            交易订单列表
        """
        if not self._engine_initialized:
            return []

        if symbol:
            sql: str = """
                SELECT order_id, symbol, direction, volume, price, status, created_at
                FROM trade_orders
                WHERE symbol = ?
                ORDER BY created_at DESC LIMIT ?
            """
            params: tuple = (symbol, limit)
        else:
            sql = """
                SELECT order_id, symbol, direction, volume, price, status, created_at
                FROM trade_orders
                ORDER BY created_at DESC LIMIT ?
            """
            params = (limit,)

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.row_factory = sqlite3.Row
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, params)
                rows: list[sqlite3.Row] = cursor.fetchall()
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"SQLite 读取 trade_orders 失败: {e}")
                return []
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def get_trades_by_date(self, trade_date: str) -> list[dict[str, Any]]:
        """读取指定日期的全部交易订单 (E243 归因引擎用).

        Args:
            trade_date: 交易日期, 格式 "YYYY-MM-DD"

        Returns:
            该日期的交易订单列表 (按 created_at 升序, 便于 FIFO 配对)
        """
        if not self._engine_initialized:
            return []

        sql: str = """
            SELECT order_id, symbol, direction, volume, price, status, created_at
            FROM trade_orders
            WHERE date(created_at) = ?
            ORDER BY created_at ASC
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.row_factory = sqlite3.Row
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, (trade_date,))
                rows: list[sqlite3.Row] = cursor.fetchall()
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"SQLite get_trades_by_date 失败: {e}")
                return []
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def save_attribution(self, record: dict[str, Any]) -> bool:
        """保存日度归因结果 (upsert: 同 trade_date 覆盖) (E243).

        Args:
            record: 归因字典

        Returns:
            True 成功, False 失败 (降级不抛异常)
        """
        if not self._engine_initialized:
            return False

        import json as _json

        sql: str = """
            INSERT OR REPLACE INTO daily_attribution
                (trade_date, strategy_name, total_trades, winning_trades,
                 losing_trades, total_pnl, avg_win, avg_loss, win_rate,
                 unsettled_count, pairs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, (
                    _safe_str(record, "trade_date"),
                    _safe_str(record, "strategy_name", default="ma_cross"),
                    int(record.get("total_trades", 0)),
                    int(record.get("winning_trades", 0)),
                    int(record.get("losing_trades", 0)),
                    _safe_float(record, "total_pnl"),
                    _safe_float(record, "avg_win"),
                    _safe_float(record, "avg_loss"),
                    _safe_float(record, "win_rate"),
                    int(record.get("unsettled_count", 0)),
                    _json.dumps(record.get("pairs", []), ensure_ascii=False),
                ))
                conn.commit()
                logger.info(f"归因结果已保存: {record.get('trade_date')}")
                return True
            except Exception as e:
                logger.error(f"SQLite 写入 daily_attribution 失败: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # ── 状态查询 ────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """数据库是否可用。"""
        if not self._engine_initialized:
            return False
        try:
            conn: sqlite3.Connection = self._get_connection()
            conn.execute("SELECT 1 FROM daily_performance LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    @property
    def db_path(self) -> str:
        return self._db_path

    def get_attribution_history(self, days: int = 5) -> list[dict[str, Any]]:
        """读取最近 N 天的归因记录 (E246 策略调度器用).

        Args:
            days: 回看天数

        Returns:
            归因记录列表, 按 trade_date 降序.
            异常时返回 [] (降级不抛异常).
        """
        if not self._engine_initialized:
            return []

        sql: str = """
            SELECT trade_date, strategy_name, total_trades, winning_trades,
                   losing_trades, total_pnl, avg_win, avg_loss, win_rate,
                   unsettled_count
            FROM daily_attribution
            WHERE trade_date >= date('now', ?)
            ORDER BY trade_date DESC
        """

        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.row_factory = sqlite3.Row
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute(sql, (f"-{days} days",))
                rows: list[sqlite3.Row] = cursor.fetchall()
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"SQLite get_attribution_history 失败: {e}")
                return []
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def count_performance(self) -> int:
        """返回净值记录总数。"""
        if not self._engine_initialized:
            return 0
        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                row = conn.execute("SELECT COUNT(*) FROM daily_performance").fetchone()
                return int(row[0]) if row else 0
            except Exception as e:
                logger.error(f"SQLite COUNT 失败: {e}")
                return 0
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # ── 内部 ────────────────────────────────────────────

    def _get_connection(self) -> sqlite3.Connection:
        """获取 SQLite 连接 (线程安全)。"""
        conn: sqlite3.Connection = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            timeout=10.0,  # 等锁超时
        )
        conn.execute("PRAGMA journal_mode=WAL")      # 写前日志，高并发
        conn.execute("PRAGMA busy_timeout=5000")      # 5秒忙等超时
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

_instance: DBService | None = None
_instance_lock: threading.Lock = threading.Lock()


def get_db_service(db_path: str = _DEFAULT_DB_PATH) -> DBService:
    """获取 DBService 全局单例 (线程安全)。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = DBService(db_path=db_path)
    return _instance


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _safe_str(d: dict[str, Any], key: str, default: str = "") -> str:
    """安全提取字符串字段。"""
    val: Any = d.get(key, default)
    if val is None:
        return default
    return str(val)


def _safe_float(d: dict[str, Any], key: str, default: float = 0.0) -> float:
    """安全提取浮点字段 (NaN/Inf → 0.0)。"""
    val: Any = d.get(key, default)
    try:
        f: float = float(val)
        if f != f or f == float("inf") or f == float("-inf"):  # NaN 检查
            return default
        return f
    except (ValueError, TypeError):
        return default
