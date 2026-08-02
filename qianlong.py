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

# 静态保护: 配置文件 (锁住不改)
PROTECTED = [
    os.path.join(FRAMEWORK, f) for f in [
        "live_trader_config.json", "blacklist.json", "factor_registry.json",
        "user_customizations\\user_factors.json",
        "user_customizations\\user_strategies.json",
        "user_customizations\\user_tdx_formulas.json",
        "config\\default.yaml", "trade_config_master.json",
        # 策略代码保护 (每个策略独立锁)
        "reversal_strategy.py", "signals\\reversal\\realtime.py",  # 弱转强+超跌反弹
        "signals\\daban\\realtime.py", "signals\\daban\\weights.py",  # 打板双刀
        "signals\\strong_auction_board.py",  # 竞价抢筹
        "full_market_ic.py", "xgb_factor_weight.py",  # 8因子
        "signal_config.json",  # 策略止盈注册表
        # 2026-07-21: 已完成模块,禁止改动
        "dingtalk_alerts.py",  # 钉钉告警+指令解析
        "atr_stop.py", "ruler_trade.py",  # ATR止损+交易尺子
        "auto_breaker.py", "deflated_sharpe.py",  # 自动熔断+DSR
        "stock_filters.py",  # Hurst+LHB过滤器
        "src\\quant_framework\\strategy\\state_strategy_map.py",  # 策略状态路由
        # 2026-07-21收盘: ATR自适应止损,不可改写
        "src\\quant_framework\\execution\\rules\\trailing_stop.py",  # 移动止盈规则
    ]
] + [
    os.path.join(WEB, "stock_names_full.csv"),
    os.path.join(WEB, "generate_signal_table.py"),  # ATR参数读取
]
# 运行时文件: 永不锁 (Flask/paper_engine需要写入)
# paper_account.json / trade_log.csv / equity_log.json / live_equity_log.json / live_positions_track.json

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

def cleanup(keep_snaps: int = 7, keep_daily: int = 30, dry_run: bool = False):
    """清理旧备份文件 (P3-01)
    规则:
      - snap_* 目录: 保留最近 keep_snaps 天
      - daily_* 目录: 保留最近 keep_daily 天
      - backup_*.zip: 保留最近 keep_daily 个
    """
    os.makedirs(BACKUP, exist_ok=True)
    now = time.time()
    deleted = 0
    freed = 0

    for entry in os.listdir(BACKUP):
        path = os.path.join(BACKUP, entry)
        age_days = (now - os.path.getmtime(path)) / 86400

        if entry.startswith('snap_') and os.path.isdir(path):
            if age_days > keep_snaps:
                size = sum(os.path.getsize(os.path.join(path, f))
                           for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                deleted += 1
                freed += size
        elif entry.startswith('daily_') and os.path.isdir(path):
            if age_days > keep_daily:
                size = sum(os.path.getsize(os.path.join(path, f))
                           for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                deleted += 1
                freed += size
        elif entry.endswith('.zip'):
            if age_days > keep_daily:
                size = os.path.getsize(path)
                if not dry_run:
                    os.remove(path)
                deleted += 1
                freed += size

    tag = "[试运行] " if dry_run else ""
    print(f"🧹 {tag}清理完成: {deleted}个文件/目录, 释放{freed/1024:.0f}KB")
    if dry_run:
        print("  加 --do 参数执行实际清理")

def consolidate_logs(dry_run: bool = False):
    """整合散落日志 (P3-02) — 一次性调试日志归档, 保留活跃日志"""
    LOG_ARCHIVE = os.path.join(FRAMEWORK, "logs", "archive")
    os.makedirs(LOG_ARCHIVE, exist_ok=True)
    active = {'watchdog.log', 'trade.log', 'quant.log', 'flask.log',
              'nssm_service.log', 'nssm_stdout.log', 'nssm_stderr.log'}
    debris = ['_err.log', '_error.log', 'flask_e', 'flask_run', 'flask_start',
              'flask_restart', 'flask_output', 'flask_stderr', 'flask_stdout',
              'flask_out', 'flask_new', 'flask_err', 'server_err', 'server_out',
              'app_run', 'app_startup', 'stderr.log', 'stdout.log', 'memory_dump',
              'streamlit_', 'terminal_', '_backup_test', 'backtest_error',
              'backtest_output', 'market_monitor', 'paper_auto']
    moved = 0
    for root_dir in [FRAMEWORK, WEB]:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            if 'archive' in dirpath or '__pycache__' in dirpath or '_tmp' in dirpath:
                continue
            for f in filenames:
                if not f.endswith(('.log', '.err')): continue
                if f in active: continue
                if not any(p in f for p in debris): continue
                src = os.path.join(dirpath, f)
                dst = os.path.join(LOG_ARCHIVE, f)
                if os.path.exists(dst):
                    dst = os.path.join(LOG_ARCHIVE, f"{os.path.splitext(f)[0]}_{int(time.time())}{os.path.splitext(f)[1]}")
                if dry_run: print(f"  [试运行] {os.path.basename(src)}")
                else: shutil.move(src, dst)
                moved += 1
    tag = "[试运行] " if dry_run else ""
    print(f"📋 {tag}日志整合: {moved}个碎片归档 → logs/archive/")
    print(f"  活跃日志: {', '.join(sorted(active))}")

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
            "run-ic": run_ic, "cleanup": cleanup, "tidy-logs": consolidate_logs}
    if len(sys.argv) < 2:
        print("用法: python qianlong.py <lock|unlock|status|snapshot|restore|watch|run-ic|cleanup|tidy-logs>")
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
