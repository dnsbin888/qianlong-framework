lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()

# Find the broken section: line 12187 "try:" to line 12203 "print..." then restore
# Replace lines 12186-12203 with simple version
new = [
    '\ttry:\n',
    '\t\timport sys as _sy; _sy.path.insert(0, r"D:\\quant_framework")\n',
    '\t\tfrom qianlong import lock as _ql_lock\n',
    '\t\t_ql_lock()\n',
    '\t\tprint("[Startup] 核心文件已自动锁定")\n',
    '\texcept Exception as _le:\n',
    '\t\tprint(f"[Startup] 自动锁失败: {_le}")\n',
]
lines[12186:12204] = new
open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
print('Done')
