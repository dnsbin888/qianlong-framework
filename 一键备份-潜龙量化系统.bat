@echo off
chcp 65001 >nul
cd /d d:\quant_framework

set msg=%1
if "%msg%"=="" set msg=备份 %date% %time%

echo ============================
echo  潜龙 — 一键备份
echo  备注: %msg%
echo ============================

C:\Program Files\Git\bin\git.exe add -A
C:\Program Files\Git\bin\git.exe commit -m "%msg%"

if %errorlevel%==0 (
    echo ✅ 备份完成
) else (
    echo ⚠️ 没有新改动或备份失败
)

echo.
pause
