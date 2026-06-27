"""策略A/B测试框架 — 多组参数并行，收盘自动对比

用法:
    from ab_test import ABTestRunner
    runner = ABTestRunner()
    runner.start()  # 创建A/B/C三组并行运行
    status = runner.get_status()  # 查看各组表现
"""

import json, os, copy
from datetime import datetime

AB_FILE = r"D:\quant_framework\ab_test_state.json"


def _load_base_config():
    cfg_file = r"D:\quant_framework\live_trader_config.json"
    if os.path.exists(cfg_file):
        return json.load(open(cfg_file, "r", encoding="utf-8"))
    return {}


class ABTestRunner:
    """A/B测试管理器"""

    def __init__(self):
        self.running = False
        self.start_time = None
        self.groups = {}

    def start(self):
        """启动A/B测试 — 创建3组参数"""
        base = _load_base_config()
        important_keys = [
            "signal_min_strength", "tp1_profit_pct", "tp1_trail_pct", "tp1_stop_loss",
            "tp2_profit_pct", "tp2_trail_pct", "tp2_stop_loss",
            "max_positions", "position_mode",
        ]
        base_params = {k: base.get(k) for k in important_keys if k in base}

        groups = {
            "A": {"name": "基准组(当前参数)", **base_params},
            "B": {"name": "激进组", **base_params,
                  "signal_min_strength": 2,
                  "tp1_profit_pct": round(base.get("tp1_profit_pct", 0.05) * 0.8, 2),
                  "tp1_stop_loss": round(base.get("tp1_stop_loss", -0.03) * 1.3, 2)},
            "C": {"name": "保守组", **base_params,
                  "signal_min_strength": 4,
                  "tp1_profit_pct": round(base.get("tp1_profit_pct", 0.05) * 1.2, 2),
                  "tp1_stop_loss": round(base.get("tp1_stop_loss", -0.03) * 0.7, 2)},
        }

        self.groups = groups
        self.running = True
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        print(f"[ABTest] 已启动3组并行测试: A(基准) B(激进) C(保守)")

        # 创建独立PaperAccount并注入数据（各组独立100万资金，互不干扰）
        from paper_engine import paper as _main_paper
        self.accounts = {}
        for gid, gcfg in groups.items():
            acc = PaperAccount()
            acc.auto_enabled = True
            acc.cash = 1_000_000  # H2: 独立初始资金
            acc.set_risk_data(factor_cache=_main_paper.factor_cache, stock_data=_main_paper.stock_data)
            self.accounts[gid] = acc
            print(f"[ABTest] {gid}组已就绪: {gcfg['name']} (独立100万)")

    def get_status(self) -> dict:
        """获取各组当前状态"""
        if not self.running:
            return {"running": False, "groups": {}}

        status = {"running": True, "start_time": self.start_time, "groups": {}}
        for gid, acc in self.accounts.items():
            try:
                gs = acc.get_status()
                status["groups"][gid] = {
                    "name": self.groups[gid]["name"],
                    "cash": round(gs.get("cash", 0), 2),
                    "total_equity": round(gs.get("total_equity", 0), 2),
                    "total_pnl": round(gs.get("total_pnl", 0), 2),
                    "total_return": gs.get("total_return", 0),
                    "position_count": len(gs.get("positions", [])),
                    "win_rate": gs.get("win_rate", 0),
                    "sharpe": gs.get("sharpe", 0),
                }
            except Exception as e:
                status["groups"][gid] = {"error": str(e)}
        return status

    def process_signals(self, signals):
        """处理信号 — 各组独立执行"""
        if not self.running:
            return {}
        results = {}
        for gid, acc in self.accounts.items():
            try:
                actions = acc.auto_trade_check(signals)
                results[gid] = len(actions)
            except Exception:
                results[gid] = 0
        return results

    def stop(self):
        self.running = False
        self._save()
        print("[ABTest] A/B测试已停止")

    def _save(self):
        try:
            json.dump({"running": self.running, "start_time": self.start_time,
                       "groups": {g: {"name": c["name"]} for g, c in self.groups.items()}},
                      open(AB_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except: pass

    def summary_to_text(self) -> str:
        status = self.get_status()
        lines = ["📊 A/B测试对比"]
        for gid, gs in status["groups"].items():
            lines.append(f"  {gid}组({gs['name']}): {gs['total_return']:+.2f}% 胜率{gs.get('win_rate',0):.0f}% Sharpe{gs.get('sharpe',0):.2f}")
        return "\n".join(lines)


runner = ABTestRunner()
