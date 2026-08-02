@echo off
chcp 65001 >nul
echo ===== 潜龙系统快照: snap_20260718_基建前 =====
set "DST=D:\quant_framework\backups\snap_20260718_基建前"

echo.
echo [1/5] QMT策略+桥接...
copy /Y "D:\quant_framework\qmt_strategies\qmt_full_strategy.py" "%DST%\qmt_full_strategy.py"
copy /Y "D:\quant_framework\src\quant_framework\execution\brokers\qmt_broker.py" "%DST%\qmt_broker.py"
copy /Y "D:\quant_framework\tdx_pool_watcher.py" "%DST%\tdx_pool_watcher.py"

echo [2/5] 回测引擎...
copy /Y "D:\quant_framework\paper_engine.py" "%DST%\paper_engine.py"
copy /Y "D:\quant_framework\paper_engine_v2.py" "%DST%\paper_engine_v2.py"
copy /Y "D:\quant_framework\backtest_engine.py" "%DST%\backtest_engine.py"
copy /Y "D:\quant_framework\_fast_backtest.py" "%DST%\_fast_backtest.py"

echo [3/5] IC/因子...
copy /Y "D:\quant_framework\full_market_ic.py" "%DST%\full_market_ic.py"
copy /Y "D:\quant_framework\factor_ic.py" "%DST%\factor_ic.py"
copy /Y "D:\quant_framework\xgb_factor_weight.py" "%DST%\xgb_factor_weight.py"
copy /Y "D:\quant_framework\strategy_validator.py" "%DST%\strategy_validator.py"
copy /Y "D:\quant_framework\strategy_metrics.py" "%DST%\strategy_metrics.py"

echo [4/5] 数据+模型...
copy /Y "D:\quant_framework\baostock_sync.py" "%DST%\baostock_sync.py"
copy /Y "D:\quant_framework\lgbm_strategy.py" "%DST%\lgbm_strategy.py"

echo [5/5] Web端...
copy /Y "D:\quant_web\generate_signal_table.py" "%DST%\generate_signal_table.py"

echo.
echo ===== 备份完成 =====
dir "%DST%" /B
echo.
pause
