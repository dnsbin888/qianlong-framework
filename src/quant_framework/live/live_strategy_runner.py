"""LiveStrategyRunner — 实时策略信号引擎 (v1.0)
================================================

从 live_trader_config.json 加载自动调参结果，在盘中根据实时行情
计算 ma_cross 策略信号（金叉/死叉），记录到 SQLite signal_log 表。

零实盘原则: 信号仅在终端打印 + SQLite 记录，不向 QMT 发送下单指令。

用法::

    runner = LiveStrategyRunner()
    for bar in bars:
        result = runner.on_bar("600000", bar)
        if result:
            print(f"触发信号: {result}")

    # 或: runner.start("600000", feed_callback=my_callback)
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from quant_framework.data.sqlite_persistence import get_db_service

logger = logging.getLogger("quant_framework.live")


class LiveStrategyRunner:
    """实时策略运行器 — 加载配置，计算信号，防震过滤，记录日志。

    支持:
        - 从 live_trader_config.json 读取最优参数
        - 纯 Python 双均线金叉/死叉计算
        - 5 分钟防震过滤
        - SQLite signal_log 持久化
    """

    def __init__(self, config_path: str = r"D:\quant_framework\live_trader_config.json") -> None:
        self._config_path: str = config_path

        # ── 加载策略参数 ──
        self._params: dict[str, int] = {"fast_period": 5, "slow_period": 20}
        self._active_strategy: str = "ma_cross"
        self._data_source: str = "csv"  # E238: 数据源 (csv / qmt_sim)
        self._load_config()

        # ── 数据库 ──
        self._db = get_db_service(r"D:\quant_web\quant_engine.db")

        # E242/E244: 心跳停止信号 — 必须在 broker 初始化之前创建
        # (REAL 模式拒绝时需要调用 _heartbeat_stop.set())
        self._heartbeat_stop: threading.Event = threading.Event()

        # E241/E244: QMT 下单通道
        self._broker = None  # 默认无 broker（纯信号模式）
        if self._qmt_account:
            # E244: qmt_env 合法性校验（大小写不敏感）
            env_upper: str = self._qmt_env.upper()
            if env_upper not in ("SIM", "REAL"):
                raise RuntimeError(
                    f"[E244] qmt_env='{self._qmt_env}' 非法。"
                    f"合法值: 'SIM' 或 'REAL'。"
                )
            self._qmt_env = env_upper  # 统一为大写

            # E244: REAL 模式交互确认
            real_confirmed: bool = False
            if env_upper == "REAL":
                if not sys.stdin.isatty():
                    logger.error(
                        "[FATAL] 非交互环境，无法进行实盘确认，拒绝启动"
                    )
                    self._heartbeat_stop.set()
                    sys.exit(1)
                if not self._confirm_real_mode():
                    self._heartbeat_stop.set()
                    print(
                        "[FATAL] 用户拒绝实盘模式，系统安全终止",
                        flush=True,
                    )
                    logger.error("[FATAL] 用户拒绝实盘模式，系统安全终止")
                    sys.exit(1)
                real_confirmed = True

            try:
                from quant_framework.execution.brokers.qmt_broker import QMTBroker
                self._broker = QMTBroker(
                    account_id=self._qmt_account,
                    env=self._qmt_env,
                    path=self._qmt_path,
                    session_id=self._qmt_session_id,
                    real_confirmed=real_confirmed,  # E244
                )
                self._broker.connect()
                if not self._broker.is_connected():
                    logger.warning("[E241] QMT 连接失败，降级为纯信号模式")
                    self._broker = None
                else:
                    mode_label: str = "实盘" if real_confirmed else "仿真"
                    logger.info(f"[E241] QMT {mode_label}下单通道已就绪")
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning(f"[E241] QMT 初始化失败，降级为纯信号模式: {e}")
                self._broker = None

        # E245: 硬止损看门狗 (复用现有配置字段)
        self._stop_loss_pct: float = -0.08   # 默认值，_load_config 会覆盖
        self._max_drawdown_pct: float = -0.05
        self._daily_start_asset: float | None = None  # 当日开盘资产
        self._daily_start_date: str = ""               # E250 P1-3: 每日重置日期
        self._pending_stop_orders: list[dict[str, Any]] = []  # E250 P1-2: broker断线待止损
        from quant_framework.risk.stop_loss_watchdog import StopLossWatchdog
        self._watchdog = StopLossWatchdog(
            db_service=self._db,
            stop_loss_pct=self._stop_loss_pct,
            max_drawdown_pct=self._max_drawdown_pct,
        )
        logger.info("[E245] 止损看门狗已就绪")

        # ── K 线缓存: symbol → list[dict] ──
        self._bars: dict[str, list[dict[str, Any]]] = {}

        # ── 防震过滤: symbol → (signal_type, timestamp) ──
        self._last_signal: dict[str, tuple[str, datetime]] = {}
        self._last_signal_time_any: dict[str, datetime] = {}  # E250 P2-3

        # ── 信号计数 ──
        self._signal_count: int = 0
        self._buy_count: int = 0           # E242
        self._sell_count: int = 0          # E242
        self._order_count: int = 0         # E242: QMT 委托提交
        self._order_fail_count: int = 0    # E242: QMT 委托失败
        self._circuit_count: int = 0       # E242: 熔断触发次数
        self._start_time: datetime = datetime.now()  # E242: 运行起始时间

        # E247: 盘后策略自动切换定时器
        self._start_scheduler_thread()

        logger.info(
            "LiveStrategyRunner 初始化完成 | "
            f"strategy={self._active_strategy} "
            f"fast_period={self._params['fast_period']}, "
            f"slow_period={self._params['slow_period']}"
        )

    # ═══════════════════════════════════════════════════════
    #  E244: REAL 模式强制交互确认
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _confirm_real_mode() -> bool:
        """REAL 模式强制交互确认。

        安全设计:
            - 用户输入 "YES" -> True（放行）
            - 其他任何输入 -> False（拒绝）
            - EOFError（非交互环境）-> False（拒绝）
            - KeyboardInterrupt -> False（拒绝）

        Returns:
            True = 用户确认实盘, False = 用户拒绝
        """
        warning_msg: str = (
            "\n"
            "=========================================================\n"
            "  [WARNING] 系统即将切换至【REAL 实盘模式】！\n"
            "  这将使用真实资金进行交易！\n"
            "  请确认您已充分了解风险。\n"
            "=========================================================\n"
            "  输入 YES 以确认启动实盘模式。\n"
            "  输入其他任何内容将终止系统。\n"
            "========================================================="
        )
        print(warning_msg, flush=True)
        logger.warning("[E244] 系统即将切换至 REAL 实盘模式！")

        try:
            user_input: str = input("请输入确认 >>> ").strip()
        except EOFError:
            logger.error("[E244] 非交互环境，无法确认，拒绝实盘模式")
            return False
        except KeyboardInterrupt:
            logger.error("[E244] 用户中断，拒绝实盘模式")
            return False

        if user_input == "YES":
            logger.info("[E244] 用户已确认实盘模式")
            return True
        else:
            return False

    # ═══════════════════════════════════════════════════════
    #  配置加载
    # ═══════════════════════════════════════════════════════

    def _load_config(self) -> None:
        """通过 ConfigLoader 加载配置，支持多策略动态调度 (E234).

        优先读取 strategy_scheduler.active_strategy 对应的参数，
        若对应策略无参数定义则降级回 ma_cross。
        """
        from quant_framework.config_loader import ConfigLoader

        config: dict[str, Any] = ConfigLoader.load_config(self._config_path)

        # E238/E239: 读取数据源配置 (必须在所有 return 之前)
        self._data_source = config.get("data_source", "csv")
        logger.info(f"数据源配置: {self._data_source}")

        # E241: QMT 仿真下单配置
        self._qmt_env: str = str(config.get("qmt_env", "SIM"))
        # E244: qmt_env 合法性校验
        _env_upper: str = self._qmt_env.upper()
        if _env_upper not in ("SIM", "REAL"):
            raise RuntimeError(
                f"[E244] qmt_env='{self._qmt_env}' 非法。"
                f"合法值: 'SIM' 或 'REAL'。"
            )
        self._qmt_env = _env_upper
        self._qmt_account: str = str(config.get("qmt_account", ""))
        self._qmt_path: str = str(config.get("qmt_path", r"D:\QMT交易端\userdata_mini"))
        self._qmt_session_id: int = int(config.get("qmt_session_id", 1))
        self._qmt_order_amount: float = float(config.get("qmt_order_amount", 5000))
        logger.info(f"QMT 环境: {self._qmt_env} | 账户: {self._qmt_account}")

        # E245: 硬止损风控参数（复用现有字段，不新增配置项）
        self._stop_loss_pct = float(config.get("daily_loss_clear_all", -0.08))
        self._max_drawdown_pct = float(config.get("max_daily_loss", -0.05))

        # 读取活跃策略名
        scheduler: dict[str, Any] = config.get("strategy_scheduler", {})
        active_strategy: str = scheduler.get("active_strategy", "ma_cross")

        # E246: 策略名校验 — 未注册 / 空值 → 降级回 ma_cross
        from quant_framework.strategy.registry import StrategyRegistry
        _reg = StrategyRegistry.instance()
        if not active_strategy or _reg.get(active_strategy) is None:
            if active_strategy:
                logger.warning(
                    f"[WARN] active_strategy='{active_strategy}' 未注册，"
                    f"降级回 ma_cross"
                )
            self._active_strategy = "ma_cross"
            logger.info(f"策略调度: active=ma_cross (fallback)")
            return

        # 读取对应策略参数
        strategy_params: dict[str, Any] = config.get("strategy_params", {})
        active_params: dict[str, Any] = strategy_params.get(active_strategy, {})

        if active_params:
            fast: int = int(active_params.get("fast_period", 5))
            slow: int = int(active_params.get("slow_period", 20))
            if isinstance(fast, int) and isinstance(slow, int) and fast < slow:
                self._params = {"fast_period": fast, "slow_period": slow}
                self._active_strategy = active_strategy
                logger.info(
                    f"从配置加载: {self._params} (strategy={active_strategy})"
                )
                logger.info(
                    f"策略调度: active={active_strategy} | "
                    f"registry_count={len(_reg.list_names())}"
                )
                return

        # 降级: active_strategy 无有效参数 → 回退 ma_cross
        if active_strategy != "ma_cross":
            logger.warning(
                f"活跃策略 '{active_strategy}' 无有效参数，降级回 ma_cross"
            )
        self._active_strategy = "ma_cross"

    # ═══════════════════════════════════════════════════════
    #  核心信号计算
    # ═══════════════════════════════════════════════════════

    def on_bar(self, symbol: str, bar_data: dict[str, Any]) -> str | None:
        """收到新 K 线时触发 — 追加数据 → 计算均线 → 检测交叉。

        Args:
            symbol: 股票代码
            bar_data: {"dt": "...", "close": ..., "high": ..., "low": ..., "volume": ...}

        Returns:
            "buy" / "sell" / None
        """
        # E240 + F8-修复: 统一熔断检查 — 最高优先级，聚合三熔断器状态
        from quant_framework.risk.circuit_breaker import CircuitBreaker
        cb_status = CircuitBreaker.check_all()
        if cb_status["blocked"]:
            self._circuit_count += 1  # E242
            if cb_status["level"] == "hard":
                logger.warning(
                    f"[CIRCUIT] 硬熔断触发: {', '.join(cb_status['reasons'])}"
                )
            return None  # 熔断激活，静默丢弃所有信号

        # ── E245/E250 P1-2: 硬止损看门狗（熔断之后、策略信号之前）──
        current_price: float = bar_data.get("close", 0.0)
        if current_price > 0:
            broker_ok: bool = self._broker is not None and self._broker.is_connected()

            # 1. 单笔止损检查 (E250 P1-2: 解耦 — 无论broker是否连接都检测)
            stop_order = self._watchdog.check_position(symbol, current_price)
            if stop_order:
                volume: int = stop_order["volume"]
                if broker_ok:
                    sell_id = self._broker.place_order(symbol, "sell", current_price, volume)
                    if sell_id:
                        self._db.save_trade({
                            "order_id": sell_id,
                            "symbol": symbol,
                            "direction": "sell",
                            "volume": volume,
                            "price": current_price,
                            "status": "submitted",
                            "fill_status": "submitted",
                        })
                        self._order_count += 1
                        logger.warning(
                            f"[STOP] {symbol} 强制平仓已提交: {stop_order['reason']}, "
                            f"{volume}股 @ {current_price} | order_id={sell_id}"
                        )
                        # FIXED_BY_E250_1: 同步轮询确认成交 (daemon线程 → sync polling)
                        self._poll_stop_fill_sync(sell_id, symbol, current_price, volume)
                    else:
                        logger.error(f"[STOP] {symbol} 平仓下单失败!")
                else:
                    # broker 断线: 记录告警 + 待执行队列
                    logger.critical(
                        f"[STOP] {symbol} 需止损但broker断线! "
                        f"持仓{volume}股@{current_price:.2f}"
                    )
                    self._pending_stop_orders.append({
                        "symbol": symbol, "volume": volume, "price": current_price
                    })
                return None  # 止损后不继续策略信号

            # 2. 总回撤检查 (E250 P1-3: 每日重置 + 不赋0.0)
            if broker_ok:
                today: str = datetime.now().strftime("%Y-%m-%d")
                if self._daily_start_date != today:
                    self._daily_start_date = today
                    val = self._broker.get_account_value()
                    if val is not None and val > 0:
                        self._daily_start_asset = val
                        logger.info(f"[DAILY] 每日起始资产重置: {val:.2f}")
                    else:
                        self._daily_start_asset = None

                if self._daily_start_asset is not None and self._daily_start_asset > 0:
                    current_asset = self._broker.get_account_value()
                    if current_asset:
                        if self._watchdog.check_drawdown(self._daily_start_asset, current_asset):
                            from quant_framework.risk.circuit_breaker import CircuitBreaker
                            CircuitBreaker.set_triggered(True)
                            logger.warning(
                                f"[STOP] 总回撤熔断触发! "
                                f"start={self._daily_start_asset:.2f}, "
                                f"current={current_asset:.2f}"
                            )
                            return None
            else:
                # broker 断线时跳过回撤检查 (无法获取当前资产)
                pass

        # ── 追加 K 线 ──
        if symbol not in self._bars:
            self._bars[symbol] = []
        self._bars[symbol].append(bar_data)

        # E250 P1-4: 限制缓存长度 (slow_period + 20)
        _max_bars: int = self._params.get("slow_period", 51) + 20
        if len(self._bars[symbol]) > _max_bars:
            self._bars[symbol] = self._bars[symbol][-_max_bars:]

        bars = self._bars[symbol]
        fast: int = self._params["fast_period"]
        slow: int = self._params["slow_period"]

        # ── 数据不足 ──
        if len(bars) < slow + 1:
            return None

        # ── 检测交叉 ──
        signal_type = self._check_crossover(bars, fast, slow)
        if signal_type is None:
            return None

        # ── 防震过滤 ──
        now = datetime.now()
        if self._should_filter(symbol, signal_type, now):
            logger.info(f"[FILTER] {symbol} {signal_type} 被防震过滤（距上次 < 5min）")
            return None

        # ── 记录信号 ──
        price = bar_data.get("close", 0.0)
        self._log_signal(symbol, signal_type, price, self._params)

        # E241: QMT 仿真下单（信号记录之后，不替换原有逻辑）
        if self._broker:
            volume: int = self._calc_volume(price)
            qmt_order_id: str | None = self._broker.place_order(
                symbol, signal_type, price, volume
            )
            if qmt_order_id:
                self._order_count += 1
                logger.info(
                    f"[E241] QMT 委托已提交: {symbol} {signal_type} "
                    f"{volume}股 @ {price} | order_id={qmt_order_id}"
                )
                self._db.save_trade({
                    "order_id": qmt_order_id,
                    "symbol": symbol,
                    "direction": signal_type,
                    "volume": volume,
                    "price": price,
                    "status": "submitted",
                })
            else:
                self._order_fail_count += 1
                logger.warning(f"[E241] QMT 下单失败: {symbol} {signal_type}")

        return signal_type

    def _poll_stop_fill_sync(
        self, order_id: str, symbol: str, price: float, volume: int
    ) -> None:
        """E250 P0-3 FIXED: 同步轮询确认止损卖单成交。

        轮询策略:
            1. 下单后每 0.5s 查询一次，最多 5s
            2. 成交 → 更新 fill_status, 日志
            3. 超时未成交 → 降价 0.01 重试一次（仅限卖出止损）
            4. 重试仍失败 → 撤单，下一根 bar 重新触发
        """
        import time as _time
        _MAX_WAIT: float = 5.0
        _INTERVAL: float = 0.5
        _PRICE_ADJUST: float = 0.01  # 降价幅度

        # ── 阶段 1: 同步轮询等待成交 ──
        elapsed: float = 0.0
        while elapsed < _MAX_WAIT:
            _time.sleep(_INTERVAL)
            elapsed += _INTERVAL
            status: str = (
                self._broker.query_order_status(order_id)
                if self._broker else "unknown"
            )
            if status == "filled":
                self._db.update_trade_fill_status(order_id, "filled")
                logger.info(
                    f"[STOP] {symbol} 止损卖单已成交 "
                    f"order_id={order_id} elapsed={elapsed:.1f}s"
                )
                return

        # ── 阶段 2: 超时未成交 → 降价重试一次 ──
        logger.warning(
            f"[STOP] {symbol} 止损单 {_MAX_WAIT}s 未成交，降价 {_PRICE_ADJUST} 重试..."
        )
        adjusted_price: float = round(price - _PRICE_ADJUST, 2)
        if adjusted_price <= 0:
            adjusted_price = price * 0.99  # 兜底: 降 1%

        if self._broker:
            # 先撤原单
            self._broker.cancel_order(order_id)
            self._db.update_trade_fill_status(order_id, "cancelled")

            # 降价重试
            retry_id = self._broker.place_order(
                symbol, "sell", adjusted_price, volume
            )
            if retry_id:
                self._db.save_trade({
                    "order_id": retry_id,
                    "symbol": symbol,
                    "direction": "sell",
                    "volume": volume,
                    "price": adjusted_price,
                    "status": "submitted",
                    "fill_status": "submitted",
                })
                logger.warning(
                    f"[STOP] {symbol} 止损降价重试已提交: "
                    f"{price:.2f} → {adjusted_price:.2f} | order_id={retry_id}"
                )
                # 重试后轮询（最多 3s）
                retry_elapsed: float = 0.0
                while retry_elapsed < 3.0:
                    _time.sleep(_INTERVAL)
                    retry_elapsed += _INTERVAL
                    retry_status = (
                        self._broker.query_order_status(retry_id)
                        if self._broker else "unknown"
                    )
                    if retry_status == "filled":
                        self._db.update_trade_fill_status(retry_id, "filled")
                        logger.info(
                            f"[STOP] {symbol} 止损降价重试成交 "
                            f"order_id={retry_id}"
                        )
                        return

                # 重试仍未成交
                logger.error(
                    f"[STOP] {symbol} 止损降价重试后仍未成交! "
                    f"order_id={retry_id} — 需要人工干预!"
                )
                if self._broker:
                    self._broker.cancel_order(retry_id)
                    self._db.update_trade_fill_status(retry_id, "cancelled")
            else:
                logger.error(f"[STOP] {symbol} 降价重试下单失败!")
        else:
            logger.error(f"[STOP] {symbol} 止损未成交且 broker 已断线!")
        self._db.update_trade_fill_status(order_id, "cancelled")

    def _calc_volume(self, price: float) -> int:
        """根据固定金额计算下单股数（取整到 100 股）(E250 P2-4: 资金检查).

        Args:
            price: 当前价格

        Returns:
            股数（100 的倍数，最少 100），可用资金不足时返回 0
        """
        if price <= 0:
            return 100

        order_amount: float = self._qmt_order_amount

        # E250 P2-4: 先查可用资金
        if self._broker and self._broker.is_connected():
            try:
                cash = self._broker.get_available_cash()
                if cash is not None and cash > 0:
                    order_amount = min(order_amount, cash * 0.95)  # 留 5% 余量
            except Exception:
                pass

        raw: float = order_amount / price
        volume: int = int(raw // 100) * 100

        if volume < 100:
            logger.warning(f"[VOLUME] 可用资金不足, 无法买入最小手数 (price={price})")
            return 0

        return max(volume, 100)

    # ═══════════════════════════════════════════════════════
    #  双均线交叉检测
    # ═══════════════════════════════════════════════════════

    def _check_crossover(
        self, bars: list[dict[str, Any]], fast_period: int, slow_period: int
    ) -> str | None:
        """纯 Python 双均线金叉/死叉检测。

        Args:
            bars: 最近 slow_period+1 根 K 线
            fast_period: 快线周期
            slow_period: 慢线周期

        Returns:
            "buy" (金叉) / "sell" (死叉) / None
        """
        n: int = slow_period + 1
        recent: list[float] = [b.get("close", 0.0) for b in bars[-n:]]

        # 当前均线
        curr_fast: float = sum(recent[-fast_period:]) / fast_period
        curr_slow: float = sum(recent[-slow_period:]) / slow_period

        # 前一根 K 线的均线
        prev_fast: float = sum(recent[-(fast_period + 1):-1]) / fast_period
        prev_slow: float = sum(recent[-(slow_period + 1):-1]) / slow_period

        # 金叉: MA_fast 从下方穿越到上方
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return "buy"

        # 死叉: MA_fast 从上方穿越到下方
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return "sell"

        return None

    # ═══════════════════════════════════════════════════════
    #  防震过滤
    # ═══════════════════════════════════════════════════════

    def _should_filter(self, symbol: str, signal_type: str, now: datetime) -> bool:
        """检查是否应过滤此信号 (同方向 < 5min + 任意方向 < 1min) (E250 P2-3).

        Args:
            symbol: 股票代码
            signal_type: "buy" / "sell"
            now: 当前时间

        Returns:
            True = 过滤掉, False = 放行
        """
        # E250 P2-3: 任意方向最小间隔 1 分钟
        last_any: datetime | None = self._last_signal_time_any.get(symbol)
        if last_any and now - last_any < timedelta(minutes=1):
            return True

        # 同方向信号，时间差 < 5 分钟 → 过滤
        if symbol in self._last_signal:
            last_type, last_time = self._last_signal[symbol]
            if last_type == signal_type and now - last_time < timedelta(minutes=5):
                return True

        return False

    # ═══════════════════════════════════════════════════════
    #  信号记录
    # ═══════════════════════════════════════════════════════

    def _log_signal(
        self, symbol: str, signal_type: str, price: float, params: dict[str, int]
    ) -> None:
        """记录模拟交易信号到终端 + SQLite。

        Args:
            symbol: 股票代码
            signal_type: "buy" / "sell"
            price: 触发价格
            params: 策略参数
        """
        self._signal_count += 1
        if signal_type == "buy":
            self._buy_count += 1
        elif signal_type == "sell":
            self._sell_count += 1

        # ── 终端输出 ──
        logger.info(
            f"[SIM] 触发 {symbol} {signal_type} at {price} "
            f"| params={params} | #{self._signal_count}"
        )

        # ── SQLite 持久化 ──
        self._db.save_signal_log(symbol, signal_type, price, params)

        # ── 更新防震缓存 ──
        self._last_signal[symbol] = (signal_type, datetime.now())
        self._last_signal_time_any[symbol] = datetime.now()  # E250 P2-3

    # ═══════════════════════════════════════════════════════
    #  启动入口 (预留 QMT 接入)
    # ═══════════════════════════════════════════════════════

    def start(self, symbol: str, feed_callback: Callable[[], dict[str, Any]] | None = None) -> None:
        """启动实时信号运行。

        数据源由 _data_source 决定:
            - "csv": feed_callback 提供 bar 数据 (CSV 回放)
            - "qmt_sim": 自动创建 QMTDataProvider (QMT → 降级 CSV)

        Args:
            symbol: 股票代码
            feed_callback: CSV 回放时必填，QMT 模式忽略
        """
        # E238/E239: QMT 实时行情模式
        if self._data_source == "api":
            self._start_qmt(symbol)
            # E239: 如果降级了 (_data_source 被改回 "csv")，继续走 CSV 逻辑
            if self._data_source != "csv":
                return  # QMT 模式正常退出
            # 降级 → fall through 到 CSV

        if feed_callback is None:
            logger.info(f"等待行情接入 (symbol={symbol}) — CSV/QMT 预留接口")
            logger.info(
                "接入方式: runner.start('600519', "
                "feed_callback=csv_reader)"
            )
            return

        logger.info(f"实时信号运行已启动: {symbol}")
        while True:
            try:
                bar = feed_callback()
                if bar:
                    sig = self.on_bar(symbol, bar)
                    if sig:
                        pass  # on_bar 内部已记录
            except KeyboardInterrupt:
                logger.info(f"信号运行停止 — 共触发 {self._signal_count} 条信号")
                break
            except Exception as e:
                logger.error(f"信号循环异常: {e}")
                break

    def _start_qmt(self, symbol: str) -> None:
        """E238: 通过 QMT xtdata 获取实时行情，失败降级 CSV。

        宪法 2.2: QMT 不可用时静默降级，不崩溃。
        宪法 2.3: 零实盘 — 只读 xtdata，不碰 xttrader。
        """
        try:
            import sys as _sys
            _sys.path.insert(0, r"D:\quant_framework")
            from qmt_data_provider import QMTDataProvider
            import queue

            provider = QMTDataProvider()
            provider.USE_QMT = True
            provider.connect()

            q: queue.Queue = queue.Queue()
            provider.subscribe_realtime([symbol], q)
            logger.info("[INFO] 数据源: QMT")

            # E242: 启动心跳线程
            hb_thread = threading.Thread(
                target=self._heartbeat_loop, name="E242-Heartbeat", daemon=True
            )
            hb_thread.start()

            # 消费实时行情
            while True:
                try:
                    snap = q.get(timeout=10)
                    bar_data: dict[str, Any] = {
                        "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "open": snap.open,
                        "high": snap.high,
                        "low": snap.low,
                        "close": snap.price,
                        "volume": snap.volume,
                    }
                    self.on_bar(symbol, bar_data)
                except queue.Empty:
                    continue
                except KeyboardInterrupt:
                    self._heartbeat_stop.set()
                    self._print_summary()
                    break

            provider.disconnect()

        except ImportError:
            logger.warning("[WARN] QMT xtdata 不可用 (xtquant 未安装)，降级回 CSV")
            self._data_source = "csv"
        except Exception as e:
            logger.warning(f"[WARN] QMT 连接失败，降级回 CSV: {e}")
            self._data_source = "csv"

    # ═══════════════════════════════════════════════════════
    #  E247: 盘后策略自动切换
    # ═══════════════════════════════════════════════════════

    def _schedule_daily_switch(self) -> None:
        """盘后策略自动切换调度 (E247).

        流程:
            1. 读取 strategy_scheduler.enabled (默认 True)
            2. 创建 StrategyScheduler 实例
            3. 读取当前 active_strategy
            4. 调用 scheduler.run_daily_selection()
            5. 新旧比较 → 日志记录
        """
        try:
            from quant_framework.config_loader import ConfigLoader

            config = ConfigLoader.load_config(self._config_path)
            scheduler_cfg: dict[str, Any] = config.get("strategy_scheduler", {})
            enabled: bool = scheduler_cfg.get("enabled", True)

            if not enabled:
                logger.info("[INFO] 策略自动切换已禁用")
                return

            from quant_framework.strategy.scheduler import StrategyScheduler

            sched = StrategyScheduler(
                db_service=self._db, config_path=self._config_path
            )

            current: str = scheduler_cfg.get("active_strategy", "ma_cross")
            best: str = sched.run_daily_selection(lookback_days=5)

            if best != current:
                logger.info(
                    f"[INFO] 策略切换: {current} -> {best} (原因: 近5日表现更优)"
                )
            else:
                logger.info(
                    f"[INFO] 策略维持: {current} (近5日评估无需切换)"
                )
        except Exception as e:
            logger.warning(f"[WARN] 策略调度异常: {e}")

    def _start_scheduler_thread(self) -> None:
        """启动盘后策略调度守护线程 (E247).

        使用 threading.Event.wait() 模式 (复用心跳线程设计).
        计算距离下一个 16:30 的秒数，到点执行 _schedule_daily_switch().
        """
        from quant_framework.config_loader import ConfigLoader

        config = ConfigLoader.load_config(self._config_path)
        scheduler_cfg: dict[str, Any] = config.get("strategy_scheduler", {})

        if not scheduler_cfg.get("enabled", True):
            logger.info("[INFO] 策略自动切换已禁用，不启动调度线程")
            return

        t = threading.Thread(
            target=self._scheduler_loop, name="E247-StrategyScheduler", daemon=True
        )
        t.start()
        logger.info("[E247] 每日策略切换任务已调度，将在 16:30 执行")

    def _scheduler_loop(self) -> None:
        """盘后定时器循环 (E247).

        每日 16:30 触发 _schedule_daily_switch().
        支持 test_delay_seconds 加速测试.
        使用 _heartbeat_stop Event 作为退出信号.
        """
        from quant_framework.config_loader import ConfigLoader

        while not self._heartbeat_stop.is_set():
            # ── 计算等待秒数 ──
            config = ConfigLoader.load_config(self._config_path)
            scheduler_cfg = config.get("strategy_scheduler", {})
            test_delay: int = int(scheduler_cfg.get("test_delay_seconds", 0) or 0)

            if test_delay > 0:
                wait_seconds: float = float(test_delay)
                logger.info(f"[INFO] [TEST] 策略调度测试模式: {test_delay}秒后触发")
            else:
                now: datetime = datetime.now()
                target: datetime = now.replace(
                    hour=16, minute=30, second=0, microsecond=0
                )
                if now >= target:
                    target += timedelta(days=1)  # E247 修正 #3: 跨日
                wait_seconds = (target - now).total_seconds()

            # ── 等待 ──
            if self._heartbeat_stop.wait(timeout=wait_seconds):
                break  # 收到停止信号

            # ── 到点执行 ──
            self._schedule_daily_switch()

            # ── 执行完后等 60 秒防重复 ──
            if self._heartbeat_stop.wait(timeout=60):
                break

    # ═══════════════════════════════════════════════════════
    #  E242: 心跳 + 退出摘要
    # ═══════════════════════════════════════════════════════

    def _heartbeat_loop(self) -> None:
        """心跳线程 — 每 30 分钟打印系统状态 (daemon=True)."""
        while not self._heartbeat_stop.wait(timeout=1800):
            self._print_heartbeat()

    def _print_heartbeat(self) -> None:
        """输出心跳日志。"""
        from quant_framework.risk.circuit_breaker import CircuitBreaker

        elapsed = datetime.now() - self._start_time
        hours, rem = divmod(int(elapsed.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)

        cb_status = "ENABLED" if CircuitBreaker.is_triggered() else "DISABLED"
        account_val = "N/A（QMT未连接）"
        if self._broker and self._broker.is_connected():
            val = self._broker.get_account_value()
            if val is not None:
                account_val = f"¥{val:,.2f}"

        logger.info(
            f"[HEARTBEAT] 运行时间: {hours:02d}:{mins:02d}:{secs:02d}\n"
            f"[HEARTBEAT] 熔断状态: {cb_status}\n"
            f"[HEARTBEAT] 本日信号数: {self._signal_count}\n"
            f"[HEARTBEAT] 当前持仓市值: {account_val}"
        )

    def _print_summary(self) -> None:
        """退出摘要报告。"""
        elapsed = datetime.now() - self._start_time
        hours, rem = divmod(int(elapsed.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)

        account_val = "N/A"
        if self._broker and self._broker.is_connected():
            val = self._broker.get_account_value()
            if val is not None:
                account_val = f"¥{val:,.2f}"

        lines = [
            "════════════════════════════════════════",
            " [E242] 仿真联调结束 — 运行摘要",
            "════════════════════════════════════════",
            f" 运行时长: {hours:02d}:{mins:02d}:{secs:02d}",
            f" 触发信号数: {self._signal_count}",
            f" 买入信号: {self._buy_count} | 卖出信号: {self._sell_count}",
            f" QMT 委托提交: {self._order_count} | 失败: {self._order_fail_count}",
            f" 熔断触发次数: {self._circuit_count}",
            f" 最终账户市值: {account_val}",
            "════════════════════════════════════════",
        ]
        for line in lines:
            logger.info(line)

    # ═══════════════════════════════════════════════════════
    #  状态查询
    # ═══════════════════════════════════════════════════════

    @property
    def params(self) -> dict[str, int]:
        """当前使用的策略参数。"""
        return dict(self._params)

    @property
    def signal_count(self) -> int:
        """已触发信号总数。"""
        return self._signal_count

    @property
    def last_signal(self) -> dict[str, tuple[str, datetime]]:
        """各 symbol 的上一次信号缓存。"""
        return dict(self._last_signal)
