"""QMT 连通性测试 — 独立脚本"""
import sys
import os

QMT_PYTHON = r"D:\国金证券QMT交易端\bin.x64\Lib\site-packages"

print("=" * 50)
print("QMT 连通性测试")
print("=" * 50)

# 1. 进程检查
print("\n[1/3] XtMiniQmt.exe 进程...")
try:
    import subprocess
    out = subprocess.check_output(
        'tasklist /FI "IMAGENAME eq XtMiniQmt.exe"',
        shell=True, text=True, timeout=10
    )
    if "XtMiniQmt.exe" in out:
        print("  OK 进程正在运行")
    else:
        print("  FAIL 进程未运行")
except Exception as e:
    print(f"  FAIL 无法检查: {e}")

# 2. xtquant 包
print("\n[2/3] xtquant 包...")
if QMT_PYTHON not in sys.path:
    sys.path.insert(0, QMT_PYTHON)
try:
    import xtquant
    ver = getattr(xtquant, "__version__", "?")
    print(f"  OK xtquant v{ver}")
    print(f"  路径: {xtquant.__file__}")
except ImportError as e:
    print(f"  FAIL 导入失败: {e}")
    print(f"  检查路径: {QMT_PYTHON}")
    print(f"  路径存在: {os.path.exists(QMT_PYTHON)}")

# 3. session 连接
print("\n[3/3] QMT session 连接...")
try:
    from xtquant import xtdata
    xtdata.connect()
    ip = xtdata.get_server_ip()
    print(f"  OK xtdata.connect() 成功")
    print(f"  服务器IP: {ip}")
    print(f"  Session: 有效")
except Exception as e:
    print(f"  FAIL 连接失败: {e}")

print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)
