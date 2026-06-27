"""P0-1: Write syntax check result to a file that can be read."""
import ast, sys, os
target = r"D:\quant_framework\backtest_engine.py"
out = r"D:\quant_framework\_p0_1_result.txt"
try:
    with open(target, "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    with open(out, "w", encoding="utf-8") as f:
        f.write("PASS\n")
    print("PASS: backtest_engine.py compiles OK")
except SyntaxError as e:
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"FAIL line {e.lineno} col {e.offset}: {e.msg}\n")
        if e.text:
            f.write(f"  code: {e.text.rstrip()}\n")
            f.write(f"  mark: {' ' * (e.offset - 1) if e.offset else ''}^\n")
    print(f"FAIL line {e.lineno}: {e.msg}")
except Exception as ex:
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"ERROR: {ex}\n")
    print(f"ERROR: {ex}")
