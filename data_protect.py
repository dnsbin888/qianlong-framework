"""数据保护层 — 防止还原/覆盖导致数据丢失
============================================
铁律:
  1. 任何写入前自动备份 (backup-before-write)
  2. 还原前校验: 如果当前数据比备份新 → 拒绝覆盖, 除非强制
  3. 写操作原子化: .tmp + os.replace

用法:
  from data_protect import safe_write, safe_restore

  safe_write("paper_account.json", data)      # 自动备份后写入
  safe_restore("paper_account.json", backup)  # 校验后还原
"""
import json, os, shutil
from datetime import datetime
from pathlib import Path

BACKUP_DIR = r"D:\quant_framework\backups\auto_safe"


def _backup_path(filepath: str) -> str:
    """生成带时间戳的备份路径"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = Path(filepath).stem
    return os.path.join(BACKUP_DIR, f"{name}_{ts}.json")


def safe_write(filepath: str, data: dict) -> bool:
    """
    安全写入: 先备份 → 再写入 → 原子替换。
    返回 True 如果成功。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # 1. 如果目标已存在，先备份
    if os.path.exists(filepath):
        backup = _backup_path(filepath)
        shutil.copy2(filepath, backup)

    # 2. 先写临时文件
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 3. 原子替换
        os.replace(tmp, filepath)
        return True
    except Exception as e:
        print(f"[DataProtect] Write failed: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def safe_restore(filepath: str, backup_path: str, force: bool = False) -> bool:
    """
    安全还原: 校验当前 vs 备份 → 防止覆盖更新的数据。

    Args:
        filepath: 要还原的文件
        backup_path: 备份源文件
        force: True=强制覆盖 (跳过校验)

    Returns:
        True 如果还原成功
    """
    if not os.path.exists(backup_path):
        print(f"[DataProtect] Backup not found: {backup_path}")
        return False

    # 加载备份
    with open(backup_path, "r", encoding="utf-8") as f:
        backup_data = json.load(f)

    # 校验: 当前数据是否比备份更新?
    if os.path.exists(filepath) and not force:
        with open(filepath, "r", encoding="utf-8") as f:
            current_data = json.load(f)

        current_trades = len(current_data.get("trade_log", []))
        backup_trades = len(backup_data.get("trade_log", []))

        if current_trades > backup_trades:
            msg = (
                f"\n{'='*60}\n"
                f"  ⚠️  还原警告: 当前数据比备份更新!\n"
                f"  当前交易: {current_trades} 笔\n"
                f"  备份交易: {backup_trades} 笔\n"
                f"  还原将丢失 {current_trades - backup_trades} 笔交易\n"
                f"  使用 force=True 强制覆盖, 或手动合并\n"
                f"{'='*60}\n"
            )
            print(msg)
            # 不覆盖 — 保留当前数据
            safe_write(filepath + ".restore_rejected", current_data)
            return False

    # 还原前备份当前
    if os.path.exists(filepath):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        shutil.copy2(filepath, _backup_path(filepath))

    # 执行还原
    return safe_write(filepath, backup_data)


def data_health_check(filepath: str) -> dict:
    """快速健康检查"""
    result = {"file": filepath, "exists": False, "valid_json": False, "size": 0, "trades": 0}
    if not os.path.exists(filepath):
        return result
    result["exists"] = True
    result["size"] = os.path.getsize(filepath)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        result["valid_json"] = True
        result["trades"] = len(data.get("trade_log", []))
    except Exception:
        pass
    return result


# ═══ CLI ═══
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python data_protect.py check|backup <file>")
        sys.exit(1)

    cmd = sys.argv[1]
    filepath = sys.argv[2] if len(sys.argv) > 2 else r"D:\quant_framework\paper_account.json"

    if cmd == "check":
        h = data_health_check(filepath)
        print(f"  {filepath}")
        print(f"    exists={h['exists']}  valid={h['valid_json']}  size={h['size']}  trades={h['trades']}")
    elif cmd == "backup":
        if os.path.exists(filepath):
            dest = _backup_path(filepath)
            shutil.copy2(filepath, dest)
            print(f"  Backed up → {dest}")
        else:
            print(f"  File not found: {filepath}")
