lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()
# Line 12202 (0-indexed 12201) - strip leading whitespace and add 0 indent
lines[12201] = 'except Exception as _le:\n'
lines[12202] = '    print(f"[Startup] 自动锁失败: {_le}")\n'
open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
print('Done')
