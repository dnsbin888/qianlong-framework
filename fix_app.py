lines = open(r'D:\quant_web\app.py', 'r', encoding='utf-8').readlines()
del lines[12186:12218]
fix = '''    try:
        import sys as _sy; _sy.path.insert(0, r"D:\\quant_framework")
        from qianlong import lock as _ql_lock
        _ql_lock()
        try:
            _lcfg = json.load(open(r"D:\\quant_framework\\live_trader_config.json", encoding="utf-8"))
            _ch = _lcfg.get("trading_channel", "ths")
            if _ch == "qmt" and not _lcfg.get("real_confirmed", False):
                print("[Startup] QMT实盘锁定 安全")
        except: pass
        try:
            _ps = json.load(open(r"D:\\quant_framework\\paper_account.json", encoding="utf-8"))
            print(f"[Startup] paper: {_ps.get('cash',0):,.0f}")
        except: pass
        print("[Startup] 核心文件已自动锁定")
    except Exception as _le:
        print(f"[Startup] 自动锁失败: {_le}")
'''
lines[12186:12186] = [x + '\n' for x in fix.strip().split('\n')]
open(r'D:\quant_web\app.py', 'w', encoding='utf-8').writelines(lines)
print('Done')
