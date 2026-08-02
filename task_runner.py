"""定时任务执行器 v1.0 — 带日志+状态记录 (行业对标: cron job log)"""
import sys, os, json, subprocess, traceback
from datetime import datetime

LOG_FILE = r"D:\quant_framework\logs\task_run_log.jsonl"

def run_task(name, script, cwd=None):
    """执行一个任务, 记录结果到JSONL日志, 打印摘要"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[TaskRunner] {ts} 执行: {name}")
    ok = False
    output = ""
    try:
        r = subprocess.run([sys.executable, "-B", script],
                          cwd=cwd or os.path.dirname(script),
                          capture_output=True, text=True, timeout=300)
        ok = r.returncode == 0
        output = r.stdout[-500:] + r.stderr[-200:]
        status = "PASS" if ok else "FAIL"
        print(f"[TaskRunner] {name}: {status}")
    except Exception as e:
        output = str(e)
        status = "ERROR"
        print(f"[TaskRunner] {name}: {status} — {e}")

    # 追加日志
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps({"time": ts, "task": name, "status": status, "output": output[:500]},
                          ensure_ascii=False) + '\n')
    return ok


def get_last_runs(n=20):
    """读取最近N条任务日志"""
    if not os.path.exists(LOG_FILE):
        return []
    runs = []
    with open(LOG_FILE, encoding='utf-8') as f:
        for line in f:
            try: runs.append(json.loads(line.strip()))
            except: pass
    return runs[-n:]


def run_pre_market():
    return run_task("盘前检查", r"D:\quant_framework\pre_market_check.py")

def run_signal_gen():
    return run_task("信号生成", r"D:\quant_web\generate_signal_table.py", cwd=r"D:\quant_web")

def run_limit_up():
    return run_task("板后预选池", r"D:\quant_framework\pre_cache_limit_up.py")

if __name__ == "__main__":
    import sys
    cmds = {"pre": run_pre_market, "signal": run_signal_gen, "limitup": run_limit_up}
    if len(sys.argv) > 1 and sys.argv[1] in cmds:
        cmds[sys.argv[1]]()
    else:
        print("用法: python task_runner.py pre|signal|limitup")
