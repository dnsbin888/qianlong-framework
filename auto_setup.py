"""一键设置全自动交易环境"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import os, shutil, json, subprocess

TASKS_XML = r"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger><StartBoundary>2026-06-03T08:30:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe</Command>
      <Arguments>d:\quant_framework\sync_to_keyboard.py</Arguments>
    </Exec>
  </Actions>
  <Settings><Enabled>true</Enabled><WakeToRun>false</WakeToRun></Settings>
</Task>"""

print("=" * 55)
print("  全自动交易环境设置")
print("=" * 55)

# 1. 创建每日自动同步任务
print("\n[1] 创建每日8:30自动同步...")
task_name = "QuantSync_Daily"
task_file = r"d:\quant_framework\sync_task.xml"
with open(task_file, "w", encoding="utf-16") as f:
    f.write(TASKS_XML)

result = os.system(f'schtasks /create /tn "{task_name}" /xml "{task_file}" /f 2>nul')
if result == 0:
    print(f"  ✅ 已创建: 每天8:30自动运行 sync_to_keyboard.py")
else:
    print(f"  ⚠️ 任务创建失败, 手动运行: python sync_to_keyboard.py")

# 2. 检查键盘软件是否在启动目录
print("\n[2] 设置键盘软件开机自启...")
startup = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
kbd_exe = r"d:\通信达技术指标\1键盘管理软件\24键专业版 ID条件单\键盘软件2025_new.exe"
if os.path.exists(kbd_exe):
    shortcut = os.path.join(startup, "量化打板键盘.lnk")
    # 创建快捷方式
    ps_cmd = f"""$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('{shortcut}'); $Shortcut.TargetPath = '{kbd_exe}'; $Shortcut.Save()"""
    os.system(f'powershell -Command "{ps_cmd}"')
    print(f"  ✅ 键盘软件已设为开机自启")

# 3. 打印操作说明
print(f"\n{'='*55}")
print(f"  全自动交易流程")
print(f"{'='*55}")
print(f"""
  每天自动:
    ├─ 8:30  Python自动同步候选池 → Table.txt (79只)
    ├─ 9:00  同花顺 + 键盘软件 (开机自启)
    ├─ 9:25  键盘软件"批量涨停封单触发"模式自动监控
    │         → 候选股封板+封单达标 → 🟢 自动买入!
    │         → 炸板 → 🔴 自动卖出!
    │         → 冲高回落 → 🟡 自动止盈!
    └─ 15:00  收盘自动清仓

  你需要做的:
    ✅ 同花顺保持登录状态
    ✅ 键盘软件保持开启
    ✅ 账户里有钱
    ❌ 盘中什么都不用做!
""")

print("设置完成!")
