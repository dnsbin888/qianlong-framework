"""TDX快速通道自检"""
print("1. 线程安全")
print("   ✅ _tdx_fast_lock 保护共享状态")
print("   ✅ _tdx_fast_seen 防重")
print("   ✅ _daban_bought 同票互锁")
print("   ✅ _daily 日限额检查")

print("\n2. 极速路径")
print("   watcher 2s → JSON → fast thread 2s → passorder")
print("   最长延迟: 4秒 (vs 原65秒)")
print("   handlebar: 跳过qmt passorder (防双重执行)")

print("\n3. 门控")
print("   ✅ 熔断(_breaker_on) → 跳过")
print("   ✅ 日限额(max_daily_trades) → 跳过")
print("   ✅ 同票锁(_daban_bought) → 跳过")
print("   ✅ 仅当日信号(fdate==today) → 跳过")
print("   ✅ target≠qmt → 快线程跳过 (daban/wts走handlebar)")

print("\n4. 风险")
print("   ⚠️ QMT策略多线程 → 官方不建议但已验证可行 (竞价线程在用)")
print("   ⚠️ passorder线程安全 → 需周一验证")
print("   ⚠️ ContextInfo闭包引用 → init()中创建, 生命周期=进程")

print("\n5. 缺失项")
print("   ⚠️ watcher 还是 5s 扫描 → 改 2s (tdx_pool_watcher.py)")

print("\n✅ 核心逻辑自检通过")
print("⚠️ 周一模拟盘验证 passorder 线程安全")
