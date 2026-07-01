@echo off
set SNAP=D:\quant_framework\backups\snap_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
mkdir %SNAP% 2>nul
copy D:\quant_framework\paper_account.json %SNAP%\ >nul
copy D:\quant_framework\trade_log.csv %SNAP%\ >nul
copy D:\quant_framework\equity_log.json %SNAP%\ >nul
copy D:\quant_framework\live_equity_log.json %SNAP%\ >nul
copy D:\quant_framework\live_positions_track.json %SNAP%\ >nul
copy D:\quant_framework\live_trader_config.json %SNAP%\ >nul
copy D:\quant_framework\blacklist.json %SNAP%\ >nul
copy D:\quant_framework\factor_registry.json %SNAP%\ >nul
copy D:\quant_framework\user_customizations\user_factors.json %SNAP%\ >nul
copy D:\quant_framework\user_customizations\user_strategies.json %SNAP%\ >nul
copy D:\quant_framework\user_customizations\user_tdx_formulas.json %SNAP%\ >nul
copy D:\quant_framework\config\default.yaml %SNAP%\ >nul
copy D:\quant_web\stock_names_full.csv %SNAP%\ >nul
echo Snapshot: %SNAP%
