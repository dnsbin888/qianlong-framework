"""更新 D:\潜龙重启.bat 加入 qianlong.py 保护"""
content = """@echo off
taskkill /F /IM python.exe >nul 2>&1
echo === QianLong Restart ===
python D:\\quant_framework\\qianlong.py lock
call D:\\quant_framework\\backup_data.bat
call D:\\quant_framework\\validate_startup.bat
if %ERRORLEVEL% neq 0 (echo VALIDATION FAILED & pause & exit /b 1)
start "QianLong" "C:\\Program Files\\Python312\\python.exe" D:\\quant_web\\app.py
"""
with open(r"D:\潜龙重启.bat", "w") as f:
    f.write(content)
print("D:\\潜龙重启.bat 已更新")
