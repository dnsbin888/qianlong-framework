@echo off
chcp 65001 >nul
echo ===== 潜龙系统快照: snap_20260719_最终版 =====
set "DST=D:\quant_framework\backups\snap_20260719_最终版"
if not exist "%DST%" mkdir "%DST%"

echo 核心引擎...
copy /Y "D:\quant_framework\paper_engine.py" "%DST%\"
copy /Y "D:\quant_web\generate_signal_table.py" "%DST%\"
copy /Y "D:\quant_web\data_loader.py" "%DST%\"
copy /Y "D:\quant_framework\live_trader.py" "%DST%\"
copy /Y "D:\quant_web\app.py" "%DST%\"

echo 模型...
copy /Y "D:\quant_framework\optuna_tune_xgb.py" "%DST%\"
copy /Y "D:\quant_framework\optuna_tune_lgbm.py" "%DST%\"
copy /Y "D:\quant_framework\xgb_factor_weight.py" "%DST%\"
copy /Y "D:\quant_framework\lgbm_strategy.py" "%DST%\"
copy /Y "D:\quant_framework\purged_cv.py" "%DST%\"
copy /Y "D:\quant_framework\baostock_sync.py" "%DST%\"

echo 风控+前端...
copy /Y "D:\quant_framework\src\quant_framework\execution\rules\daily_limits.py" "%DST%\"
copy /Y "D:\quant_framework\src\quant_framework\execution\rules\engine.py" "%DST%\"
copy /Y "D:\quant_web\templates\ml_signals.html" "%DST%\"
copy /Y "D:\quant_framework\reversal_strategy.py" "%DST%\"
copy /Y "D:\quant_framework\signals\reversal\realtime.py" "%DST%\"
copy /Y "D:\quant_framework\signals\daban\realtime.py" "%DST%\"
copy /Y "D:\quant_framework\signals\daban\weights.py" "%DST%\"

echo 配置+蓝图...
copy /Y "D:\quant_framework\signal_config.json" "%DST%\"
copy /Y "D:\quant_framework\trade_config_master.json" "%DST%\"
copy /Y "D:\quant_framework\系统防误改方案.md" "%DST%\"
copy /Y "D:\quant_framework\development-blueprint-20260719.md" "%DST%"

echo 模型文件...
copy /Y "D:\quant_framework\lgbm_model_stock.pkl" "%DST%\"
copy /Y "D:\quant_framework\xgb_model.json" "%DST%\"
copy /Y "D:\quant_framework\ridge_model.pkl" "%DST%\"

echo.
echo ===== 快照完成 =====
dir "%DST%" /B /S | find /c ".py"
echo 个文件
pause
