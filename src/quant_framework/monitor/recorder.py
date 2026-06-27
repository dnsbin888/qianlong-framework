"""Trade Recorder — persist orders, trades, and equity snapshots.

Stores all trading activity in SQLite for later analysis, audit,
and performance reporting.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger("quant_framework.recorder")


class TradeRecorder:
    """Persistent trade/order/equity recorder backed by SQLite.

    Creates tables on first use:
    - orders: All order submissions and status changes
    - trades: Individual fills
    - equity_snapshots: Periodic portfolio snapshots
    - signals: Strategy signal history

    Usage:
        recorder = TradeRecorder("./data/trades.db")
        recorder.record_order(order)
        recorder.record_trade(trade)
        recorder.record_equity(timestamp, equity, cash)
    """

    def __init__(self, db_path: str = "./data/trades.db") -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()  # E250 P1-5: 线程安全
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            order_type TEXT DEFAULT 'limit',
            price REAL,
            requested_volume INTEGER NOT NULL,
            filled_volume INTEGER DEFAULT 0,
            avg_fill_price REAL DEFAULT 0,
            status TEXT DEFAULT 'created',
            commission REAL DEFAULT 0,
            reject_reason TEXT DEFAULT '',
            created_time TEXT NOT NULL,
            updated_time TEXT NOT NULL,
            filled_time TEXT,
            metadata TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            volume INTEGER NOT NULL,
            commission REAL DEFAULT 0,
            timestamp TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            market_value REAL DEFAULT 0,
            daily_pnl REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL,
            reason TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            metadata TEXT DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_equity_strategy ON equity_snapshots(strategy_id);
        """)

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a connection. (E250 P1-5: 线程安全)"""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ---- Record ----

    def record_order(self, order: Any) -> None:
        """Insert or update an order record."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, strategy_id, symbol, direction, order_type, price,
                requested_volume, filled_volume, avg_fill_price, status,
                commission, reject_reason, created_time, updated_time, filled_time, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id,
                order.strategy_id,
                order.symbol,
                order.direction.value if hasattr(order.direction, "value") else str(order.direction),
                order.order_type.value if hasattr(order.order_type, "value") else "limit",
                order.price,
                order.requested_volume,
                order.filled_volume,
                order.avg_fill_price,
                order.status.value if hasattr(order.status, "value") else str(order.status),
                order.commission,
                getattr(order, "reject_reason", ""),
                order.created_time.isoformat() if order.created_time else datetime.now().isoformat(),
                order.updated_time.isoformat() if order.updated_time else datetime.now().isoformat(),
                order.filled_time.isoformat() if order.filled_time else None,
                json.dumps(order.metadata, ensure_ascii=False) if order.metadata else "{}",
            ),
        )
        conn.commit()

    def record_trade(self, trade: Any) -> None:
        """Insert a trade fill record."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO trades
               (trade_id, order_id, strategy_id, symbol, direction, price, volume,
                commission, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.trade_id,
                trade.order_id,
                trade.strategy_id,
                trade.symbol,
                trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction),
                trade.price,
                trade.volume,
                trade.commission,
                trade.timestamp.isoformat() if trade.timestamp else datetime.now().isoformat(),
                json.dumps(trade.metadata, ensure_ascii=False) if trade.metadata else "{}",
            ),
        )
        conn.commit()

    def record_equity(
        self, strategy_id: str, equity: float, cash: float, market_value: float = 0.0
    ) -> None:
        """Record an equity snapshot."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO equity_snapshots (timestamp, strategy_id, equity, cash, market_value)
               VALUES (?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), strategy_id, equity, cash, market_value),
        )
        conn.commit()

    def record_signal(self, signal: Any) -> None:
        """Record a trading signal."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO signals (timestamp, strategy_id, symbol, direction, price, reason, confidence, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                signal.strategy_id,
                signal.symbol,
                signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction),
                signal.price,
                signal.reason,
                signal.confidence,
                json.dumps(signal.metadata, ensure_ascii=False) if signal.metadata else "{}",
            ),
        )
        conn.commit()

    # ---- Query ----

    def load_orders(
        self, strategy_id: str | None = None, start: str | None = None, end: str | None = None
    ) -> list[dict[str, Any]]:
        """Load orders with optional filters."""
        conn = self._get_conn()
        query = "SELECT * FROM orders WHERE 1=1"
        params: list[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if start:
            query += " AND created_time >= ?"
            params.append(start)
        if end:
            query += " AND created_time <= ?"
            params.append(end)
        query += " ORDER BY created_time DESC"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def load_trades(
        self, symbol: str | None = None, strategy_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Load trade history."""
        conn = self._get_conn()
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def load_equity_curve(self, strategy_id: str) -> list[dict[str, Any]]:
        """Load equity curve for a strategy."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM equity_snapshots WHERE strategy_id = ? ORDER BY timestamp",
            (strategy_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ---- Lifecycle ----

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "TradeRecorder":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
