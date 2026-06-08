"""
方案一 vs 方案二 回测对比 — 用数据说话

方案一（稳健型）：+5%回1.8%卖1/4 → +7%回2%卖1/4 → +12%回3%卖1/4
方案二（激进型）：+7%回1.8%卖1/2 → +10%回2%卖1/4 → +12%回3%卖1/4
"""
import subprocess, sys, json, os, re
from datetime import datetime

PY = sys.executable
SCRIPT = r"d:\quant_framework\run_backtest_fast.py"
COMMON = ["--start", "2023-01-01", "--end", "2025-06-01",
           "--max-stocks", "100", "--max-pos", "3",
           "--hold-days", "3", "--strategy", "tdx_resonance"]

# 方案一：稳健型
SCHEME_1 = {
    "trail1-profit": 0.05, "trail1-drop": 0.018, "sell-ratio-1": 0.25,
    "trail2-profit": 0.07, "trail2-drop": 0.02,  "sell-ratio-2": 0.25,
    "trail3-profit": 0.12, "trail3-drop": 0.03,  "sell-ratio-3": 0.25,
    "stop-loss": -0.05,
    "take-profit": 0.30,  # 大到几乎不触发
    "position-pct": 0.30,
    "limit-up-enabled": 1, "limit-up-open-drop": 0.03,
}

# 方案二：激进型
SCHEME_2 = {
    "trail1-profit": 0.07, "trail1-drop": 0.018, "sell-ratio-1": 0.50,
    "trail2-profit": 0.10, "trail2-drop": 0.02,  "sell-ratio-2": 0.25,
    "trail3-profit": 0.12, "trail3-drop": 0.03,  "sell-ratio-3": 0.25,
    "stop-loss": -0.05,
    "take-profit": 0.30,
    "position-pct": 0.30,
    "limit-up-enabled": 1, "limit-up-open-drop": 0.03,
}

def run_scheme(name, params):
    cmd = [PY, SCRIPT] + COMMON
    for k, v in params.items():
        cmd.extend([f"--{k}", str(v)])
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    print(f"  CMD: {' '.join(cmd[-20:])}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    output = result.stdout
    if result.stderr:
        output += "\nSTDERR:\n" + result.stderr[-500:]
    print(output[-1000:])

    # 解析关键指标
    metrics = {}
    for line in output.split("\n"):
        for key, pattern in [
            ("total_return", r"总收益.*?([+-]?\d+\.?\d*%)"),
            ("sharpe", r"夏普比率.*?([+-]?\d+\.?\d*)"),
            ("max_drawdown", r"最大回撤.*?([+-]?\d+\.?\d*%)"),
            ("win_rate", r"胜率.*?([+-]?\d+\.?\d*%)"),
            ("n_trades", r"总交易.*?(\d+)"),
            ("annual_return", r"年化收益.*?([+-]?\d+\.?\d*%)"),
            ("profit_factor", r"盈亏比.*?([+-]?\d+\.?\d*)"),
        ]:
            m = re.search(pattern, line)
            if m:
                metrics[key] = m.group(1)
    return metrics, output

# 跑方案一
m1, out1 = run_scheme("方案一（稳健型：+5%→+7%→+12% 各卖1/4）", SCHEME_1)

# 跑方案二
m2, out2 = run_scheme("方案二（激进型：+7%卖1/2→+10%→+12% 各卖1/4）", SCHEME_2)

# 汇总对比
print(f"\n{'='*65}")
print(f"  📊 方案对比总结")
print(f"{'='*65}")
print(f"  {'指标':<20} {'方案一 (稳健)':>15} {'方案二 (激进)':>15}")
print(f"  {'-'*20} {'-'*15} {'-'*15}")
for key, label in [
    ("total_return", "总收益"), ("annual_return", "年化收益"),
    ("sharpe", "夏普比率"), ("max_drawdown", "最大回撤"),
    ("win_rate", "胜率"), ("n_trades", "交易次数"),
    ("profit_factor", "盈亏比"),
]:
    v1 = m1.get(key, "?")
    v2 = m2.get(key, "?")
    print(f"  {label:<20} {v1:>15} {v2:>15}")

print(f"\n  方案一逻辑: 盈≥5%回1.8%卖1/4 → 盈≥7%回2%卖1/4 → 盈≥12%回3%卖1/4")
print(f"  方案二逻辑: 盈≥7%回1.8%卖1/2 → 盈≥10%回2%卖1/4 → 盈≥12%回3%卖1/4")
print(f"  已保存: d:\\quant_framework\\equity_curve.csv / trade_log.csv")
