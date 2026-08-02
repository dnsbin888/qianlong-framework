# Fix line 12202 indentation: should be 8 spaces (same level as try at 12191)
lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()
# Line 12202 is index 12201
old = lines[12201]
lines[12201] = '        except Exception as _le:\n'  # 8 spaces
lines[12202] = '            print(f"[Startup] 自动锁失败: {_le}")\n'
open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
print("Done - indentation fixed")
