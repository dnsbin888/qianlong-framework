@echo off
chcp 65001 >nul
cd /d d:\quant_framework

echo ============================
echo  潜龙 — 还原到上一个版本
echo ============================
echo.
echo ⚠️ 警告：此操作会丢弃当前未备份的所有改动！
echo.
set /p confirm=确认还原？(y/N): 

if /i "%confirm%"=="y" (
    echo.
    C:\Program Files\Git\bin\git.exe log --oneline -5
    echo.
    set /p target=输入要还原的版本号（回车=还原到上一个版本）: 
    
    if "%target%"=="" (
        C:\Program Files\Git\bin\git.exe checkout -- .
        echo ✅ 已还原到上一个版本
    ) else (
        C:\Program Files\Git\bin\git.exe checkout %target% -- .
        echo ✅ 已还原到版本 %target%
    )
) else (
    echo 已取消
)

echo.
pause
