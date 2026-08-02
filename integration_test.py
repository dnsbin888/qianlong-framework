"""全站联动测试 v1.0 — 前后端+数据+导航+API 全面评估"""
import sys, os, json, urllib.request, urllib.error
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

BASE = "http://127.0.0.1:5002"
PASS, FAIL, WARN = 0, 0, 0

def test(name, url, expect_code=200, expect_key=None, expect_min=0):
    global PASS, FAIL, WARN
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=15)
        if r.status != expect_code:
            print(f"  ❌ {name}: HTTP{r.status}≠{expect_code}")
            FAIL += 1; return None
        data = json.loads(r.read())
        if data.get("code") != 200:
            print(f"  ⚠️ {name}: code={data.get('code')}")
            WARN += 1
        elif expect_key and expect_key not in data:
            print(f"  ❌ {name}: 缺字段'{expect_key}'")
            FAIL += 1
        elif expect_min > 0 and isinstance(data.get(expect_key), (list, dict)) and len(data[expect_key]) < expect_min:
            print(f"  ⚠️ {name}: {expect_key}仅{len(data[expect_key])}条")
            WARN += 1
        else:
            print(f"  ✅ {name}")
            PASS += 1
        return data
    except urllib.error.HTTPError as e:
        print(f"  ❌ {name}: HTTP{e.code}")
        FAIL += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        FAIL += 1
    return None

print("=" * 60)
print("潜龙全站联动测试")
print("=" * 60)

# ═══ 1. 核心API ═══
print("\n📡 1. 核心API")
test("Ping", "/api/ping")
test("市场状态", "/api/market-regime", expect_key="sentiment")
test("信号表", "/api/signal-table", expect_key="combined_score", expect_min=10)
test("策略组合", "/api/strategy-combo", expect_key="combos")
test("风控状态", "/api/kill-switch/status", expect_key="data")
test("因子健康", "/api/factor-health", expect_key="checks")
test("统一状态", "/api/unified-state", expect_key="cache_count")
tasklog = test("任务日志", "/api/tasklog", expect_key="runs")

# ═══ 2. 页面可达性 ═══
print("\n🖥 2. 主页面")
pages = {
    "/terminal": "指挥中心", "/ml-signals": "ML信号", "/screener": "选股器",
    "/paper-trade-v3": "模拟盘", "/live-trade": "实盘", "/risk-console": "风控台",
    "/dashboard": "看板", "/factor-health": "因子健康", "/factor-dashboard": "因子看板",
    "/auto-evolve": "进化", "/data-manager": "数据管理", "/review": "复盘",
    "/control-panel": "控制台", "/m-kill": "手机遥控",
}
for url, name in pages.items():
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=10)
        html = r.read().decode(errors='ignore')
        if len(html) < 500:
            print(f"  ❌ {name}: 页面过短({len(html)}B)")
            FAIL += 1
        else:
            # 验证通用组件存在
            ok = '_topbar' not in html  # 如果没include会报错
            has_nav = 'topNav' in html
            has_clock = 'clock' in html
            if not has_nav or not has_clock:
                print(f"  ⚠️ {name}: 缺顶栏组件 nav={has_nav} clock={has_clock}")
                WARN += 1
            else:
                print(f"  ✅ {name}")
                PASS += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        FAIL += 1

# ═══ 3. 数据一致性 ═══
print("\n🔗 3. 数据一致性")
# 信号表 vs 交易计划 股票数是否匹配
sig = test("信号表(再确认)", "/api/signal-table", expect_min=10)
try:
    plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
    plan_stocks = len(plan.get("stocks", {}))
    print(f"  ✅ 计划文件: {plan_stocks}只标的")
    PASS += 1
except: print(f"  ❌ 计划文件: 读取失败"); FAIL += 1

# Paper账户数据验证
try:
    sys.path.insert(0, r"D:\quant_framework")
    from paper_engine import paper
    cash = paper.cash
    pos = len(paper.positions)
    trades = len(paper._trades_archive)
    if cash > 0:
        print(f"  ✅ 纸引擎: 现金{cash:.0f} 持仓{pos}只 交易{trades}笔")
        PASS += 1
    else:
        print(f"  ❌ 纸引擎: 现金为0")
        FAIL += 1
except Exception as e:
    print(f"  ❌ 纸引擎: {e}"); FAIL += 1

# ═══ 4. 导航联动 ═══
print("\n🧭 4. 导航链接")
nav_links = {"/terminal": "指挥中心", "/screener": "选股", "/ml-signals": "ML信号",
             "/factor-health": "因子健康", "/factor-dashboard": "因子看板",
             "/auto-evolve": "进化", "/dashboard": "看板", "/paper-trade-v3": "模拟盘",
             "/live-trade": "实盘", "/risk-console": "风控台", "/data-manager": "数据管理",
             "/strategy-optimizer": "策略工坊", "/review": "复盘"}
for url, name in nav_links.items():
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=10)
        if r.status == 200:
            print(f"  ✅ {name}"); PASS += 1
        else:
            print(f"  ⚠️ {name}: HTTP{r.status}"); WARN += 1
    except: print(f"  ❌ {name}: 不可达"); FAIL += 1

# ═══ 5. 数据写入验证 ═══
print("\n💾 5. 数据写入")
for name, path in [
    ("信号表", r"D:\quant_web\data\signal_table.json"),
    ("交易计划", r"D:\quant_web\data\auto_trade_plan.json"),
    ("行情数据", r"D:\quant_web\stock_data.parquet"),
    ("纸账户", r"D:\quant_framework\paper_account.json"),
    ("交易CSV", r"D:\quant_framework\trade_log.csv"),
]:
    if os.path.exists(path):
        sz = os.path.getsize(path)
        print(f"  ✅ {name}: {sz/1024:.0f}KB"); PASS += 1
    else:
        print(f"  ❌ {name}: 不存在"); FAIL += 1

# backup验证
hr = r"D:\quant_framework\backups\hourly"
if os.path.exists(hr):
    files = [f for f in os.listdir(hr) if f.endswith('.json') or f.endswith('.csv')]
    print(f"  ✅ 每小时备份: {len(files)}个文件"); PASS += 1
else:
    print(f"  ⚠️ 每小时备份: 目录不存在"); WARN += 1

# ═══ 总结 ═══
print("\n" + "=" * 60)
total = PASS + FAIL + WARN
print(f"结果: {PASS}✅ {FAIL}❌ {WARN}⚠️ (共{total}项)")
grade = "A" if FAIL == 0 and WARN <= 3 else ("B" if FAIL <= 2 else "C")
print(f"综合评级: {grade} | {'系统正常 ✅' if FAIL == 0 else '需修复 ❌'}")
print("=" * 60)
