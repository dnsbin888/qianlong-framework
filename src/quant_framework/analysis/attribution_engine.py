"""AttributionEngine — 盘后盈亏归因引擎 (E243)
================================================

读取当日 trade_orders，FIFO 配对买卖订单，计算盈亏，生成报告。

红线:
    - 只读 trade_orders，只写 daily_attribution + JSON 报告
    - 绝不触发任何下单逻辑
    - 未匹配订单标记为"待结算"，不丢弃
    - 不改动 trade_orders / daily_performance / signal_log 的 DDL 和方法

用法::

    from quant_framework.analysis.attribution_engine import AttributionEngine
    engine = AttributionEngine()
    engine.run_daily_analysis("2026-06-20")
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from quant_framework.data.sqlite_persistence import get_db_service

logger = logging.getLogger("attribution_engine")

_REPORTS_DIR: str = r"D:\quant_framework\reports"


class AttributionEngine:
    """盘后归因引擎 — FIFO 配对 + 盈亏计算 + 报告生成。

    Args:
        strategy_name: 策略名 (默认 "ma_cross")
    """

    def __init__(self, strategy_name: str = "ma_cross") -> None:
        self._strategy_name: str = strategy_name
        self._db = get_db_service(r"D:\quant_web\quant_engine.db")

        # 确保报告目录存在 (宪法 5.3: 自动创建)
        try:
            os.makedirs(_REPORTS_DIR, exist_ok=True)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    #  主方法
    # ═══════════════════════════════════════════════════════

    def run_daily_analysis(self, trade_date: str) -> dict[str, Any]:
        """对指定日期执行盘后归因分析。

        Args:
            trade_date: 交易日期 "YYYY-MM-DD"

        Returns:
            归因记录 dict，含 summary + pairs + unsettled
        """
        logger.info(f"归因分析开始: {trade_date}")

        try:
            # ── 1. 可选时间检查 ──
            if not self._assert_after_hours():
                return {"status": "blocked", "reason": "盘中不允许运行归因分析 (16:00前)", "trade_date": trade_date}

            # ── 2. 读取当日订单 ──
            orders: list[dict[str, Any]] = self._db.get_trades_by_date(trade_date)

            if not orders:
                logger.info(f"当日 ({trade_date}) 无交易记录，生成空报告")
                record = self._empty_record(trade_date)
                self._db.save_attribution(record)
                self._save_json_report(record)
                return record

            logger.info(f"当日订单数: {len(orders)}")

            # ── 3. 按 symbol 分组 → FIFO 配对 ──
            pairs, unsettled = self._fifo_pair(orders)

            # ── 4. 聚合统计 ──
            summary = self._aggregate(pairs, unsettled)

            # ── 5. 构建记录 ──
            record: dict[str, Any] = {
                "trade_date": trade_date,
                "strategy_name": self._strategy_name,
                "summary": summary,
                "pairs": pairs,
                "unsettled": unsettled,
                "generated_at": datetime.now().isoformat(),
            }

            # ── 6. 写入 DB + JSON ──
            db_record = self._to_db_record(trade_date, summary, pairs, unsettled)
            self._db.save_attribution(db_record)
            self._save_json_report(record)

            logger.info(
                f"归因完成: {trade_date} | "
                f"交易={summary['total_trades']} | "
                f"胜率={summary['win_rate']:.2%} | "
                f"总盈亏={summary['total_pnl']:.2f}"
            )
            return record

        except Exception as e:
            logger.error(f"归因分析异常 ({trade_date}): {e}", exc_info=True)
            record = self._empty_record(trade_date)
            record["error"] = str(e)
            return record

    # ═══════════════════════════════════════════════════════
    #  FIFO 配对
    # ═══════════════════════════════════════════════════════

    def _fifo_pair(
        self, orders: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """FIFO 配对买卖订单。

        Args:
            orders: 按 created_at 升序的订单列表

        Returns:
            (pairs: 已配对, unsettled: 待结算)
        """
        # 按 symbol 分组
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for o in orders:
            sym = o.get("symbol", "")
            by_symbol.setdefault(sym, []).append(o)

        pairs: list[dict[str, Any]] = []
        unsettled: list[dict[str, Any]] = []

        for sym, sym_orders in by_symbol.items():
            buy_queue: list[dict[str, Any]] = []

            for o in sym_orders:
                # E243 审查修正 #4: 大小写不敏感
                direction = o.get("direction", "").lower()
                volume = float(o.get("volume", 0))
                price = float(o.get("price", 0))

                if direction == "buy":
                    buy_queue.append(dict(o))

                elif direction == "sell":
                    sell_remaining = volume

                    while sell_remaining > 0 and buy_queue:
                        buy = buy_queue[0]
                        buy_vol = float(buy.get("volume", 0))
                        buy_price = float(buy.get("price", 0))

                        matched_vol = min(buy_vol, sell_remaining)
                        pnl = (price - buy_price) * matched_vol

                        pairs.append({
                            "symbol": sym,
                            "buy_order_id": buy.get("order_id", ""),
                            "sell_order_id": o.get("order_id", ""),
                            "buy_price": buy_price,
                            "sell_price": price,
                            "volume": matched_vol,
                            "pnl": round(pnl, 2),
                            "status": "closed",
                        })

                        sell_remaining -= matched_vol
                        buy["volume"] = buy_vol - matched_vol

                        if float(buy.get("volume", 0)) <= 0:
                            buy_queue.pop(0)

                    # 部分成交：sell 还有剩余但 buy_queue 空了
                    if sell_remaining > 0:
                        unsettled.append({
                            "symbol": sym,
                            "order_id": o.get("order_id", ""),
                            "direction": "sell",
                            "price": price,
                            "volume": sell_remaining,
                            "original_volume": volume,
                            "status": "待结算",
                        })

            # 剩余未配对的 buy 订单
            for buy in buy_queue:
                unsettled.append({
                    "symbol": sym,
                    "order_id": buy.get("order_id", ""),
                    "direction": "buy",
                    "price": float(buy.get("price", 0)),
                    "volume": float(buy.get("volume", 0)),
                    "original_volume": float(buy.get("volume", 0)),
                    "status": "待结算",
                })

        return pairs, unsettled

    # ═══════════════════════════════════════════════════════
    #  聚合统计
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _aggregate(
        pairs: list[dict[str, Any]], unsettled: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """计算聚合指标。"""
        total_trades: int = len(pairs)
        winning_trades: int = 0
        losing_trades: int = 0
        total_pnl: float = 0.0
        win_pnls: list[float] = []
        loss_pnls: list[float] = []

        for p in pairs:
            pnl = p.get("pnl", 0.0)
            total_pnl += pnl
            if pnl > 0:
                winning_trades += 1
                win_pnls.append(pnl)
            elif pnl < 0:
                losing_trades += 1
                loss_pnls.append(pnl)

        avg_win: float = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        avg_loss: float = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        win_rate: float = winning_trades / total_trades if total_trades > 0 else 0.0

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "win_rate": round(win_rate, 4),
            "unsettled_count": len(unsettled),
        }

    # ═══════════════════════════════════════════════════════
    #  DB 记录转换
    # ═══════════════════════════════════════════════════════

    def _to_db_record(
        self,
        trade_date: str,
        summary: dict[str, Any],
        pairs: list[dict[str, Any]],
        unsettled: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """将归因结果转为 DB 写入格式。"""
        return {
            "trade_date": trade_date,
            "strategy_name": self._strategy_name,
            "total_trades": summary["total_trades"],
            "winning_trades": summary["winning_trades"],
            "losing_trades": summary["losing_trades"],
            "total_pnl": summary["total_pnl"],
            "avg_win": summary["avg_win"],
            "avg_loss": summary["avg_loss"],
            "win_rate": summary["win_rate"],
            "unsettled_count": summary["unsettled_count"],
            "pairs": pairs,
        }

    def _empty_record(self, trade_date: str) -> dict[str, Any]:
        """生成空记录。"""
        return {
            "trade_date": trade_date,
            "strategy_name": self._strategy_name,
            "summary": {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "win_rate": 0.0,
                "unsettled_count": 0,
            },
            "pairs": [],
            "unsettled": [],
            "generated_at": datetime.now().isoformat(),
        }

    # ═══════════════════════════════════════════════════════
    #  JSON 报告
    # ═══════════════════════════════════════════════════════

    def _save_json_report(self, record: dict[str, Any]) -> None:
        """将归因结果保存为 JSON 报告。"""
        try:
            os.makedirs(_REPORTS_DIR, exist_ok=True)
            filename: str = f"daily_{record['trade_date'].replace('-', '')}.json"
            filepath: str = os.path.join(_REPORTS_DIR, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

            logger.info(f"报告已保存: {filepath}")
        except Exception as e:
            logger.error(f"报告保存失败: {e}")

    # ═══════════════════════════════════════════════════════
    #  可选时间检查
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _assert_after_hours() -> bool:
        """盘后时间检查 — 盘中阻止，盘后放行 (E250 P2-2)。"""
        now = datetime.now()
        if now.hour < 16:
            logger.warning("归因引擎在盘中调用被阻止 (当前 < 16:00)")
            return False
        return True
