"""策略验证器 v2.0 — 薄壳入口, 调统一引擎 generate_signal_table.run_backtest()
保留独立文件是为了:
  1. 向后兼容 (已有调用)
  2. 独立 argparse (不影响 generate_signal_table 生产默认值)
"""
import sys
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

from generate_signal_table import run_backtest

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--strategy', help='只跑指定策略')
    p.add_argument('--start', default=None, help='默认最近2年(铁律: 旧数据噪声>信号)')
    p.add_argument('--end', default=None, help='默认今天')
    args = p.parse_args()

    print("=" * 60)
    print("  策略验证器 v2.0 → generate_signal_table.run_backtest()")
    print("=" * 60)
    run_backtest(start=args.start, end=args.end, strategy_filter=args.strategy)
