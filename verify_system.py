"""系统完整性验证脚本"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from pathlib import Path
import pandas as pd
import numpy as np
from optimizer import grid_search, run_backtest
from portfolio_backtest import portfolio_backtest

OK = 0
FAIL = 0

def check(name, condition, detail=""):
    global OK, FAIL
    if condition:
        print(f"  [PASS] {name}")
        OK += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        FAIL += 1

print("=" * 60)
print("  Quant Platform — 系统完整性验证")
print("=" * 60)

# 1. Data
print("\n[1] 数据层")
data_dir = Path("data/market")
stock_dirs = [d for d in data_dir.iterdir() if d.is_dir() and len(d.name) == 6]
csv_files = [d for d in stock_dirs if (d / "1d.csv").exists()]
check("数据目录存在", data_dir.exists())
check("股票数据目录数", len(stock_dirs) > 0, f"count={len(stock_dirs)}")
check("有效CSV文件", len(csv_files) > 0, f"count={len(csv_files)}")

# Check one file
if csv_files:
    df = pd.read_csv(csv_files[0] / "1d.csv", index_col=0, parse_dates=True)
    check("数据可读取", len(df) > 100, f"{csv_files[0].name}: {len(df)} rows")
    check("包含OHLCV列", all(c in df.columns for c in ["open","high","low","close","volume"]))

# 2. Backtest Engine
print("\n[2] 回测引擎")
df_600000 = pd.read_csv(data_dir / "600000" / "1d.csv", index_col=0, parse_dates=True)
result = run_backtest(df_600000, "macd_cross", {"fast": 8, "slow": 20, "signal": 6})
check("回测可执行", result.total_trades > 0, f"{result.total_trades} trades")
check("返回合理收益", -100 < result.total_return < 1000, f"{result.total_return:+.2f}%")
check("Sharpe计算", -5 < result.sharpe < 5, f"{result.sharpe:.3f}")
check("最大回撤计算", 0 <= result.max_drawdown <= 100, f"{result.max_drawdown:.2f}%")

# 3. Grid Search
print("\n[3] 参数优化")
results = grid_search(df_600000, "macd_cross")
check("网格搜索可执行", len(results) > 10, f"{len(results)} combinations")
best = sorted(results, key=lambda r: r.sharpe, reverse=True)[0]
check("最佳参数存在", best.sharpe > -1, f"sharpe={best.sharpe:.3f}")

# 4. Portfolio Backtest
print("\n[4] 组合回测")
test_stocks = [d.name for d in csv_files[:5]]
pf_result = portfolio_backtest("macd_cross", test_stocks, optimize=False)
check("组合回测可执行", pf_result.num_stocks > 0, f"{pf_result.num_stocks} stocks")
check("组合收益计算", isinstance(pf_result.total_return, float))

# 5. Config
print("\n[5] 配置系统")
from quant_framework.config import FrameworkConfig, load_config
cfg_path = Path("config/default.yaml")
if cfg_path.exists():
    cfg = load_config(cfg_path)
    check("配置加载", cfg.framework.mode in ("paper", "live", "backtest"))
    check("风险配置", cfg.risk.max_drawdown_pct > 0)
else:
    print("  [SKIP] 配置文件不存在，运行配置向导")

# 6. Provider Registry
print("\n[6] 数据源注册")
from quant_framework.data.providers import DataProviderRegistry
providers = DataProviderRegistry.list_providers()
check("Provider自注册", len(providers) >= 4, f"{providers}")

# 7. Risk Engine
print("\n[7] 风控引擎")
from quant_framework.risk.engine import RiskEngine
from quant_framework.risk.rules import MaxDrawdownRule, PositionLimitRule, DailyLossLimitRule
re = RiskEngine()
re.add_global_rule(MaxDrawdownRule(0.20))
re.add_global_rule(PositionLimitRule(0.30))
re.add_global_rule(DailyLossLimitRule(50000))
check("风控引擎创建", re.total_rule_count == 3, f"{re.total_rule_count} rules")

# 8. Strategy Registry
print("\n[8] 策略市场")
from quant_framework.strategy.registry import StrategyRegistry
sr = StrategyRegistry.instance()
check("策略注册", sr.count >= 8, f"{sr.count} strategies")

# Summary
print("\n" + "=" * 60)
total = OK + FAIL
print(f"  结果: {OK}/{total} 通过, {FAIL} 失败")
if FAIL == 0:
    print("  系统状态: 全部正常，可投入使用")
else:
    print(f"  系统状态: {FAIL} 项需要修复")
print("=" * 60)
