@echo off
chcp 65001 >nul
set CODE=%~1
set CODE=%CODE:qlstock://=%
set CODE=%CODE:/=%
if "%CODE%"=="" exit /b

:: 写入桥接文件
echo {"symbol":"%CODE%","action":"view","timestamp":"%TIME%"} > "d:\quant_framework\bridge_stock.json"

:: 复制到剪贴板
echo %CODE%| clip

:: 激活同花顺
python -c "import pygetwindow as gw; wins=gw.getWindowsWithTitle('同花顺'); [w.activate() for w in wins[:1]]" 2>nul
exit /b
