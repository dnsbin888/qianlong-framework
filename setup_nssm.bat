@echo off
echo === 潜龙 NSSM 进程守护 部署 ===
echo.

REM Step 1: 备份
echo [1/4] 备份系统...
python D:\quant_framework\qianlong.py snapshot
python D:\quant_framework\qianlong.py unlock
python D:\quant_framework\backup_data.bat

REM Step 2: 下载 NSSM
echo [2/4] 下载 NSSM...
powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%TEMP%\nssm.zip'" 2>nul
if not exist %TEMP%\nssm.zip (
  echo NSSM 下载失败, 请手动下载: https://nssm.cc/download
  echo 将 nssm.exe 放到 D:\quant_framework\tools\ 目录下
  pause
  exit /b 1
)
powershell -Command "Expand-Archive -Path '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force" 2>nul
mkdir D:\quant_framework\tools 2>nul
copy %TEMP%\nssm\nssm-2.24\win64\nssm.exe D:\quant_framework\tools\ /Y >nul
echo NSSM 下载完成

REM Step 3: 注册服务
echo [3/4] 注册 Windows 服务...
D:\quant_framework\tools\nssm.exe stop QianLong 2>nul
D:\quant_framework\tools\nssm.exe remove QianLong confirm 2>nul
D:\quant_framework\tools\nssm.exe install QianLong D:\潜龙重启.bat
D:\quant_framework\tools\nssm.exe set QianLong AppDirectory D:\
D:\quant_framework\tools\nssm.exe set QianLong AppRestartDelay 10000
D:\quant_framework\tools\nssm.exe set QianLong AppThrottle 300000
D:\quant_framework\tools\nssm.exe set QianLong AppStdout D:\quant_framework\logs\nssm_stdout.log
D:\quant_framework\tools\nssm.exe set QianLong AppStderr D:\quant_framework\logs\nssm_stderr.log
echo 服务注册完成

REM Step 4: 启动
echo [4/4] 启动服务...
taskkill /F /IM python.exe >nul 2>&1
D:\quant_framework\tools\nssm.exe start QianLong
echo.
echo === 部署完成 ===
echo 服务名: QianLong
echo 管理命令:
echo   启动: nssm start QianLong
echo   停止: nssm stop QianLong
echo   状态: nssm status QianLong
echo   卸载: nssm remove QianLong
echo.
python D:\quant_framework\qianlong.py lock
pause
