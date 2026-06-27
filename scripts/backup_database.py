"""潜龙系统数据库自动备份脚本 (E260)
=======================================

每日 15:30 收盘后执行，保留最近 7 天备份。

用法::

    python scripts/backup_database.py

安全约束:
    - 独立运行，不依赖 Flask 或任何运行中服务
    - 备份失败不中断（日志记录，跳过该文件）
    - VACUUM + 复制 + ZIP 压缩
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── 配置 ──
_BACKUP_DIR: Path = Path(r"D:\quant_framework\backups")
_KEEP_DAYS: int = 7

_DB_PATHS: list[str] = [
    r"D:\quant_web\quant_engine.db",
    r"D:\quant_framework\data\factor_db.sqlite",
    r"D:\quant_framework\data\paper_trades.db",
    r"D:\quant_framework\data\live_positions.db",
]

# ── 日志 ──
_LOGS_DIR: Path = Path(r"D:\quant_framework\logs")
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_LOGS_DIR / "backup.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("backup")


class DatabaseBackup:
    """数据库备份器 — VACUUM + 复制 + ZIP 压缩 + 滚动清理。"""

    def __init__(self, backup_dir: Path = _BACKUP_DIR, keep_days: int = _KEEP_DAYS) -> None:
        self._backup_dir: Path = backup_dir
        self._keep_days: int = keep_days
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    # ── 主入口 ──

    def backup_all(self) -> dict[str, Any]:
        """备份全部数据库。

        Returns:
            {"success": [...], "failed": [...]}
        """
        results: dict[str, Any] = {"success": [], "failed": []}

        for db_path in _DB_PATHS:
            db = Path(db_path)
            if not db.exists():
                logger.warning(f"数据库不存在，跳过: {db_path}")
                continue
            try:
                if self._backup_single(db):
                    results["success"].append(db.name)
                else:
                    results["failed"].append(db.name)
            except Exception as e:
                logger.error(f"备份异常 {db.name}: {e}")
                results["failed"].append(db.name)

        # 清理旧备份
        self._cleanup_old()

        return results

    # ── 单文件备份 ──

    def _backup_single(self, db_path: Path) -> bool:
        """备份单个数据库: VACUUM → 复制 → ZIP → 删临时文件。"""
        db_name: str = db_path.stem
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. VACUUM
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.close()
        except Exception as e:
            logger.warning(f"VACUUM 失败 ({db_name}): {e}")

        # 2. 复制
        tmp_path: Path = self._backup_dir / f"{db_name}_{timestamp}.db"
        shutil.copy2(str(db_path), str(tmp_path))

        # 3. ZIP 压缩
        zip_path: Path = self._backup_dir / f"{db_name}_{timestamp}.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(tmp_path), arcname=tmp_path.name)

        # 4. 删除临时文件
        tmp_path.unlink()

        size_kb: float = zip_path.stat().st_size / 1024
        logger.info(f"备份成功: {db_name} → {zip_path.name} ({size_kb:.0f} KB)")
        return True

    # ── 滚动清理 ──

    def _cleanup_old(self) -> None:
        """删除超过 _keep_days 天的旧备份。"""
        cutoff: datetime = datetime.now() - timedelta(days=self._keep_days)
        deleted: int = 0

        for zip_file in self._backup_dir.glob("*.zip"):
            try:
                parts: list[str] = zip_file.stem.split("_")
                if len(parts) >= 2:
                    date_str: str = parts[-2]  # YYYYMMDD
                    file_date: datetime = datetime.strptime(date_str, "%Y%m%d")
                    if file_date < cutoff:
                        zip_file.unlink()
                        deleted += 1
            except Exception:
                pass

        if deleted:
            logger.info(f"清理旧备份: {deleted} 个文件")

    # ── 备份列表 ──

    def list_backups(self) -> list[dict[str, Any]]:
        """获取现有备份列表。"""
        backups: list[dict[str, Any]] = []
        for zip_file in sorted(self._backup_dir.glob("*.zip"), reverse=True):
            backups.append({
                "file": zip_file.name,
                "size": zip_file.stat().st_size,
                "date": datetime.fromtimestamp(zip_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "path": str(zip_file),
            })
        return backups


# ── 主入口 ──

def main() -> int:
    logger.info("=" * 50)
    logger.info("潜龙系统数据库备份")
    logger.info("=" * 50)

    backup = DatabaseBackup()
    results = backup.backup_all()

    logger.info(f"完成: 成功={len(results['success'])}, 失败={len(results['failed'])}")
    if results["success"]:
        logger.info(f"已备份: {', '.join(results['success'])}")
    if results["failed"]:
        logger.error(f"备份失败: {', '.join(results['failed'])}")

    return 1 if results["failed"] else 0


if __name__ == "__main__":
    exit(main())
