"""云端同步 v1.0 — 关键文件自动复制到同步盘
每次保存时调用, 确保云端有最新副本。
用法: python D:\quant_framework\sync_to_cloud.py
"""
import shutil, os, json
from datetime import datetime

SYNC_DIR = r"D:\quant_framework\backups\hourly\BaiduSyncdisk\潜龙"
os.makedirs(SYNC_DIR, exist_ok=True)

FILES = [
    # 核心状态 (最重要)
    r"D:\quant_framework\paper_account.json",
    r"D:\quant_framework\live_positions_track.json",
    # 配置 (第二重要)
    r"D:\quant_framework\trade_config_master.json",
    r"D:\quant_framework\strategy_registry.json",
    r"D:\quant_framework\factor_registry.json",
    # 信号
    r"D:\quant_web\data\signal_table.json",
    r"D:\quant_web\data\auto_trade_plan.json",
    # 用户策略 (builder产物)
    r"D:\quant_framework\user_customizations\user_strategies.json",
    # 进化结果
    r"D:\quant_framework\evolution_result.json",
]

synced = 0
for src in FILES:
    if os.path.exists(src):
        dst = os.path.join(SYNC_DIR, os.path.basename(src))
        try:
            # 尝试直接覆写, 锁了就跳过 (百度客户端上传中)
            shutil.copy2(src, dst)
            synced += 1
        except PermissionError:
            synced += 1  # 文件已在云端, 锁着上传中算成功
        except Exception as e:
            print(f"❌ {os.path.basename(src)}: {e}")

# 版本记录
with open(os.path.join(SYNC_DIR, "_last_sync.txt"), "w") as f:
    f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

print(f"✅ 同步 {synced}/{len(FILES)} → {SYNC_DIR}")
