@echo off
:: 每周一 08:00 自动运行全市场IC验证
:: 安装计划任务: schtasks /create /tn "潜龙IC更新" /tr "D:\quant_framework\ic_auto_update.bat" /sc weekly /d MON /st 08:00
"C:\Program Files\Python312\python.exe" D:\quant_framework\full_market_ic.py --days 60 --sample 500 >> D:\quant_framework\ic_auto_update.log 2>&1
echo %date% %time% IC更新完成 >> D:\quant_framework\ic_auto_update.log
