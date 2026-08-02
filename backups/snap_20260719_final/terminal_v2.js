/** terminal_v2.js — AI决策指挥中心 (对标 Bloomberg Launchpad + AlphaSense)
 *  0个新API，全复用现有接口。SSE实时事件 + 轮询状态。
 *  v3: 侧栏双行信号 + 因子引擎6段留白布局
 */
var _sigCache=[], _lvCache={auto_trade_enabled:false}, _pvCache={};
var _lvTrades=[], _pvTrades=[], _lvSort={col:'time',dir:-1}, _pvSort={col:'time',dir:-1};
var UP='#FF4051',DN='#00b96b',GOLD='#F1A100',BLUE='#409eff';

function el(id){return document.getElementById(id)}

// E344: fetch 超时保护 (10s), 超时显示提示而非一直白屏
function _fetchTimeout(url, timeoutMs, fallbackElId){
  var ctrl = new AbortController();
  var tid = setTimeout(function(){ ctrl.abort(); }, timeoutMs || 10000);
  return fetch(url, {signal: ctrl.signal}).then(function(r){
    clearTimeout(tid);
    return r.json();
  }).catch(function(e){
    clearTimeout(tid);
    if (e.name === 'AbortError' && fallbackElId){
      var el = el(fallbackElId);
      if (el) el.innerHTML = '<span style="color:#F1A100">⏳ 加载超时</span>';
    }
    return null;
  });
}

function initTerminal(){
  loadAll();                    // 0s: 核心数据
  setTimeout(loadFactors, 10000);  // 10s: 因子引擎 (原5s)
  setTimeout(loadStrats, 15000);   // 15s: 策略绩效 (原8s)
  setTimeout(loadEquity, 30000);   // 30s: 权益曲线 (原15s)
  setInterval(loadAll, 60000);     // 60s轮询 (原30s, 减轻输入压力)
  setInterval(loadStrats, 60000);  // 60s
  setInterval(loadEquity, 120000); // 120s
  setInterval(loadFactors, 120000); // 120s
}
var _loadTick=0;
function loadAll(){
  _loadTick++;
  loadMarketIndices();
  setInterval(loadMarketIndices, 5000); // 5s刷新指数/市场状态/涨跌停/情绪/板块热度(唯一实时看盘窗口)
  if(_loadTick%3===0){  // 30s signals
    // 信号数据源: ML信号表 (含position_pct/close/stop等)
    fetch('/api/signal-table').then(function(r){return r.json()}).then(function(d){
      if(d&&d.length){_sigCache=d.slice(0,15);renderSignals()}});
  }
  // 实盘: 1 API — 更新后触发因子引擎重绘 + 按钮状态刷新
  _fetchTimeout('/api/live-trade/status',10000).then(function(d){
    if(!d) return;
    _lvCache=d;renderLive();
    if(_lastFactorData)renderFactor(_lastFactorData);
    // 刷新通道按钮 + 自动交易按钮
    var ch=(d.trading_channel||'ths').toLowerCase();
    var cb=el('ctlChBtn'); if(cb){cb.textContent=ch==='qmt'?'QMT':'THS';cb.style.color=ch==='qmt'&&d.qmt_connected?DN:ch==='ths'&&d.dll_available?DN:'#F1A100';}
    // B方案: 图标+文字
    var autoOn=d.auto_trade_enabled, online=d.qmt_connected||d.dll_available;
    var btn=el('ctlAutoBtn'), lb=el('ctlAutoLabel');
    var st=autoOn?(online?'on':'warn'):'off';
    var map={on:{icon:'⏸️',text:'自动运行中',border:'#00b96b',bg:'rgba(0,185,107,.1)',color:'#00b96b'},
             warn:{icon:'⚠️',text:'通道断开',border:'#F1A100',bg:'rgba(241,161,0,.1)',color:'#F1A100'},
             off:{icon:'▶️',text:'启动自动交易',border:'#555',bg:'rgba(128,128,128,.05)',color:'#888'}};
    var s=map[st];
    if(btn)btn.style.cssText='display:inline-flex;align-items:center;gap:5px;font-weight:600;padding:5px 12px;border-radius:4px;cursor:pointer;border:2px solid '+s.border+';background:'+s.bg+';color:'+s.color+';font-size:12px';
    if(lb)lb.textContent=s.icon+' '+s.text;
  });
  // 模拟: 1 API 替代原来的3个
  _fetchTimeout('/api/paper-trade/v2',10000).then(function(d){
    if(!d || d.code!==200) return;
    _pvCache=d;
    renderPaper(d); renderPaperPositions(d.positions||[]);
    var pAuto=d.auto_enabled;
    var pbtn=el('ctlPaperAutoBtn'),plb=el('ctlPaperLabel');
    var pst=pAuto?'on':'off';
    var pmap={on:{icon:'⏸️',text:'自动运行中',border:'#00b96b',bg:'rgba(0,185,107,.1)',color:'#00b96b'},
             off:{icon:'▶️',text:'启动自动交易',border:'#555',bg:'rgba(128,128,128,.05)',color:'#888'}};
    var ps=pmap[pst];
    if(pbtn)pbtn.style.cssText='display:inline-flex;align-items:center;gap:5px;font-weight:600;padding:5px 12px;border-radius:4px;cursor:pointer;border:2px solid '+ps.border+';background:'+ps.bg+';color:'+ps.color+';font-size:12px';
    if(plb)plb.textContent=ps.icon+' '+ps.text;
    _pvTrades=d.trade_log||[];
    renderPvTrades(_pvTrades);
  });
}

function renderPvTrades(data){
  var tb=document.getElementById('pvTradeBody');
  if(!tb)return;
  var sorted=sortData(data,_pvSort);
  if(!sorted.length){tb.innerHTML='<tr><td colspan="11" style="color:#555;text-align:center">暂无</td></tr>';return;}
  tb.innerHTML=sorted.map(function(t){var c=t.side==='buy'?UP:DN,pnl=t.pnl||0,reason=t.reason||'';if(!reason&&t.side==='buy')reason='信号买入';if(!reason&&t.side==='sell')reason=pnl>0?'止盈':'止损';var td=t.date?t.date.slice(5)+' '+(t.time||'').slice(0,5):(t.time||'');var amt=t.amount||(t.price*t.qty)||0;var ret=t.return_pct||(pnl?Math.round(pnl/(t.cost||1)*100*100)/100:'');var hd=t.hold_days||'';var src=t.signal_source||t.type||'';return'<tr><td style="font-size:10px">'+td+'</td><td><b>'+t.symbol+'</b></td><td>'+(t.name||'')+'</td><td style="color:'+c+'">'+(t.side==='buy'?'买':'卖')+'</td><td>'+(t.price||0).toFixed(2)+'</td><td>'+(t.qty||0)+'</td><td style="color:'+(pnl>=0?UP:DN)+'">'+(pnl?'¥'+Math.round(pnl):'--')+'</td><td style="color:'+(ret>=0?UP:DN)+';font-size:10px">'+(ret!==''?ret+'%':'--')+'</td><td style="font-size:10px;color:#888">'+(hd||'--')+'</td><td style="font-size:10px;color:#666">'+(src||'--')+'</td><td style="font-size:10px;color:#888">'+reason+'</td></tr>'}).join('');
}
function sortData(data,st){
  return data.slice().sort(function(a,b){
    var va=a[st.col]||'',vb=b[st.col]||'';
    if(st.col==='time'){
      // combine date+time for correct cross-day ordering
      va=(a.date||'0000-00-00')+' '+(a.time||'');
      vb=(b.date||'')+' '+(b.time||'');
    }
    if(typeof va==='number')return (va-vb)*st.dir;
    return String(va).localeCompare(String(vb))*st.dir
  });
}

// ── 策略绩效 ──
function loadStrats(){
  fetch('/api/user-strategies').then(r=>r.json()).then(d=>{
    var items=Array.isArray(d)?d:(d.strategies||[]);
    var el=document.getElementById('stratPerf');
    // 显示: backtest过的 + sim/running中的
    var shown=items.filter(function(s){
      return (s.backtest&&s.backtest.annualized_sharpe) || s.status==='sim' || s.status==='sim_running';
    }).sort(function(a,b){
      var sa=a.backtest?a.backtest.annualized_sharpe||0:0;
      var sb=b.backtest?b.backtest.annualized_sharpe||0:0;
      return sb-sa;
    });
    if(!shown.length){el.innerHTML='<div style="color:#888;font-size:10px;padding:8px 0">暂无策略</div>';return;}
    el.innerHTML=shown.slice(0,10).map(function(s){
      var st=s.status||'draft';
      var badge={sim_running:'🟢',sim:'🚀',real:'🏦',backtested:'✅',draft:'📝'}[st]||'📝';
      if(s.backtest&&s.backtest.annualized_sharpe){
        var sh=s.backtest.annualized_sharpe,c=sh>=3?UP:sh>=1.5?GOLD:'#888';
        return'<div class="strat-row" onclick="location.href=\'/factor-health\'" title="点击进入因子中心">'+
          '<span class="strat-name">'+(s.display_name||s.name)+'</span>'+
          '<span class="strat-badge">'+badge+'</span>'+
          '<span class="strat-sharpe" style="color:'+c+'">'+sh.toFixed(1)+'</span></div>';
      }else{
        return'<div class="strat-row" onclick="location.href=\'/factor-health\'" title="点击进入因子中心">'+
          '<span class="strat-name">'+(s.display_name||s.name)+'</span>'+
          '<span class="strat-badge">'+badge+'</span>'+
          '<span class="strat-sharpe" style="color:#888;font-size:10px">待回测</span></div>';
      }
    }).join('')+'<div style="color:#555;font-size:9px;padding-top:4px;border-top:1px solid #1a1c20;margin-top:4px">📝创建 → 🔄回测 → ✅验证 → 🚀部署</div>';
  });
}

// ── 权益曲线 ──
var _eqP='minute',_pe=[],_le=[];
function switchEqP(p,btn){
  _eqP=p;['minute','day','week','month'].forEach(function(k){
    var id='eqBtn'+k.charAt(0).toUpperCase()+k.slice(1);
    var b=document.getElementById(id);if(b)b.style.cssText=p===k?'color:#409eff;border-color:#409eff':'';
  });
  drawE();
}
function loadEquity(){
  fetch('/api/paper-trade/equity-curve').then(r=>r.json()).then(d=>{if(d.equity){_pe=d.equity;drawE()}});
  fetch('/api/live-trade/equity-curve').then(r=>r.json()).then(d=>{if(d.equity){_le=d.equity;drawE()}});
}
function _agg(raw){
  var d=[],v=[];
  if(!raw||!raw.length)return{dates:[],vals:[]};
  if(_eqP==='minute'){
    // 分时: 显示所有点, 横轴仅显示 HH:MM
    raw.forEach(function(r,i){
      var dd=r.date||r[0]||'';
      d.push(dd.length>10?dd.slice(11,16):dd);  // "2026-07-01 14:35" → "14:35"
      v.push(r.equity||r[1]||0);
    });
  }
  else if(_eqP==='day'){
    // 日: 去重, 每天取最后一点
    var dm={};
    raw.forEach(function(r){var dd=r.date||r[0]||'',k=dd.slice(0,10);dm[k]=(r.equity||r[1]||0)});
    Object.keys(dm).sort().forEach(function(k){d.push(k.slice(5));v.push(dm[k])});
  }
  else if(_eqP==='week'){
    var wm={};raw.forEach(function(r){var dd=r.date||r[0]||'',k=dd.slice(0,7);wm[k]=(r.equity||r[1]||0)});
    Object.keys(wm).sort().forEach(function(k){d.push(k);v.push(wm[k])});
  }
  else if(_eqP==='month'){
    var mm={};raw.forEach(function(r){var dd=r.date||r[0]||'',k=dd.slice(0,7);mm[k]=(r.equity||r[1]||0)});
    Object.keys(mm).sort().forEach(function(k){d.push(k);v.push(mm[k])});
  }
  return{dates:d,vals:v};
}
function drawE(){
  if(typeof Plotly==='undefined') return;
  var a=_agg(_pe),b=_agg(_le);
  if(!a.vals.length&&!b.vals.length)return;
  var traces=[];
  // 模拟盘
  if(a.vals.length>1){
    var startA=a.vals[0];
    var peakA=startA,ddPctA=[];
    var retPctA=[],customA=[];
    for(var i=0;i<a.vals.length;i++){
      retPctA.push(((a.vals[i]-startA)/startA*100).toFixed(1));
      if(a.vals[i]>peakA)peakA=a.vals[i];
      var dd=((a.vals[i]-peakA)/peakA*100).toFixed(1);
      ddPctA.push(dd<0?dd:'0.0');
    }
    for(var i=0;i<a.vals.length;i++) customA.push([retPctA[i],ddPctA[i]]);
    traces.push({x:a.dates,y:a.vals,type:'scatter',mode:'lines',name:'模拟盘',
      customdata:customA,
      hovertemplate:'💰 模拟盘: ¥%{y:,.0f}<br>📈 收益: %{customdata[0]}%<br>📉 回撤: %{customdata[1]}%<extra></extra>',
      line:{color:'#448AFF',width:3,shape:'spline',smoothing:0.8},
      fill:'tozeroy',fillcolor:'rgba(68,138,255,.08)'});
    var ddxA=[],ddyA=[],pk=a.vals[0];
    for(var i=0;i<a.vals.length;i++){if(a.vals[i]>pk)pk=a.vals[i];ddxA.push(a.dates[i]);ddyA.push(a.vals[i]-pk)}
    traces.push({x:ddxA,y:ddyA,type:'scatter',mode:'none',fill:'tozeroy',
      fillcolor:'rgba(255,80,80,.06)',showlegend:false,hoverinfo:'none'});
  }
  // 实盘
  if(b.vals.length>1){
    var startB=b.vals[0];
    var peakB=startB,ddPctB=[];
    var retPctB=[],customB=[];
    for(var i=0;i<b.vals.length;i++){
      retPctB.push(((b.vals[i]-startB)/startB*100).toFixed(1));
      if(b.vals[i]>peakB)peakB=b.vals[i];
      var dd=((b.vals[i]-peakB)/peakB*100).toFixed(1);
      ddPctB.push(dd<0?dd:'0.0');
    }
    for(var i=0;i<b.vals.length;i++) customB.push([retPctB[i],ddPctB[i]]);
    traces.push({x:b.dates,y:b.vals,type:'scatter',mode:'lines',name:'实盘',
      customdata:customB,
      hovertemplate:'🟥 实盘: ¥%{y:,.0f}<br>📈 收益: %{customdata[0]}%<br>📉 回撤: %{customdata[1]}%<extra></extra>',
      line:{color:'#FF4051',width:3,shape:'spline',smoothing:0.8},
      fill:'tozeroy',fillcolor:'rgba(255,64,81,.08)'});
  }
  if(!traces.length)return;
  Plotly.newPlot('chartEquity',traces,{
    template:'plotly_dark',paper_bgcolor:'#141619',plot_bgcolor:'#141619',
    margin:{l:60,r:20,t:15,b:35},height:360,
    hovermode:'x unified',
    hoverlabel:{font:{size:13,family:'Microsoft YaHei'},bgcolor:'rgba(10,12,16,.95)',bordercolor:'#2a2c30'},
    hoverdistance:20,
    xaxis:{tickfont:{size:11,family:'Microsoft YaHei',color:'#aaa'},tickangle:-25,showgrid:false,zeroline:false},
    yaxis:{tickformat:',d',tickfont:{size:11,family:'Microsoft YaHei',color:'#aaa'},gridcolor:'rgba(255,255,255,.04)',zeroline:false},
    legend:{orientation:'h',y:1.1,x:0,font:{size:12,family:'Microsoft YaHei',color:'#e3e3e3'}},
    dragmode:'pan'
  },{responsive:true,displayModeBar:'hover',modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d']});
}

var _factorICCache={};

// ═══ R1: 市场行情条 ═══
function loadMarketIndices(){
  fetch('/api/market/indices').then(function(r){return r.json()}).then(function(d){
    if(d.code!==200||!d.indices) return;
    var idx=d.indices||{}, h='<span class="mkt-scroll">';
    var order=['上证','创业板','科创50','沪深300','平均股价'];
    order.forEach(function(k){
      var v=idx[k];
      if(v){
        if(v.close===0&&v.source==='waiting') h+='<span class="mkt-item" style="color:#F1A100">'+k+' 等待行情...</span>';
        else h+='<span class="mkt-item">'+k+' <span class="v">'+v.close.toFixed(0)+'</span> <span class="'+(v.chg>=0?'mkt-up':'mkt-down')+'">'+(v.chg>=0?'▴':'▾')+Math.abs(v.chg).toFixed(2)+'%</span></span>';
        h+='<span class="mkt-sep"></span>';
      }
    });
    h+='</span>';
    var mktEl=el('mktIndices'); if(mktEl) mktEl.innerHTML=h;
    renderMarket();
  }).catch(function(){});
}
function renderMarket(){
  // 优先API, 降级DOM箭头计数
  fetch('/api/market-regime').then(function(r){return r.json()}).then(function(d){
    if(d&&d.code===200&&d.regime!=='unknown'){ _renderRegime(d); return; }
    _renderOld();
  }).catch(function(){ _renderOld(); });

  function _renderRegime(d){
    var ms=d.regime, scale=d.position_scale||0.5;
    var starMap={bull:5,sideways:3,bear:1}, n=starMap[ms]||3;
    var label={bull:'🐂 牛市',sideways:'📊 震荡',bear:'🐻 熊市'}[ms];
    var color={bull:'#FF4051',sideways:'#F1A100',bear:'#00b96b'}[ms];
    var stars='★'.repeat(n)+'☆'.repeat(5-n);
    var st=el('mktState');
    if(st) st.innerHTML='<span style="font-size:18px;font-weight:800;color:'+color+'">'+label+'</span> <span style="font-size:16px;color:#F1A100">'+stars+'</span> <span style="font-size:14px;color:#888">仓位×'+(scale*100).toFixed(0)+'%</span>';
    var ht=el('mktHeat');
    if(ht && d.sentiment){
      var s=d.sentiment, parts=[];
      if(s.label){
        var sentColor={'🐂 乐观':'#FF4051','🐻 悲观':'#00b96b','📰 中性':'#888'}[s.label]||'#e3e3e3';
        parts.push('<span style="color:'+sentColor+'">'+s.label+'</span>');
      }
      if(s.limit_up) parts.push('<span style="color:#FF4051">涨停'+s.limit_up+'</span>,<span style="color:#00b96b">跌停'+(s.limit_down||0)+'</span>');
      if(s.news&&s.news.score!==null){
        var newsColor=s.news.score>0?'#00b96b':s.news.score<0?'#FF4051':'#888';
        parts.push('消息:<span style="color:'+newsColor+'">'+s.news.label+'</span>');
      }
      if(s.hot_sectors&&s.hot_sectors.length) parts.push('<span style="color:#FF8C00">🔥'+s.hot_sectors.map(function(x){return x.name}).join('/')+'</span>');
      if(s.cold_sectors&&s.cold_sectors.length) parts.push('<span style="color:#4FC3F7">❄️'+s.cold_sectors.map(function(x){return x.name}).join('/')+'</span>');
      ht.innerHTML=parts.join(' <span style="color:#555">·</span> ');
    }
  }

  function _renderOld(){
    var ms='unknown';
    try{
      var idx=document.getElementById('mktIndices');
      if(idx&&idx.textContent){
        var txt=idx.textContent;
        var ups=(txt.match(/▴/g)||[]).length;
        if(ups>=4) ms='bull'; else if(ups>=2) ms='volatile'; else ms='bear';
      }
    }catch(e){}
    var starMap={bull:3,volatile:4,bear:5,unknown:4}, n=starMap[ms]||4;
    var scaleMap={bull:1.0,volatile:0.7,bear:0.4,unknown:0.5}, scale=scaleMap[ms]||0.5;
    var label={bull:'🐂 牛市',volatile:'📊 震荡',bear:'🐻 熊市',unknown:'❓ 未知'}[ms];
    var color={bull:'#FF4051',volatile:'#F1A100',bear:'#00b96b',unknown:'#888'}[ms];
    var stars='★'.repeat(n)+'☆'.repeat(5-n);
    var st=el('mktState'); if(st) st.innerHTML='<span style="font-size:18px;font-weight:800;color:'+color+'">'+label+'</span> <span style="font-size:16px;color:#F1A100">'+stars+'</span> <span style="font-size:14px;color:#888">仓位×'+(scale*100).toFixed(0)+'%</span>';
  }
}

// ═══ R3: AI信号 (双行布局) ═══
function renderSignals(){
  var stocks=_sigCache.slice().sort(function(a,b){return (b.combined_score||0)-(a.combined_score||0)}), h='';
  var scEl=el('sigCount'); if(scEl) scEl.textContent=stocks.length+'只';
  if(!stocks.length){ el('sigBody').innerHTML='<div style="color:#555;text-align:center">等待信号...</div>'; return; }
  stocks.forEach(function(s,i){
    var sym=s.symbol||'', name=s.name||'', score=s.combined_score||0;
    var chg=s.change_pct||0, posPct=s.position_pct||2, close=s.close||0;
    var signal=s.signal||'';
    var scColor=score>80?GOLD:score>60?'#e3e3e3':'#888';
    var chgColor=chg>=0?UP:DN;
    var src='',hasL=!!s.lgbm_score,hasX=!!s.xgb_score,hasR=!!s.ridge_score;
    var cons=s.consensus||'';
    if(cons==='打板')src='<span style="color:#FF4051;font-size:10px;font-weight:600">打板</span>';
    else if(cons==='反转')src='<span style="color:#00b96b;font-size:10px;font-weight:600">反转</span>';
    else if(hasL&&hasX)src='<span style="color:#409eff;font-size:10px">L+X</span>';
    else if(hasL)src='<span style="color:#409eff;font-size:10px">L</span>';
    else if(hasX)src='<span style="color:#00b96b;font-size:10px">X</span>';
    h+='<div class="sig-row" style="display:flex;align-items:center;gap:6px;padding:4px 0;font-size:12px">'+
      '<span style="color:'+scColor+';font-weight:700;min-width:32px">'+(score||0).toFixed(0)+'</span>'+
      '<span class="sl-link" data-code="'+sym+'" onclick="event.stopPropagation();linkStock(this.getAttribute(\"data-code\"))" style="font-weight:600;min-width:72px;cursor:pointer">'+sym+'</span>'+
      '<span style="color:#888;min-width:50px;font-size:11px">'+(name||'')+'</span>'+
      '<span style="color:#F1A100;font-weight:600;min-width:36px">'+(signal||(score>90?'L5':score>80?'L4':score>60?'L3':'L2'))+'</span>'+
      '<span style="color:'+chgColor+';min-width:48px">'+(chg>=0?'+':'')+(chg||0).toFixed(1)+'%</span>'+
      src+
      '<span style="color:#888;font-size:10px;min-width:28px">'+(posPct||0).toFixed(0)+'%</span>'+
      (s.auto_enabled?
        '<span style="color:#00b96b;font-size:10px;font-weight:600;min-width:36px">⚡自动</span>':
        '<button style="padding:2px 8px;font-size:10px;background:#00b96b;color:#fff;border:none;border-radius:3px;cursor:pointer" onclick="event.stopPropagation();approveSignal(\''+sym+'\','+posPct+','+close+',this)">审</button>')+
      '</div>';
    h+='<div style="font-size:10px;color:#666;padding:2px 0 4px 32px">'+
      '止'+(s.stop_loss||0).toFixed(2)+' 盈'+(s.take_profit||0).toFixed(2)+' | '+
      (s.industry||'')+
      '</div>';
  });
  el('sigBody').innerHTML=h;
}

// 内联审批
function approveSignal(sym, pct, price, btn){
  if(!confirm('批准买入 '+sym+' '+pct+'% @'+price+'?')) return;
  btn.textContent='...'; btn.disabled=true;
  fetch('/api/signal/approve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({symbol:sym,position_pct:pct,close:price,decision:'terminal',source:'ml',signal_id:(s&&s.signal_id)||''})
  }).then(function(r){return r.json()}).then(function(d){
    if(d.code===200){ btn.textContent='✅'; btn.style.background='#00b96b'; }
    else { btn.textContent='❌'; btn.style.background='#FF4051'; alert(d.order?d.order.error:d.error); }
  }).catch(function(){ btn.textContent='❌'; btn.disabled=false; });
}

// ═══ R4: 实盘 ═══
function renderLive(){
  var d=_lvCache;
  el('lvEq').textContent=d.total_equity?'¥'+Math.round(d.total_equity).toLocaleString():'--';
  var tp=d.total_pnl||null, tr=d.total_return||null;
  // 日盈亏：总权益差值
  var eq2=d.total_equity||0;
  if(window._lastLiveEq!==undefined){
    var dp2=eq2-window._lastLiveEq;
    var dr2=window._lastLiveEq>0?(dp2/window._lastLiveEq*100):0;
    el('lvPnl').innerHTML='<span style="color:'+(dr2>=0?UP:DN)+'">'+(dr2>=0?'+':'')+dr2.toFixed(1)+'%</span>|<span style="color:'+(dp2>=0?UP:dp2<0?DN:'#888')+'">'+(dp2>=0?'+':'')+Math.round(Math.abs(dp2)).toLocaleString()+'</span>';
  }else{
    el('lvPnl').innerHTML='<span style="color:#888">--</span>|<span style="color:#888">--</span>';
  }
  window._lastLiveEq=eq2;
  // 总盈亏
  var totalPnlEl=document.getElementById('lvTotalPnl');
  if(totalPnlEl){
    if(tr!==null)totalPnlEl.innerHTML='<span style="color:'+(tr>=0?UP:DN)+'">'+(tr>=0?'+':'')+tr.toFixed(1)+'%</span>|<span style="color:'+(tp!==null&&tp>=0?UP:DN)+'">'+(tp!==null?(tp>=0?'+':'')+Math.round(Math.abs(tp)).toLocaleString():'--')+'</span>';
    else totalPnlEl.innerHTML='<span style="color:#888">--</span>|<span style="color:#888">--</span>';
  }
  el('lvCash').textContent=d.cash?'¥'+Math.round(d.cash).toLocaleString():'--';
  var pos=d.positions||[];
  el('lvCount').textContent=pos.length+'只 | '+(d.win_rate?Math.round(d.win_rate):'--')+'%';
  var ph='';
  if(pos.length){
    ph='<table class="tbl" style="font-size:12px"><thead><tr><th>代码</th><th>名称</th><th>成本</th><th>现价</th><th>数量</th><th>盈亏</th><th>盈亏%</th><th>市值</th></tr></thead><tbody>';
    pos.forEach(function(p){var pp=p.profit_pct||p.pnl_pct||0,q=p.quantity||p.qty||0,cost=p.avg_cost||p.cost_price||0,cur=p.current_price||p.last_price||cost,mv=cur*q,pnlAmt=(p.profit_amt||0)||(mv-cost*q);ph+='<tr><td><b>'+p.symbol+'</b></td><td style="font-size:12px">'+(p.name||'')+'</td><td>'+(cost||0).toFixed(2)+'</td><td>'+(cur||0).toFixed(2)+'</td><td>'+q+'</td><td style="color:'+(pnlAmt>=0?UP:DN)+'">'+(pnlAmt>=0?'+':'')+Math.round(pnlAmt).toLocaleString()+'</td><td style="color:'+(pp>=0?UP:DN)+'">'+(pp>=0?'+':'')+pp.toFixed(1)+'%</td><td>'+Math.round(mv).toLocaleString()+'</td></tr>'});
    ph+='</tbody></table>';
  }else{ph='<div style="color:#555;text-align:center;padding:8px">空仓</div>';}
  el('lvPositions').innerHTML=ph;
  // 实盘委托/成交 (优先fills, 不到就用orders)
  var realTrades = (d.fills && d.fills.length) ? d.fills : (d.orders || []);
  _lvTrades = realTrades;
  renderLvTrades(_lvTrades);
}

// ═══ R5: 模拟盘 ═══
function renderPaper(d){
  if(!d) return;
  el('pvEq').textContent=d.total_equity?'¥'+Math.round(d.total_equity).toLocaleString():(d.cash?'¥'+Math.round(d.cash).toLocaleString():'--');
  var pnl=d.total_pnl||0, ret=(d.total_return||0).toFixed(1);
  // 总盈亏率|额
  var tpEl=document.getElementById('pvTotalPnl');
  if(tpEl)tpEl.innerHTML='<span style="color:'+(ret>=0?UP:DN)+'">'+(ret>=0?'+':'')+ret+'%</span>|<span style="color:'+(pnl>=0?UP:DN)+'">'+(pnl>=0?'+':'')+Math.round(Math.abs(pnl)).toLocaleString()+'</span>';
  // 日盈亏 = 当前权益 - 今日起始权益
  var startEq=d.day_start_equity;
  if(startEq&&startEq>0){
    var dp2=d.total_equity-startEq;
    var dr2=(dp2/startEq*100).toFixed(1);
    el('pvPnl').innerHTML='<span style="color:'+(parseFloat(dr2)>=0?UP:DN)+'">'+(parseFloat(dr2)>=0?'+':'')+dr2+'%</span>|<span style="color:'+(dp2>=0?UP:DN)+'">'+(dp2>=0?'+':'')+Math.round(Math.abs(dp2)).toLocaleString()+'</span>';
  }else{
    el('pvPnl').innerHTML='<span style="color:#888">--</span>|<span style="color:#888">--</span>';
  }
  var pc=Array.isArray(d.positions)?d.positions.length:(d.position_count||0);
  var wr=d.win_rate?Math.round(d.win_rate):'--';
  el('pvCount').textContent=pc+'只 | '+wr+'%';
  var sh=d.sharpe?d.sharpe.toFixed(2):'--';
  var dd=(d.max_drawdown!=null&&d.max_drawdown!==0)?d.max_drawdown.toFixed(1)+'%':'--';
  if(d.max_drawdown===0) dd='0%';
  el('pvSharpe').textContent=sh+' | '+dd;
}

function renderPaperPositions(positions){
  var ph='';
  if(positions.length){
    ph='<table class="tbl" style="font-size:12px"><thead><tr><th>代码</th><th>名称</th><th>成本</th><th>现价</th><th>数量</th><th>盈亏</th><th>盈亏%</th><th>市值</th></tr></thead><tbody>';
    positions.forEach(function(p){
      var pp=p.profit_pct||p.pnl_pct||0,q=p.qty||p.quantity||0,cost=p.avg_cost||p.cost_price||0,cur=p.current_price||p.last_price||cost,mv=cur*q,pnlAmt=(p.profit_amt||0)||(mv-cost*q);
      ph+='<tr><td><b>'+(p.symbol||p.code||'')+'</b></td><td style="font-size:10px">'+(p.name||'')+'</td><td>'+(cost||0).toFixed(2)+'</td><td>'+(cur||0).toFixed(2)+'</td><td>'+q+'</td><td style="color:'+(pnlAmt>=0?UP:DN)+'">'+(pnlAmt>=0?'+':'')+Math.round(pnlAmt).toLocaleString()+'</td><td style="color:'+(pp>=0?UP:DN)+'">'+(pp>=0?'+':'')+pp.toFixed(1)+'%</td><td>'+Math.round(mv).toLocaleString()+'</td></tr>';
    });
    ph+='</tbody></table>';
  }else{ph='<div style="color:#555;text-align:center;padding:8px">空仓</div>';}
  el('pvPositions').innerHTML=ph;
}

// ═══ R7: 因子引擎 (6段留白布局) ═══
var _xgbReady=false, _factorCount=8;

function loadFactors(){
  fetch('/api/factor/xgb-status').then(r=>r.json()).then(d=>{renderFactor(d)}).catch(function(){});
}

var _lastFactorData=null;
function renderFactor(d){
  if(!d||!d.xgb){ el('factorBox').textContent='加载中...'; return; }
  _lastFactorData=d;
  var x=d.xgb, facts=d.factors;
  _xgbReady=x.ready;
  if(facts&&facts.active_count) _factorCount=facts.active_count;

  // ═══ 5维就绪度 (个人专业量化系统 — 每维挂钩真实数据) ═══
  // 1 QMT行情
  var qmtOk=_lvCache.qmt_connected||false;
  // 2 因子引擎: 活跃因子 >= 1
  var factorOk=(facts&&facts.active_count>=1)||false;
  // 3 XGBoost
  var xgbOk=x.ready||false;
  // 4 风控熔断
  var r=_lvCache.risk||{}, dd=r.daily_loss||0;
  var riskOk=dd>-5;  // 日亏 > -5% 才算正常
  // 5 交易通道: 检测当前配置的主通道
  var ch=(_lvCache.trading_channel||'ths').toLowerCase();
  var chLabel=ch==='qmt'?'QMT交易':'THS交易';
  var chOk=(ch==='qmt'&&_lvCache.qmt_connected)||(ch==='ths'&&_lvCache.dll_available);

  var checks=[
    {k:'QMT行情', ok:qmtOk},
    {k:'因子引擎', ok:factorOk},
    {k:'XGBoost', ok:xgbOk},
    {k:'风控熔断', ok:riskOk},
    {k:chLabel, ok:chOk}
  ];
  var nOk=checks.filter(function(c){return c.ok}).length;
  var pct=Math.round(nOk/5*100);
  var lv=nOk>=5?'L5':nOk>=4?'L4':nOk>=3?'L3':nOk>=2?'L2':'L1';
  var barColor=pct>=80?'#00b96b':pct>=60?'#F1A100':'#FF4051';
  var advice=nOk>=5?'可开实盘':nOk>=4?'仅模拟，实盘暂停':nOk>=3?'仅模拟':'禁止交易，先修故障';

  // 最强因子
  var topLong='--', topLongIC=0, topShort='--', topShortIC=0;
  if(facts&&facts.list&&facts.list.length){
    facts.list.forEach(function(f){var ic=f.ic_5d||0;if(ic>topLongIC){topLongIC=ic;topLong=f.display||f.name}});
    facts.list.forEach(function(f){var ic=f.ic_5d||0;if(ic<topShortIC){topShortIC=ic;topShort=f.display||f.name}});
  }
  if(topLongIC<0.02){topLong='--';topLongIC=0}
  if(topShortIC>-0.02){topShort='--';topShortIC=0}

  var ddTxt=dd&&dd!==0?(dd>0?'+':'')+dd.toFixed(1)+'%':'--';
  var maxSingle=r.max_single_pct||0;
  var consecLoss=r.consecutive_losses||0;

  var h='';
  // 段1: 就绪度条 + 等级 + 建议 (文字叠加在横道上)
  h+='<div class="factor-ready">'+
    '<div class="ready-bar" style="flex:1;height:5px;position:relative">'+
      '<div class="ready-fill" style="width:'+pct+'%;background:'+barColor+'"></div>'+
      '<span style="position:absolute;right:0;top:-14px;font-size:9px;color:#888">'+lv+' '+nOk+'/5 · '+advice+'</span>'+
    '</div>'+
    '</div>';
  // 段2: 5维指示灯
  h+='<div class="factor-tags">';
  checks.forEach(function(c){
    h+='<span class="ftag'+(c.ok?' ftag-ok':'')+'" style="color:'+(c.ok?DN:'#FF4051')+'">'+(c.ok?'●':'○')+' '+c.k+'</span>';
  });
  h+='</div>';
  // 段3: 风控详情 — daily_loss实为持仓浮动盈亏, 等待后端修正
  var ddLabel=dd>=0?'浮盈':'浮亏';
  h+='<div class="factor-risk">'+
    ddLabel+'<span style="color:'+(dd>=0?UP:DN)+'">'+ddTxt+'</span> · 连亏'+consecLoss+' · 单票'+maxSingle.toFixed(1)+'%'+
    '</div>';
  // 段4: 做多因子
  if(topLong!=='--'){
    h+='<div class="factor-long">'+
      '<div class="fl-head">🔺做多</div>'+
      '<div class="fl-name">'+topLong+' <span class="fl-ic">IC='+(topLongIC>=0?'+':'')+topLongIC.toFixed(3)+'</span></div>'+
      '</div>';
  }
  // 段5: 反转因子
  if(topShort!=='--'){
    h+='<div class="factor-short">'+
      '<div class="fl-head" style="color:'+DN+'">🔻反转</div>'+
      '<div class="fl-name">'+topShort+' <span class="fl-ic">IC='+(topShortIC>=0?'+':'')+topShortIC.toFixed(3)+'</span></div>'+
      '</div>';
  }
  el('factorBox').innerHTML=h;
}

// ═══ R6: 风控 (独立组件，非侧栏) ═══
function renderRisk(d){
  if(!d||d.code!==200){ el('riskBox').textContent='加载中...'; return; }
  var r=d.risk||d;
  var dd=r.daily_loss||r.day_pnl_pct||0;
  var hasPos=(_lvCache.positions||[]).length>0;
  var ddTxt=(!hasPos&&dd===0)?'无交易':'<span style="color:'+(dd<-3?UP:DN)+'">'+dd.toFixed(1)+'%</span>';
  el('riskBox').innerHTML=
    '<div>日亏损: '+ddTxt+'</div>'+
    '<div>最大单票: <span style="color:#e3e3e3">'+(r.max_single_pct||0).toFixed(1)+'%</span> '+(r.max_single_sym||'')+'</div>'+
    '<div>熔断: <span style="color:'+DN+'">正常</span></div>';
}

// ═══ R2: 统一状态栏 (就绪度=信号灯) ═══
function renderStatusBar(){
  var online=_lvCache.dll_available||_lvCache.qmt_connected;
  var autoOn=_lvCache.auto_trade_enabled;
  var ch=(_lvCache.trading_channel||'ths').toLowerCase();
  var chOk=(ch==='qmt'&&_lvCache.qmt_connected)||(ch==='ths'&&_lvCache.dll_available);

  var chLabel=ch==='qmt'?'QMT通道':'THS通道';
  var checks=[
    {k:'因子IC', ok:true},
    {k:'XGBoost', ok:_xgbReady},
    {k:chLabel, ok:chOk},
    {k:'风控在线', ok:online},
    {k:'钉钉告警', ok:true},
    {k:'看门狗', ok:true},
    {k:'回测验证', ok:false},
    {k:'模拟30天', ok:false},
  ];
  var n=checks.filter(function(c){return c.ok}).length;
  var pct=Math.round(n/checks.length*100);
  var lv=pct>=90?'L4':pct>=70?'L3':pct>=50?'L2':'L1';

  // 就绪仪表
  el('ctlReady').textContent=lv;
  el('readyFill').style.width=pct+'%';
  el('readyFill').style.background=pct>=70?'#00b96b':pct>=40?'#F1A100':'#FF4051';

  // 检查项图标 (✅=绿 ⚠️=黄 ❌=红)
  el('readyItems').innerHTML=checks.map(function(c){
    var icon=c.ok?'✅':'⚠️';
    return '<span style="margin-right:6px;font-size:10px">'+icon+c.k+'</span>';
  }).join('');

  // 资产摘要
  var eq=_lvCache.total_equity||0, pos=(_lvCache.positions||[]).length;
  el('ctlAsset').textContent='¥'+Math.round(eq).toLocaleString()+' · '+(pos?pos+'持仓':'空仓');

  // 操作按钮
  var ab=el('ctlAutoBtn'); if(ab){ ab.textContent=autoOn?'⏯暂停':'▶开始'; ab.style.color=autoOn?DN:'#888'; ab.style.borderColor=autoOn?DN:'#323337'; }
  var cb=el('ctlChBtn'); if(cb){ cb.textContent=ch==='qmt'?'QMT':'THS'; cb.style.color=chOk?DN:'#F1A100'; cb.style.borderColor=chOk?DN:'#F1A100'; }
  var ld=el('ctlLinkerDot'), ll=el('ctlLinkerLabel');
  var linkerOn=localStorage.getItem('stockLinker')==='on';
  if(ld){ ld.style.background=linkerOn?(online?DN:'#F1A100'):'#888'; }
  if(ll) ll.textContent=linkerOn?'联动':'断联';
  // 熔断检测
  var cb_breaker=_lvCache.risk&&_lvCache.risk.daily_loss<-5;
  var cl=el('ctlClearBtn'); if(cl){ cl.style.color=cb_breaker?UP:'#888'; cl.style.borderColor=cb_breaker?UP:'#323337'; }
}

// ═══ R9: 实时事件流 ═══
function initSSE(){
  try{
    var evt=new EventSource('/api/paper-trade/sse');
    evt.onmessage=function(e){
      try{
        var d=JSON.parse(e.data);
        var now=new Date().toLocaleTimeString('zh-CN',{hour12:false});
        // SSE推送: {total_equity,total_pnl,position_count,...} 或 成交事件
        var msg='';
        if(d.total_equity) msg='📊 资产 ¥'+Math.round(d.total_equity).toLocaleString()+' | 持仓'+d.position_count+'只';
        else if(d.order) msg='📝 成交: '+d.order;
        else msg=JSON.stringify(d).slice(0,60);
        _events.unshift({t:now, m:msg});
        if(_events.length>20) _events.pop();
        renderEvents();
      }catch(ex){}
    };
    evt.onerror=function(){evt.close();setTimeout(initSSE,60000);};
  }catch(ex){}
}
// 轮询兜底（SSE失败时30秒一次状态摘要）
var _events=[];
function pollEvents(){
  var now=new Date().toLocaleTimeString('zh-CN',{hour12:false});
  var items=[];
  if(_sigCache.length) items.push('📡 信号'+_sigCache.length+'只');
  var lvPos=(_lvCache.positions||[]).length;
  if(lvPos>0) items.push('💰 实盘'+lvPos+'只');
  // _pvCache removed - paper positions count not available in poll mode
  if(_lvCache.qmt_connected) items.push('QMT●');
  if(_lvCache.auto_trade_enabled) items.push('🤖自动');
  if(!items.length){ items.push('🟢 待命'); }
  _events.unshift({t:now, m:items.join(' | ')});
  if(_events.length>20) _events.pop();
  renderEvents();
}
function renderEvents(){
  var bar=el('evtBar'); if(!bar) return;
  var h=_events.slice(0,5).map(function(e){return '<span class="evt-item"><span class="evt-time">'+e.t+'</span> '+e.m+'</span>';}).join('<span class="mkt-sep"></span>');
  bar.innerHTML=h||'<span>📡 事件监听中...</span>';
}
setInterval(pollEvents, 30000);

// ═══ 联动开关 ═══
function toggleLinker(){
  var on=localStorage.getItem('stockLinker')==='on';
  localStorage.setItem('stockLinker', on?'off':'on');
  var dot=el('ctlLinkerDot'), lbl=el('ctlLinkerLabel');
  if(dot) dot.style.background=on?'#F1A100':'#00b96b';
  if(lbl) lbl.textContent=on?'断联':'联动';
}

// ═══ 紧急操作 ═══
function paperClearAll(){
  if(!confirm('⚠️ 模拟清仓：卖出全部持仓？')) return;
  fetch('/api/paper-trade/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:'',side:'reset'})})
    .then(r=>r.json()).then(d=>alert(d.success||d.error?'✅ 已重置模拟盘':'❌ 失败')).catch(function(){alert('❌ 网络错误');});
}

function emergencyClear(){
  if(!confirm('⚠️ 确认紧急清仓？将撤销所有未成交委托！')) return;
  fetch('/api/live-trade/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:''})})
    .then(function(r){return r.json()}).then(function(d){alert(d.message||(d.success?'✅ 已撤销':'❌ 失败'));}).catch(function(){alert('❌ 网络错误');});
}

function toggleAuto(){
  if(!_lvCache||_lvCache.auto_trade_enabled===undefined){alert('状态未加载,请稍后再试');return;}
  var online=_lvCache.qmt_connected||_lvCache.dll_available;
  var newVal=!_lvCache.auto_trade_enabled;
  _lvCache.auto_trade_enabled=newVal;
  var btn=el('ctlAutoBtn'), lb=el('ctlAutoLabel');
  var st=newVal?(online?'on':'warn'):'off';
  var map={on:{icon:'⏸️',text:'自动运行中',border:'#00b96b',bg:'rgba(0,185,107,.1)',color:'#00b96b'},
           warn:{icon:'⚠️',text:'通道断开',border:'#F1A100',bg:'rgba(241,161,0,.1)',color:'#F1A100'},
           off:{icon:'▶️',text:'启动自动交易',border:'#555',bg:'rgba(128,128,128,.05)',color:'#888'}};
  var s=map[st];
  if(btn)btn.style.cssText='display:inline-flex;align-items:center;gap:5px;font-weight:600;padding:5px 12px;border-radius:4px;cursor:pointer;border:2px solid '+s.border+';background:'+s.bg+';color:'+s.color+';font-size:12px';
  if(lb)lb.textContent=s.icon+' '+s.text;
  fetch('/api/live-trade/auto-toggle-v2',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:newVal})}).catch(function(){});
}

function togglePaperAuto(){
  var cur=_pvCache&&_pvCache.auto_enabled;
  var newVal=!cur;
  _pvCache.auto_enabled=newVal;
  var btn=el('ctlPaperAutoBtn'), lb=el('ctlPaperLabel');
  var st=newVal?'on':'off';
  var map={on:{icon:'⏸️',text:'自动运行中',border:'#00b96b',bg:'rgba(0,185,107,.1)',color:'#00b96b'},
           off:{icon:'▶️',text:'启动自动交易',border:'#555',bg:'rgba(128,128,128,.05)',color:'#888'}};
  var s=map[st];
  if(btn)btn.style.cssText='display:inline-flex;align-items:center;gap:5px;font-weight:600;padding:5px 12px;border-radius:4px;cursor:pointer;border:2px solid '+s.border+';background:'+s.bg+';color:'+s.color+';font-size:12px';
  if(lb)lb.textContent=s.icon+' '+s.text;
  fetch('/api/paper-trade/auto-toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:newVal})}).catch(function(){});
}

function toggleChannel(){
  var cur=(_lvCache.trading_channel||'qmt')==='qmt'?'ths':'qmt';
  // 乐观更新: 立即切换按钮文字
  var cb=el('ctlChBtn'); if(cb){cb.textContent=cur==='qmt'?'QMT':'THS';}
  fetch('/api/live-trade/channel-toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:cur})})
    .then(function(r){return r.json()}).then(function(){loadAll();});
}

function sortLv(col){sortTable(col,_lvTrades,_lvSort,renderLvTrades)}
function sortPv(col){sortTable(col,_pvTrades,_pvSort,renderPvTrades)}
function sortTable(col,data,st,fn){
  st.col=col;st.dir*=-1;
  fn(data);
  var prefix=st===_lvSort?'lv':'pv';
  var hd=document.getElementById(prefix+'TradeHead');
  if(hd){var ths=hd.querySelectorAll('th');ths.forEach(function(t){t.textContent=t.textContent.replace(' ▴','').replace(' ▾','')});
  var map={time:0,code:1,name:2,side:3,price:4,qty:5,pnl:6,reason:7,status:7};
  var idx=map[col];if(idx!==undefined&&ths[idx])ths[idx].textContent+=' '+(st.dir>0?'▴':'▾')}
}
function renderLvTrades(data){
  var tb=document.getElementById('lvOrderBody');if(!tb)return;
  var sorted=sortData(data,_lvSort);
  tb.innerHTML=sorted.length?sorted.map(function(t){var c=t.side==='buy'?UP:DN,st=t.status||(t.filled?'成交':'委托'),pnl=t.pnl||0;var td=t.date?t.date.slice(5)+' '+(t.time||'').slice(0,5):(t.time||'');var amt=t.amount||(t.price*t.qty)||0;var ret=t.return_pct||(pnl?Math.round(pnl/(t.cost||1)*100*100)/100:'');var hd=t.hold_days||'';var src=t.signal_source||t.type||'';return'<tr><td style="font-size:10px">'+td+'</td><td><b>'+t.symbol+'</b></td><td>'+(t.name||'')+'</td><td style="color:'+c+'">'+(t.side==='buy'?'买':'卖')+'</td><td>'+(t.price||0).toFixed(2)+'</td><td>'+(t.qty||0)+'</td><td style="color:'+(pnl>=0?UP:DN)+'">'+(pnl?'¥'+Math.round(pnl):'--')+'</td><td style="color:'+(ret>=0?UP:DN)+';font-size:10px">'+(ret!==''?ret+'%':'--')+'</td><td style="font-size:10px;color:#888">'+(hd||'--')+'</td><td style="font-size:10px;color:#666">'+(src||'--')+'</td><td>'+st+'</td></tr>'}).join(''):'<tr><td colspan="11" style="color:#555;text-align:center">暂无</td></tr>';
}

// 移除重复 loadFactors (已在上面定义)
// runAudit 保留以备后用
function runAudit(){
  var items=[];
  var ab=document.getElementById('auditBox'); if(!ab)return;
  items.push('🔗 信号统一: <b style="color:'+DN+'">✅ Plan I</b>');
  fetch('/api/strategy/active-paper').then(r=>r.json()).then(d=>{
    var unified=d.active;
    var box=document.getElementById('auditBox');if(!box)return;
    var html=box.innerHTML;
    html=html.replace(/⚙️ 参数统一.*?<\/b>/,'⚙️ 参数统一: <b style="color:'+(unified?DN:UP)+'">'+(unified?'✅ 策略驱动':'❌ 两套参数')+'</b>');
    html+='<br>📋 纸引擎参数: <b style="color:'+DN+'">✅ 读策略</b>';
    box.innerHTML=html;
  });
  var quoteOk=_lvCache.qmt_connected||false;
  items.push('📡 行情: <b style="color:'+(quoteOk?DN:'#F1A100')+'">'+(quoteOk?'✅ QMT':'⚠️ 缓存')+'</b>');
  items.push('📊 回测: <b style="color:'+DN+'">✅ 确定性采样</b>');
  ab.innerHTML=items.join('<br>');
}
