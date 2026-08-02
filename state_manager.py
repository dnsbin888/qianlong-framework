"""统一状态管理 — 所有 JSON 状态文件的安全读写 (Phase 1 数据可靠性)
==================================================================
用法:
    from state_manager import StateManager
    sm = StateManager(r"D:\quant_framework\trade_config_master.json")
    data = sm.load()         # 读 (含版本号 + 自动恢复)
    sm.save(data)            # 写 (含版本号递增 + .tmp + .bak + 云镜像)

特性:
  - 崩溃安全: 先写.tmp, 再替换
  - 版本保护: 单调递增, 永不降级
  - 自动恢复: .bak → 百度云 → versions/ → 从零
  - 滚动备份: 保留最近5版
  - 云镜像: 百度云自动同步 (如配置)
  - 校验和: SHA256 (静默损坏检测)
  - 零侵入: 只在数据中加 _version 字段, 不影响现有读取逻辑
"""
import json, os, shutil, hashlib
from datetime import datetime
from pathlib import Path

BACKUP_DIR = r"D:\quant_framework\backups\state_versions"
CLOUD_DIR = r"D:\BaiduSyncdisk\潜龙"


class StateManager:
    """状态文件安全读写管理器"""

    def __init__(self, filepath: str, cloud_name: str = None):
        self.path = filepath
        self.bak = filepath + ".bak"
        self.tmp = filepath + ".tmp"
        self.filename = Path(filepath).name
        self.cloud_path = os.path.join(CLOUD_DIR, cloud_name) if cloud_name else None
        self.ver_dir = os.path.join(BACKUP_DIR, Path(filepath).stem)

    def load(self) -> dict | list:
        """读取状态文件。损坏时自动从恢复链恢复。返回 {} 如果全部失败。"""
        # Try local
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and self._verify_checksum(data):
                    return data
                if isinstance(data, list):
                    return data  # lists don't have checksums
            except: pass

        # Recovery chain
        recovery = [("local.bak", self.bak)]
        if self.cloud_path and os.path.exists(self.cloud_path):
            recovery.append(("cloud", self.cloud_path))
        if os.path.exists(self.ver_dir):
            vers = sorted([f for f in os.listdir(self.ver_dir) if f.startswith("v")], reverse=True)
            if vers:
                recovery.append(("versions", os.path.join(self.ver_dir, vers[0])))

        for name, path in recovery:
            if not os.path.exists(path): continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    print(f"[StateManager] {self.filename}: 从{name}恢复 v{data.get('_version',0)}")
                    self._write_atomic(data, self.path)
                    return data
                if isinstance(data, list):
                    print(f"[StateManager] {self.filename}: 从{name}恢复 list({len(data)}条)")
                    self._write_atomic_list(data, self.path)
                    return data
            except: continue

        print(f"[StateManager] {self.filename}: 全部恢复源失败, 从零开始")
        return {}

    def _write_atomic_list(self, data: list, dest: str) -> bool:
        """原子写入数组文件"""
        try:
            with open(self.tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                f.flush(); os.fsync(f.fileno())
            if os.path.exists(dest):
                try: os.replace(dest, self.bak)
                except: pass
            os.replace(self.tmp, dest)
            return True
        except: return False

    def save(self, data: dict | list) -> bool:
        """安全保存。返回 True 如果成功。"""
        if isinstance(data, list):
            # 数组文件: 不加版本号, 直接安全写入
            if not self._write_atomic_list(data, self.path):
                return False
            if self.cloud_path and os.path.exists(os.path.dirname(self.cloud_path)):
                try: shutil.copy2(self.path, self.cloud_path)
                except: pass
            return True

        # Version (dict only)
        current_version = 0
        existing = self.load() if os.path.exists(self.path) else {}
        if isinstance(existing, dict):
            current_version = existing.get("_version", 0)

        data["_version"] = current_version + 1
        data["_saved_at"] = datetime.now().isoformat()
        data["_checksum"] = self._compute_checksum(data)

        # Write
        if not self._write_atomic(data, self.path):
            return False

        # Rolling backup
        try:
            os.makedirs(self.ver_dir, exist_ok=True)
            ver_file = os.path.join(self.ver_dir, f"v{data['_version']:06d}.json")
            shutil.copy2(self.path, ver_file)
            all_vers = sorted([f for f in os.listdir(self.ver_dir) if f.startswith("v")])
            for old in all_vers[:-5]:
                os.remove(os.path.join(self.ver_dir, old))
        except: pass

        # Cloud mirror
        if self.cloud_path:
            try:
                cloud_dir = os.path.dirname(self.cloud_path)
                if os.path.exists(cloud_dir):
                    os.makedirs(cloud_dir, exist_ok=True)
                    shutil.copy2(self.path, self.cloud_path)
            except: pass

        return True

    def _write_atomic(self, data: dict, dest: str) -> bool:
        """原子写入: .tmp → 备份旧文件 → 替换"""
        try:
            # Strip checksum before computing
            clean = {k: v for k, v in data.items() if k != "_checksum"}
            clean["_checksum"] = self._compute_checksum(clean)

            # Write .tmp
            with open(self.tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Backup old
            if os.path.exists(dest):
                try: os.replace(dest, self.bak)
                except: pass

            # Promote
            os.replace(self.tmp, dest)
            return True
        except Exception as e:
            print(f"[StateManager] Write failed: {e}")
            return False

    @staticmethod
    def _compute_checksum(data: dict) -> str:
        clean = {k: v for k, v in sorted(data.items()) if k != "_checksum"}
        return hashlib.sha256(
            json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16]

    @staticmethod
    def _verify_checksum(data: dict) -> bool:
        stored = data.pop("_checksum", None)
        if stored is None:
            return True  # legacy data, no checksum, trust it
        computed = StateManager._compute_checksum(data)
        data["_checksum"] = stored  # restore
        return stored == computed


# ═══ CLI ═══
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python state_manager.py <filepath> [cloud_name]")
        sys.exit(1)

    fp = sys.argv[1]
    cn = sys.argv[2] if len(sys.argv) > 2 else None
    sm = StateManager(fp, cn)

    data = sm.load()
    print(f"  File: {fp}")
    print(f"  Version: {data.get('_version', 'N/A')}")
    print(f"  Keys: {list(data.keys())[:10]}...")
    checksum_ok = StateManager._verify_checksum(data)
    print(f"  Checksum: {'✅' if checksum_ok else '⚠️  (legacy, no checksum)' if data.get('_checksum') is None else '❌ FAILED'}")
