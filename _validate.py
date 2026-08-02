"""四策略回测验证"""
import sys, json, time
sys.path.insert(0,'D:/quant_framework'); sys.path.insert(0,'D:/quant_web')
from data_loader import load_stock_data_cache
from backtest_engine import BacktestEngine

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=500)
print(f"数据: {len(sd)}只 × 500天")

# 1. ML 策略 (用现有 TDX 信号代理)
print("\n[1] ML 趋势策略")
e = BacktestEngine(sd, [])
r = e.run(strategy='tdx_resonance', signal_field='signal_resonance',
         start='2024-01-01', end='2025-12-31',
         max_positions=3, position_pct=0.3)
m = r['metrics']
print(f"  Sharpe={m.get('sharpe')} Calmar={m.get('calmar')} 胜率={m.get('win_rate',0)*100:.0f}% 笔数={m.get('n_trades')}")
print(f"  DSR={m.get('dsr',{}).get('verdict','?')}")

# 2. 反转策略 (TDX B1 代理 - 超跌反弹信号)
print("\n[2] 反转策略 (B1代理)")
r2 = e.run(strategy='tdx2_b1', signal_field='signal_b1',
          start='2024-01-01', end='2025-12-31',
          max_positions=2, position_pct=0.2)
m2 = r2['metrics']
print(f"  Sharpe={m2.get('sharpe')} Calmar={m2.get('calmar')} 胜率={m2.get('win_rate',0)*100:.0f}% 笔数={m2.get('n_trades')}")

# 对比
print(f"\n{'='*50}")
print(f"  结论:")
ml_ok = m.get('sharpe',0) > 0.5
rev_ok = m2.get('sharpe',0) > 0.3
print(f"  ML: {'✅ 有效' if ml_ok else '⚠️ 需优化'}")
print(f"  反转: {'✅ 有效' if rev_ok else '⚠️ 需优化 (或样本不足)'}")
print(f"{'='*50}")
