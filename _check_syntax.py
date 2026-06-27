"""一键语法检查 — 扫描 quant_framework 所有 .py 文件"""
import py_compile, os, glob

paths = [
    r"D:\quant_framework\src\quant_framework\risk",
    r"D:\quant_framework\src\quant_framework\strategy",
    r"D:\quant_framework\src\quant_framework\analysis",
    r"D:\quant_framework\src\quant_framework\execution",
    r"D:\quant_framework\src\quant_framework\live",
    r"D:\quant_framework\src\quant_framework\engine",
    r"D:\quant_framework\src\quant_framework\data",
    r"D:\quant_framework\src\quant_framework\core",
]

errors = []
for path in paths:
    if not os.path.exists(path):
        continue
    for f in glob.glob(os.path.join(path, "*.py")):
        try:
            py_compile.compile(f, doraise=True)
        except SyntaxError as e:
            print(f"❌ {f}: line {e.lineno}: {e.msg}")
            errors.append((f, e.lineno, e.msg))

if errors:
    print(f"\n⚠️ {len(errors)} 个文件有语法错误")
else:
    print("\n✅ 所有模块语法检查通过")
