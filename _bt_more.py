"""跑一次大规模回测，目标100+笔交易，验证策略可用性。"""
import subprocess, sys, json

config = {
    "strategy": "tdx_resonance", "start": "2022-01-01", "end": "2025-06-30",
    "max-pos": 5, "position-pct": 0.3, "stop-loss": -0.05, "take-profit": 0.30,
    "hold-days": 5,
    "trail1-profit": 0.05, "trail1-drop": 0.018, "sell-ratio-1": 0.25,
    "trail2-profit": 0.07, "trail2-drop": 0.02, "sell-ratio-2": 0.25,
    "trail3-profit": 0.12, "trail3-drop": 0.03, "sell-ratio-3": 0.25,
    "limit-up-enabled": 1, "limit-up-open-drop": 0.03,
}
cmd = [sys.executable, r"d:\quant_framework\run_backtest_fast.py",
       "--max-stocks", "500", "--config", json.dumps(config)]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
for line in r.stdout.split("\n"):
    if any(w in line for w in ["总交易","胜率","总收益","夏普","最大回撤","盈亏比","退出方式","止损","止盈","追踪止盈","正常到期","Done"]):
        print(line.strip())
