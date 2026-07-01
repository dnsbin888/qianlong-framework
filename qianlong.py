r"""潜龙数据保护 v2.0 - 统一 CLI 工具
用法: python D:\quant_framework\qianlong.py <command>

命令:
  lock      锁定所有关键数据文件 (日常状态)
  unlock    解锁 (自动创建快照)
  status    查看保护状态
  snapshot  创建恢复快照
  restore   恢复到最近快照
  watch     启动文件监控 (后台运行)
  run-ic    运行全市场IC计算 (自动解锁→运行→锁上)

对标: 超越 vnpy/QMT - 个人量化系统数据安全最优解
"""
import os, sys, json, time, shutil, hashlib, subprocess

FRAMEWORK = r"D:\quant_framework"
WEB = r"D:\quant_web"
BACKUP = os.path.join(FRAMEWORK, "backups")

PROTECTED = [
    os.path.join(FRAMEWORK, f) for f in [
        "paper_account.json", "trade_log.csv", "equity_log.json",
        "live_equity_log.json", "live_positions_track.json",
        "live_trader_config.json", "blacklist.json", "factor_registry.json",
        "user_customizations\\user_factors.json",
        "user_customizations\\user_strategies.json",
        "user_customizations\\user_tdx_formulas.json",
        "config\\default.yaml", "trade_config_master.json",
    ]
] + [os.path.join(WEB, "stock_names_full.csv")]

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode == 0

def lock():
    """锁住所有关键文件"""
    count = 0
    for f in PROTECTED:
        if os.path.exists(f):
            run(f'attrib +R "{f}"')
            count += 1
    print(f"🔒 已锁定 {count}/{len(PROTECTED)} 个文件")

def unlock():
    """解锁 (自动快照)"""
    snapshot()
    count = 0
    for f in PROTECTED:
        if os.path.exists(f):
            run(f'attrib -R "{f}"')
            count += 1
    print(f"🔓 已解锁 {count} 个文件 (快照已保存)")

def status():
    """查看保护状态"""
    locked = sum(1 for f in PROTECTED if os.path.exists(f) and bool(os.stat(f).st_file_attributes & 1))
    total = sum(1 for f in PROTECTED if os.path.exists(f))
    print(f"保护状态: {locked}/{total} 已锁定")
    if locked < total:
        print(f"  ⚠ {total - locked} 个文件未锁定")

def snapshot():
    """创建恢复快照"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap = os.path.join(BACKUP, f"snap_{ts}")
    os.makedirs(snap, exist_ok=True)
    for f in PROTECTED:
        if os.path.exists(f):
            shutil.copy2(f, snap)
    print(f"📸 快照: {snap}")

def restore():
    """恢复到最近快照"""
    snaps = sorted(
        [d for d in os.listdir(BACKUP) if d.startswith("snap_")],
        reverse=True
    )
    if not snaps:
        print("❌ 无可用快照")
        return
    latest = os.path.join(BACKUP, snaps[0])
    print(f"恢复来源: {snaps[0]}")
    for f in PROTECTED:
        name = os.path.basename(f)
        src = os.path.join(latest, name)
        if os.path.exists(src):
            shutil.copy2(src, f)
            print(f"  ✅ {name}")
    print("✅ 恢复完成")

def watch(interval=30):
    """后台监控文件变化"""
    print(f"👁 文件监控已启动 (每{interval}秒)")
    baseline = {}
    for f in PROTECTED:
        if os.path.exists(f):
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            baseline[f] = h.hexdigest()
    try:
        while True:
            time.sleep(interval)
            for f, old_hash in baseline.items():
                if not os.path.exists(f):
                    print(f"  ⚠ {os.path.basename(f)} 已删除")
                    continue
                h = hashlib.sha256()
                with open(f, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                new_hash = h.hexdigest()
                if new_hash != old_hash:
                    print(f"  ⚡ {time.strftime('%H:%M:%S')} {os.path.basename(f)} 已变更")
                    baseline[f] = new_hash
    except KeyboardInterrupt:
        print("\n👁 监控已停止")

def run_ic():
    """安全运行IC: 自动解锁→运行→锁上 (容错: 不因unlock失败而中止)"""
    try: unlock()
    except: pass
    print()
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(FRAMEWORK, "full_market_ic.py")],
                       cwd=FRAMEWORK)
    print()
    try: lock()
    except: pass
    return r.returncode

def main():
    cmds = {"lock": lock, "unlock": unlock, "status": status,
            "snapshot": snapshot, "restore": restore, "watch": watch,
            "run-ic": run_ic}
    if len(sys.argv) < 2:
        print("用法: python qianlong.py <lock|unlock|status|snapshot|restore|watch|run-ic>")
        print(f"保护文件: {len(PROTECTED)} 个")
        return
    cmd = sys.argv[1]
    if cmd == "watch" and len(sys.argv) > 2:
        watch(int(sys.argv[2]))
    elif cmd in cmds:
        cmds[cmd]()
    else:
        print(f"未知命令: {cmd}")

if __name__ == "__main__":
    main()
