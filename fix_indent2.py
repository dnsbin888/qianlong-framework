lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()

# Find the try at line 12187 (0-indexed 12186)
try_line = lines[12186]
# Get its indentation (leading whitespace)
indent = try_line[:len(try_line) - len(try_line.lstrip())]
print(f"try indent: {repr(indent)}")

# Fix lines 12201-12202 (0-indexed)
lines[12201] = indent + 'except Exception as _le:\n'
lines[12202] = indent + '    print(f"[Startup] 自动锁失败: {_le}")\n'

open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
print('Done')
