lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()

# Find the broken try/except block by content
start = end = -1
for i, line in enumerate(lines):
    if 'import sys as _sy' in line and 'qianlong' not in line:
        start = i - 1  # the try: line
    if start > 0 and '自动锁失败' in line:
        end = i + 1
        break

if start > 0 and end > 0:
    print(f'Found broken block: lines {start+1}-{end}')
    new = [
        '\t\ttry:\n',
        '\t\t\timport sys as _sy; _sy.path.insert(0, r"D:\\quant_framework")\n',
        '\t\t\tfrom qianlong import lock as _ql_lock\n',
        '\t\t\t_ql_lock()\n',
        '\t\t\tprint("[Startup] 核心文件已自动锁定")\n',
        '\t\texcept Exception as _le:\n',
        '\t\t\tprint(f"[Startup] 自动锁失败: {_le}")\n',
    ]
    lines[start:end] = new
    open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
    print('Done')
else:
    print(f'Not found: start={start} end={end}')
