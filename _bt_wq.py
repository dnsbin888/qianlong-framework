"""WQ101+自研策略 回测"""
import sys;sys.path.insert(0,r'D:\quant_web');sys.path.insert(0,r'D:\quant_framework')
from ql_backtest import run
r=run('WQ101+自研策略V1')
print(r)
