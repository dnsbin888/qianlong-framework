@echo off
chcp 65001 >nul 2>&1
title Quant Platform v3.0
cd /d d:\quant_framework

:: Find Python
set "PY="
where python.exe >nul 2>&1 && for /f "tokens=*" %%i in ('where python.exe 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
    for %%d in (
        "C:\Program Files\Python312\python.exe"
        "C:\Program Files\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) do (if not defined PY if exist %%d set "PY=%%~d")
)
if not defined PY (
    echo Python not found! Please install Python 3.10+
    pause
    exit /b 1
)

cls
echo.
echo   ===========================================
echo     Quant Platform v3.0  --  量化策略平台
echo   ===========================================
echo.
echo   [1] 启动 Web 专业看板 (推荐)
echo       浏览器打开 http://localhost:8501
echo       组合总览、策略分析、参数优化、数据健康
echo.
echo   [2] 启动终端菜单
echo       Rich TUI 菜单，回测/下载/配置向导
echo.
echo   [3] 短线策略回测 (双信号共振)
echo.
echo   [4] MACD金叉回测 (茅台示例)
echo.
echo   [5] 数据增量更新
echo.
echo   [0] 退出
echo.
set /p CHOICE="  请选择 (0-5): "

if "%CHOICE%"=="1" (
    cls
    echo 正在启动 Web 看板...
    echo 浏览器打开: http://localhost:8501
    echo 按 Ctrl+C 停止
    echo.
    "%PY%" -m streamlit run dashboard.py --server.port 8501 --server.headless true
)
if "%CHOICE%"=="2" (
    cls
    "%PY%" launcher.py
)
if "%CHOICE%"=="3" (
    cls
    echo 短线策略回测 — 双信号共振
    echo.
    "%PY%" scripts/scalper.py --formula dragon --stocks 300 --max-pos 3 --hold 2
    pause
)
if "%CHOICE%"=="4" (
    cls
    echo 运行 MACD 金叉回测 — 贵州茅台 (600519)
    echo.
    "%PY%" quick_start.py 600519 20200101
    pause
)
if "%CHOICE%"=="5" (
    cls
    echo 增量更新数据...
    "%PY%" scripts/data_pipeline.py update
    pause
)
if "%CHOICE%"=="0" exit /b 0

pause
