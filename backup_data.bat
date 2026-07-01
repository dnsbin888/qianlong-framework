@echo off
:: 潜龙数据备份 v1.0 — 14文件全覆盖
:: 用法: 潜龙重启.bat 开头调用, 或手动运行
set B=D:\quant_framework\backups\daily_%date:~0,4%%date:~5,2%%date:~8,2%
mkdir %B% 2>nul

echo === 潜龙数据备份 %date% %time% ===

:: IP资产 (用户自定义)
copy D:\quant_framework\user_customizations\user_factors.json     %B%\ >nul && echo   [IP] user_factors.json
copy D:\quant_framework\user_customizations\user_strategies.json  %B%\ >nul && echo   [IP] user_strategies.json
copy D:\quant_framework\user_customizations\user_tdx_formulas.json %B%\ >nul && echo   [IP] user_tdx_formulas.json

:: 交易数据
copy D:\quant_framework\paper_account.json          %B%\ >nul && echo   [DATA] paper_account.json
copy D:\quant_framework\trade_log.csv               %B%\ >nul && echo   [DATA] trade_log.csv
copy D:\quant_framework\equity_log.json             %B%\ >nul && echo   [DATA] equity_log.json
copy D:\quant_framework\live_equity_log.json        %B%\ >nul && echo   [DATA] live_equity_log.json
copy D:\quant_framework\live_positions_track.json   %B%\ >nul && echo   [DATA] live_positions_track.json

:: 配置
copy D:\quant_framework\live_trader_config.json     %B%\ >nul && echo   [CFG] live_trader_config.json
copy D:\quant_framework\blacklist.json              %B%\ >nul && echo   [CFG] blacklist.json
copy D:\quant_framework\factor_registry.json        %B%\ >nul && echo   [CFG] factor_registry.json
copy D:\quant_framework\config\default.yaml         %B%\ >nul && echo   [CFG] default.yaml

:: 参考数据
copy D:\quant_web\stock_names_full.csv              %B%\ >nul && echo   [REF] stock_names_full.csv

:: 轮转: 删除30天前的备份
forfiles /p D:\quant_framework\backups /d -30 /c "cmd /c rmdir /s /q @path" 2>nul

echo === 备份完成: %B% ===
