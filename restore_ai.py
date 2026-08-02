import zipfile, os

target = r"D:\quant_web\static\js\ai-float.js"
backup_dir = r"D:\quant_web\backup"

# 遍历最近的备份, 找包含 ai-float.js 的
backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.zip')], reverse=True)
for b in backups:
    path = os.path.join(backup_dir, b)
    try:
        z = zipfile.ZipFile(path)
        names = z.namelist()
        for n in names:
            if 'ai-float' in n.lower():
                print(f"Found in: {b} -> {n}")
                z.extract(n, r"D:\quant_web\static\js")
                # 如果提取到子目录, 移动到正确位置
                extracted = os.path.join(r"D:\quant_web\static\js", n)
                if os.path.exists(extracted):
                    if n != 'ai-float.js':
                        import shutil
                        shutil.move(extracted, target)
                    print(f"Restored: ai-float.js")
                z.close()
                import sys; sys.exit(0)
        z.close()
    except: pass

print("NOT FOUND in any backup")
