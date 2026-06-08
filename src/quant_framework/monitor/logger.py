"""Structured logging for strategies.

Uses Python's standard logging with JSON formatting for file output
and colored console output for real-time monitoring.

Each strategy gets its own named logger that includes strategy_id
in every message for traceability.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON Lines (one JSON object per line).

    Output format:
        {"timestamp": "2024-01-01T12:00:00", "level": "INFO", "strategy_id": "macd_1",
         "event": "signal_generated", "symbol": "600000", "direction": "buy"}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
        }

        # Include strategy_id if present
        if hasattr(record, "strategy_id"):
            log_entry["strategy_id"] = record.strategy_id

        # If the message is a dict, merge it; otherwise use as "message"
        if isinstance(record.msg, dict):
            log_entry.update(record.msg)
        else:
            log_entry["message"] = record.msg

        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ColoredConsoleFormatter(logging.Formatter):
    """Formatter with ANSI colors for console output."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    GREY = "\033[90m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:<8}{self.RESET}"
        time_str = f"{self.GREY}{datetime.now().strftime('%H:%M:%S')}{self.RESET}"

        strategy = ""
        if hasattr(record, "strategy_id"):
            strategy = f"[{record.strategy_id}] "

        msg = record.msg
        if isinstance(msg, dict):
            # Pretty-print dict messages
            parts = [f"{k}={v}" for k, v in msg.items() if k != "timestamp"]
            msg = " ".join(parts)

        return f"{time_str} {level} {strategy}{msg}"


def setup_framework_logging(
    log_dir: str = "./logs",
    log_level: str = "INFO",
    console: bool = True,
    json_file: bool = True,
) -> None:
    """Configure the quant framework's root logger.

    Args:
        log_dir: Directory for log files.
        log_level: Minimum log level.
        console: Enable colored console output.
        json_file: Enable JSON file output.
    """
    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger("quant_framework")
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.handlers.clear()

    # JSON file handler
    if json_file:
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "quant_framework.log"),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=10,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

        # Error-only file
        error_handler = RotatingFileHandler(
            os.path.join(log_dir, "quant_framework_error.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter())
        root.addHandler(error_handler)

    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper()))
        console_handler.setFormatter(ColoredConsoleFormatter())
        root.addHandler(console_handler)


class StrategyLogger:
    """Per-strategy structured logger.

    Wraps the standard logging with strategy context automatically
    injected into every log record.

    Usage:
        logger = StrategyLogger("macd_1")
        logger.info("signal_generated", symbol="600000", direction="buy")
        # Output: {"strategy_id": "macd_1", "event": "signal_generated", "symbol": "600000", ...}
    """

    def __init__(self, strategy_id: str, name: str = "") -> None:
        self.strategy_id = strategy_id
        self._logger = logging.getLogger(f"quant_framework.strategy.{strategy_id}")

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        extra = {"strategy_id": self.strategy_id}
        msg = {"event": event, **kwargs}
        self._logger.log(level, msg, extra=extra)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    # Convenience methods for common events
    def signal(self, signal: Any) -> None:
        self.info("signal", symbol=signal.symbol, direction=signal.direction.value,
                  price=signal.price, reason=signal.reason)

    def order_submitted(self, order: Any) -> None:
        self.info("order_submitted", order_id=order.order_id, symbol=order.symbol,
                  direction=order.direction.value, volume=order.requested_volume)

    def order_filled(self, order: Any) -> None:
        self.info("order_filled", order_id=order.order_id, symbol=order.symbol,
                  fill_price=order.avg_fill_price, volume=order.filled_volume)

    def order_rejected(self, order: Any, reason: str) -> None:
        self.warning("order_rejected", order_id=order.order_id, reason=reason)

    def risk_blocked(self, order_id: str, reason: str) -> None:
        self.warning("risk_blocked", order_id=order_id, reason=reason)
