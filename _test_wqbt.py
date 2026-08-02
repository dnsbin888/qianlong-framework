import sys;sys.path.insert(0,r'D:\quant_web');sys.path.insert(0,r'D:\quant_framework')
from strategy_builder import run_backtest
r=run_backtest('WQ101+自研策略V1',days=60,walk_forward=False)
print(r)
