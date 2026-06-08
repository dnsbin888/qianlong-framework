@echo off
chcp 65001 >nul 2>&1
title 潜龙 — 策略回测看板
cd /d d:\quant_framework

set "PY="
where python.exe >nul 2>&1 && for /f "tokens=*" %%i in ('where python.exe 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
    for %%d in (
        "C:\Program Files\Python312\python.exe"
    ) do (if not defined PY if exist %%d set "PY=%%~d")
)

if not defined PY (
    echo Python not found!
    pause
    exit /b 1
)

echo.
echo   ==========================================
echo      潜龙 — 策略回测看板 (Flask)
echo   ==========================================
echo.
echo    本地访问: http://localhost:5002/dashboard
echo    请确保 Streamlit 已在 localhost:8501 运行
echo.
echo    按 Ctrl+C 停止服务
echo.
"%PY%" run_web.py
pause
