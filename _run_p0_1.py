"""P0-1: Compile-check backtest_engine.py, write result to a file."""
import ast, sys, traceback
out_path = r"D:\quant_framework\_p0_1_result.txt"
try:
    with open(r"D:\quant_framework\backtest_engine.py", "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    with open(out_path, "w") as out:
        out.write("PASS\n")
except SyntaxError as e:
    with open(out_path, "w") as out:
        out.write(f"FAIL line {e.lineno}: {e.msg}\n{e.text}\n")
except Exception as e:
    with open(out_path, "w") as out:
        out.write(f"ERROR: {e}\n{traceback.format_exc()}\n")
