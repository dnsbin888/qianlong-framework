@echo off
set B=D:\quant_framework\backups\daily_%date:~0,4%%date:~5,2%%date:~8,2%
mkdir %B% 2>nul
copy D:\quant_framework\user_customizations\user_factors.json %B%\ >nul
copy D:\quant_framework\user_customizations\user_strategies.json %B%\ >nul
copy D:\quant_framework\user_customizations\user_tdx_formulas.json %B%\ >nul
copy D:\quant_framework\paper_account.json %B%\ >nul
copy D:\quant_framework\trade_log.csv %B%\ >nul
copy D:\quant_framework\equity_log.json %B%\ >nul
copy D:\quant_framework\live_equity_log.json %B%\ >nul
copy D:\quant_framework\live_positions_track.json %B%\ >nul
copy D:\quant_framework\live_trader_config.json %B%\ >nul
copy D:\quant_framework\trade_config_master.json %B%\ >nul
copy D:\quant_framework\blacklist.json %B%\ >nul
copy D:\quant_framework\factor_registry.json %B%\ >nul
copy D:\quant_framework\config\default.yaml %B%\ >nul
copy D:\quant_web\stock_names_full.csv %B%\ >nul
forfiles /p D:\quant_framework\backups /d -30 /c "cmd /c rmdir /s /q @path" 2>nul
echo Backup OK: %B%
exit /b 0
