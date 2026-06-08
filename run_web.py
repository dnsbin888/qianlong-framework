"""Flask — 托管 dashboard.html（含 iframe 嵌入 Streamlit）。

用法:
    cd /d d:\quant_framework
    python run_web.py
    浏览器访问: http://localhost:5002/dashboard

依赖:
    pip install flask
"""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from flask import Flask, render_template_string, send_from_directory

app = Flask(__name__,
            static_folder=os.path.join(PROJECT_DIR, 'static'),
            template_folder=os.path.join(PROJECT_DIR, 'templates'))

# ---- 仪表盘页面（iframe 嵌入 Streamlit）----
@app.route('/dashboard')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

# ---- 静态文件 ----
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ═══════════════════════════════════════════════════════════
#  Dashboard HTML — 完全复制主页顶栏+侧边栏风格
# ═══════════════════════════════════════════════════════════
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>策略回测 — 潜龙</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🐉</text></svg>">
<link rel="stylesheet" href="/static/css/style.css?v=3">
<style>
  .content {
    display: flex;
    flex-direction: column;
    height: calc(100vh - 44px);
    overflow: hidden;
  }
  .content iframe {
    flex: 1;
    width: 100%;
    border: none;
    background: #000;
  }
</style>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
</head>
<body>

<!-- ══════════════════ 顶栏（完全复制主页）═════════════════╗ -->
<header class="topbar">
  <div class="topbar-left">
    <span class="logo-text">🐉 潜龙</span>
    <nav class="topbar-nav" id="topNav">
      <a href="/">智能狙击大模型</a>
      <a href="/dashboard" class="active">策略回测</a>
    </nav>
  </div>
  <div class="topbar-right">
    <span class="clock" id="clock">--:--:--</span>
    <span style="color:#666;font-size:11px">| 通达信日线数据</span>
  </div>
</header>

<!-- ══════════════════ 主体容器 ══════════════════╗ -->
<div class="main-container">

  <!-- ── 侧边栏（完全复制主页风格，宽度 300px）─── -->
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-title">📊 策略配置</div>

    <div class="filter-group">
      <label class="filter-label">信号选择</label>
      <select class="filter-select" id="signalSelect" onchange="onSignalChange()">
        <option value="signal_final" selected>终极选股(XG+B1)</option>
        <option value="signal_resonance">双信号共振</option>
        <option value="signal_xg">涨停突破牛线</option>
        <option value="signal_b1">底部反转(B1)</option>
        <option value="all">全部股票</option>
      </select>
    </div>

    <div class="filter-group">
      <label class="filter-label">回测周期</label>
      <select class="filter-select" id="periodSelect">
        <option value="1m">近 1 个月</option>
        <option value="3m" selected>近 3 个月</option>
        <option value="6m">近 6 个月</option>
        <option value="1y">近 1 年</option>
        <option value="2y">近 2 年</option>
      </select>
    </div>

    <div class="filter-group">
      <label class="filter-label">初始资金</label>
      <select class="filter-select" id="capitalSelect">
        <option value="50000">¥50,000</option>
        <option value="100000" selected>¥100,000</option>
        <option value="200000">¥200,000</option>
        <option value="500000">¥500,000</option>
        <option value="1000000">¥1,000,000</option>
      </select>
    </div>

    <div class="sidebar-divider"></div>

    <div class="sidebar-title">📈 显示选项</div>
    <div class="filter-group">
      <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:12px;color:#409eff">
        <input type="checkbox" checked style="accent-color:#409eff">
        显示收益曲线
      </label>
    </div>
    <div class="filter-group">
      <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:12px;color:#409eff">
        <input type="checkbox" checked style="accent-color:#409eff">
        显示回撤曲线
      </label>
    </div>

    <div class="sidebar-divider"></div>

    <div class="sidebar-title">📉 绩效指标</div>
    <div class="sidebar-stats">
      <div class="stat-item"><span class="stat-label">总收益率</span><span class="stat-val up" id="statReturn">--</span></div>
      <div class="stat-item"><span class="stat-label">夏普比率</span><span class="stat-val" id="statSharpe">--</span></div>
      <div class="stat-item"><span class="stat-label">最大回撤</span><span class="stat-val down" id="statDrawdown">--</span></div>
      <div class="stat-item"><span class="stat-label">胜率</span><span class="stat-val" id="statWinRate">--</span></div>
    </div>

    <div class="sidebar-divider"></div>
    <button class="btn-refresh" onclick="refreshIframe()">🔄 刷新回测</button>
    <div class="sidebar-stats" style="margin-top:12px;border-top:1px solid #2a2c30;padding-top:8px;">
      <div class="stat-item"><span class="stat-label">更新时间</span><span class="stat-val" id="statTime" style="font-size:11px">--</span></div>
    </div>
  </aside>

  <!-- ── 主内容区：iframe 嵌入 Streamlit ── -->
  <main class="content">
    <iframe
      id="streamlitFrame"
      src="http://localhost:8501/"
      allow="clipboard-read; clipboard-write"
      loading="eager">
    </iframe>
  </main>
</div>

<!-- ══════════════════ JS ══════════════════╗ -->
<script>
/* ---- 时钟 ---- */
function updateClock(){
  var d=new Date(), p=function(s){return ('0'+s).slice(-2);};
  document.getElementById('clock').textContent=
    p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
updateClock(); setInterval(updateClock,1000);

/* ---- 顶栏导航高亮 ---- */
(function(){
  var nav=document.getElementById('topNav');
  if(!nav) return;
  nav.querySelectorAll('a').forEach(function(a){
    a.classList.toggle('active', a.getAttribute('href')===location.pathname);
  });
})();

/* ---- iframe 高度自适应 ---- */
function resizeIframe(){
  var f=document.getElementById('streamlitFrame');
  if(f) f.style.height=(window.innerHeight-44)+'px';
}
window.addEventListener('resize', resizeIframe);
resizeIframe();

/* ---- 刷新 iframe ---- */
function refreshIframe(){
  var f=document.getElementById('streamlitFrame');
  if(f){ f.src=f.src; }
  document.getElementById('statTime').textContent=
    new Date().toLocaleTimeString('zh-CN',{hour12:false});
}

/* ---- 占位回调 ---- */
function onSignalChange(){
  console.log('Signal:', document.getElementById('signalSelect').value);
}
</script>
</body>
</html>"""

if __name__ == '__main__':
    PORT = 5002
    print('🐉 潜龙 — 策略回测页面')
    print('   本地访问: http://localhost:{}/dashboard'.format(PORT))
    print('   请确保 Streamlit 已在 localhost:8501 运行')
    print()
    app.run(host='0.0.0.0', port=PORT, debug=False)
