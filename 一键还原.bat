@echo off
chcp 65001 >nul
cd /d d:\quant_framework

echo ============================
echo  潜龙 — 版本还原工具
echo ============================
echo.

echo 最近 10 个版本（最新的在上面）:
echo.
setlocal enabledelayedexpansion
set idx=1
for /f "tokens=1,*" %%a in ('"C:\Program Files\Git\bin\git.exe" log --oneline -10') do (
    echo  [!idx!] %%a %%b
    set "commit_!idx!=%%a"
    set /a idx+=1
)
echo.
set /a total=idx-1

if %total%==0 (
    echo 只有一个版本，无可选择的历史。
    goto end
)

set /p choice=选择要还原到的版本 [1-%total%]（回车=取消）: 

if "%choice%"=="" goto end

if %choice% geq 1 if %choice% leq %total% (
    call set "target=%%commit_%choice%%%"
    echo.
    echo 即将还原到: %target%
    echo ⚠️ 当前未备份的改动会丢失！
    set /p confirm=确认？(y/N): 
    if /i "!confirm!"=="y" (
        "C:\Program Files\Git\bin\git.exe" restore --source=%target% --worktree -- .
        echo ✅ 已还原到版本 %target%
        echo   刷新页面即可看到效果。
    ) else (
        echo 已取消
    )
) else (
    echo 无效选择
)

:end
echo.
pause
