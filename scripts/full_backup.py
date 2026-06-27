"""潜龙系统全量备份 — 源代码+配置+治理文档+模板 (不含大缓存文件)

用法: python full_backup.py
输出: D:\quant_backups\backup_YYYYMMDD_HHMMSS.zip
"""
import os, sys, zipfile, time
from datetime import datetime

BACKUP_ROOT = r"D:\quant_backups"
os.makedirs(BACKUP_ROOT, exist_ok=True)

# ── 备份清单 ──
BACKUP_SOURCES = [
    # 1. Web 应用 (源代码+模板+配置)
    (r"D:\quant_web", [
        "*.py",           # 所有 Python 源文件
        "*.html",         # 模板
        "*.css", "*.js",  # 静态资源
        "*.json",         # 配置
        "*.csv",          # 数据文件
        "*.md",           # 文档
        "templates/**/*",
        "static/**/*",
        "docs/**/*.md",   # 治理文档
        "tasks/**/*.md",  # 任务文档
    ]),
    # 2. 量化框架 (核心模块)
    (r"D:\quant_framework", [
        "*.py",
        "*.json",
        "*.md",
        "*.csv",
        "src/quant_framework/**/*.py",
        "scripts/*.py",
        "factors/**/*.py",
        "execution/**/*.py",
        "data/**/*.json",
        "factor_registry.json",
        "trade_config.json",
        "full_market_ic_report.json",
        "*.pkl",           # 小缓存 (quote_cache, performance)
    ]),
]

# ── 排除项 ──
EXCLUDE_PATTERNS = [
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    "*.pyc", "*.pyo", "*.bak", "*.backup*", "*.broken*",
    "stock_data.pkl.gz", "stock_data.pkl",   # 284MB 旧缓存, 已废弃
    "stock_data.parquet",                     # 192MB 可重建
    "factor_cache.pkl",                       # 可重建
    "quote_cache.pkl",                        # 实时行情缓存, 可重建
    "*.zip",                                  # 不备份旧备份
    "backups/**", "snapshot*/**",             # 旧快照
    "backup/**", "archive/**",
]

def should_exclude(path: str) -> bool:
    """检查路径是否应排除。"""
    import fnmatch
    name = os.path.basename(path)
    for pat in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return True
        if pat.endswith("/**") and pat[:-3] in path.replace("\\", "/"):
            return True
        if "**" not in pat and pat in path.replace("\\", "/"):
            return True
    return False

def collect_files() -> list:
    """收集所有需要备份的文件。"""
    import glob as _glob
    files = []
    for root_dir, patterns in BACKUP_SOURCES:
        if not os.path.isdir(root_dir):
            print(f"  ⚠ 目录不存在, 跳过: {root_dir}")
            continue
        os.chdir(root_dir)  # 切换到源目录以便相对路径
        for pat in patterns:
            for f in _glob.glob(pat, recursive=True):
                fpath = os.path.join(root_dir, f)
                if os.path.isfile(fpath) and not should_exclude(fpath):
                    files.append(fpath)
    return sorted(set(files))

def create_backup():
    """创建备份 zip。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(BACKUP_ROOT, f"backup_{timestamp}.zip")

    print(f"[Backup] 收集文件...")
    files = collect_files()
    total_mb = sum(os.path.getsize(f) for f in files) / (1024 * 1024)

    print(f"[Backup] {len(files)} 个文件, ~{total_mb:.0f}MB")
    print(f"[Backup] 写入 {zip_path}...")

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arcname = f.replace("D:\\", "")  # 去掉盘符, 方便跨机恢复
            zf.write(f, arcname)
            count += 1

    zip_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"[Backup] ✅ 完成: {count} 文件 → {zip_path} ({zip_mb:.1f}MB)")

    # 清理旧备份 (保留最近10个)
    old = sorted(
        [f for f in os.listdir(BACKUP_ROOT) if f.startswith("backup_") and f.endswith(".zip")],
        reverse=True
    )
    for old_f in old[10:]:
        os.remove(os.path.join(BACKUP_ROOT, old_f))
        print(f"[Backup] 清理旧备份: {old_f}")

    return zip_path

if __name__ == "__main__":
    t0 = time.time()
    path = create_backup()
    print(f"[Backup] {time.time()-t0:.1f}s")
    print(f"[Backup] 恢复命令: python -m zipfile -e {path} D:\\restore_target")
