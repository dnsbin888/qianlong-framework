@echo off
set ERR=0
if not exist D:\quant_framework\paper_account.json    set /a ERR+=1
if not exist D:\quant_framework\trade_log.csv         set /a ERR+=1
if not exist D:\quant_framework\live_trader_config.json set /a ERR+=1
if not exist D:\quant_framework\factor_registry.json  set /a ERR+=1
if not exist D:\quant_framework\blacklist.json        set /a ERR+=1
if not exist D:\quant_web\stock_names_full.csv        set /a ERR+=1
if %ERR% gtr 0 (
  echo FAIL: %ERR% files missing
  exit /b 1
)
echo PASS: all files present
exit /b 0
