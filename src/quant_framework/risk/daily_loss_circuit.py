"""P3-4: 账户级日亏硬熔断 — 日亏>2%暂停开仓, >5%强制平仓。"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# 默认阈值（不动现有配置值）
SOFT_LIMIT_PCT = 0.02   # 2% — 暂停开仓
HARD_LIMIT_PCT = 0.05   # 5% — 强制平仓
CHECK_INTERVAL = 30     # 秒


class DailyLossCircuitBreaker:
    """账户级日亏硬熔断。

    每日重置，逐笔追踪：
    - 软熔断(2%): 暂停开仓，允许平仓
    - 硬熔断(5%): 强制平仓所有持仓
    """

    def __init__(self) -> None:
        self._initial_equity: float = 0.0
        self._current_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_pnl_pct: float = 0.0
        self._frozen: bool = False      # 软熔断
        self._emergency: bool = False   # 硬熔断
        self._last_check: float = 0.0
        self._today: str = ""

    def _reset_if_new_day(self) -> None:
        """跨日自动重置。"""
        today = time.strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self._frozen = False
            self._emergency = False
            self._daily_pnl = 0.0
            self._daily_pnl_pct = 0.0
            self._initial_equity = self._current_equity
            if self._initial_equity <= 0:
                self._load_equity()
            logger.info(f"[熔断] 新交易日重置: 初始权益={self._initial_equity:.0f}")

    def _load_equity(self) -> None:
        """从模拟盘加载当前权益。"""
        paths = [
            r"D:\quant_framework\paper_account.json",
            r"D:\quant_web\paper_account.json",
        ]
        for pp in paths:
            if not os.path.exists(pp):
                continue
            try:
                with open(pp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                eq = data.get("equity") or data.get("total_asset") or 0
                if eq > 0:
                    self._current_equity = float(eq)
                    if self._initial_equity <= 0:
                        self._initial_equity = float(eq)
                    return
            except Exception:
                continue
        self._current_equity = 100000.0
        self._initial_equity = 100000.0

    def update_equity(self, equity: float) -> None:
        """更新当前权益（每次交易后调用）。"""
        self._current_equity = equity
        self._reset_if_new_day()
        if self._initial_equity > 0:
            self._daily_pnl = equity - self._initial_equity
            self._daily_pnl_pct = self._daily_pnl / self._initial_equity

    def check(self) -> dict[str, Any]:
        """检查当前熔断状态。

        返回: {
            "status": "normal" | "soft_frozen" | "hard_emergency",
            "daily_pnl_pct": float,
            "can_buy": bool,
            "must_liquidate": bool,
            "label": str,
            "emoji": str,
        }
        """
        self._reset_if_new_day()
        self._load_equity()
        if self._initial_equity <= 0:
            self._initial_equity = self._current_equity

        self._daily_pnl = self._current_equity - self._initial_equity
        self._daily_pnl_pct = self._daily_pnl / (self._initial_equity + 0.01)

        # 硬熔断判断
        if self._daily_pnl_pct <= -HARD_LIMIT_PCT:
            self._emergency = True
            self._frozen = True
        elif not self._emergency and self._daily_pnl_pct <= -SOFT_LIMIT_PCT:
            self._frozen = True

        # 恢复判断: 亏损回到阈值以上 + 手动恢复
        # 注意: 熔断后不自动恢复（需新交易日）

        if self._emergency:
            return {
                "status": "hard_emergency",
                "daily_pnl_pct": round(self._daily_pnl_pct * 100, 2),
                "can_buy": False,
                "must_liquidate": True,
                "label": "🔴 日亏熔断",
                "emoji": "🔴",
            }
        elif self._frozen:
            return {
                "status": "soft_frozen",
                "daily_pnl_pct": round(self._daily_pnl_pct * 100, 2),
                "can_buy": False,
                "must_liquidate": False,
                "label": "🟡 日亏警戒",
                "emoji": "🟡",
            }
        else:
            return {
                "status": "normal",
                "daily_pnl_pct": round(self._daily_pnl_pct * 100, 2),
                "can_buy": True,
                "must_liquidate": False,
                "label": "🟢 正常",
                "emoji": "🟢",
            }


# 全局单例
_breaker: DailyLossCircuitBreaker | None = None


def get_daily_loss_status() -> dict[str, Any]:
    """快捷函数：获取当前日亏熔断状态。"""
    global _breaker
    if _breaker is None:
        _breaker = DailyLossCircuitBreaker()
    return _breaker.check()


def update_daily_equity(equity: float) -> None:
    """快捷函数：更新权益（交易后调用）。"""
    global _breaker
    if _breaker is None:
        _breaker = DailyLossCircuitBreaker()
    _breaker.update_equity(equity)
