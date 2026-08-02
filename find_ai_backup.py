import zipfile, os

backup_dir = r"D:\quant_web\backup"
# Check earliest backups
for b in sorted(os.listdir(backup_dir)):
    if not b.endswith('.zip'): continue
    path = os.path.join(backup_dir, b)
    try:
        z = zipfile.ZipFile(path)
        names = z.namelist()
        for n in names:
            if 'ai' in n.lower() or 'float' in n.lower():
                print(f"FOUND in {b}: {n}")
                break
        z.close()
    except: pass
print("Done searching")
