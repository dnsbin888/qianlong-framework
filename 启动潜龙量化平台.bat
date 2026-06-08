@echo off
chcp 65001 >nul
title 潜龙量化平台 v2.0

echo.
echo   ============================================================
echo     🐉 潜龙量化平台 v2.0 — 启动中
echo   ============================================================
echo.

:: ── 启动 Flask (主站 5000端口) ──
echo   [1/2] 启动 Web 主站 (端口 5000)...
cd /d D:\quant_web
set PYTHONUTF8=1
start "潜龙-主站" cmd /c "chcp 65001 >nul && set PYTHONUTF8=1 && python -X utf8 app.py"

:: ── 启动 Streamlit (仪表盘 8501端口) ──
echo   [2/2] 启动 仪表盘 (端口 8501)...
cd /d D:\quant_framework
start "潜龙-仪表盘" cmd /c "chcp 65001 >nul && set PYTHONUTF8=1 && python -X utf8 -m streamlit run quant_dashboard.py --server.port 8501 --server.headless true"

echo.
echo   ============================================================
echo     ✅ 潜龙量化平台 已启动!
echo.
echo     📊 主站:    http://localhost:5000
echo     📈 仪表盘:  http://localhost:8501
echo   ============================================================
echo.
echo   ⚠ Flask首次启动需30-60秒加载数据，请稍等。
echo.

timeout /t 3
exit
