"""临时：检查 realtime_quotes.py 语法"""
import sys
sys.path.insert(0, r"D:\quant_framework")

try:
    with open(r"D:\quant_framework\realtime_quotes.py", "r", encoding="utf-8") as f:
        lines = f.readlines()
    # 显示 119-130 行
    for i in range(118, min(135, len(lines))):
        print(f"{i+1:4d}: {lines[i].rstrip()}")
    print("\n--- compiling ---")
    compile(open(r"D:\quant_framework\realtime_quotes.py").read(), "realtime_quotes.py", "exec")
    print("OK: no syntax error")
except SyntaxError as e:
    print(f"SyntaxError: {e}")
    print(f"  line={e.lineno}, offset={e.offset}, text={e.text}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
