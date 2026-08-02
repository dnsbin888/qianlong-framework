"""前端信号推送诊断"""
import sys, json
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')

print("="*60)
print("  前端信号推送诊断")
print("="*60)

# 1. Flask是否在运行
print("\n[1] Flask服务")
import urllib.request
try:
    r = urllib.request.urlopen("http://localhost:5002/api/ping", timeout=3)
    print(f"  ✅ 5002端口在线: {r.status}")
except Exception as e:
    print(f"  ❌ 5002端口不通: {e}")

# 2. API接口测试
print("\n[2] API数据")
try:
    # signal_table
    r = urllib.request.urlopen("http://localhost:5002/api/signal-table", timeout=3)
    data = json.loads(r.read())
    print(f"  ✅ /api/signal-table: {len(data)}条")
    if data:
        s0 = data[0]
        print(f"    首条: {s0.get('symbol')} score={s0.get('combined_score')} q={s0.get('quality_score')}")
except Exception as e:
    print(f"  ❌ /api/signal-table: {e}")

try:
    # qmt_signals
    r = urllib.request.urlopen("http://localhost:5002/api/qmt-signals", timeout=3)
    data = json.loads(r.read())
    print(f"  ✅ /api/qmt-signals: {len(data) if isinstance(data,list) else 'obj'}条")
except Exception as e:
    print(f"  ⚠️ /api/qmt-signals: {e} (可能无QMT信号)")

# 3. signal_card.js 语法检查
print("\n[3] signal_card.js 语法")
try:
    js = open(r"D:\quant_web\static\js\signal_card.js", encoding='utf-8').read()
    # 简单检查: 括号平衡
    opens = js.count('{')
    closes = js.count('}')
    parens_ok = opens == closes
    print(f"  {{}} 配对: {'✅' if parens_ok else '❌'} ({opens} vs {closes})")
    opens2 = js.count('(')
    closes2 = js.count(')')
    parens2_ok = opens2 == closes2
    print(f"  () 配对: {'✅' if parens2_ok else '❌'} ({opens2} vs {closes2})")
    # 检查关键变量
    for v in ['qsHtml','ridgeHtml','hasR','rValid']:
        count = js.count(v)
        print(f"  {v}: {'✅ 已定义' if count>0 else '❌ 未定义'} ({count}次)")
except Exception as e:
    print(f"  ❌ 文件读取: {e}")

# 4. terminal.html 引用检查
print("\n[4] terminal.html 引用")
try:
    tpl = open(r"D:\quant_web\templates\terminal.html", encoding='utf-8').read()
    scripts = ['signal_card.js', 'terminal_v2.js', 'nav_bar.js', 'plotly.min.js']
    for s in scripts:
        present = s in tpl
        print(f"  {s}: {'✅' if present else '❌ 缺失'}")
except Exception as e:
    print(f"  ❌ {e}")

print("\n" + "="*60)
print("  如果以上都正常, 打开 http://localhost:5002/terminal")
print("  按F12→Console, 看红色报错, 贴给我")
print("="*60)
