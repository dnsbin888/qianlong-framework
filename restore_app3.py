lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()

# Find the broken block
start = end = -1
for i, line in enumerate(lines):
    if 'import sys as _sy' in line and 'qianlong' not in line:
        start = i - 1
    if start > 0 and '自动锁失败' in line:
        end = i + 1
        break

if start > 0 and end > 0:
    # Use 8-space indentation (consistent with surrounding)
    new = [
        '        try:\n',
        '            import sys as _sy; _sy.path.insert(0, r"D:\\quant_framework")\n',
        '            from qianlong import lock as _ql_lock\n',
        '            _ql_lock()\n',
        '            print("[Startup] 核心文件已自动锁定")\n',
        '        except Exception as _le:\n',
        '            print(f"[Startup] 自动锁失败: {_le}")\n',
    ]
    lines[start:end] = new
    open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
    print('Done')
else:
    print(f'Not found: start={start} end={end}')
