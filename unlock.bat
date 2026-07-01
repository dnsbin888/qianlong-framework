@echo off
:: 解锁前先快照 (坏了可恢复)
call D:\quant_framework\snapshot.bat
echo.
echo === UNLOCK WARNING ===
echo Any program can now modify critical data files.
echo Run lock.bat after your changes.
echo.
attrib -R D:\quant_framework\paper_account.json
attrib -R D:\quant_framework\trade_log.csv
attrib -R D:\quant_framework\equity_log.json
attrib -R D:\quant_framework\live_equity_log.json
attrib -R D:\quant_framework\live_positions_track.json
attrib -R D:\quant_framework\live_trader_config.json
attrib -R D:\quant_framework\blacklist.json
attrib -R D:\quant_framework\factor_registry.json
attrib -R D:\quant_framework\user_customizations\user_factors.json
attrib -R D:\quant_framework\user_customizations\user_strategies.json
attrib -R D:\quant_framework\user_customizations\user_tdx_formulas.json
attrib -R D:\quant_framework\config\default.yaml
attrib -R D:\quant_web\stock_names_full.csv
echo UNLOCKED: 14 files (snapshot saved)
