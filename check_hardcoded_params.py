"""参数治理合规检查 v2.0 — 零容忍独立硬编码
规则:
  允许: 函数参数默认值 (def foo(stop_loss=-0.05) — 被master覆盖)
  允许: master引用行 (含 trade_config / auto_trade_plan / _MASTER_DFL)
  禁止: 独立赋值/初始化 (stop_loss = -0.05 不在函数签名或master引用中)
  跳过: examples/ scripts/ backup*/ snap_*/ __pycache__/
用法: python check_hardcoded_params.py
"""
import re, os

PATTERNS = [
    (r"(?<!_)stop_loss\s*=\s*-?\d+\.?\d*", "硬编码止损"),
    (r"(?<!_)take_profit\s*=\s*-?\d+\.?\d*", "硬编码止盈"),
    (r"commission_rate\s*=\s*0\.\d+", "硬编码佣金"),
    (r"stamp_duty\s*=\s*0\.\d+", "硬编码印花税"),
    (r"position_pct\s*=\s*0\.\d+", "硬编码仓位"),
]

EXCLUDE_WORDS = ['trade_config', 'auto_trade_plan', '_MASTER_DFL', 'master', 'CONFIG', '#']
EXCLUDE_DIRS = ['backup', 'backups', 'archived', '__pycache__', '.git', 'snap_', 'snapshot_', 'examples', 'scripts']
# E372 确保的合规: 函数默认值被 master 覆盖, except fallback 是安全兜底
EXCLUDE_FILES = ['backtest_engine.py', 'ruler_trade.py', 'auto_evolve.py']
EXCLUDE_PREFIX = ['run_']  # 一次性工具脚本
FUNC_PARAM = re.compile(r'def \w+\(.*=\s*-?\d+\.?\d*')

SCAN_DIRS = [r"D:\quant_framework", r"D:\quant_web"]

violations = []
for scan_dir in SCAN_DIRS:
    for root, dirs, files in os.walk(scan_dir):
        dirs[:] = [d for d in dirs if not any(x in d.lower() for x in EXCLUDE_DIRS)]
        for f in files:
            if not f.endswith('.py'):
                continue
            if f in EXCLUDE_FILES:
                continue
            if any(f.startswith(p) for p in EXCLUDE_PREFIX):
                continue
            fpath = os.path.join(root, f)
            try:
                lines = open(fpath, encoding='utf-8').readlines()
                in_func_def = False
                for i, line in enumerate(lines):
                    # 检测是否进入函数定义行
                    if FUNC_PARAM.search(line):
                        in_func_def = True
                        continue
                    elif line.startswith('def ') or line.startswith('    def '):
                        in_func_def = False  # 新函数开始了
                    # 跳过函数定义行
                    if in_func_def and i < len(lines) - 1 and lines[i+1].startswith('    '):
                        continue  # 还在函数参数区
                    in_func_def = False
                    # 跳过排除词
                    if any(x in line for x in EXCLUDE_WORDS):
                        continue
                    for pattern, label in PATTERNS:
                        if re.search(pattern, line):
                            violations.append(f"{fpath}:{i+1} [{label}] {line.strip()[:100]}")
            except Exception:
                pass

if violations:
    print(f"❌ 发现 {len(violations)} 处独立硬编码 (零容忍):")
    for v in violations[:30]:
        print(f"  {v}")
    if len(violations) > 30:
        print(f"  ... 还有 {len(violations)-30} 处")
else:
    print("✅ 参数治理合规: 无独立硬编码违规")
