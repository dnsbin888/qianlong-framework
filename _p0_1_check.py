"""P0-1: 检查 backtest_engine.py 语法错误"""
import ast, sys
path = r"D:\quant_framework\backtest_engine.py"
try:
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()
    ast.parse(source)
    print("✅ PASS: backtest_engine.py 编译通过")
except SyntaxError as e:
    print(f"❌ FAIL line {e.lineno}: {e.msg}")
    print(f"   代码: {e.text}")
    sys.exit(1)
