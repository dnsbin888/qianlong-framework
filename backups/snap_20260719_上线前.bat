@echo off
chcp 65001 >nul
echo ===== 潜龙系统快照: snap_20260719_上线前 =====
set "DST=D:\quant_framework\backups\snap_20260719_上线前"
if not exist "%DST%" mkdir "%DST%"

echo [1/4] QMT策略+桥接...
copy /Y "D:\quant_framework\qmt_strategies\qmt_full_strategy.py" "%DST%\"
copy /Y "D:\quant_framework\qmt_quote_bridge.py" "%DST%\"
copy /Y "D:\quant_framework\src\quant_framework\execution\brokers\qmt_broker.py" "%DST%\"

echo [2/4] 回测+模型...
copy /Y "D:\quant_framework\paper_engine.py" "%DST%\"
copy /Y "D:\quant_framework\optuna_tune_lgbm.py" "%DST%\"
copy /Y "D:\quant_framework\optuna_tune_xgb.py" "%DST%\"
copy /Y "D:\quant_framework\optuna_tune_cb.py" "%DST%\"
copy /Y "D:\quant_framework\purged_cv.py" "%DST%\"
copy /Y "D:\quant_framework\baostock_sync.py" "%DST%\"
copy /Y "D:\quant_framework\xgb_factor_weight.py" "%DST%\"

echo [3/4] 信号+风控+前端...
copy /Y "D:\quant_web\generate_signal_table.py" "%DST%\"
copy /Y "D:\quant_web\data_loader.py" "%DST%\"
copy /Y "D:\quant_web\templates\ml_signals.html" "%DST%\"
copy /Y "D:\quant_web\static\js\terminal_v2.js" "%DST%\"
copy /Y "D:\quant_framework\signal_config.json" "%DST%\"
copy /Y "D:\quant_framework\trade_config_master.json" "%DST%\"
copy /Y "D:\quant_framework\src\quant_framework\execution\rules\daily_limits.py" "%DST%\"
copy /Y "D:\quant_framework\src\quant_framework\execution\rules\engine.py" "%DST%\"
copy /Y "D:\quant_framework\signals\daban\realtime.py" "%DST%\"
copy /Y "D:\quant_framework\signals\daban\weights.py" "%DST%\"

echo [4/4] 蓝图+分析...
copy /Y "D:\quant_framework\治理方案分析_最优组合_vFINAL.md" "%DST%\"
copy /Y "D:\quant_framework\三方案终审_vFINAL.md" "%DST%\"
copy /Y "D:\quant_framework\方案终审_排序vs打分_vFINAL.md" "%DST%\"

echo.
echo ===== 备份完成 =====
dir "%DST%" /B
echo.
pause
