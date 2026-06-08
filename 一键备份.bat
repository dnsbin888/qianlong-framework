@echo off
chcp 65001 >nul
cd /d d:\quant_framework
echo ============================
echo  潜龙 — 一键备份
echo ============================
echo.
"C:\Program Files\Git\bin\git.exe" add -A
"C:\Program Files\Git\bin\git.exe" commit -m "自动备份 %date% %time%"
if %errorlevel%==0 (echo ✅ 备份完成) else (echo ⚠️ 没有新改动)
echo.
pause