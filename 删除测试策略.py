import os

d = r"D:\国金证券QMT交易端\python"
files = ["新建策略文件.py", "新建策略文件1.py", "新建策略文件2.py", "新建策略文件3.py"]

for f in files:
    p = os.path.join(d, f)
    if os.path.exists(p):
        os.remove(p)
        print(f"已删除: {f}")
    else:
        print(f"不存在: {f}")

print("完成")
input()
