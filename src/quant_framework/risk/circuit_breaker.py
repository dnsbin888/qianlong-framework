"""CircuitBreaker — 全局风控熔断器 (E240)
=========================================

独立于策略引擎的风控开关。
配置文件 ``circuit_breaker.enabled = true`` 时，所有策略信号被静默。

安全约束:
    - 绝对优先级: is_triggered()=True 时 on_bar 直接 return
    - 原子写入: 通过 ConfigLoader.save_config 写配置
    - TTL 缓存: 5 秒内不重复读文件，避免高频 IO
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("quant_framework.risk")

_CONFIG_PATH: str = r"D:\quant_framework\live_trader_config.json"
_CACHE_TTL: float = 5.0  # 秒


class CircuitBreaker:
    """全局风控熔断器 — 读取配置开关，控制策略信号。

    用法::

        if CircuitBreaker.is_triggered():
            return None  # 熔断激活，静默丢弃信号

        CircuitBreaker.set_triggered(True)   # 激活
        CircuitBreaker.set_triggered(False)  # 解除
    """

    _cached_value: bool = False
    _cached_config: bool = False  # E250 P0-2: 配置缓存，读取失败时保持
    _cached_at: float = 0.0

    @classmethod
    def is_triggered(cls) -> bool:
        """检查熔断是否激活（带 5 秒 TTL 缓存）。

        Returns:
            True = 熔断激活，策略应静默
        """
        now: float = time.time()
        if now - cls._cached_at < _CACHE_TTL:
            return cls._cached_value

        # 缓存过期，重新读取配置文件
        cls._cached_value = cls._read_config()
        cls._cached_at = now
        return cls._cached_value

    @classmethod
    def _read_config(cls) -> bool:
        """从配置文件读取熔断状态。

        E250 P0-2: 读取失败时保持上一次配置值，不默认 False。
        """
        try:
            from quant_framework.config_loader import ConfigLoader
            config = ConfigLoader.load_config(_CONFIG_PATH)
            cb: dict[str, Any] = config.get("circuit_breaker", {})
            cls._cached_config = bool(cb.get("enabled", False))
            return cls._cached_config
        except Exception as e:
            logger.warning(
                f"熔断器读取配置失败，保持上一次配置值: {cls._cached_config}, "
                f"error: {e}"
            )
            return cls._cached_config

    @classmethod
    def set_triggered(cls, value: bool = True) -> None:
        """设置熔断状态并原子写入配置文件。

        宪法 5.3: 写入失败时记录日志，不抛异常。

        Args:
            value: True=激活熔断, False=解除熔断
        """
        try:
            from quant_framework.config_loader import ConfigLoader

            config: dict[str, Any] = ConfigLoader.load_config(_CONFIG_PATH)
            config.setdefault("circuit_breaker", {})
            config["circuit_breaker"]["enabled"] = value
            ConfigLoader.save_config(_CONFIG_PATH, config)

            # 立即刷新缓存
            cls._cached_value = value
            cls._cached_at = time.time()

            if value:
                logger.info("[INFO] 熔断器已激活 — 所有策略信号将被静默")
            else:
                logger.info("[INFO] 熔断器已解除 — 策略信号恢复正常")
        except Exception as e:
            logger.error(f"熔断器写入配置失败: {e}")

    @staticmethod
    def check_all(start_asset: float | None = None, current_asset: float | None = None) -> dict:
        """F8-修复: 统一熔断入口 — 聚合三个熔断器状态，任一触发即停。

        检查链:
            1. CircuitBreaker.is_triggered() — 配置文件开关（全局熔断）
            2. DailyLossCircuitBreaker.check() — 账户级日亏分级（2%/5%）
            3. 实盘日亏损 inline 检查 — 当日亏损超 max_daily_loss

        Args:
            start_asset: 当日开盘总资产（可选，用于回撤检查）
            current_asset: 当前总资产（可选，用于回撤检查）

        Returns:
            {
                "blocked": bool,          # 总开关：True = 应停止交易
                "can_buy": bool,          # 是否允许买入
                "can_sell": bool,         # 始终 True（止损不受熔断限制）
                "must_liquidate": bool,   # 是否需强制平仓
                "level": "normal"|"soft"|"hard",  # 熔断级别
                "reasons": [str],         # 所有触发的熔断原因
            }
        """
        blocked = False
        can_buy = True
        can_sell = True  # 止损永远允许
        must_liquidate = False
        level = "normal"
        reasons = []

        # ── 1. 全局熔断 (配置文件 circuit_breaker.enabled) ──
        try:
            if CircuitBreaker.is_triggered():
                blocked = True
                can_buy = False
                level = "hard"
                reasons.append("全局熔断: 配置文件 circuit_breaker.enabled=true")
        except Exception:
            pass

        # ── 2. DayLossCircuitBreaker (账户级 2%/5%) ──
        try:
            from quant_framework.risk.daily_loss_circuit import get_daily_loss_status
            dl_status = get_daily_loss_status()
            dl_status_name = dl_status.get("status", "normal")
            if dl_status_name == "hard_emergency":
                blocked = True
                can_buy = False
                must_liquidate = True
                level = "hard"
                reasons.append(f"日亏硬熔断: 亏损{dl_status.get('daily_pnl_pct', 0)}%")
            elif dl_status_name == "soft_frozen":
                blocked = True
                can_buy = False
                if level != "hard":
                    level = "soft"
                reasons.append(f"日亏软熔断: 亏损{dl_status.get('daily_pnl_pct', 0)}%")
        except Exception as e:
            logger.debug(f"DailyLossCircuitBreaker 检查跳过: {e}")

        # ── 3. 账户回撤检查 (start_asset vs current_asset) ──
        if start_asset is not None and current_asset is not None and start_asset > 0:
            drawdown = (current_asset - start_asset) / start_asset
            if drawdown <= -0.08:  # 8% 硬回撤
                blocked = True
                can_buy = False
                must_liquidate = True
                level = "hard"
                reasons.append(f"账户回撤{drawdown*100:.1f}%触发硬熔断")
            elif drawdown <= -0.05:  # 5% 软回撤
                blocked = True
                can_buy = False
                if level != "hard":
                    level = "soft"
                reasons.append(f"账户回撤{drawdown*100:.1f}%触发软熔断")

        return {
            "blocked": blocked,
            "can_buy": can_buy,
            "can_sell": can_sell,
            "must_liquidate": must_liquidate,
            "level": level,
            "reasons": reasons,
        }
