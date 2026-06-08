r"""统一回测数据存储 — P1#5 + P2#6 + P2#8 修复。

解决:
  P1#5: trade_log.csv 被多个脚本互相覆盖
  P2#6: 6个页面各自独立读CSV，无统一数据管理
  P2#8: cache_ohlcv.pkl 与回测数据不同步

用法:
  from quant_framework.data.backtest_store import BacktestStore

  store = BacktestStore(r"d:\quant_framework")
  store.save_run("tdx2_final", equity_curve, trades)
  latest = store.load_latest()
"""

from __future__ import annotations

import os, json, hashlib, time
from datetime import datetime
from typing import Any

import pandas as pd

# 最后一份回测结果的元数据文件
_META_FILE = "backtest_meta.json"
# 对比功能专用: 历史回测记录清单
_HISTORY_FILE = "run_history.json"
# 最多保留的历史回测次数
_MAX_HISTORY = 50
# 当前 session 开始时间 (用于判断是否为新 session)
_SESSION_START = datetime.now().strftime("%Y%m%d_%H%M%S")


class BacktestStore:
    """统一回测数据存储 — 所有脚本/页面共享此实例。"""

    def __init__(self, data_dir: str = r"d:\quant_framework"):
        self._dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        # Session 管理: 用 session marker 判断是否为新 session
        # 只有距离上次保存超过 4 小时才清空历史（避免 Streamlit rerun 清空）
        self._maybe_clear_stale()

    # ═══════════════════ 写入 ═══════════════════

    def save_run(
        self,
        strategy_name: str,
        equity_curve: pd.DataFrame | list[dict],
        trades: pd.DataFrame | list[dict],
        config: dict | None = None,
        sentiment: pd.DataFrame | None = None,
    ) -> str:
        """保存一次回测运行结果。

        自动生成带时间戳的文件名，避免覆盖。
        同时更新 _latest_* 符号链接/副本指向最新结果。

        Returns:
            run_id — 运行标识符 (如 "tdx2_final_20260608_143022")
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 加计数器防冲突 (同一秒内多次回测)
        run_id = f"{strategy_name}_{ts}"
        counter = 1
        while os.path.exists(os.path.join(self._dir, f"equity_{run_id}.csv")):
            run_id = f"{strategy_name}_{ts}_{counter}"
            counter += 1

        # 转换为 DataFrame
        eq_df = pd.DataFrame(equity_curve) if isinstance(equity_curve, list) else equity_curve.copy()
        tr_df = pd.DataFrame(trades) if isinstance(trades, list) else trades.copy()

        # 保存带时间戳的文件
        eq_path = os.path.join(self._dir, f"equity_{run_id}.csv")
        tr_path = os.path.join(self._dir, f"trades_{run_id}.csv")
        eq_df.to_csv(eq_path, index=False, encoding="utf-8-sig")
        tr_df.to_csv(tr_path, index=False, encoding="utf-8-sig")

        # 同时更新 _latest 副本（兼容旧路径读取的页面）
        latest_eq = os.path.join(self._dir, "equity_curve.csv")
        latest_tr = os.path.join(self._dir, "trade_log.csv")
        eq_df.to_csv(latest_eq, index=False, encoding="utf-8-sig")
        tr_df.to_csv(latest_tr, index=False, encoding="utf-8-sig")

        # 保存情绪数据（如果提供）
        if sentiment is not None:
            se_df = pd.DataFrame(sentiment) if isinstance(sentiment, list) else sentiment.copy()
            se_path = os.path.join(self._dir, f"sentiment_{run_id}.csv")
            se_df.to_csv(se_path, index=False, encoding="utf-8-sig")
            se_df.to_csv(os.path.join(self._dir, "sentiment_data.csv"), index=False, encoding="utf-8-sig")

        # 写元数据
        meta = {
            "run_id": run_id,
            "strategy": strategy_name,
            "timestamp": ts,
            "config": config or {},
            "n_trades": len(tr_df),
            "n_days": len(eq_df),
            "equity_file": os.path.basename(eq_path),
            "trades_file": os.path.basename(tr_path),
            "cache_hash": self._get_cache_hash(),
        }
        self._save_meta(meta)

        # 保留最近5次的文件 (时间戳文件)，清理旧的
        self._cleanup_old_runs(keep=5)

        # 刷新 session marker (活跃的 session 不会过期)
        self._touch_session_marker()

        # 写入 run_history.json (对比功能专用)
        history_entry = {
            "run_id": run_id,
            "strategy": strategy_name,
            "timestamp": ts,
            "config": config or {},
            "n_trades": len(tr_df),
            "n_days": len(eq_df),
            "equity_file": os.path.basename(eq_path),
            "trades_file": os.path.basename(tr_path),
            "total_return": float(eq_df["equity"].iloc[-1] / eq_df["equity"].iloc[0] - 1) if len(eq_df) > 1 and "equity" in eq_df.columns else 0.0,
        }
        self._append_to_history(history_entry)

        return run_id

    def _save_meta(self, meta: dict) -> None:
        path = os.path.join(self._dir, _META_FILE)
        # 追加到历史列表
        history = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    history = existing.get("history", [])
            except Exception:
                pass
        history.append(meta)
        # 只保留最近20条
        history = history[-20:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"latest": meta, "history": history}, f, ensure_ascii=False, indent=2)

    def _maybe_clear_stale(self) -> None:
        """Session 管理: 只有距离上次活动超过4小时才清空旧历史。

        避免 Streamlit st.rerun() 每次都重建 BacktestStore 导致历史被清空。
        同时保持进程隔离：重启应用或隔夜后自动清空。
        """
        import glob
        marker_path = os.path.join(self._dir, ".session_marker")
        now = time.time()
        is_new_session = True

        if os.path.exists(marker_path):
            try:
                with open(marker_path, "r") as f:
                    last_ts = float(f.read().strip())
                # 4小时内视为同一 session
                if now - last_ts < 4 * 3600:
                    is_new_session = False
            except Exception:
                pass

        if not is_new_session:
            return  # 同一 session，不清空

        # 新 session: 清空历史文件
        history_path = os.path.join(self._dir, _HISTORY_FILE)
        old_history = []
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    old_history = json.load(f)
            except Exception:
                pass

        if old_history:
            for entry in old_history:
                eq_file = entry.get("equity_file", "")
                tr_file = entry.get("trades_file", "")
                for fname in [eq_file, tr_file]:
                    if fname:
                        fpath = os.path.join(self._dir, fname)
                        if os.path.exists(fpath):
                            try:
                                os.remove(fpath)
                            except Exception:
                                pass

        self._save_history([])
        self._touch_session_marker()

    def _touch_session_marker(self) -> None:
        """更新 session marker 时间戳。"""
        marker_path = os.path.join(self._dir, ".session_marker")
        with open(marker_path, "w") as f:
            f.write(str(time.time()))

    def _save_history(self, history: list) -> None:
        """写入 run_history.json。"""
        path = os.path.join(self._dir, _HISTORY_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _load_history(self) -> list[dict]:
        """读取 run_history.json。"""
        path = os.path.join(self._dir, _HISTORY_FILE)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _append_to_history(self, entry: dict) -> None:
        """追加一条记录到 run_history.json，保持不超过 _MAX_HISTORY 条。"""
        history = self._load_history()
        history.append(entry)

        # 超过上限时删除最旧的记录及其文件
        while len(history) > _MAX_HISTORY:
            oldest = history.pop(0)
            for fname in [oldest.get("equity_file", ""), oldest.get("trades_file", "")]:
                if fname:
                    fpath = os.path.join(self._dir, fname)
                    if os.path.exists(fpath):
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass

        self._save_history(history)

    def _get_cache_hash(self) -> str:
        """获取 cache_ohlcv.pkl 的哈希（用于 P2#8 同步检查）。"""
        cache_path = os.path.join(self._dir, "cache_ohlcv.pkl")
        if not os.path.exists(cache_path):
            return "none"
        try:
            with open(cache_path, "rb") as f:
                # 只读前1MB做快速哈希
                data = f.read(1_048_576)
            return hashlib.md5(data).hexdigest()[:12]
        except Exception:
            return "error"

    def _cleanup_old_runs(self, keep: int = 5) -> None:
        """清理旧的回测文件，保留最近 N 次。"""
        import glob
        for prefix in ["equity_", "trades_", "sentiment_"]:
            files = sorted(glob.glob(os.path.join(self._dir, f"{prefix}*_*.csv")), reverse=True)
            for f in files[keep:]:
                try:
                    os.remove(f)
                except Exception:
                    pass

    # ═══════════════════ 读取 ═══════════════════

    def load_latest(self) -> dict:
        """加载最新回测结果。

        Returns:
            {"trades": DataFrame, "equity": DataFrame, "sentiment": DataFrame,
             "meta": dict, "available": bool, "cache_synced": bool}
        """
        result: dict[str, Any] = {
            "trades": pd.DataFrame(),
            "equity": pd.DataFrame(),
            "sentiment": pd.DataFrame(),
            "meta": {},
            "available": False,
            "cache_synced": False,
        }

        trade_path = os.path.join(self._dir, "trade_log.csv")
        equity_path = os.path.join(self._dir, "equity_curve.csv")
        sentiment_path = os.path.join(self._dir, "sentiment_data.csv")

        try:
            if os.path.exists(equity_path) and os.path.getsize(equity_path) > 10:
                eq = pd.read_csv(equity_path)
                if not eq.empty and "date" in eq.columns:
                    eq["date"] = pd.to_datetime(eq["date"])
                    eq.set_index("date", inplace=True)
                result["equity"] = eq
                result["available"] = True
        except Exception:
            pass

        try:
            if os.path.exists(trade_path) and os.path.getsize(trade_path) > 10:
                tr = pd.read_csv(trade_path)
                if not tr.empty and "buy_date" in tr.columns:
                    tr["buy_date"] = pd.to_datetime(tr["buy_date"])
                result["trades"] = tr
        except Exception:
            pass

        try:
            if os.path.exists(sentiment_path) and os.path.getsize(sentiment_path) > 10:
                result["sentiment"] = pd.read_csv(sentiment_path)
        except Exception:
            pass

        # 加载元数据
        meta_path = os.path.join(self._dir, _META_FILE)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                result["meta"] = meta_data.get("latest", {})

                # P2#8: 检查 cache 同步状态
                cached_hash = self._get_cache_hash()
                stored_hash = result["meta"].get("cache_hash", "")
                result["cache_synced"] = (cached_hash == stored_hash and cached_hash != "none")
            except Exception:
                pass

        return result

    def load_run(self, run_id: str) -> dict | None:
        """加载指定的历史回测结果。"""
        eq_path = os.path.join(self._dir, f"equity_{run_id}.csv")
        tr_path = os.path.join(self._dir, f"trades_{run_id}.csv")

        # 兼容: 先试时间戳文件名，再试旧格式
        if not os.path.exists(eq_path):
            # 尝试从 history 查找
            for entry in self.list_runs():
                if entry.get("run_id") == run_id:
                    eq_file = entry.get("equity_file", "")
                    tr_file = entry.get("trades_file", "")
                    eq_path = os.path.join(self._dir, eq_file) if eq_file else ""
                    tr_path = os.path.join(self._dir, tr_file) if tr_file else ""
                    break

        if not eq_path or not os.path.exists(eq_path):
            return None
        if os.path.getsize(eq_path) < 10:
            return None

        try:
            eq = pd.read_csv(eq_path)
            if eq.empty:
                return None
            if "date" in eq.columns:
                eq["date"] = pd.to_datetime(eq["date"])
                eq.set_index("date", inplace=True)

            tr = pd.DataFrame()
            if tr_path and os.path.exists(tr_path) and os.path.getsize(tr_path) > 10:
                tr = pd.read_csv(tr_path)
                if not tr.empty and "buy_date" in tr.columns:
                    tr["buy_date"] = pd.to_datetime(tr["buy_date"])
            return {"equity": eq, "trades": tr, "run_id": run_id}
        except Exception:
            return None

    def list_runs(self) -> list[dict]:
        """列出当前 session 内所有历史回测运行 (从 run_history.json 读取)。"""
        return self._load_history()

    def is_cache_synced(self) -> bool:
        """检查回测数据是否与 factor cache 同步。"""
        result = self.load_latest()
        return result["cache_synced"]

    # ═══════════════════ 指标计算（统一） ═══════════════════
    # P3#12: generate_report 和 visual_dashboard 共享同一套指标计算

    @staticmethod
    def compute_metrics(equity: pd.DataFrame, trades: pd.DataFrame,
                        initial_capital: float = 1_000_000.0) -> dict:
        """统一回测指标计算。

        所有页面 (quant_dashboard, generate_report, visual_dashboard)
        都调用此方法，确保指标一致。
        """
        metrics: dict[str, Any] = {
            "total_return": 0.0, "annual_return": 0.0, "sharpe": 0.0,
            "max_drawdown": 0.0, "calmar": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "total_pnl": 0.0, "n_trades": len(trades),
            "n_days": len(equity), "best_trade": 0.0, "worst_trade": 0.0,
        }

        eq_series = pd.Series(dtype=float)
        if "equity" in equity.columns:
            eq_series = equity["equity"]
        elif not equity.empty:
            eq_series = equity.iloc[:, 0]

        if eq_series.empty or len(eq_series) < 2:
            return metrics

        # 总收益
        if eq_series.iloc[0] > 0:
            metrics["total_return"] = float(eq_series.iloc[-1] / eq_series.iloc[0] - 1)

        # 年化收益
        days = (eq_series.index[-1] - eq_series.index[0]).days if hasattr(eq_series.index[-1], 'day') else len(eq_series)
        years = max(days / 365.25, 0.1)
        metrics["annual_return"] = float((1 + metrics["total_return"]) ** (1 / years) - 1)

        # 日收益率
        daily_ret = eq_series.pct_change().dropna()
        if len(daily_ret) > 1 and daily_ret.std() > 0:
            metrics["sharpe"] = float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5))

        # 最大回撤
        peak = eq_series.expanding().max()
        dd = (eq_series - peak) / peak
        metrics["max_drawdown"] = float(dd.min())

        # Calmar
        if abs(metrics["max_drawdown"]) > 0.001:
            metrics["calmar"] = float(metrics["annual_return"] / abs(metrics["max_drawdown"]))

        # 胜率
        if len(daily_ret) > 0:
            metrics["win_rate"] = float((daily_ret > 0).mean())

        # 交易统计
        if not trades.empty and "pnl" in trades.columns:
            pnls = trades["pnl"].dropna()
            wins = pnls[pnls > 0]
            losses = pnls[pnls < 0]
            metrics["total_pnl"] = float(pnls.sum())
            metrics["n_trades"] = len(trades)
            if len(wins) > 0:
                metrics["best_trade"] = float(wins.max())
            if len(losses) > 0:
                metrics["worst_trade"] = float(losses.min())
            if len(trades) > 0:
                metrics["win_rate"] = float(len(wins) / len(trades))
            if losses.sum() != 0:
                metrics["profit_factor"] = float(abs(wins.sum() / losses.sum()) if len(losses) > 0
                                                  else (999.0 if len(wins) > 0 else 0.0))

        return metrics


# ═══════════════════ 全局单例 ═══════════════════

_default_store: BacktestStore | None = None


def get_store(data_dir: str = r"d:\quant_framework") -> BacktestStore:
    """获取全局 BacktestStore 单例。"""
    global _default_store
    if _default_store is None:
        _default_store = BacktestStore(data_dir)
    return _default_store
