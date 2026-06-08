@echo off
chcp 65001 >nul
echo.
echo ╔══════════════════════════════════════════════╗
echo ║    A股量化策略分析平台 v1.0                 ║
echo ║    通达信选股公式 + T+1短线策略             ║
echo ╚══════════════════════════════════════════════╝
echo.
echo 正在生成最新报告并打开浏览器...
echo.

cd /d d:\quant_framework
"C:\Program Files\Python312\python.exe" generate_report.py
start "" "d:\quant_framework\回测报告.html"

echo.
echo 报告已打开。关闭此窗口。
timeout /t 3 >nul
