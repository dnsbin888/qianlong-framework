"""盘前检查增强版 (蓝图 v3.0 O2-3)

检查项 (10项):
  1. QMT连接状态
  2. SQLite数据库完整性
  3. 磁盘空间 (>1GB)
  4. 行情数据新鲜度 (<300s)
  5. Flask进程存活
  6. 信号中心有数据
  7. PaperAutoLoop运行状态
  8. RuleEngine规则加载
  9. 昨日对账结果
  10. 备份状态

用法: python pre_market_check.py
      建议 09:25 自动执行
"""

import os, sys, json, time, sqlite3, logging

logger = logging.getLogger(__name__)

CHECK_RESULTS = []


def ok(name: str, detail: str = ""):
    CHECK_RESULTS.append({"name": name, "status": "✅", "detail": detail})
    print(f"  ✅ {name}: {detail}" if detail else f"  ✅ {name}")


def warn(name: str, detail: str = ""):
    CHECK_RESULTS.append({"name": name, "status": "⚠️", "detail": detail})
    print(f"  ⚠️ {name}: {detail}")


def fail(name: str, detail: str = ""):
    CHECK_RESULTS.append({"name": name, "status": "❌", "detail": detail})
    print(f"  ❌ {name}: {detail}")


def check_qmt() -> bool:
    """1. QMT连接状态"""
    try:
        from qmt_data_bridge import is_qmt_available
        if is_qmt_available():
            ok("QMT连接", "xtquant可用")
            return True
        else:
            warn("QMT连接", "xtquant不可用, 降级TDX")
            return False
    except Exception as e:
        fail("QMT连接", str(e))
        return False


def check_sqlite() -> bool:
    """2. SQLite数据库完整性"""
    dbs = [
        r"D:\quant_web\data\ml\factors.db",
        r"D:\quant_web\data\ml\trades.db",
        r"D:\quant_web\quant_engine.db",
    ]
    all_ok = True
    for db_path in dbs:
        if not os.path.exists(db_path):
            warn(f"SQLite-{os.path.basename(db_path)}", "文件不存在")
            all_ok = False
            continue
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA integrity_check")
            conn.close()
        except Exception as e:
            fail(f"SQLite-{os.path.basename(db_path)}", str(e))
            all_ok = False
    if all_ok:
        ok("SQLite", f"{len(dbs)}个数据库完整")
    return all_ok


def check_disk() -> bool:
    """3. 磁盘空间"""
    import shutil
    for drive in ["D:\\", "C:\\"]:
        usage = shutil.disk_usage(drive)
        free_gb = usage.free / (1024**3)
        if free_gb < 1:
            fail(f"磁盘-{drive}", f"仅剩{free_gb:.1f}GB")
            return False
    ok("磁盘空间", f"D盘{shutil.disk_usage('D:\\').free/(1024**3):.0f}GB可用")
    return True


def check_data_freshness() -> bool:
    """4. 行情数据新鲜度"""
    try:
        from realtime_quotes import _quote_cache
        if _quote_cache and _quote_cache.get("data"):
            ts = _quote_cache.get("updated_at", 0)
            age = time.time() - ts if ts else 999
            if age < 300:
                ok("数据新鲜度", f"{age:.0f}秒前")
                return True
            else:
                warn("数据新鲜度", f"{age:.0f}秒前 (>300s)")
                return False
    except Exception:
        pass
    warn("数据新鲜度", "无法检测")
    return False


def check_flask() -> bool:
    """5. Flask进程存活"""
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:5002/api/health", timeout=5)
        if r.status == 200:
            ok("Flask", "端口5002正常")
            return True
    except Exception:
        pass
    warn("Flask", "端口5002无响应")
    return False


def check_signals() -> bool:
    """6. 信号中心"""
    import urllib.request, json as _j
    try:
        r = urllib.request.urlopen("http://127.0.0.1:5002/api/signal-center", timeout=10)
        data = _j.loads(r.read().decode())
        n = len(data.get("signals", []))
        if n > 0:
            ok("信号中心", f"{n}条信号")
            return True
        else:
            warn("信号中心", "0条信号")
            return False
    except Exception:
        fail("信号中心", "不可达")
        return False


def check_paper_loop() -> bool:
    """7. PaperAutoLoop"""
    import urllib.request, json as _j
    try:
        r = urllib.request.urlopen("http://127.0.0.1:5002/api/paper-trade/auto-loop/status", timeout=5)
        d = _j.loads(r.read().decode())
        if d.get("running"):
            ok("PaperAutoLoop", f"扫描{d.get('scan_count',0)}次")
            return True
        else:
            warn("PaperAutoLoop", "未运行")
            return False
    except Exception:
        warn("PaperAutoLoop", "API不可达")
        return False


def check_rule_engine() -> bool:
    """8. RuleEngine规则加载"""
    try:
        sys.path.insert(0, r"D:\quant_framework\src")
        from quant_framework.execution.rules.engine import RuleEngine
        engine = RuleEngine()
        n = engine.rule_count
        if n >= 5:
            ok("RuleEngine", f"{n}条规则")
            return True
        else:
            warn("RuleEngine", f"仅{n}条规则")
            return False
    except Exception as e:
        fail("RuleEngine", str(e))
        return False


def check_recon() -> bool:
    """9. 昨日对账"""
    log_path = r"D:\quant_framework\reconciliation.log"
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            lines = f.readlines()
        if lines:
            last = json.loads(lines[-1])
            diffs = last.get("differences", 0)
            if diffs == 0:
                ok("昨日对账", "无漂移")
                return True
            else:
                warn("昨日对账", f"{diffs}只漂移")
                return False
    warn("昨日对账", "无记录")
    return False


def check_backup() -> bool:
    """10. 备份状态"""
    backup_dir = r"D:\quant_web\data\backup"
    if os.path.exists(backup_dir):
        files = [f for f in os.listdir(backup_dir) if f.endswith(".db.")]
        if files:
            ok("备份", f"{len(files)}个文件")
            return True
    warn("备份", "无今日备份")
    return False


def run_all():
    print("=" * 50)
    print(f"潜龙盘前检查 — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    checks = [
        check_qmt, check_sqlite, check_disk, check_data_freshness,
        check_flask, check_signals, check_paper_loop, check_rule_engine,
        check_recon, check_backup,
    ]
    for fn in checks:
        try:
            fn()
        except Exception as e:
            fail(fn.__name__, str(e))

    passed = sum(1 for c in CHECK_RESULTS if c["status"] == "✅")
    warnings = sum(1 for c in CHECK_RESULTS if c["status"] == "⚠️")
    failed = sum(1 for c in CHECK_RESULTS if c["status"] == "❌")

    print("=" * 50)
    print(f"结果: {passed}✅ {warnings}⚠️ {failed}❌")
    return passed, warnings, failed


if __name__ == "__main__":
    run_all()
