@echo off
:: 潜龙启动校验 — 检查关键文件存在
echo === 潜龙启动校验 ===

set ERR=0
for %%f in (
  D:\quant_framework\paper_account.json
  D:\quant_framework\trade_log.csv
  D:\quant_framework\live_trader_config.json
  D:\quant_framework\factor_registry.json
  D:\quant_framework\blacklist.json
  D:\quant_web\stock_names_full.csv
) do (
  if not exist %%f (
    echo ❌ 缺失: %%f
    set /a ERR+=1
  )
)

if %ERR% gtr 0 (
  echo ❌ 启动校验失败 (%ERR%个文件缺失^)
  exit /b 1
)
echo ✅ 启动校验通过
