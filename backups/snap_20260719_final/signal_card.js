/** signal_card.js — E349+E367: 实时信号卡片 (QMT+ML合并)
 *  数据来源: /api/signal-table (ML日线) + /api/qmt-signals (QMT盘中)
 *  sessionStorage: 筛选记忆 (刷新保留, 系统重启清空)
 */
var _sigCardData = [], _qmtSignals = [];
var _sigCardApproved = {};
// E372: 双重记忆 — sessionStorage(刷新) + 后端API(跨进程)
(function(){
  try{
    var _today = new Date().toISOString().slice(0,10);
    var _key = 'e372_approved_' + _today;
    var _saved = sessionStorage.getItem(_key);
    if(_saved){ _sigCardApproved = JSON.parse(_saved); }
    // 清除过期的sending状态 (页面刷新后旧请求已失效)
    for(var k in _sigCardApproved){ if(_sigCardApproved[k]==='sending') delete _sigCardApproved[k]; }
  }catch(e){}
  // 后端兜底
  fetch('/api/signal-state').then(function(r){return r.json()}).then(function(d){
    for(var k in d){ if(!_sigCardApproved[k]) _sigCardApproved[k] = d[k].state === 'approved' ? 'done' : 'rejected'; }
    try{renderSigCard()}catch(e){}
  }).catch(function(){});
})();
function _saveApproved(){
  try{
    var _today = new Date().toISOString().slice(0,10);
    sessionStorage.setItem('e372_approved_'+_today, JSON.stringify(_sigCardApproved));
  }catch(e){}
}

// E367: 三维筛选 (多选开关)
var _FILTERS = { type:{stock:true, etf:true}, src:{qmt:true, ml:true}, sig:{resonance:true, lgbm:true, diverge:true, blocked:true}, strat:'all' };
function sigStratChange(){ _FILTERS.strat=document.getElementById('sigStratFilter').value; renderSigCard(); }
var _FILTER_EL_MAP = {
  type: {stock:'sigTypeStock', etf:'sigTypeETF'},
  src: {qmt:'sigSrcQmt', ml:'sigSrcML'},
  sig: {resonance:'sigFilterRes', lgbm:'sigFilterLgbm', diverge:'sigFilterDiv', blocked:'sigFilterBlock'}
};

function _loadFilters(){
  try{ var s=sessionStorage.getItem('e367_filters'); if(s){ var d=JSON.parse(s); for(var g in d){ for(var k in d[g]){ _FILTERS[g][k]=d[g][k]; }}}} catch(e){}
  _FILTERS.sig.diverge = true;
  _FILTERS.sig.blocked = true;
}
function _saveFilters(){
  sessionStorage.setItem('e367_filters', JSON.stringify(_FILTERS));
}
function _applyFilterUI(){
  for(var g in _FILTER_EL_MAP){
    for(var k in _FILTER_EL_MAP[g]){
      var el=document.getElementById(_FILTER_EL_MAP[g][k]); if(!el) continue;
      if(_FILTERS[g][k]){ el.classList.add('on'); } else { el.classList.remove('on'); }
    }
  }
}
_loadFilters();

/** 筛选按钮点击 (三维多选, 每组至少一个选中) */
function sigCardToggle(group, val){
  _FILTERS[group][val] = !_FILTERS[group][val];
  var any=false; for(var k in _FILTERS[group]){ if(_FILTERS[group][k]) any=true; }
  if(!any){ _FILTERS[group][val]=true; return; }
  _saveFilters(); _applyFilterUI(); renderSigCard();
}

/** 页面启动 */
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(function(){ _applyFilterUI(); loadSignals(); }, 500);
  setInterval(loadSignals, 30000);
  // SSE: QMT实时信号秒级推送到浏览器
  try{
    var _evtSource=new EventSource('/api/stream');
    _evtSource.onmessage=function(e){
      try{ var d=JSON.parse(e.data);
        if(d.type==='qmt_signal'){ _qmtSignals.push(d); renderSigCard(); }
      }catch(ex){}
    };
  }catch(e){}
});

function loadSignals() {
  // 日线ML信号
  var ctrl = new AbortController();
  var tid = setTimeout(function() { ctrl.abort(); }, 10000);
  fetch('/api/signal-table', {signal: ctrl.signal}).then(function(r) { return r.json(); }).then(function(data) {
    clearTimeout(tid); _sigCardData = data || []; renderSigCard();
  }).catch(function() {
    clearTimeout(tid);
    var body = document.getElementById('sigCardBody');
    if (body && !_sigCardData.length && !_qmtSignals.length) {
      body.innerHTML = '<div style="text-align:center;color:var(--ql-gold);padding:20px;font-size:12px">⚠️ 信号加载失败<br><span style="font-size:10px;color:var(--ql-text-dim)">30s后重试</span></div>';
    }
  });
  // E367: QMT盘中信号
  fetch('/api/qmt-signals', {signal: (new AbortController()).signal}).then(function(r) { return r.json(); }).then(function(data) {
    _qmtSignals = data || []; renderSigCard();
  }).catch(function(){});
}

function isIndexSymbol(sym) {
  var s = (sym||'').toLowerCase();
  return s.indexOf('sh88')===0 || s.indexOf('sz88')===0 || s.indexOf('sh000')===0 || s.indexOf('sz399')===0;
}

function renderSigCard() {
  var body = document.getElementById('sigCardBody');
  var countEl = document.getElementById('sigCardCount');
  if (!body) return;

  var UP = '#FF4051', DN = '#00b96b', GOLD = '#F1A100', BLUE = '#409eff';
  var TYPE_MAP = {STOCK:'股', ETF:'ETF', BOND:'债', INDEX:'指'};

  // ═══ 构建合并列表: QMT信号(前) + ML信号(后) ═══
  var allSignals = [];
  for (var qi = 0; qi < _qmtSignals.length; qi++) {
    var q = _qmtSignals[qi];
    q._src = 'qmt';
    q._type = 'STOCK';
    q.lgbm_score = q.lgbm || 0;
    q.xgb_score = q.xgb || 0;
    q.cb_score = 0;
    q.combined_score = q.combined_score || (q.lgbm||0) || (q.xgb||0) || 0;
    q.change_pct = 0;
    q.signal = q.combined_score > 80 ? 'Lv5 强买' : q.combined_score > 60 ? 'Lv4 买入' : 'Lv3 关注';
    allSignals.push(q);
  }
  for (var mi = 0; mi < _sigCardData.length; mi++) {
    var m = _sigCardData[mi]; m._src = 'ml'; allSignals.push(m);
  }

  // ═══ 三维筛选 ═══
  var filtered = allSignals.filter(function(s){
    var okType = false;
    if ((s._type==='STOCK'||s.type==='STOCK') && _FILTERS.type.stock) okType = true;
    if ((s._type==='ETF'||s.type==='ETF') && _FILTERS.type.etf) okType = true;
    if (!okType) return false;
    if (s._src==='qmt' && !_FILTERS.src.qmt) return false;
    if (s._src==='ml' && !_FILTERS.src.ml) return false;
    if (s.type==='BOND'||s.type==='INDEX') return false;
    if (isIndexSymbol(s.symbol)) return false;
    if (s._src==='qmt') return true;
    var dec = s.decision||'';
    var anySig=false; for(var k in _FILTERS.sig){ if(_FILTERS.sig[k]) anySig=true; }
    if(!anySig) return true;
    if(_FILTERS.sig.resonance && /共振/.test(dec)) return true;
    if(_FILTERS.sig.lgbm && /LGBM/.test(dec)) return true;
    // XGB/分歧/拦截/无标签 默认放行
    if(/分歧|拦截|降级|超限|XGB/.test(dec)) return true;
    if(!dec||dec==='—'||dec==='-') return true;
    if(/🔄|🎯/.test(dec)) return true;  // 反转+打板 总是显示
    return false;
  });

  // 策略筛选
  if(_FILTERS.strat && _FILTERS.strat!=='all'){
    filtered=filtered.filter(function(s){
      var dec=s.decision||'';
      if(_FILTERS.strat==='qmt') return s._src==='qmt';
      if(_FILTERS.strat==='ml') return s._src==='ml' && (/ML|LGBM|XGB|Ridge|共振/.test(dec));
      if(_FILTERS.strat==='reversal') return /🔄/.test(dec);
      if(_FILTERS.strat==='pattern') return /🎯/.test(dec);
      return true;
    });
  }

  // 优先级排序: QMT实时 → 综合分
  filtered.sort(function(a,b){
    var aQMT=a._src==='qmt'?1:0, bQMT=b._src==='qmt'?1:0;
    if(aQMT!==bQMT) return bQMT-aQMT;
    return (b.combined_score||0)-(a.combined_score||0);
  });

  if (countEl) countEl.textContent = '· ' + filtered.length + '只';

  if (!filtered.length) {
    body.innerHTML = '<div style="text-align:center;color:var(--ql-text-dim);padding:20px;font-size:12px">📭 无匹配信号<br><span style="font-size:10px;color:var(--ql-text-faint)">尝试切换筛选条件</span></div>';
    return;
  }

  // QMT去重: 同股票同信号5分钟内只保留最新一条
  var _dedup = {}, _deduped = [];
  for (var di = 0; di < filtered.length; di++) {
    var dx = filtered[di];
    if (dx._src === 'qmt') {
      var dk = dx.symbol + '|' + (dx.signal_type || '');
      var dt = (dx.time || '').slice(0, 5);
      if (_dedup[dk] === dt) continue;
      _dedup[dk] = dt;
    }
    _deduped.push(dx);
  }
  filtered = _deduped;

  var html = '', lastSrc = '';
  for (var i = 0; i < filtered.length; i++) {
    var s = filtered[i];
    var isQMT = s._src === 'qmt';
    if (s._src !== lastSrc) {
      html += '<div style="font-size:8px;color:'+(isQMT?BLUE:'var(--ql-text-faint)')+';letter-spacing:1px;padding:2px 8px">'+(isQMT?'▎QMT 盘中实时':'▎日线 ML 评分')+'</div>';
      lastSrc = s._src;
    }

    var sym = s.symbol || '';
    var name = (s.name && s.name !== sym) ? s.name : '';
    // QMT缺名称时从本地缓存查
    if (!name && typeof _stockNames !== 'undefined' && _stockNames) {
      var lookup = sym.replace(/^(sh|sz|bj)/,'');
      name = _stockNames[lookup] || _stockNames[sym] || '';
    }
    var ind = s.industry || '';
    var nameFallback = name || ind || (s.type&&TYPE_MAP[s.type]!=='股'?TYPE_MAP[s.type]:sym.replace(/^(sh|sz|bj)/,''));
    if (nameFallback === sym) nameFallback = sym.replace(/^(sh|sz|bj)/,'');

    var chg = s.current_change_pct || s.change_pct || 0;
    var chgColor = chg > 0 ? UP : chg < 0 ? DN : '';
    var chgStr = (chg>0?'+':'')+chg.toFixed(2)+'%';
    if (isQMT) { var _qp=s.current_price||s.price||s.close||0; var _qc=s.current_change_pct; chgStr=(_qp>0?'¥'+_qp.toFixed(2):'—')+(_qc?' <span style=\"font-size:10px;color:'+(_qc>=0?UP:DN)+'\">'+(_qc>0?'+':'')+_qc.toFixed(2)+'%</span>':''); };

    // 行业冷暖
    var heatDot, heatBg;
    if (chg > 3)      { heatDot = '#FF4051'; heatBg = 'rgba(255,64,81,.15)'; }
    else if (chg > 1) { heatDot = '#FF6D3F'; heatBg = 'rgba(255,109,63,.12)'; }
    else if (chg > 0) { heatDot = '#F1A100'; heatBg = 'rgba(241,161,0,.10)'; }
    else if (chg > -1){ heatDot = '#888';    heatBg = 'rgba(128,128,128,.08)'; }
    else if (chg > -3){ heatDot = '#00b96b'; heatBg = 'rgba(0,185,107,.10)'; }
    else              { heatDot = '#00e676'; heatBg = 'rgba(0,230,118,.15)'; }
    if (!ind)         { heatDot = '#555';    heatBg = 'transparent'; }
    var indTag = ind ?
      '<span style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 6px;border-radius:3px;background:'+heatBg+';color:'+heatDot+';font-weight:600;white-space:nowrap;flex-shrink:0">'+
        '<span style="width:6px;height:6px;border-radius:50%;background:'+heatDot+';display:inline-block;flex-shrink:0"></span>'+ind+'</span>' : '';

    // 共识点 (L●X●C● = 三模型)
    var lRaw = (s.lgbm_score && s.lgbm_score > 0) ? s.lgbm_score : 0;
    var xRaw = (s.xgb_score && s.xgb_score > 0) ? s.xgb_score : 0;
    var cRaw = (s.cb_score && s.cb_score > 0) ? s.cb_score : 0;
    var rRaw = (s.ridge_score && s.ridge_score > 0) ? s.ridge_score : 0;
    var bothML = lRaw >= 15 && xRaw >= 15;
    var lValid = lRaw >= 15, xValid = xRaw >= 15, cValid = cRaw >= 20, rValid = rRaw >= 15;
    var ridgeHtml = (rRaw > 0) ? '<span style="color:#e040fb;font-weight:600;font-size:10px">'+rRaw.toFixed(0)+'</span>' : '<span style="color:#333">--</span>';
    var dots = (lValid?'<span style="color:'+BLUE+';font-weight:600;font-size:10px">L'+lRaw.toFixed(0)+'</span> ':'')+
      (xValid?'<span style="color:'+GOLD+';font-weight:600;font-size:10px">X'+xRaw.toFixed(0)+'</span> ':'');

    // 决策标签 (精简版)
    var dec = s.decision || '';
    var decStyle = 'font-size:9px;padding:1px 5px;border-radius:2px;font-weight:600';
    if (/共振/.test(dec)) decStyle += ';background:rgba(0,230,118,.12);color:#00b96b';
    else if (/分歧/.test(dec)) decStyle += ';background:rgba(241,161,0,.12);color:'+GOLD;
    else if (/拦截|超限/.test(dec)) decStyle += ';background:rgba(255,64,81,.1);color:'+UP;
    else if (/降级/.test(dec)) decStyle += ';background:rgba(128,128,128,.1);color:#888';
    else decStyle += ';background:rgba(64,158,255,.1);color:'+BLUE;
    var decShort = '';
    if (/共振/.test(dec)) decShort = '共振';
    else if (bothML) decShort = '';

    var sl = s.stop_loss ? '止'+(s.stop_loss||0).toFixed(1) : '';
    var tp = s.take_profit ? '盈'+(s.take_profit||0).toFixed(1) : '';
    var exitInfo = (sl&&tp)?sl+' '+tp:(sl||tp);
    var approved = _sigCardApproved[sym];

    // 综合评分
    var cs = s.combined_score || 0;
    var scColor = cs>80?GOLD:cs>60?'#e3e3e3':'#888';
    var lvNum = s.signal?(s.signal.match(/Lv(\d)/)||[])[1]:0;
    if(!lvNum) lvNum=cs>90?5:cs>80?4:cs>60?3:cs>40?2:1;
    var lvLabel = lvNum>=5?'L5🔥':'L'+lvNum;
    var lvColor = lvNum>=5?GOLD:lvNum>=4?'#e3e3e3':lvNum>=3?'#888':'#666';

    // QMT专有
    var qmtMLok = isQMT && ((s.lgbm||0)>=60 || (s.xgb||0)>=60);
    var rowOpacity = (isQMT&&!qmtMLok)?';opacity:0.55':'';
    var tagHtml, tagStyle;
    if(isQMT){
      tagHtml='📡 '+(s.signal_type||'QMT信号');
      tagStyle='font-size:10px;padding:2px 6px;border-radius:2px;font-weight:600;background:rgba(64,158,255,.15);color:'+BLUE;
    }else{
      if(decShort) { tagHtml=decShort; tagStyle=decStyle; }
      else { tagHtml=''; tagStyle=''; }
    }

    // 按钮
    var btnHtml;
    if(s.auto_enabled){
      btnHtml='<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid #00b96b;background:rgba(0,230,118,.1);color:#00b96b;font-weight:600">⚡自动</span>';
    }else if(approved==='done'){
      btnHtml='<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid #00b96b;background:rgba(0,230,118,.1);color:#00b96b;font-weight:600">✓ 已批</span>';
    }else if(approved==='sending'){
      btnHtml='<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid '+GOLD+';background:rgba(241,161,0,.1);color:'+GOLD+';font-weight:600">⏳ 提交中</span>';
    }else if(isQMT&&qmtMLok){
      btnHtml='<button onclick="sigCardApprove(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid '+BLUE+';background:'+BLUE+';color:#fff;font-weight:600;font-family:inherit">批准实盘</button>'+
              '<button onclick="sigCardReject(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid var(--ql-border-input);background:transparent;color:var(--ql-text-dim);font-family:inherit;margin-left:3px">忽略</button>';
    }else if(isQMT&&!qmtMLok){
      btnHtml='<button onclick="sigCardReject(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid var(--ql-border-input);background:transparent;color:var(--ql-text-dim);font-family:inherit">忽略</button>';
    }else if((s.position_pct||0)>0){
      btnHtml='<button onclick="sigCardApprove(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid '+BLUE+';background:rgba(64,158,255,.12);color:'+BLUE+';font-weight:600;font-family:inherit">批准</button>'+
              '<button onclick="sigCardReject(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid var(--ql-border-input);background:transparent;color:var(--ql-text-dim);font-family:inherit;margin-left:3px">拒绝</button>';
    }else{
      btnHtml='<button onclick="sigCardReject(\''+sym+'\')" style="font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid var(--ql-border-input);background:transparent;color:var(--ql-text-dim);font-family:inherit">忽略</button>';
    }

    var qmtTime=isQMT?(s.time||''):'';
    var timeStr=qmtTime?'<span style="font-size:11px;font-weight:700;color:'+BLUE+';font-family:var(--ql-font-mono);min-width:40px">'+qmtTime.slice(0,5)+'</span>':'';
    var qmtBorder=isQMT?'border-left:3px solid '+BLUE+';':'';

    // 策略标签
    var stratTag='';
    if(isQMT) stratTag='<span style="font-size:8px;padding:0 5px;border-radius:6px;font-weight:700;background:rgba(0,185,107,.12);color:#00b96b;line-height:16px;display:inline-block">QMT</span>';
    else if(/🔄/.test(dec)) stratTag='<span style="font-size:8px;padding:0 5px;border-radius:6px;font-weight:700;background:rgba(210,153,34,.12);color:#d29922;line-height:16px;display:inline-block">反转</span>';
    else if(/🎯/.test(dec)) stratTag='<span style="font-size:8px;padding:0 5px;border-radius:6px;font-weight:700;background:rgba(248,81,73,.12);color:#f85149;line-height:16px;display:inline-block">打板</span>';
    else stratTag='<span style="font-size:8px;padding:0 5px;border-radius:6px;font-weight:700;background:rgba(88,166,255,.12);color:#58a6ff;line-height:16px;display:inline-block">ML</span>';

    html+='<div class="sig-card-row" style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-bottom:1px solid rgba(255,255,255,.03);'+qmtBorder+';cursor:pointer;font-size:13px'+rowOpacity+'">'+
      timeStr+stratTag+
      '<span style="font-size:17px;font-weight:700;min-width:30px;text-align:right;color:'+(cs>0?scColor:'var(--ql-text-faint)')+';font-family:Consolas,monospace">'+(cs>0?cs.toFixed(0):'—')+'</span>'+
      '<span style="font-weight:700;color:var(--ql-text-primary);font-family:Consolas,monospace;min-width:80px;font-size:13px">'+sym+'</span>'+
      '<span style="color:var(--ql-text-dim);min-width:60px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">'+nameFallback+'</span>'+
      '<span style="font-size:11px;font-weight:600;min-width:26px;text-align:center;color:'+lvColor+'">'+lvLabel+'</span>'+
      '<span style="font-weight:600;min-width:56px;text-align:right;font-size:12px;color:'+chgColor+'">'+chgStr+'</span>'+
      indTag+
      (tagHtml?'<span style="'+tagStyle+'">'+tagHtml+'</span>':'')+
      (isQMT?(qmtMLok?'<span style="font-size:10px;color:#00b96b;font-weight:600">ML✓</span>':'<span style="font-size:10px;color:'+UP+';font-weight:600">ML✗</span>'):dots)+
      ((s.position_pct||0)>0?'<span style="font-size:11px;font-weight:700;color:'+BLUE+'">'+(s.position_pct||0).toFixed(0)+'%</span> ':'')+
      '<span style="flex:1"></span>'+
      btnHtml+
    '</div>';
  }

  body.innerHTML = html;
  var rows = body.querySelectorAll('.sig-card-row');
  for (var j = 0; j < rows.length; j++) {
    rows[j].addEventListener('mouseenter', function() { this.style.background = 'rgba(64,158,255,.04)'; });
    rows[j].addEventListener('mouseleave', function() { this.style.background = ''; });
  }
}

function sigCardApprove(sym) {
  var s = null, src = 'ml';
  for (var i = 0; i < _sigCardData.length; i++) { if (_sigCardData[i].symbol === sym) { s = _sigCardData[i]; break; } }
  if (!s) { for (var j = 0; j < _qmtSignals.length; j++) { if (_qmtSignals[j].symbol === sym) { s = _qmtSignals[j]; src = 'qmt'; break; } } }
  if (!s) return;
  // E368: 实盘二次确认
  if (src === 'qmt') {
    var pos = s.position_pct||0;
    if (!confirm('⚠️ 实盘买入确认\n\n'+sym+' '+((s.name&&s.name!==sym)?s.name:'')+'\n价格 ¥'+(s.close||0).toFixed(2)+'\n仓位 '+pos.toFixed(0)+'%\n\n确认提交实盘订单？')) return;
  }
  _sigCardApproved[sym] = 'sending'; _saveApproved(); renderSigCard();
  // 5秒超时保护 + AbortController
  var ctrl = new AbortController();
  var timer = setTimeout(function(){ctrl.abort();}, 5000);
  fetch('/api/signal/approve', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({symbol:sym, position_pct:s.position_pct||0, close:s.close||0, decision:s.decision||'', source:src, signal_id:s.signal_id||''}),
    signal: ctrl.signal
  }).then(function(r){clearTimeout(timer);return r.json();}).then(function(d){
    _sigCardApproved[sym] = d.code===200?'done':false; renderSigCard();
    if(d.code!==200){var err=(d.order&&d.order.error)?d.order.error:(d.error||'未知错误');alert('下单失败: '+err);}
  }).catch(function(e){clearTimeout(timer);_sigCardApproved[sym]=false;renderSigCard();if(e.name!=='AbortError'){alert('网络错误');}});
}

function sigCardReject(sym) {
  _sigCardData = _sigCardData.filter(function(item){return item.symbol!==sym});
  _qmtSignals = _qmtSignals.filter(function(item){return item.symbol!==sym});
  _sigCardApproved[sym] = 'rejected'; _saveApproved();
  renderSigCard();
  fetch('/api/signal/reject', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym,decision:''})}).catch(function(){});
}
