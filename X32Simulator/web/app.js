const $ = s => document.querySelector(s);
// 推子/发送电平的 dB 曲线: 协议 `level` 类型的 **4 段伪对数**
//   f>=0.5 → 40f-30 | f>=0.25 → 80f-50 | f>=0.0625 → 160f-70 | 否则 480f-90
// 锚点 0.75→0 dB, 0.5→-10, 0.25→-30, 0.0625→-60。与后端 params.level_db 同源;
// 以前用的是 20*log10(f/0.75), 只有 0.75 一个点对得上 (0.25 处偏 +20 dB)。
const dbOf = f => (f<=0 ? -90
  : f>=0.5 ? f*40-30
  : f>=0.25 ? f*80-50
  : f>=0.0625 ? f*160-70
  : f*480-90);
const dbTxt = db => db<=-89 ? "-∞" : db.toFixed(1);
let S = null;

// ---- HTML 转义: 所有拼进 innerHTML 的用户可控串都必须过它 ----
// ⚠️ 通道名/卡片名/描述/控制键名/图标/FX 名/快照名/扫描到的真台名 全部可以由
//    外部（含局域网里任何一台机器）控制。本项目没有鉴权、监听 0.0.0.0，
//    所以差一个转义就是"外部可控 → 任意 JS 执行 → 调全部 API 接管台面"。
//    新增任何 innerHTML 拼接地，先问自己：这里面哪个字段是用户填的？
function esc(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ---- 通道名称: 调音台 / 卡片 / 控制键统一显示 ----
const P2G={c:'ch',b:'bus',d:'dca',m:'main',a:'auxin',u:'usb',x:'fxret',t:'mtx'};
function nameOf(g,i){
  if(!S||!S.names) return '';
  return g==='main' ? (S.names.main||'') : ((S.names[g]||{})[String(i)]||'');
}
function dispName(g,i){
  const n=nameOf(g,i);
  if(n) return n;
  if(g==='main') return '主输出';
  if(g==='bus') return 'B'+i;
  if(g==='dca') return 'D'+i;
  return String(i).padStart(2,'0');
}

function rowHTML(prefix, i){
  const L={m:'<b>MAIN</b><small class="nm2"></small>', d:i=>`<b>D${i}</b><small class="nm2"></small>`, b:i=>`<b>B${i}</b><small class="nm2"></small>`,
           a:i=>`<b>A${i}</b><small class="nm2"></small>`, u:i=>`<b>U${i}</b><small class="nm2"></small>`, x:i=>`<b>FX${i}</b><small class="nm2"></small>`, t:i=>`<b>M${i}</b><small class="nm2"></small>`};
  const num = prefix==='m' ? L.m : (L[prefix] ? L[prefix](i)
    : `<b class="chnum" onclick="openChParams(${i})" title="通道参数: 增益/反相/延时/低切/插入/噪声门">${String(i).padStart(2,'0')}</b><small class="nm2"></small>`);
  return `<div class="row" id="r-${prefix}-${i}">
    <div class="meter"><i></i></div>
    <div class="num">${num}</div>
    <input type="range" min="0" max="1" step="0.01" value="0.75"
      oninput="drag('${prefix}',${i},this)" onchange="dragEnd('${prefix}',${i},this)"
      ondblclick="zeroFader('${prefix}',${i})" title="双击回 0 dB">
    <span class="db">0.0 dB</span>
    <button class="fadj" onclick="nudge('${prefix}',${i},-0.5)" title="-0.5 dB">−</button>
    <button class="fadj" onclick="nudge('${prefix}',${i},0.5)" title="+0.5 dB">＋</button>
    <button class="mute" onclick="toggle('${prefix}',${i},this)">M</button>
  </div>`;
}
// ±0.5 dB 精调 (与后端 level_db 4 段曲线一致) / 双击回 0 dB
function dbToFader(db){
  db=Math.min(Math.max(db,-90),10);
  if(db>=-10) return (db+30)/40;
  if(db>=-30) return (db+50)/80;
  if(db>=-60) return (db+70)/160;   // -60..-30 段 (分界 -60, 不是 -50)
  return (db+90)/480;
}
function nudge(p,i,d){
  const r=document.getElementById('r-'+p+'-'+i); if(!r) return;
  const sl=r.querySelector('input');
  const db=Math.min(Math.max(dbOf(+sl.value)+d,-90),10);
  const v=dbToFader(db);
  sl.value=v; sl.dataset.target=v; delete anim[r.id];
  r.querySelector('.db').textContent=(db<=-89.5?'-∞':db.toFixed(1))+' dB';
  send(p,i,'fader',+v.toFixed(4));
}
function zeroFader(p,i){
  const r=document.getElementById('r-'+p+'-'+i); if(!r) return;
  const sl=r.querySelector('input');
  sl.value=0.75; sl.dataset.target=0.75; delete anim[r.id];
  r.querySelector('.db').textContent='0.0 dB';
  send(p,i,'fader',0.75);
}

function init(){
  let h=''; for(let i=1;i<=32;i++) h+=rowHTML('c',i); $('#chlist').innerHTML=h;
  h='';  for(let i=1;i<=6;i++)  h+=rowHTML('a',i); $('#auxlist').innerHTML=h;
  h='';  for(let i=1;i<=2;i++)  h+=rowHTML('u',i); $('#usblist').innerHTML=h;
  h='';  for(let i=1;i<=8;i++)  h+=rowHTML('x',i); $('#fxretlist').innerHTML=h;
  h='';  for(let i=1;i<=16;i++)  h+=rowHTML('b',i); $('#buslist').innerHTML=h;
  h='';  for(let i=1;i<=6;i++)  h+=rowHTML('t',i); $('#mtxlist').innerHTML=h;
  h='';  for(let i=1;i<=8;i++)  h+=rowHTML('d',i); $('#dcalist').innerHTML=h;
  $('#mainrow').innerHTML=rowHTML('m',0);
  buildFx();
  document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
    document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); $('#pg-'+b.dataset.p).classList.add('on');
  });
  poll();
  pollMeters();
}

function drag(p,i,el){
  const r=el.closest('.row');
  const db=dbOf(+el.value);
  r.querySelector('.db').textContent=(+el.value<=0.001?'-∞':db.toFixed(1))+' dB';
  send(p,i,'fader',+el.value);
}
function dragEnd(){}
function toggle(p,i,el){
  const on = el.classList.contains('on') ? 1 : 0;
  send(p,i,'on',on);
}
// 写入节流: 拖动中 50ms 窗口合并, 多条走 /api/setbatch, 少请求量
const pend={}; let pendT=null;
function send(p,i,param,value,bus){
  const KMAP={c:'ch',b:'bus',d:'dca',m:'main',a:'auxin',u:'usb',x:'fxret',t:'mtx',S:'send'};
  const kind=KMAP[p]||'ch';
  const it={kind:kind,idx:i,param:param,value:value};
  // 发送是二维目标: bus 一起进合并键, 否则 CH01→Bus1 和 CH01→Bus2 会互相覆盖
  if(kind==='send') it.bus=bus|0;
  pend[kind+'|'+i+'|'+(kind==='send'?(bus|0):'')+'|'+param]=it;
  if(!pendT) pendT=setTimeout(flushSend,50);
}
function flushSend(){
  pendT=null;
  const arr=Object.keys(pend).map(k=>pend[k]);
  for(const k in pend) delete pend[k];
  if(!arr.length) return;
  fetch(arr.length===1?'/api/set':'/api/setbatch',
    {method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify(arr.length===1?arr[0]:{sets:arr})});
}
// ---- 通道参数面板: 前置 / 动态 / 均衡 / 发送 ----
// 声明式控件表: f=字段名 必须与后端 params.py 的 ParamSpec 一致 (量程/档位也一致),
// 改任一边都要同步改另一边 —— test_params.py 会把不一致打成红灯。
// t: num=滑块+数值 / bool=两态按钮 / enum=下拉
// 顺序必须与 CH_PARAM_SPECS 完全一致 (回归按序比对)。
const EQ_TYPES=[[0,'LCut'],[1,'LShv'],[2,'PEQ'],[3,'VEQ'],[4,'HShv'],[5,'HCut']];
// 侧链源 (keysrc): 协议 int[0..64] {OFF,In01..32,Aux1..6,USB L/R,Fx1L..Fx4R,Bus01..16}
// 顺序即索引, 与后端 params._KEYS 逐项一致 (回归会比对长度与顺序)。
const KEYS_OPTS=[[0,'OFF'],[1,'In01'],[2,'In02'],[3,'In03'],[4,'In04'],[5,'In05'],[6,'In06'],[7,'In07'],[8,'In08'],[9,'In09'],[10,'In10'],[11,'In11'],[12,'In12'],[13,'In13'],[14,'In14'],[15,'In15'],[16,'In16'],[17,'In17'],[18,'In18'],[19,'In19'],[20,'In20'],[21,'In21'],[22,'In22'],[23,'In23'],[24,'In24'],[25,'In25'],[26,'In26'],[27,'In27'],[28,'In28'],[29,'In29'],[30,'In30'],[31,'In31'],[32,'In32'],[33,'Aux1'],[34,'Aux2'],[35,'Aux3'],[36,'Aux4'],[37,'Aux5'],[38,'Aux6'],[39,'USB L'],[40,'USB R'],[41,'Fx1L'],[42,'Fx1R'],[43,'Fx2L'],[44,'Fx2R'],[45,'Fx3L'],[46,'Fx3R'],[47,'Fx4L'],[48,'Fx4R'],[49,'Bus01'],[50,'Bus02'],[51,'Bus03'],[52,'Bus04'],[53,'Bus05'],[54,'Bus06'],[55,'Bus07'],[56,'Bus08'],[57,'Bus09'],[58,'Bus10'],[59,'Bus11'],[60,'Bus12'],[61,'Bus13'],[62,'Bus14'],[63,'Bus15'],[64,'Bus16']];
// 插入点目标: int[0..22] {OFF, FX1L..FX8R, AUX1..6}
const INS_OPTS=[[0,'OFF'],[1,'FX1L'],[2,'FX1R'],[3,'FX2L'],[4,'FX2R'],[5,'FX3L'],[6,'FX3R'],[7,'FX4L'],[8,'FX4R'],[9,'FX5L'],[10,'FX5R'],[11,'FX6L'],[12,'FX6R'],[13,'FX7L'],[14,'FX7R'],[15,'FX8L'],[16,'FX8R'],[17,'AUX1'],[18,'AUX2'],[19,'AUX3'],[20,'AUX4'],[21,'AUX5'],[22,'AUX6']];
// 侧链滤波器类型: enum int[0..8]
const FILTER_TYPES=[[0,'LC6'],[1,'LC12'],[2,'HC6'],[3,'HC12'],[4,'1.0'],[5,'2.0'],[6,'3.0'],[7,'5.0'],[8,'10.0']];
const GATE_MODES=[[0,'EXP2'],[1,'EXP3'],[2,'EXP4'],[3,'GATE'],[4,'DUCK']];
const CH_PARAM_UI=[
  {f:'trim',       l:'增益',     t:'num',  u:'dB',     lo:-18, hi:18,  step:0.25, dec:2, dv:0},
  {f:'invert',     l:'反相',     t:'bool', lb:['正常','反相']},
  {f:'delay_on',   l:'延时',     t:'bool', lb:['关','开']},
  {f:'delay_ms',   l:'延时时间', t:'num',  u:'ms',     lo:0.3, hi:500, step:0.1,  dec:1, dv:0.3, dep:'delay_on'},
  {f:'hpf_on',     l:'低切',     t:'bool', lb:['关','开']},
  {f:'hpf_slope',  l:'低切斜率', t:'enum', u:'dB/oct', opts:[[12,'12'],[18,'18'],[24,'24']], dep:'hpf_on'},
  {f:'hpf_f',      l:'低切频率', t:'num',  u:'Hz',     lo:20,  hi:400, step:1,    dec:0, dv:80, dep:'hpf_on', lg:1},
  {f:'ins_on',     l:'插入',     t:'bool', lb:['关','开']},
  {f:'ins_pos',    l:'插入位置', t:'enum', opts:[[0,'PRE'],[1,'POST']], dep:'ins_on'},
  {f:'ins_sel',    l:'插入目标', t:'enum', opts:INS_OPTS, dep:'ins_on'},
  {f:'gate_on',    l:'噪声门',   t:'bool', lb:['关','开']},
  {f:'gate_thr',   l:'门限',     t:'num',  u:'dB',     lo:-80, hi:0,   step:0.5,  dec:1, dv:-40, dep:'gate_on'},
  {f:'gate_range', l:'门范围',   t:'num',  u:'dB',     lo:3,   hi:60,  step:1,    dec:0, dv:15, dep:'gate_on'},
  // ---- 噪声门: 补齐的 8 个旋钮 (对齐协议表) ----
  {f:'gate_mode',  l:'门类型',   t:'enum', opts:GATE_MODES, dep:'gate_on'},
  {f:'gate_attack',l:'门启动',   t:'num',  u:'ms',     lo:0,   hi:120, step:1,    dec:0, dv:10, dep:'gate_on'},
  {f:'gate_hold',  l:'门保持',   t:'num',  u:'ms',     lo:0.02,hi:2000,step:0.01, dec:1, dv:20, dep:'gate_on', lg:1},
  {f:'gate_release',l:'门释放',  t:'num',  u:'ms',     lo:5,   hi:4000,step:1,    dec:0, dv:200,dep:'gate_on', lg:1},
  {f:'gate_keysrc',l:'门侧链源', t:'enum', opts:KEYS_OPTS, dep:'gate_on'},
  {f:'gate_filter_on',  l:'门侧链滤波', t:'bool', lb:['关','开'], dep:'gate_on'},
  {f:'gate_filter_type',l:'门滤波类型', t:'enum', opts:FILTER_TYPES, dep:'gate_filter_on'},
  {f:'gate_filter_f',   l:'门滤波频率', t:'num',  u:'Hz', lo:20, hi:20000, step:1, dec:0, dv:1000, dep:'gate_filter_on', lg:1},
  // ---- 动态 (压缩/扩展) ----
  {f:'dyn_on',     l:'压缩',     t:'bool', lb:['关','开']},
  {f:'dyn_mode',   l:'动态类型', t:'enum', opts:[[0,'COMP'],[1,'EXP']], dep:'dyn_on'},
  {f:'dyn_det',    l:'检波',     t:'enum', opts:[[0,'PEAK'],[1,'RMS']], dep:'dyn_on'},
  {f:'dyn_env',    l:'包络',     t:'enum', opts:[[0,'LIN'],[1,'LOG']], dep:'dyn_on'},
  {f:'dyn_thr',    l:'压缩阈值', t:'num',  u:'dB',     lo:-80, hi:0,   step:0.5,  dec:1, dv:-20, dep:'dyn_on'},
  {f:'dyn_ratio',  l:'压缩比',   t:'enum', opts:[[1.1,'1.1:1'],[1.3,'1.3:1'],[1.5,'1.5:1'],[2,'2:1'],[2.5,'2.5:1'],[3,'3:1'],[4,'4:1'],[5,'5:1'],[7,'7:1'],[10,'10:1'],[20,'20:1'],[100,'100:1']], dep:'dyn_on'},
  {f:'dyn_knee',   l:'拐点',     t:'num',  u:'',       lo:0,   hi:5,   step:1,    dec:0, dv:2, dep:'dyn_on'},
  {f:'dyn_mgain',  l:'补偿增益', t:'num',  u:'dB',     lo:0,   hi:24,  step:0.5,  dec:1, dv:0, dep:'dyn_on'},
  {f:'dyn_attack', l:'启动时间', t:'num',  u:'ms',     lo:0,   hi:120, step:1,    dec:0, dv:10, dep:'dyn_on'},
  {f:'dyn_hold',   l:'保持时间', t:'num',  u:'ms',     lo:0.02,hi:2000,step:0.01, dec:1, dv:20, dep:'dyn_on', lg:1},
  {f:'dyn_release',l:'释放时间', t:'num',  u:'ms',     lo:5,   hi:4000,step:1,    dec:0, dv:200, dep:'dyn_on', lg:1},
  {f:'dyn_pos',    l:'压缩位置', t:'enum', opts:[[0,'PRE'],[1,'POST']], dep:'dyn_on'},
  {f:'dyn_keysrc', l:'压缩侧链源', t:'enum', opts:KEYS_OPTS, dep:'dyn_on'},
  {f:'dyn_mix',    l:'干湿比',   t:'num',  u:'%',      lo:0,   hi:100, step:5,    dec:0, dv:100, dep:'dyn_on'},
  {f:'dyn_auto',   l:'自动增益', t:'bool', lb:['关','开'], dep:'dyn_on'},
  {f:'dyn_filter_on',   l:'侧链滤波', t:'bool', lb:['关','开'], dep:'dyn_on'},
  {f:'dyn_filter_type', l:'侧链滤波类型', t:'enum', opts:FILTER_TYPES, dep:'dyn_filter_on'},
  {f:'dyn_filter_f',    l:'侧链滤波频率', t:'num',  u:'Hz', lo:20, hi:20000, step:1, dec:0, dv:1000, dep:'dyn_filter_on', lg:1},
  // ---- 均衡 4 段 ----
  {f:'eq_on',      l:'均衡',     t:'bool', lb:['关','开']},
  {f:'eq1_type',   l:'EQ1 类型', t:'enum', opts:EQ_TYPES, dep:'eq_on'},
  {f:'eq1_f',      l:'EQ1 频率', t:'num',  u:'Hz',     lo:20,  hi:20000, step:1,  dec:0, dv:100, dep:'eq_on', lg:1},
  {f:'eq1_g',      l:'EQ1 增益', t:'num',  u:'dB',     lo:-15, hi:15,  step:0.25, dec:2, dv:0, dep:'eq_on'},
  {f:'eq1_q',      l:'EQ1 Q值',  t:'num',  u:'',       lo:0.3, hi:10,  step:0.01, dec:2, dv:2, dep:'eq_on'},
  {f:'eq2_type',   l:'EQ2 类型', t:'enum', opts:EQ_TYPES, dep:'eq_on'},
  {f:'eq2_f',      l:'EQ2 频率', t:'num',  u:'Hz',     lo:20,  hi:20000, step:1,  dec:0, dv:500, dep:'eq_on', lg:1},
  {f:'eq2_g',      l:'EQ2 增益', t:'num',  u:'dB',     lo:-15, hi:15,  step:0.25, dec:2, dv:0, dep:'eq_on'},
  {f:'eq2_q',      l:'EQ2 Q值',  t:'num',  u:'',       lo:0.3, hi:10,  step:0.01, dec:2, dv:2, dep:'eq_on'},
  {f:'eq3_type',   l:'EQ3 类型', t:'enum', opts:EQ_TYPES, dep:'eq_on'},
  {f:'eq3_f',      l:'EQ3 频率', t:'num',  u:'Hz',     lo:20,  hi:20000, step:1,  dec:0, dv:2000, dep:'eq_on', lg:1},
  {f:'eq3_g',      l:'EQ3 增益', t:'num',  u:'dB',     lo:-15, hi:15,  step:0.25, dec:2, dv:0, dep:'eq_on'},
  {f:'eq3_q',      l:'EQ3 Q值',  t:'num',  u:'',       lo:0.3, hi:10,  step:0.01, dec:2, dv:2, dep:'eq_on'},
  {f:'eq4_type',   l:'EQ4 类型', t:'enum', opts:EQ_TYPES, dep:'eq_on'},
  {f:'eq4_f',      l:'EQ4 频率', t:'num',  u:'Hz',     lo:20,  hi:20000, step:1,  dec:0, dv:8000, dep:'eq_on', lg:1},
  {f:'eq4_g',      l:'EQ4 增益', t:'num',  u:'dB',     lo:-15, hi:15,  step:0.25, dec:2, dv:0, dep:'eq_on'},
  {f:'eq4_q',      l:'EQ4 Q值',  t:'num',  u:'',       lo:0.3, hi:10,  step:0.01, dec:2, dv:2, dep:'eq_on'},
];
// 发送 (二维: 通道 × Bus)。取值不走 /api/state, 单独 /api/sends?ch=N。
// 电平在线上是 0..1, 界面上按 % 显示 —— pct:1 标记这一层换算只在 UI 发生。
const SEND_UI=[
  {f:'send_on',    l:'发送开关', t:'bool', lb:['关','开']},
  // 协议类型 level: 161 档 = 0..100% 按 0.625% 等分 (f=k/160)。
  // 滑块仍按 % 走 (手机上百分比好理解), 但标签同时给出真台显示的 dB ——
  // 50% 在真台是 -10 dB, 不是"一半音量", 只写百分比会误导。
  {f:'send_level', l:'发送电平', t:'num',  u:'%', lo:0, hi:100, step:0.625, dec:1, dv:75, pct:1, db:1},
  // 官方枚举 {<-EQ, EQ->, PRE, POST, GRP}; 真台按 1&2 / 3&4 成对共享
  {f:'send_tap',   l:'发送取点', t:'enum', opts:[[0,'<-EQ'],[1,'EQ->'],[2,'PRE'],[3,'POST'],[4,'GRP']]},
];
const CH_TABS=[['pre','前置'],['gate','门'],['dyn','动态'],['eq','均衡'],['send','发送']];
function chpTabOf(f){
  if(f.indexOf('send_')===0) return 'send';
  if(f.indexOf('gate_')===0) return 'gate';
  if(f.indexOf('dyn_')===0) return 'dyn';
  if(f.indexOf('eq')===0) return 'eq';
  return 'pre';
}
function chpSpec(f){
  for(const r of CH_PARAM_UI) if(r.f===f) return r;
  for(const r of SEND_UI) if(r.f===f) return r;
  return null;
}
let chpIdx=0, chpBus=1, chpSend={}, chpSendT=null, chpCurTab='pre';
// 对数滑块: 位置 0..1000 ↔ 真实值 lo*(hi/lo)^(pos/1000)。
// 频率类参数 (低切 / EQ 四段) 必须用它 —— 线性刻度下 20..20000 在手机上
// 一像素跳几十 Hz, 想设 100 Hz 只能靠运气, 而 100 Hz 恰恰是最常用的点。
const CHP_POS_MAX=1000;
function chpPos(r,v){
  const lo=Math.max(r.lo,1e-6), hi=r.hi;
  const x=Math.min(Math.max(+v||lo,lo),hi);
  return Math.round(Math.log(x/lo)/Math.log(hi/lo)*CHP_POS_MAX);
}
function chpVal(r,pos){
  const lo=Math.max(r.lo,1e-6), hi=r.hi;
  return lo*Math.pow(hi/lo,Math.min(Math.max(+pos||0,0),CHP_POS_MAX)/CHP_POS_MAX);
}
function chpFmt(r,v){
  const x=+v||0;
  const txt=(r.dec?x.toFixed(r.dec):String(Math.round(x)));
  if(r.db) return txt+'%'+(x<=0?' (-∞)':' ('+dbOf(x/100).toFixed(1)+' dB)');
  return txt+(r.u?(' '+r.u):'');
}
// 数值标签做成可点: 手机上滑块只能"拖个大概", 要精确到 0.25dB 必须能敲键盘。
// 点一下变成 number 输入框 (inputmode=decimal 直接弹数字键盘), 回车/失焦提交, Esc 取消。
function chpRowHTML(r){
  const id='chp-'+r.f;
  const o='ondblclick';
  if(r.t==='bool')
    return `<div class="chp-row" id="${id}-row"><span class="chp-l">${r.l}</span>`
      +`<button type="button" class="chp-tog" id="${id}" onclick="chpTog('${r.f}')">—</button>`
      +`<span class="chp-v"></span></div>`;
  if(r.t==='enum')
    return `<div class="chp-row" id="${id}-row"><span class="chp-l">${r.l}</span>`
      +`<select class="nkin" id="${id}" onchange="chpSet('${r.f}',this.value)">`
      +r.opts.map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('')+`</select>`
      +`<span class="chp-v">${r.u||''}</span></div>`;
  return `<div class="chp-row" id="${id}-row"><span class="chp-l">${r.l}</span>`
    +`<input type="range" id="${id}" min="${r.lg?0:r.lo}" max="${r.lg?CHP_POS_MAX:r.hi}"`
    +` step="${r.lg?1:r.step}"`
    +` oninput="chpSlide('${r.f}',this)">`
    +`<span class="chp-v" id="${id}-v" onclick="chpEdit('${r.f}')" title="点一下输入精确值"></span></div>`;
}
function chpBodyHTML(){
  let h='';
  CH_TABS.forEach(([k])=>{
    const rows=(k==='send'?SEND_UI:CH_PARAM_UI.filter(r=>chpTabOf(r.f)===k));
    h+=`<div class="chp-tab" data-tab="${k}">`
      +(k==='send'?`<div class="chp-row"><span class="chp-l">Bus</span>`
        +`<select class="nkin" id="chpBus" onchange="chpBusChg(this.value)">`
        +Array.from({length:16},(_,i)=>`<option value="${i+1}">Bus ${String(i+1).padStart(2,'0')}</option>`).join('')
        +`</select><span class="chp-v">16 路</span></div>`:'')
      +rows.map(chpRowHTML).join('')+'</div>';
  });
  return h;
}
// 控台能力 (数字增益量程) 由服务端给: 旧固件 ±12, 新固件 ±18。
// 前端表里写的是 18 (新固件的规格), 老固件下把滑块范围收窄, 免得拖到 18 被弹回。
let CHP_TRIM=18;
function chpLoadLimits(){
  fetch('/api/info').then(r=>r.json()).then(d=>{
    if(!d||!d.trimRange||d.trimRange===CHP_TRIM) return;
    CHP_TRIM=+d.trimRange;
    const el=document.getElementById('chp-trim');
    if(el){ el.min=-CHP_TRIM; el.max=CHP_TRIM; }
    const r=chpSpec('trim');
    if(r){ r.lo=-CHP_TRIM; r.hi=CHP_TRIM; }
  }).catch(()=>{});
}
function openChParams(i){
  chpIdx=i; chpCurTab='pre';
  chpLoadLimits();
  const nm=nameOf('ch',i);
  $('#chpTitle').textContent='CH '+String(i).padStart(2,'0')+(nm?' · '+nm:'');
  $('#chParamBody').innerHTML=chpBodyHTML();
  chpTab('pre');
  $('#chParamModal').classList.add('on');
  document.body.style.overflow='hidden';
  chpSync();
}
function closeChParams(){
  $('#chParamModal').classList.remove('on'); document.body.style.overflow='';
}
function chpTab(k){
  chpCurTab=k;
  const body=$('#chParamBody'); if(!body) return;
  body.querySelectorAll('.chp-tab').forEach(el=>{
    el.classList.toggle('on', el.dataset.tab===k);
  });
  const bar=$('#chpTabBar');
  if(bar) bar.querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.tab===k));
  if(k==='send') chpLoadSends();
  else chpSync();
}
// 状态回读: 只更新控件, 不重建 (拖动中不动滑块, 手机上会把手指下的位置拽回去)
function chpSync(){
  const m=$('#chParamModal');
  if(!m||!m.classList.contains('on')||!S||!S.ch) return;
  const row=S.ch[String(chpIdx)]; if(!row) return;
  CH_PARAM_UI.forEach(r=>{
    const el=document.getElementById('chp-'+r.f); if(!el) return;
    const v=row[r.f];
    if(r.t==='bool'){
      const on=!!v;
      el.textContent=on?r.lb[1]:r.lb[0];
      el.classList.toggle('on',on);
    } else if(r.t==='enum'){
      if(document.activeElement!==el) el.value=String(+v);
    } else if(v!==undefined&&v!==null){
      if(document.activeElement!==el) el.value=r.lg?chpPos(r,v):v;
      const lab=document.getElementById('chp-'+r.f+'-v');
      if(lab&&!lab.querySelector('input')) lab.textContent=chpFmt(r,v);
    }
    // 从属项 (延时时间/低切斜率…) 在主开关关闭时变淡 —— 仍可调, 只是提示"当前不生效"
    const wrap=document.getElementById('chp-'+r.f+'-row');
    if(wrap&&r.dep) wrap.classList.toggle('off',!row[r.dep]);
  });
}
function chpSendRow(){ return chpSend[String(chpBus)]||{level:0,on:0,tap:4}; }
function chpLoadSends(){
  if(!$('#chParamModal')||!$('#chParamModal').classList.contains('on')) return;
  fetch('/api/sends?ch='+chpIdx).then(r=>r.json()).then(d=>{
    chpSend=d.sends||{}; chpSyncSend();
  }).catch(()=>{});
}
function chpSyncSend(){
  const row=chpSendRow();
  SEND_UI.forEach(r=>{
    const el=document.getElementById('chp-'+r.f); if(!el) return;
    let v=row[r.f==='send_level'?'level':(r.f==='send_on'?'on':'tap')];
    if(r.pct) v=(+v||0)*100;
    if(r.t==='bool'){
      el.textContent=v?r.lb[1]:r.lb[0]; el.classList.toggle('on',!!v);
    } else if(r.t==='enum'){
      if(document.activeElement!==el) el.value=String(Math.round(+v||0));
    } else {
      if(document.activeElement!==el) el.value=v;
      const lab=document.getElementById('chp-'+r.f+'-v');
      if(lab&&!lab.querySelector('input')) lab.textContent=chpFmt(r,v);
    }
  });
}
function chpBusChg(v){ chpBus=+v||1; chpSyncSend(); }
function chpIsSend(f){ return f.indexOf('send_')===0; }
// 写库: 发送走 /api/setbatch 的 bus 字段, 其余按通道参数
function chpWrite(f,v){
  if(chpIsSend(f)){
    send('S',chpIdx,f,v,chpBus);
    const row=chpSend[String(chpBus)]||(chpSend[String(chpBus)]={level:0,on:0,tap:4});
    const key=(f==='send_level'?'level':(f==='send_on'?'on':'tap'));
    row[key]=v;
    clearTimeout(chpSendT); chpSendT=setTimeout(chpLoadSends,500);
  } else {
    send('c',chpIdx,f,v);
  }
}
function chpSlide(f,el){
  const r=chpSpec(f); if(!r) return;
  const v=r.lg?chpVal(r,el.value):(+el.value), w=r.pct?v/100:v;
  const lab=document.getElementById('chp-'+f+'-v');
  if(lab&&!lab.querySelector('input')) lab.textContent=chpFmt(r,v);
  chpWrite(f,w);
}
function chpSet(f,v){
  const r=chpSpec(f); if(!r) return;
  const nv=(r.t==='enum'&&r.opts&&r.opts.some(o=>/^[A-Z]/.test(String(o[1]))))?+v:+v;
  chpWrite(f,nv);
}
function chpTog(f){
  if(chpIsSend(f)){
    const cur=chpSendRow().on?1:0, nv=cur?0:1;
    chpWrite(f,nv);
    const r=chpSpec(f), el=document.getElementById('chp-'+f);
    if(r&&el){ el.textContent=nv?r.lb[1]:r.lb[0]; el.classList.toggle('on',!!nv); }
    return;
  }
  if(!S||!S.ch) return;
  const row=S.ch[String(chpIdx)]; if(!row) return;
  const nv=row[f]?0:1;
  chpWrite(f,nv);
  // 乐观切换: 写盘+轮询有 ~250ms 延迟, 不先动一下会觉得"点了没反应"
  const r=chpSpec(f), el=document.getElementById('chp-'+f);
  if(r&&el){ el.textContent=nv?r.lb[1]:r.lb[0]; el.classList.toggle('on',!!nv); }
}
// 键盘精调: 点数值 → 原地变输入框
function chpEdit(f){
  const r=chpSpec(f); if(!r||r.t!=='num') return;
  const lab=document.getElementById('chp-'+f+'-v'); if(!lab) return;
  if(lab.querySelector('input')) return;
  const cur=chpIsSend(f)
    ? (()=>{ const row=chpSendRow(); const v=row[f==='send_level'?'level':'on']||0; return r.pct?v*100:v; })()
    : (()=>{ const row=(S&&S.ch)?S.ch[String(chpIdx)]:null; const v=row?row[f]:undefined; return (v===undefined||v===null)?(r.dv||0):v; })();
  const step=r.lg?1:(r.dec?Math.max(r.step,Math.pow(10,-r.dec)):r.step);
  lab.innerHTML=`<input class="chp-in" type="number" inputmode="decimal"`
    +` min="${r.lo}" max="${r.hi}" step="${step}" value="${(+cur).toFixed(r.dec)}">`;
  const inp=lab.querySelector('input');
  let done=false;
  const commit=(ok)=>{
    if(done) return; done=true;
    const v=parseFloat(inp.value);
    lab.textContent=chpFmt(r, isNaN(v)?cur:(r.pct?Math.min(Math.max(v,0),100)/100:Math.min(Math.max(v,r.lo),r.hi))* (r.pct?100:1));
    if(ok&&!isNaN(v)){
      let w=r.pct?Math.min(Math.max(v,0),100)/100:Math.min(Math.max(v,r.lo),r.hi);
      chpWrite(f,w);
      const sl=document.getElementById('chp-'+f);
      if(sl&&document.activeElement!==sl) sl.value=r.lg?chpPos(r,w):w;
    }
  };
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){ e.preventDefault(); commit(true); inp.blur(); }
    else if(e.key==='Escape'){ e.preventDefault(); commit(false); inp.blur(); }
  });
  inp.addEventListener('blur',()=>commit(true));
  inp.focus(); inp.select();
}
function runShow(id){ fetch('/api/show',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})}); }
function stopShow(){ fetch('/api/show',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:null})}); }
// ⚠️ 保存类接口失败时必须让用户看见: 后端对"落盘失败/非法输入"返回
//    {"ok":false,"error":"..."} (HTTP 4xx/5xx), 曾经的 fire-and-forget 会把
//    "没存上"表现成"存好了" —— 持久化假成功的最外一环。
function api(path,obj){ fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(obj)}).then(r=>r.json().catch(()=>({}))).then(d=>{
      if(d&&(d.error||d.ok===false)) showToast(d.error||'操作失败');
    }).catch(()=>{}); }
// ---- 台面预设：可选范围的保存 ----
const PS_GROUPS=[['ch','输入通道'],['bus','Mix Bus'],['dca','DCA'],['auxin','Aux In'],
  ['usb','USB'],['fxret','FX 返回'],['mtx','Matrix'],['main','主输出'],
  ['fx','FX 机架'],['scene','场景号']];
const PS_PARAMS=[['fader','推子'],['on','静音'],['name','名称'],['dca','DCA 归属'],
  ['send_level','发送电平'],['send_on','发送开关'],['send_tap','发送取点']]
  .concat(CH_PARAM_UI.map(r=>[r.f,r.l]));   // 12 个通道处理参数也能单独勾选存/回归
const PS_CAP={ch:32,bus:16,dca:8,auxin:6,usb:2,fxret:8,mtx:6,main:1,fx:8,scene:1};
const PS_PATH_GROUPS=['ch','bus','dca','auxin','usb','fxret','mtx','main'];
let psGroups=['ch','bus','dca','auxin','usb','fxret','mtx','main'], psParams=['fader','on'];
function openSnapModal(){
  psGroups=['ch','bus','dca','auxin','usb','fxret','mtx','main']; psParams=['fader','on'];
  $('#snapName').value=''; $('#snapIdx').value='';
  renderPsChips(); $('#snapModal').classList.add('on'); document.body.style.overflow='hidden';
}
function closeSnapModal(){ $('#snapModal').classList.remove('on'); document.body.style.overflow=''; }
function psUsable(g,p){
  if(p==='dca') return g==='ch';
  if(p==='fader'||p==='on'||p==='name') return g!=='fx'&&g!=='scene';
  if(chpSpec(p)) return g==='ch';      // 增益/延时/低切… 只有输入通道有
  return false;
}
function renderPsChips(){
  const gEl=$('#snapGroups'), pEl=$('#snapParams');
  if(!gEl||!pEl) return;
  gEl.innerHTML=PS_GROUPS.map(([g,l])=>{
    const on=psGroups.indexOf(g)>=0;
    return `<span class="chip${on?' on':''}" onclick="psTog('g','${g}',this)">${l}</span>`;
  }).join('');
  pEl.innerHTML=PS_PARAMS.map(([p,l])=>{
    const usable=psGroups.some(g=>psUsable(g,p));
    const on=psParams.indexOf(p)>=0;
    return `<span class="chip${on?' on':''}${usable?'':' dis'}" ${usable?'':'title="当前所选组不支持该参数"'}
      onclick="psTog('p','${p}',this)">${l}</span>`;
  }).join('');
  psCount();
}
function psTog(what,v,el){
  if(el.classList.contains('dis')) return;
  const arr=what==='g'?psGroups:psParams;
  const i=arr.indexOf(v);
  if(i>=0){ if(arr.length<=1) return; arr.splice(i,1); } else arr.push(v);
  renderPsChips();
}
function psIdxList(g){
  const raw=($('#snapIdx')?$('#snapIdx').value:'').trim();
  if(!raw) return null;
  const cap=PS_CAP[g]||0, out=[];
  raw.split(',').forEach(part=>{
    part=part.trim();
    const m=part.match(/^(\d+)\s*-\s*(\d+)$/);
    if(m){
      let a=+m[1], b=+m[2]; if(a>b){const t=a;a=b;b=t;}
      for(let i=a;i<=b;i++){ if(i>=1&&i<=cap&&out.indexOf(i)<0) out.push(i); }
    } else {
      const n=+part;
      if(n>=1&&n<=cap&&out.indexOf(n)<0) out.push(n);
    }
  });
  return out.length?out.sort((x,y)=>x-y):null;
}
function psScope(){
  const idx={};
  PS_PATH_GROUPS.forEach(g=>{
    if(psGroups.indexOf(g)<0) return;
    const list=psIdxList(g);
    if(list&&g!=='main') idx[g]=list;
  });
  // FX 机架/场景号没有"推子/静音"这类参数：只勾它们时自动带上整槽/场景，
  // 否则会存出一条"范围里什么都没有"的空预设
  const params=psParams.slice();
  if(psGroups.indexOf('fx')>=0 && params.indexOf('slot')<0) params.push('slot');
  if(psGroups.indexOf('scene')>=0 && params.indexOf('scene')<0) params.push('scene');
  return {groups:psGroups.slice(),params:params,indices:idx};
}
function psCount(){
  const sc=psScope(); let n=0;
  PS_PATH_GROUPS.forEach(g=>{
    if(psGroups.indexOf(g)<0) return;
    const rows=(g==='main')?1:(sc.indices[g]?sc.indices[g].length:(PS_CAP[g]||0));
    psParams.forEach(p=>{ if(psUsable(g,p)) n+=rows; });
  });
  if(psGroups.indexOf('fx')>=0) n+=8*4+8*3;
  if(psGroups.indexOf('scene')>=0) n+=1;
  const el=$('#snapCount');
  if(el) el.textContent='将保存约 '+n+' 个值（FX 整槽按 8 槽 × 4 属性 + 算法参数计）';
}
function doSaveSnap(){
  const sc=psScope();
  if(!sc.groups.length) return;
  api('/api/snapshot',{action:'save',name:($('#snapName').value||'').trim(),scope:sc});
  closeSnapModal();
}
function recallSnap(id,sel){ api('/api/snapshot',{action:'recall',id:id,seconds:+sel.value}); }
function delSnap(id){ api('/api/snapshot',{action:'delete',id:id}); }
function stopRecall(){ api('/api/snapshot',{action:'stop'}); }
function setKeyCfg(){ api('/api/keycfg',{step_db:+$('#stepSel').value,ramp:+$('#rampSel').value}); }

// ---- 控台驱动切换 ----
function setConsole(id){
  fetch('/api/ctrlprofile',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})}).then(r=>r.json()).then(d=>{
      if(!d.ok){ showToast(d.error||'切换失败'); return; }
      showToast('控台 → '+d.label+(d.warning?'（未标定）':'')
        +(d.fxNote?(' · '+d.fxNote):''));
    }).catch(()=>showToast('切换失败'));
}
// ---- 网络端口 (设置页) ----
// 250ms 的状态轮询会重渲染设置页 —— 用户已经改了但还没保存的输入不能被冲掉,
// 所以用 window.portDirty 标记「手上有未保存的改动」, 有标记时就不回填输入框。
window.portDirty=false;
function portNum(v,allowZero){
  v=String(v==null?'':v).trim();
  if(v==='') return null;                       // 空 = 恢复该类端口的内置默认
  if(!/^[0-9]+$/.test(v)) return NaN;           // 非纯数字 → 明确报错, 不静默
  const n=parseInt(v,10);
  if(allowZero && n===0) return 0;              // 网页端口 0 = 关闭
  return (n>=1 && n<=65535) ? n : NaN;
}
function savePorts(){
  const u=portNum($('#pUdp').value,false), w=portNum($('#pWeb').value,true);
  if(isNaN(u)){ showToast('OSC/UDP 端口需为 1–65535 的整数'); return; }
  if(isNaN(w)){ showToast('网页端口需为 0–65535 的整数（0 = 关闭网页）'); return; }
  if(w!==null && w>0 && w===(u===null?(S&&S.ports?S.ports.defUdp:10023):u)){
    showToast('两个端口不能相同'); return;
  }
  fetch('/api/ports',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({udp:u,web:w})})
   .then(r=>r.json()).then(d=>{
     if(!d.ok){ showToast(d.error||'保存失败'); return; }
     window.portDirty=false;
     showToast(d.restart?'已保存 —— 重启模拟器后生效':'已保存，与当前生效端口一致');
   }).catch(()=>showToast('保存失败'));
}
function resetPorts(){
  fetch('/api/ports',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({udp:null,web:null})})
   .then(r=>r.json()).then(d=>{
     if(!d.ok){ showToast(d.error||'恢复失败'); return; }
     window.portDirty=false;
     showToast('已恢复默认端口'+(d.restart?'，重启后生效':''));
   }).catch(()=>showToast('恢复失败'));
}
function renderPorts(){
  const p=S&&S.ports; if(!p) return;
  const u=$('#pUdp'), w=$('#pWeb');
  if(!window.portDirty){                        // 有未保存改动时不动输入框
    if(u && document.activeElement!==u) u.value=(p.savedUdp==null?'':p.savedUdp);
    if(w && document.activeElement!==w) w.value=(p.savedWeb==null?'':p.savedWeb);
  }
  const st=$('#portStat');
  if(st){
    st.textContent=p.pending?'⚠ 待重启生效':'✓ 当前生效中';
    st.style.color=p.pending?'#d9a13c':'#22c55e';
  }
  const iu=$('#pinfoUdp'), iw=$('#pinfoWeb'), iu2=$('#pinfoUdp2');
  if(iu) iu.textContent=p.udp;
  if(iw) iw.textContent=(p.web>0?p.web:'已关闭');
  if(iu2) iu2.textContent=p.udp;
  const nt=$('#portNote');
  if(nt){
    let h='当前生效: OSC/UDP <b>'+p.udp+'</b> · 网页 <b>'+(p.web>0?p.web:'已关闭')+'</b>'
      +`（内置默认 ${p.defUdp} / ${p.defWeb}）`;
    if(p.envLocked){
      h+='<br>⚠️ 本实例是<b>线上部署</b>，网页端口由平台环境变量指定，在此修改不会生效。';
    } else if(p.savedWeb===0){
      h+='<br>提示: 网页端口填 <b>0</b> 表示关闭网页控制台，只保留 OSC/UDP 服务。';
    }
    if(p.pending){
      h+='<br><b style="color:#d9a13c">已保存但尚未生效</b> —— 当前仍在用旧端口，'
        +'请 Ctrl+C 停掉模拟器后重新启动。';
    }
    if(window.portDirty){
      h+='<br>✏️ 输入框里有<b>未保存</b>的改动 —— 点「💾 保存」提交。';
    }
    nt.innerHTML=h;
  }
}
function renderConsole(){
  const sel=$('#ctrlSel');
  if(!sel||!S||!S.console) return;
  const c=S.console;
  if(sel.options.length!==c.all.length){
    sel.innerHTML=c.all.map(p=>
      `<option value="${esc(p.id)}">${esc(p.label)}${p.ready?'':'（待标定）'}</option>`).join('');
  }
  if(document.activeElement!==sel) sel.value=c.id;
  const st=$('#ctrlStat');
  if(st){
    st.textContent=c.ready?(c.verified?'✓ 已标定':'⚠ 未核对'):'⚠ 待标定';
    st.style.color=c.ready?(c.verified?'#22c55e':'#d9a13c'):'#ef4444';
  }
  const nt=$('#ctrlNote');
  if(nt){
    if(c.ready&&c.verified){
      nt.innerHTML=`地址表已与真机核对 · 通道 <b>${c.counts.ch}</b> · Bus <b>${c.counts.bus}</b>`
        +` · DCA <b>${c.counts.dca}</b> · FX <b>${c.counts.fx}</b> 槽`
        +(c.fxLib?` · 效果库 <b>${c.fxLib}</b> 种（混响页已同步）`:'');
    } else if(c.ready){
      nt.innerHTML='⚠️ '+esc(c.note||'地址表未经真机核对');
    } else {
      nt.innerHTML=`⚠️ <b>${esc(c.label)}</b> 的驱动尚未标定：${esc(c.note||'')}<br>`
        +`此状态下所有写入会被<b>拒绝并记日志</b>,卡片不会真正控制到台子 —— 这是刻意的,`
        +`免得蒙错地址写到别的参数上。`;
    }
  }
  const cp=$('#ctrlCaps');
  if(cp){
    cp.textContent=(!c.ready&&c.pending.length)
      ? ('需要提供的地址映射: '+c.pending.slice(0,8).join(' / ')+(c.pending.length>8?' …':''))
      : '';
  }
}

// 控制键长按连发: 按下立即触发一次, 按住 500ms 后每 150ms 重复
let kpT=null,kpI=null;
function kpStart(id){
  api('/api/key',{id:id});
  kpStop();
  kpT=setTimeout(()=>{ kpI=setInterval(()=>api('/api/key',{id:id}),150); },500);
}
function kpStop(){
  if(kpT){ clearTimeout(kpT); kpT=null; }
  if(kpI){ clearInterval(kpI); kpI=null; }
}
document.addEventListener('pointerup',kpStop);
document.addEventListener('pointercancel',kpStop);

// ---- 新增控制键 ----
function updNkIdx(){
  const k=$('#nkKind').value;
  const N={ch:32,bus:16,dca:8,auxin:6,usb:2,fxret:8,mtx:6,main:1}[k]||1;
  let h=''; for(let i=1;i<=N;i++) h+=`<option value="${i}">${esc(dispName(k,i))}</option>`;
  $('#nkIdx').innerHTML=h;
}

// ---- 通道命名编辑器 (设置页) ----
const NM_COUNT={ch:32,bus:16,dca:8,auxin:6,usb:2,fxret:8,mtx:6};
function updNmIdx(){
  const g=$('#nmGroup').value,n=NM_COUNT[g];
  let h='';
  for(let i=1;i<=n;i++){
    const nm=nameOf(g,i);
    h+=`<option value="${i}">${String(i).padStart(2,'0')}${nm?' · '+esc(nm):''}</option>`;
  }
  $('#nmIdx').innerHTML=h;
}
function saveName(){
  api('/api/name',{group:$('#nmGroup').value,idx:+$('#nmIdx').value,name:$('#nmVal').value.trim()});
  $('#nmVal').value='';
}
let editKeyId = null;
function saveKey(){
  const name=$('#nkName').value.trim();
  $('#nkName').classList.toggle('inv', !name);
  if(!name){ keyErr('请填写按键名称(必填)'); return; }
  const btn=$('#keySaveBtn');
  btn.classList.add('btnspin'); btn.textContent='保存中…';
  hideKeyErr();
  const url=editKeyId?'/api/keyedit':'/api/keyadd';
  const payload=editKeyId
    ?{id:editKeyId, icon:$('#nkIcon').value.trim()||'⚡', name:name,
      kind:$('#nkKind').value, idx:+$('#nkIdx').value, sign:+$('#nkSign').value}
    :{icon:$('#nkIcon').value.trim()||'⚡', name:name,
      kind:$('#nkKind').value, idx:+$('#nkIdx').value, sign:+$('#nkSign').value};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)})
  .then(r=>{
    if(!r.ok) throw new Error('服务器错误 '+r.status);
    return r.json();
  })
  .then(d=>{
    if(!d || !d.ok) throw new Error('保存被拒绝');
    $('#nkName').value=''; $('#nkIcon').value='';
    closeKeyModal();
  })
  .catch(err=>keyErr('保存失败: '+(err&&err.message?err.message:'网络异常,请重试')))
  .finally(()=>{
    btn.classList.remove('btnspin'); btn.textContent='💾 添加控制键';
  });
}
function openKeyModal(key){
  editKeyId = key ? key.id : null;
  $('#keyModalTitle').textContent = key ? '✏️ 编辑控制键' : '➕ 新增控制键';
  $('#keySaveBtn').textContent = key ? '💾 保存修改' : '💾 添加控制键';
  $('#keyModal').classList.add('on');
  document.body.style.overflow='hidden';
  if(key){
    $('#nkIcon').value = key.icon || '';
    $('#nkName').value = key.name || '';
    $('#nkKind').value = String(key.kind || '').replace('_fader','');
    updNkIdx();
    $('#nkIdx').value = String(key.idx || 1);
    $('#nkSign').value = key.sign < 0 ? '-1' : '1';
  } else {
    $('#nkIcon').value=''; $('#nkName').value='';
    updNkIdx();
  }
  hideKeyErr();
  setTimeout(()=>$('#nkName').focus(),120);
}
function closeKeyModal(){
  $('#keyModal').classList.remove('on');
  document.body.style.overflow='';
  hideKeyErr();
  editKeyId = null;
  $('#nkName').classList.remove('inv');
}
function editCard(id){
  const c=(S.cards||[]).find(x=>x.id===id);
  if(c) openCardModal(c);
}
function editKey(id){
  const k=(S.quickKeys||[]).find(x=>x.id===id);
  if(k) openKeyModal(k);
}
function keyErr(msg){ const e=$('#keyErr'); e.textContent=msg; e.classList.add('on'); }
function hideKeyErr(){ $('#keyErr').classList.remove('on'); }
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ closeCardModal(); closeKeyModal(); } });
function delKey(id){ api('/api/keydel',{id:id}); }

// ---- 新建演出卡片 ----
let caActs=[];
let caMulti=[];                       // 当前动作类型下勾选的目标编号
const CA_MULTI={mute:'ch',mute_bus:'bus',fade_ch:'ch',fade_bus:'bus'};
const CA_LABEL={mute:'静音通道',mute_bus:'静音 Bus',scene:'切场景',unmute_all:'全部取消静音',wait:'等待',
  fade_all:'全体推子渐变',fade_odd_even:'奇偶过渡',fade_ch:'通道渐变',fade_bus:'Bus 渐变',
  fade_from:'通道从指定值渐变',fade_from_bus:'Bus 从指定值渐变',rel_db:'相对增减 dB',
  set:'通用设置',osc:'原始 OSC'};

// ---- 通用动作 set 的分组 / 参数表（必须与服务端 SET_GROUPS/SET_PARAMS 对齐）----
// 分组是**语义**的,落到哪个地址由控台驱动决定 —— 所以同一张卡片能换控台使用。
const ST_GROUPS=[['ch','输入通道',32],['bus','Bus 输出',16],['dca','DCA 编组',8],
  ['main','主输出',1],['auxin','Aux In',6],['usb','USB 返回',2],['fxret','FX 返回',8],
  ['mtx','Matrix',6],['fx','FX 效果槽',8],['scene','场景',99],
  ['send','发送 (通道→Bus)',32]];
const ST_PARAMS={ch:['fader','on'].concat(CH_PARAM_UI.map(r=>r.f)),
  bus:['fader','on'],dca:['fader','on'],main:['fader','on'],
  auxin:['fader','on'],usb:['fader','on'],fxret:['fader','on'],mtx:['fader','on'],
  fx:['time','predelay','damp','mix','on','type','name'],scene:['load'],
  send:['send_level','send_on','send_tap']};
const ST_PLABEL={fader:'推子 %',on:'开关 (1开启/0静音)',mix:'混合比 (0-1)',
  time:'混响时间 (秒)',predelay:'预延迟 (毫秒)',damp:'阻尼 (0-1)',
  type:'算法 (效果库 61 种, 见效果器页)',name:'名称 (文本)',load:'切场景'};
// 12 个通道参数沿用面板里的中文名与单位 (两边只有这一处定义, 不会漂)
CH_PARAM_UI.forEach(r=>{ ST_PLABEL[r.f]=r.l+(r.u?(' ('+r.u+')'):''); });
function numIn(id,label,def){
  return `<input id="${id}" type="number" value="${def}" placeholder="${label}"
    title="${label}" aria-label="${label}" class="nkin" style="width:84px">`;
}
function curveSel(){
  return `<select id="caCurve" class="nkin"><option value="linear">直线</option><option value="ease_out">先快后慢</option><option value="ease_in">先慢后快</option><option value="smooth">S 形</option></select>`;
}
function chipGrid(g,n){
  let h='<div class="chipgrid" id="caChips">';
  for(let i=1;i<=n;i++){
    const nm=nameOf(g,i);
    h+=`<span class="chip" data-i="${i}" onclick="togChip(this)">${g==='ch'?String(i).padStart(2,'0'):'B'+i}${nm?'<em>'+nm+'</em>':''}</span>`;
  }
  return h+'</div>';
}
function togChip(el){
  el.classList.toggle('on');
  const i=+el.dataset.i;
  const p=caMulti.indexOf(i);
  if(p>=0) caMulti.splice(p,1); else caMulti.push(i);
  hideCardErr();
}
function updCaParams(){
  const k=$('#caKind').value;
  caMulti=[]; hideCardErr();
  let h='';
  if(CA_MULTI[k]) h+=chipGrid(CA_MULTI[k],CA_MULTI[k]==='ch'?32:16);
  if(k==='mute'||k==='mute_bus'){ $('#caParams').innerHTML=h; return; }
  if(k==='scene'){
    h+=`<span class="ds" style="margin:0">场景</span>`+numIn('caScene','场景号',1);
    h+=`<select id="caSceneMode" class="nkin" onchange="caSceneModeChg()">`
      +`<option value="global">全局调用（整场还原）</option>`
      +`<option value="partial">局部调用（只动预设范围）</option></select>`;
    h+=`<span id="caSceneExtra" style="display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap"></span>`;
    h+=`<div class="ds" id="caSceneNote" style="width:100%;margin:2px 0 0;color:#d9a13c"></div>`;
  }
  else if(k==='preset') h+=`<select id="caPreset" class="nkin">${presetOpts()}</select>`
    +numIn('caSec','渐变秒数',2)+`<span class="ds" style="margin:0">秒</span>`+curveSel();
  else if(k==='wait') h+=numIn('caSec','等待秒数',3);
  else if(k==='unmute_all'){ /* 无参数 */ }
  else if(k==='fade_all') h+=numIn('caP1','目标 %',0)+numIn('caSec','秒数',3)+curveSel();
  else if(k==='fade_odd_even') h+=numIn('caP1','奇数 %',80)+numIn('caP2','偶数 %',0)+numIn('caSec','秒数',4)+curveSel();
  else if(k==='fade_ch'||k==='fade_bus') h+=numIn('caP1','到 %',80)+numIn('caSec','秒数',3)+curveSel()
    +`<button type="button" class="learnbtn" onclick="learnTo()">⭐ 取当前值</button>`;
  else if(k==='fade_from') h+=numIn('caCh','通道号',15)+numIn('caP0','从 %',0)+numIn('caP1','到 %',90)+numIn('caSec','秒数',5)+curveSel();
  else if(k==='fade_from_bus') h+=numIn('caBus','Bus 号',1)+numIn('caP0','从 %',0)+numIn('caP1','到 %',90)+numIn('caSec','秒数',5)+curveSel();
  else if(k==='rel_db') h+=`<select id="caGrp" class="nkin" onchange="caMulti=[];updRelChips()">
      <option value="ch">输入通道</option><option value="bus">Bus 输出</option><option value="dca">DCA 编组</option></select>`
    +`<span id="caRelChips" style="display:inline-flex"></span>`
    +numIn('caDb','±dB',3)+numIn('caSec','秒数',2)+curveSel();
  else if(k==='set') h+=setFormHTML();
  else if(k==='osc') h+=oscFormHTML();
  $('#caParams').innerHTML=h;
  if(k==='rel_db') updRelChips();
  if(k==='set') stGroupChg();
  if(k==='scene') caSceneModeChg();
}
// ---- 通用设置表单 ----
function setFormHTML(){
  return `<select id="stGroup" class="nkin" onchange="stGroupChg()">`
    + ST_GROUPS.map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('') + `</select>`
    + `<input id="stId" type="number" value="1" min="1" class="nkin" style="width:78px" title="目标编号">`
    + `<span id="stBusWrap"></span>`
    + `<select id="stParam" class="nkin" onchange="stParamChg()"></select>`
    + `<span id="stValWrap"></span>`
    + `<input id="stDur" type="number" value="0" min="0" step="0.5" class="nkin" style="width:104px"
             title="仅推子类支持渐变;0 = 瞬间到位">`
    + `<button type="button" class="learnbtn" onclick="learnSet()">⤓ 抓取台面值</button>`;
}
function stGroupChg(){
  const g=$('#stGroup').value;
  const ps=ST_PARAMS[g]||['fader'];
  $('#stParam').innerHTML=ps.map(p=>`<option value="${p}">${ST_PLABEL[p]||p}</option>`).join('');
  const idEl=$('#stId');
  const row=ST_GROUPS.find(x=>x[0]===g)||['','',32];
  const cap=row[2]||32;
  if(g==='main'){ idEl.value=0; idEl.disabled=true; }
  else { idEl.disabled=false; idEl.max=cap; if(!+idEl.value||+idEl.value>cap) idEl.value=1; }
  // 发送是二维目标: 编号是通道, 还要一个 Bus。不给这一栏, 用户只能写"第 5 个打包值",
  // 那种数字对内没有任何意义。
  const bw=$('#stBusWrap');
  if(bw) bw.innerHTML=(g==='send')
    ? `<select id="stBus" class="nkin" title="发往哪个 Bus">`
      +Array.from({length:16},(_,i)=>`<option value="${i+1}">Bus ${String(i+1).padStart(2,'0')}</option>`).join('')
      +`</select>`
    : '';
  stParamChg();
}
function stParamChg(){
  const p=$('#stParam').value, w=$('#stValWrap');
  if(!w) return;
  const sp=chpSpec(p);
  if(p==='on') w.innerHTML=`<select id="stValSel" class="nkin"><option value="1">开启</option><option value="0">静音 / 旁通</option></select>`;
  else if(p==='type') w.innerHTML=`<select id="stValSel" class="nkin">`
    + fxAlgoOpts('(清空)') + `</select>`;
  else if(p==='name') w.innerHTML=`<input id="stValTxt" class="nkin" style="width:120px" maxlength="12" placeholder="名称">`;
  else if(p==='load') w.innerHTML=`<span class="ds" style="margin:0">按上面的编号切换场景</span>`;
  else if(sp&&sp.t==='bool')
    w.innerHTML=`<select id="stValSel" class="nkin">`
      +`<option value="1">${sp.lb?sp.lb[1]:'开'}</option>`
      +`<option value="0">${sp.lb?sp.lb[0]:'关'}</option></select>`;
  else if(sp&&sp.t==='enum')
    w.innerHTML=`<select id="stValSel" class="nkin">`
      +sp.opts.map(o=>`<option value="${o[0]}">${o[1]}</option>`).join('')+`</select>`
      +`<span class="ds" style="margin:0 0 0 6px;min-width:34px">${sp.u||''}</span>`;
  else if(sp)
    w.innerHTML=`<input id="stValNum" type="number" class="nkin" style="width:100px"`
      +` min="${sp.lo}" max="${sp.hi}" step="${sp.step}" value="${sp.dv!==undefined?sp.dv:sp.lo}">`
      +`<span class="ds" style="margin:0 0 0 6px;min-width:34px">${sp.u||''}</span>`;
  // ⚠️ 单位必须**贴着输入框**显示：混响参数吃的是原生单位（秒 / 毫秒），
  //    而 fader/mix 又是 0–1。少了单位标签，用户极易把"混响时间 80"填成 0.8 秒，
  //    跑起来"没什么效果"还以为是台子坏了。
  else {
    const u={fader:'%',time:'秒',predelay:'毫秒',damp:''}[p]||'';
    const dv=p==='fader'?80:(p==='time'?2.4:(p==='predelay'?20:0.45));
    w.innerHTML=`<input id="stValNum" type="number" class="nkin" style="width:100px" step="0.01" value="${dv}">`
      +`<span class="ds" style="margin:0 0 0 6px;min-width:34px">${u}</span>`;
  }
}
// ---- 原始 OSC 表单 ----
function oscFormHTML(){
  return `<input id="oscAddr" class="nkin" style="flex:1;min-width:190px" placeholder="/ch/05/mix/fader" maxlength="128">`
    + `<input id="oscArgs" class="nkin" style="flex:1;min-width:140px" placeholder="参数,逗号分隔(可留空) 如 0.8">`
    + `<div class="ds" style="width:100%;color:#d9a13c">⚠️ 原始 OSC 是<b>控台专有</b>的:地址写死,换到别的控台可能指向别的参数、甚至完全无效。通用需求请优先用上面的「通用设置」。</div>`;
}
function updRelChips(){
  const g=$('#caGrp')?$('#caGrp').value:'ch';
  $('#caRelChips').innerHTML=chipGrid(g,g==='ch'?32:g==='bus'?16:8);
}
function learnTo(){
  const k=$('#caKind').value;
  const g=CA_MULTI[k]; if(!g) return;
  if(caMulti.length!==1){ cardErr('「取当前值」需要先在列表里只勾选 1 个目标'); return; }
  const i=caMulti[0];
  const src=g==='ch'?(S.ch[i]||{}):(S.bus[i]||{});
  if(src.fader===undefined){ cardErr('暂时读不到当前值,请稍候'); return; }
  const el=document.getElementById('caP1');
  if(el) el.value=Math.round(src.fader*100);
  hideCardErr();
}
function presetOpts(){
  const list=(S.snapshots||[]);
  if(!list.length) return '<option value="0">（还没有预设：先到卡片页「保存预设」）</option>';
  return list.map(sp=>`<option value="${sp.id}">${esc(sp.name)} · ${sp.count||0} 值</option>`).join('');
}
function caSceneModeChg(){
  const modeEl=$('#caSceneMode'), box=$('#caSceneExtra'), note=$('#caSceneNote');
  if(!modeEl||!box) return;
  if(modeEl.value==='partial'){
    box.innerHTML=`<select id="caScenePreset" class="nkin">${presetOpts()}</select>`
      +numIn('caSec','推子秒数',1)+`<span class="ds" style="margin:0">秒</span>`+curveSel();
    if(note) note.textContent='局部调用不发场景号：只把预设范围内的参数应用过来，其余通道一个字节都不动。';
  } else {
    box.innerHTML='';
    if(note) note.textContent='全局调用会按场景整场还原（包括卡片里没写过的通道）；真台上还受 Scene Safes 影响。';
  }
}
// 通用设置：从当前台面抓一个值填进来（省得手抄 dB / 混响秒数）
function curValue(g,p,i){
  try{
    if(g==='fx'){
      const fx=(S.fx||[]).find(x=>x.slot===i);
      if(!fx) return null;
      if(p==='type') return fx.type||'';
      if(p==='name') return fx.name||'';
      if(p==='on') return fx.on?1:0;
      if(p==='mix') return fx.mix;
      if(p==='time'||p==='predelay'||p==='damp'){
        const v=fx[p];
        return (v===undefined||v===null)?null:v;
      }
      return null;
    }
    if(g==='scene') return +S.scene||1;
    const row=(g==='main')?S.main:(S[g]||{})[String(i)];
    if(!row) return null;
    if(p==='on') return row.on?1:0;
    if(p==='name'){ const nm=(S.names||{})[g]||{}; return nm[String(i)]||''; }
    if(p==='fader') return row.fader;
    // 新增通道参数: 行字典里同名字段直接返回 (缺省 = 该组没这个参数 → null)
    if(row[p]!==undefined) return row[p];
  }catch(e){}
  return null;
}
function learnSet(){
  const g=$('#stGroup').value, p=$('#stParam').value;
  const i=(g==='main')?0:(+($('#stId').value)||1);
  const sp=chpSpec(p);
  if(g==='send'){
    // 发送值不在 /api/state 里 (512 项会把状态包撑爆), 抓取时单独拉一次
    const bus=+((($('#stBus')||{}).value)||1);
    fetch('/api/sends?ch='+i).then(r=>r.json()).then(d=>{
      const row=(d.sends||{})[String(bus)];
      if(!row){ cardErr('读不到 CH'+i+' → Bus'+bus); return; }
      const v=(p==='send_level')?row.level:(p==='send_on'?row.on:row.tap);
      fillStVal(sp,p,v);
    }).catch(()=>cardErr('读取发送值失败'));
    return;
  }
  const v=curValue(g,p,i);
  if(v===null){ cardErr('读不到「'+g+' '+i+'」的当前值（编号越界，或该参数尚未建模）'); return; }
  fillStVal(sp,p,v);
}
function fillStVal(sp,p,v){
  if(p==='on'||p==='load'){
    const sel=$('#stValSel'); if(sel) sel.value=String(v?1:0);
    cardInfo('已抓取：'+(v?'开启':'静音'));
  } else if(sp&&sp.t==='bool'){
    const sel=$('#stValSel'); if(sel) sel.value=String(v?1:0);
    cardInfo('已抓取：'+(v?sp.lb[1]:sp.lb[0]));
  } else if(sp&&sp.t==='enum'){
    const sel=$('#stValSel'); if(sel) sel.value=String(v);
    cardInfo('已抓取：'+v+' '+(sp.u||''));
  } else if(p==='type'){
    const sel=$('#stValSel'); if(sel) sel.value=v||'';
    cardInfo('已抓取算法：'+(v||'(空)'));
  } else if(p==='name'){
    const el=$('#stValTxt'); if(el) el.value=v||'';
    cardInfo('已抓取名称：'+(v||'(空)'));
  } else {
    const el=$('#stValNum');
    const isPct=(p==='fader'||p==='send_level');
    if(el) el.value=isPct?Math.round(v*100):v;
    cardInfo('已抓取：'+(isPct?Math.round(v*100)+'%':(sp?chpFmt(sp,v):v)));
  }
}
function cardInfo(msg){
  const e=$('#cardErr'); if(!e) return;
  e.textContent=msg; e.classList.add('on'); e.style.color='var(--green)';
  setTimeout(()=>{ e.style.color=''; },2200);
}
function pct(v){ return Math.min(Math.max(+(v||0),0),100)/100; }
function addAct(){
  const k=$('#caKind').value, a={kind:k};
  const g=id=>{const e=document.getElementById(id);return e?e.value:null;};
  if(k==='rel_db'){
    const grp=$('#caGrp')?$('#caGrp').value:'ch';
    if(!caMulti.length){
      const cg=$('#caChips');
      if(cg){ cg.classList.add('inv'); setTimeout(()=>cg.classList.remove('inv'),1200); }
      cardErr('请先勾选要增减的目标,再点「＋加入动作」');
      return;
    }
    a.group=grp;
    a[{ch:'chs',bus:'buses',dca:'dcas'}[grp]]=caMulti.slice().sort((x,y)=>x-y);
    a.delta_db=Math.min(Math.max(+g('caDb')||3,-30),30);
    a.duration=Math.max(+g('caSec')||2,0.5);
    a.curve=g('caCurve');
    caActs.push(a); renderActs(); return;
  }
  if(k==='set'){
    const grp=$('#stGroup').value, p=$('#stParam').value;
    let i=+($('#stId').value)||0;
    if(grp==='main') i=0;
    if(grp!=='main'&&grp!=='scene'&&i<1){ cardErr('请填写目标编号'); return; }
    if(grp==='scene'&&i<1){ cardErr('请填写场景号'); return; }
    a.target={group:grp,param:p,id:i};
    // 发送: id 是通道, 再带上 Bus。后端按 send_pack(通道, Bus) 落地
    if(grp==='send') a.target.bus=+((($('#stBus')||{}).value)||1);
    const sp=chpSpec(p);
    if(p==='on') a.value=(+($('#stValSel')?$('#stValSel').value:0))?1:0;
    else if(sp&&sp.t==='bool') a.value=(+($('#stValSel')?$('#stValSel').value:0))?1:0;
    else if(sp&&sp.t==='enum') a.value=+($('#stValSel')?$('#stValSel').value:0);
    else if(p==='type') a.value=($('#stValSel')?$('#stValSel').value:'');   // 库来自当前控台
    else if(p==='name') a.value=($('#stValTxt')?$('#stValTxt').value.trim():'');
    else if(p==='load') a.value=1;
    else {
      let v=+($('#stValNum')?$('#stValNum').value:0)||0;
      if(p==='fader'||p==='send_level') v=pct(v);   // 表单里填百分比, 传 0..1
      a.value=v;                          // 新通道参数吃原生单位 (dB/ms/Hz), 不做百分比换算
    }
    const d=+($('#stDur')?$('#stDur').value:0)||0;
    if(d>0&&p==='fader') a.duration=Math.max(d,0.5);   // 只有推子类能渐变
    else if(d>0) cardErr('提示:非推子参数不支持渐变,已按瞬间执行');
    caActs.push(a); renderActs(); return;
  }
  if(k==='osc'){
    const addr=($('#oscAddr')?$('#oscAddr').value.trim():'');
    if(!addr){ cardErr('请填写 OSC 地址'); return; }
    if(addr[0]!=='/'){ cardErr('OSC 地址必须以 / 开头,例如 /ch/05/mix/fader'); return; }
    if(addr.indexOf(' ')>=0){ cardErr('OSC 地址不能含空格'); return; }
    a.address=addr;
    const raw=($('#oscArgs')?$('#oscArgs').value.trim():'');
    if(raw){
      a.args=raw.split(',').map(s=>s.trim()).filter(s=>s!=='').map(s=>
        (/^-?\d+(\.\d+)?$/.test(s))?+s:s);      // 纯数字转数值, 其余当字符串
    }
    caActs.push(a); renderActs(); return;
  }
  if(CA_MULTI[k]){
    if(!caMulti.length){
      const cg=$('#caChips');
      if(cg){ cg.classList.add('inv'); setTimeout(()=>cg.classList.remove('inv'),1200); }
      cardErr('请先在列表里勾选目标'+(k==='mute'||k==='fade_ch'?'通道':'Bus')+',再点「＋加入动作」');
      return;
    }
    const sel=caMulti.slice().sort((x,y)=>x-y);
    if(k==='mute') a.chs=sel;
    else if(k==='mute_bus') a.buses=sel;
    else if(k==='fade_ch'){ a.chs=sel; a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
    else { a.buses=sel; a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
  }
  else if(k==='scene'){
    a.scene=+(g('caScene')||1);
    const mode=g('caSceneMode')||'global';
    a.mode=mode;
    if(mode==='partial'){
      const pid=+(g('caScenePreset')||0);
      if(!pid){ cardErr('局部调用需要先选一条预设（没有就先到卡片页保存一条）'); return; }
      a.presetId=pid;
      a.duration=Math.max(+g('caSec')||1,0.05);
      a.curve=g('caCurve');
    }
  }
  else if(k==='preset'){
    const pid=+(g('caPreset')||0);
    if(!pid){ cardErr('请先选要调用的预设'); return; }
    a.presetId=pid;
    a.duration=Math.max(+g('caSec')||2,0.5);
    a.curve=g('caCurve');
  }
  else if(k==='wait') a.duration=Math.max(+g('caSec')||3,0.5);
  else if(k==='unmute_all'){ /* 无参数 */ }
  else if(k==='fade_all'){ a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_odd_even'){ a.odd_to=pct(g('caP1')); a.even_to=pct(g('caP2')); a.duration=Math.max(+g('caSec')||4,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_from'){ a.ch=+(g('caCh')||15); a.frm=pct(g('caP0')); a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||5,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_from_bus'){ a.bus=+(g('caBus')||1); a.frm=pct(g('caP0')); a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||5,0.5); a.curve=g('caCurve'); }
  caActs.push(a); renderActs();
}
function actTxt(a){
  const cv={linear:'直线',ease_out:'先快后慢',ease_in:'先慢后快',smooth:'S 形'};
  const t=x=>Math.round(x*100)+'%';
  const lst=(g,arr)=>(arr||[]).map(i=>dispName(g,i)).join('、');
  if(a.kind==='mute') return '静音 '+lst('ch',a.chs||(a.ch?[a.ch]:[]));
  if(a.kind==='mute_bus') return '静音 '+lst('bus',a.buses||(a.bus?[a.bus]:[]));
  if(a.kind==='scene'){
    if((a.mode||'global')==='partial'){
      const sp=(S.snapshots||[]).find(x=>x.id===a.presetId);
      return `场景 ${a.scene} 局部（${sp?'预设「'+sp.name+'」':'预设已删除'}）`;
    }
    return '切到场景 '+a.scene;
  }
  if(a.kind==='preset'){
    const sp=(S.snapshots||[]).find(x=>x.id===a.presetId);
    return `调用预设「${sp?sp.name:'已删除'}」`;
  }
  if(a.kind==='unmute_all') return '全部取消静音';
  if(a.kind==='wait') return '等待 '+(+a.duration||1)+' 秒';
  if(a.kind==='fade_all') return `全体推子→${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_odd_even') return `奇数→${t(a.odd_to)} 偶数→${t(a.even_to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_ch') return `${lst('ch',a.chs||(a.ch?[a.ch]:[]))} →${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_bus') return `${lst('bus',a.buses||(a.bus?[a.bus]:[]))} →${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_from') return `${dispName('ch',a.ch)} 从${t(a.frm)} 经${a.duration}秒→${t(a.to)} ${cv[a.curve]}`;
  if(a.kind==='fade_from_bus') return `${dispName('bus',a.bus)} 从${t(a.frm)} 经${a.duration}秒→${t(a.to)} ${cv[a.curve]}`;
  if(a.kind==='rel_db'){
    const g2=a.group||'ch';
    const ids=a[{ch:'chs',bus:'buses',dca:'dcas'}[g2]]||[];
    return `${lst(g2,ids)} ${a.delta_db>=0?'+':'−'}${Math.abs(+a.delta_db||0)}dB ${a.duration}秒 ${cv[a.curve]}`;
  }
  if(a.kind==='set'){
    const tg=a.target||{}, g2=tg.group, p=tg.param||'fader', i=(g2==='main')?0:(+tg.id||0);
    const gname=((ST_GROUPS.find(x=>x[0]===g2)||['',g2])[1]);
    if(g2==='scene') return '切到场景 '+i;
    if(g2==='fx'){
      if(p==='on') return `FX${i} ${a.value?'开启':'旁通'}`;
      if(p==='type') return `FX${i} 算法=${a.value||'(清空)'}`;
      if(p==='name') return `FX${i} 名称=${a.value}`;
      if(p==='mix') return `FX${i} 混合比=${(+a.value||0).toFixed(2)}`;
      return `FX${i} ${(ST_PLABEL[p]||p)} = ${a.value}`;
    }
    // 标签必须能区分"CH05 推子"和"CH05→Bus03 发送电平": 发送是二维目标,
    // 只写通道号会出现两条一模一样的摘要, 用户没法核对卡片。
    const lbl=(g2==='main')?'主输出':(g2==='send')
      ? `CH${String(i).padStart(2,'0')}→Bus${String((a.target&&a.target.bus)||1).padStart(2,'0')}`
      : `${gname} ${String(i).padStart(2,'0')}`;
    if(p==='on') return `${lbl} ${a.value?'开启':'静音'}`;
    if(p==='send_on') return `${lbl} 发送${a.value?'开':'关'}`;
    if(p==='send_level') return `${lbl} 电平=${Math.round((+a.value||0)*100)}%`;
    if(p==='send_tap')
      return `${lbl} 取点=${(['<-EQ','EQ->','PRE','POST','GRP'][+a.value||0])||''}`;
    const dd=(+a.duration>0)?` 经${a.duration}秒`:'';
    const sp0=(p==='fader')?null:chpSpec(p);
    if(sp0&&sp0.t!=='bool')
      return `${lbl} ${sp0.l}=${(+a.value||0)}${sp0.u?(' '+sp0.u):''}`;
    return `${lbl} →${Math.round((+a.value||0)*100)}%${dd}`;
  }
  if(a.kind==='osc') return `⚠️ 原始 OSC ${a.address}${(a.args&&a.args.length)?' '+a.args.join(' '):''}`;
  return '?';
}
function renderActs(){
  $('#actChips').innerHTML=caActs.map((a,i)=>
    `<span style="display:inline-flex;align-items:center;gap:6px;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:14px;padding:4px 10px;font-size:12px;margin:2px">
      ${i+1}. ${esc(actTxt(a))} <b style="cursor:pointer;color:var(--red)" onclick="caActs.splice(${i},1);renderActs()">×</b></span>`).join('')
    || '<div class="ds" style="font-size:12px">还没有动作,选类型后点「＋加入动作」。</div>';
}
let editCardId = null;
function saveCard(){
  const name=$('#ncName').value.trim();
  $('#ncName').classList.toggle('inv', !name);
  if(!name){ cardErr('请填写卡片名称(必填)'); return; }
  if(!caActs.length){ cardErr('请至少加入一个动作,再保存卡片'); return; }
  const btn=$('#cardSaveBtn');
  btn.classList.add('btnspin'); btn.textContent='保存中…';
  hideCardErr();
  const url=editCardId?'/api/cardedit':'/api/cardadd';
  const cardData={name:name,desc:$('#ncDesc').value,actions:caActs,
                  next:($('#ncNext')?$('#ncNext').value:'')};
  const payload=editCardId
    ?{id:editCardId, card:cardData}
    :{card:cardData};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)})
  .then(r=>{
    if(!r.ok) throw new Error('服务器错误 '+r.status);
    return r.json();
  })
  .then(d=>{
    if(!d || !d.ok) throw new Error((d&&d.error)||'保存被拒绝');
    // 服务端会回报"清洗时丢了几个动作、为什么"—— 必须让用户看见，
    // 否则就是"保存成功但跑起来少了东西"的假成功。
    const dp=d.dropped||[];
    if(dp.length) toast('⚠️ 已保存，但有 '+dp.length+' 个动作被丢弃：'+dp.join('；'));
    else toast(editCardId?'✏️ 卡片已更新':'🎬 卡片已创建');
    caActs=[]; renderActs(); $('#ncName').value=''; $('#ncDesc').value='';
    closeCardModal();
  })
  .catch(err=>cardErr('保存失败: '+(err&&err.message?err.message:'网络异常,请重试')))
  .finally(()=>{
    btn.classList.remove('btnspin'); btn.textContent='💾 保存卡片';
  });
}
function openCardModal(card){
  editCardId = card ? card.id : null;
  $('#cardModalTitle').textContent = card ? '✏️ 编辑演出卡片' : '➕ 新建演出卡片';
  $('#cardSaveBtn').textContent = card ? '💾 保存修改' : '💾 保存卡片';
  // ⚠️ 三个字段都必须**无条件**重置。曾经的写法把 name/desc/caActs 全放在
  //    `if(card){...}` 里 —— 新建卡片加几个动作 → 不保存直接关 → 再点"新建"，
  //    上次的动作和名字都还在，用户会以为"自动保存了"。
  $('#ncName').value = card ? (card.name || '') : '';
  $('#ncDesc').value = card ? (card.desc || '') : '';
  caActs = card ? JSON.parse(JSON.stringify(card.actions || [])) : [];
  // 接续选择: 除自身外的所有卡片
  const nxt=$('#ncNext');
  nxt.innerHTML='<option value="">(无 · 执行完就停)</option>'
    +(S.cards||[]).filter(x=>x.id!==editCardId)
      .map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');
  nxt.value=(card&&card.next)||'';
  $('#cardModal').classList.add('on');
  document.body.style.overflow='hidden';
  updCaParams(); renderActs(); hideCardErr();
  setTimeout(()=>$('#ncName').focus(),120);
}
function closeCardModal(){
  $('#cardModal').classList.remove('on');
  document.body.style.overflow='';
  hideCardErr();
  if(editCardId){
    editCardId = null;
    caActs = []; renderActs();
    $('#ncName').value=''; $('#ncDesc').value='';
  }
  $('#ncName').classList.remove('inv');
}
function cardErr(msg){ const e=$('#cardErr'); e.textContent=msg; e.classList.add('on'); }
function hideCardErr(){ $('#cardErr').classList.remove('on'); }
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeCardModal(); });
function delCard(id){ api('/api/carddel',{id:id}); }
updNkIdx(); updCaParams(); renderActs(); updNmIdx();

// ---- 混响控制: FX 槽 / Tap 定拍 / 混响时间同步 ----
// 与服务端同一套语义: BPM 取最近 8 个间隔均值, 抖动(<0.15s)忽略, 超时(>2s)重开;
// 时间 = 拍数 × 60/BPM (附点 ×1.5); 实际写入由服务端按算法范围钳制。
// 算法清单不再写死 —— 由服务端按**当前控台驱动**下发 (S.fx.algos)。
// 换控台 = 换一套效果库, 下拉选项跟着重建; 这样"选的就是这台台子上真有的效果"。
function fxAlgoOpts(emptyLabel){
  const lib = (S && S.fx && S.fx.algos) || {};
  const keys = Object.keys(lib);
  if (!keys.length) return '<option value="">— 效果库未就绪 —</option>';
  const el = (emptyLabel === undefined) ? '— 空槽 —' : emptyLabel;
  return `<option value="">${esc(el)}</option>`
       + keys.map(k => `<option value="${esc(k)}">${esc(lib[k].label)}</option>`).join('');
}
const FX_BEATS = {'1/16':0.25, '1/8':0.5, '1/4':1, '1/2':2, '1':4, '2':8};
const FX_NOTE_OPTS = [['1/16','1/16 音符'], ['1/8','1/8 音符'], ['1/4','1/4 音符'],
                      ['1/2','1/2 音符'], ['1','全音符'], ['2','2 小节']];
function fxNoteSecs(bpm, note, dotted){
  let beats = FX_BEATS[note] || 1;
  if (dotted) beats *= 1.5;
  bpm = Math.min(Math.max(+bpm || 120, 30), 300);
  return beats * 60 / bpm;
}
// 参数控件不再为每种算法写死 —— 服务端按标定表下发全部参数 (field/label/
// unit/lo/hi/kind/opts/value/unset), 这里统一渲染: 数值=滑杆, 枚举=下拉。
// 未设置 (unset) 的参数不拿 0 冒充: 大量参数 lo>0 (频率/时间), 0 越界会把
// 滑杆骗到一端 —— 显示"未设置", 拖一下才真正写上台面。
function fxStep(lo, hi){
  const span = Math.abs((+hi) - (+lo)) || 1;
  for (const st of [100, 50, 10, 5, 1, 0.5, 0.1, 0.05, 0.01, 0.001])
    if (span / st <= 200) return st;
  return 1000;
}
function fxDec(lo, hi){
  const st = fxStep(lo, hi);
  return st >= 1 ? 0 : (st >= 0.1 ? 1 : 2);
}
function fxParsHTML(s, st){
  if (!st || !st.loaded){
    const lib = (S && S.fx && S.fx.algos) || {};
    const c = (S && S.fx && S.fx.console) || {};
    if (c.locked || !Object.keys(lib).length)
      return '<div class="ds" style="margin:0">控台「' + esc(c.label || '当前控台')
           + '」的效果库还没标定，这个槽暂时不可用（写入会被拒绝并记日志）。</div>';
    const names = Object.keys(lib).map(k => esc(lib[k].label)).join(' / ');
    return '<div class="ds" style="margin:0">空槽 — 把上面的类型选成 ' + names + ' 中的一个。</div>';
  }
  if (!st.known)
    return '<div class="ds" style="margin:0">⚠ 真台上这个槽装的是「' + esc(st.type)
         + '」，不在本控台「' + esc(((S.fx.console || {}).label) || '当前')
         + '」的效果库里 — 没有标定参数，这个槽只能看不能调。</div>';
  const clamp0 = (lo, hi) => Math.min(Math.max(0, +lo), +hi);
  const rows = (st.params || []).map(p => {
    const unit = p.unit ? ' ' + p.unit : '';
    if (p.kind === 'i'){
      const opts = (p.opts || []).map((o, i) =>
        `<option value="${i}">${esc(o)}</option>`).join('');
      const unset = (p.value === null || p.value === undefined);
      const shown = unset ? '未设置'
        : esc(String((p.opts || [])[p.value] != null ? (p.opts)[p.value] : p.value));
      return `<div class="fxrow"><span class="fxlab">${esc(p.label)}</span>
        <select class="fxsel" data-f="${p.field}" onchange="fxSet(${s},'${p.field}',+this.value)">
          ${unset ? '<option value="">未设置</option>' : ''}${opts}</select>
        <span class="fxval" data-v="${p.field}">${shown}</span></div>`;
    }
    const dec = fxDec(p.lo, p.hi), step = fxStep(p.lo, p.hi);
    const unset = (p.value === null || p.value === undefined);
    const val = unset ? clamp0(p.lo, p.hi) : +p.value;
    return `<div class="fxrow"><span class="fxlab">${esc(p.label)}</span>
      <input type="range" class="fxsl" data-f="${p.field}" data-unit="${esc(p.unit || '')}"
        data-dec="${dec}" min="${p.lo}" max="${p.hi}" step="${step}" value="${val.toFixed(dec)}"
        oninput="fxSlide(${s},'${p.field}',this)">
      <span class="fxval" data-v="${p.field}">${unset ? '未设置' : val.toFixed(dec) + unit}</span></div>`;
  }).join('');
  // mix 是槽级字段 (不在算法参数表里), 固定排在最后
  return rows + `<div class="fxrow"><span class="fxlab">混合比</span>
      <input type="range" class="fxsl" data-f="mix" data-unit="" data-dec="2"
        min="0" max="1" step="0.01" value="${(+st.mix).toFixed(2)}"
        oninput="fxSlide(${s},'mix',this)">
      <span class="fxval" data-v="mix">${(+st.mix).toFixed(2)}</span></div>`;
}
function fxCardHTML(s){
  const opts = fxAlgoOpts();
  return `<div class="card" id="fxc-${s}">
    <div class="nm">FX${s}
      <select class="nkin fx-type" onchange="fxSet(${s},'type',this.value)">${opts}</select>
      <input class="nkin fx-name" style="width:110px" maxlength="12" placeholder="名称"
        onchange="fxSet(${s},'name',this.value)">
      <button class="mute fx-on" onclick="fxToggle(${s})" title="效果开关">M</button>
      <span class="ds fx-tag" style="margin:0"></span></div>
    <div class="fxpars"></div></div>`;
}
function buildFx(){
  $('#fxNote').innerHTML = FX_NOTE_OPTS.map(([v,t]) => `<option value="${v}">${t}</option>`).join('');
  let g = '';
  for (let s = 1; s <= 8; s++)
    g += `<label class="chip" id="fxg-${s}"><input type="checkbox" onchange="fxGroupSet()">FX${s}</label>`;
  $('#fxGroup').innerHTML = g;
  let h = '';
  for (let s = 1; s <= 8; s++) h += fxCardHTML(s);
  $('#fxlist').innerHTML = h;
}
function updFx(){
  if (!S || !S.fx) return;
  const slots = S.fx.slots || [], sync = S.fx.sync || {}, tap = S.fx.tap || {};
  // 效果库换了（＝切了控台）→ 重建算法下拉。libId 就是控台 id, 由服务端下发,
  // 前端不做任何"猜"—— 库内容永远以服务端为准。
  if (window.fxLibId !== S.fx.libId){
    window.fxLibId = S.fx.libId;
    renderFxLibrary();
  }
  renderFxConsole(S.fx.console || {});
  slots.forEach(st => {
    const card = document.getElementById('fxc-' + st.slot);
    if (!card) return;
    const ts = card.querySelector('.fx-type');
    if (ts){
      // 真台上可能装着本控台效果库里没有的算法 —— 给它补一个条目, 否则 select
      // 会落成空白, 用户就看不到"真台上到底装了什么"了。
      if (st.type && ![...ts.options].some(o => o.value === st.type)){
        const o = document.createElement('option');
        o.value = st.type;
        o.textContent = st.type + '（不在本控台效果库）';
        ts.appendChild(o);
      }
      if (document.activeElement !== ts && ts.value !== st.type) ts.value = st.type;
    }
    const nm = card.querySelector('.fx-name');
    if (document.activeElement !== nm && nm.value !== st.name) nm.value = st.name;
    card.querySelector('.fx-on').classList.toggle('on', !st.on);
    const tag = card.querySelector('.fx-tag');
    // 下拉里已经有算法名了，这里显示该算法的时间范围（Tap 换算会被钳制到这个区间）；
    // 非混响类算法没有时间参数，退而显示参数个数
    const txt = !st.loaded ? '空槽'
               : (!st.known ? '⚠ 未知算法（不在本控台效果库）'
               : (st.time != null ? (st.tmin + '–' + st.tmax + ' ' + (st.tunit || 's'))
                                  : (((st.params || []).length) + ' 个参数')));
    if (tag.textContent !== txt) tag.textContent = txt;
    // 算法变了就重建这一张卡的参数区 (range 的 min/max 随算法范围变)
    const pars = card.querySelector('.fxpars');
    // 算法变了、或控台锁定状态变了 → 重建参数区（range 的 min/max 随算法范围变；
    // 空槽的提示文案也随"当前控台是否标定"变，只看 type 会漏掉换控台那一次）
    const sig = (st.type || '') + '|' + (((S.fx.console || {}).locked) ? 'L' : '');
    if (pars.dataset.algo !== sig){
      pars.dataset.algo = sig;
      pars.innerHTML = fxParsHTML(st.slot, st);
    }
    // 值刷新: 算法参数 + 槽级 mix 一把抓。enum 是下拉, 数值是滑杆;
    // 未设置 (value=null) 只更新文字, 不动滑杆 (滑杆停在钳制后的 0 位)。
    (st.params || []).concat([{field: 'mix', unit: '', kind: 'f', lo: 0, hi: 1,
                               value: st.mix}]).forEach(p => {
      if (p.kind === 'i'){
        const sel = pars.querySelector(`.fxsel[data-f="${p.field}"]`);
        if (sel && document.activeElement !== sel)
          sel.value = (p.value === null || p.value === undefined) ? '' : p.value;
        return;
      }
      const sl = pars.querySelector(`.fxsl[data-f="${p.field}"]`); if (!sl) return;
      const ve = pars.querySelector(`.fxval[data-v="${p.field}"]`);
      if (p.value === null || p.value === undefined){
        if (ve) ve.textContent = '未设置';
        return;
      }
      const dec = fxDec(p.lo, p.hi);
      const shown = (+p.value).toFixed(dec);
      if (document.activeElement !== sl && +sl.value !== +shown) sl.value = shown;
      if (ve) ve.textContent = shown + (p.unit ? ' ' + p.unit : '');
    });
  });
  const msel = $('#fxMode');
  if (msel && document.activeElement !== msel && msel.value !== sync.mode) msel.value = sync.mode;
  const fck = $('#fxFollow'); if (fck && fck.checked !== !!sync.followTap) fck.checked = !!sync.followTap;
  const nsel = $('#fxNote');
  if (nsel && document.activeElement !== nsel && nsel.value !== sync.note) nsel.value = sync.note;
  const dck = $('#fxDotted'); if (dck && dck.checked !== !!sync.dotted) dck.checked = !!sync.dotted;
  const gset = new Set(sync.group || []);
  for (let s = 1; s <= 8; s++){
    const el = document.getElementById('fxg-' + s); if (!el) continue;
    const inp = el.querySelector('input');
    if (inp && inp.checked !== gset.has(s)) inp.checked = gset.has(s);
    el.classList.toggle('on', gset.has(s));
  }
  const bpm = tap.bpm || 0;
  $('#tapBpm').textContent = bpm ? (bpm.toFixed(1) + ' BPM') : '— BPM';
  $('#tapInfo').textContent = bpm
    ? ('已点 ' + (tap.count || 0) + ' 下 · 取最近 8 个间隔的均值')
    : '连点 2 下以上定拍';
  $('#fxTapSecs').textContent = bpm
    ? ('当前时值换算 = ' + fxNoteSecs(bpm, sync.note, sync.dotted).toFixed(3) + ' 秒') : '';
}
// ---- 混响页：效果库随控台自动切换 ----
// 只在"效果库真的换了"时重建下拉（见 updFx 的 libId 判断）——
// 否则 250ms 一次的轮询会把用户正在选的下拉反复重画。
function renderFxLibrary(){
  const slots = (S && S.fx && S.fx.slots) || [];
  slots.forEach(st => {
    const card = document.getElementById('fxc-' + st.slot);
    if (!card) return;
    const sel = card.querySelector('.fx-type');
    if (!sel) return;
    const cur = st.type || '';
    sel.innerHTML = fxAlgoOpts();
    if (cur && ![...sel.options].some(o => o.value === cur)){
      const o = document.createElement('option');
      o.value = cur; o.textContent = cur + '（不在本控台效果库）';
      sel.appendChild(o);
    }
    sel.value = cur;
  });
}
function renderFxConsole(c){
  const nm = $('#fxConsoleName'), nm2 = $('#fxConsoleName2'),
        tag = $('#fxConsoleTag'), note = $('#fxConsoleNote'),
        btn = $('#fxPullBtn'), pn = $('#fxPullNote');
  if (nm)  nm.textContent  = c.label || '—';
  if (nm2) nm2.textContent = c.label || '当前控台';
  if (tag){
    tag.textContent = c.verified ? '✅ 效果库已标定'
                    : (c.ready ? '⚠ 结构已填 · 未上真机核对' : '⚠ 待标定');
  }
  const nk = (c.label || '') + '|' + (c.libCount || 0) + '|' + !!c.locked;
  if (note && note.dataset.k !== nk){
    note.dataset.k = nk;
    note.innerHTML = c.locked
      ? ('控台「' + esc(c.label || '?') + '」的效果库还没标定 —— 混响页暂时<b>只能看不能写</b>。'
         + '写入会被拒绝并记进日志：地址蒙错会写到别的参数上，比干脆不动作难查得多。')
      : ('本控台效果库共 <b>' + (c.libCount || 0) + '</b> 种算法，占用 <b>' + (c.slots || 0)
         + '</b> 个效果槽。下面的算法下拉就是这台台子上真正有的效果。');
  }
  const list = $('#fxlist');
  if (list){                       // 用 pointer-events 而不是 disabled：参数区是重建的，
    const locked = !!c.locked;     // disabled 会随重建丢失，pointer-events 不会
    list.style.pointerEvents = locked ? 'none' : '';
    list.style.opacity = locked ? '0.42' : '';
  }
  const on = !!(S && S.bridge && S.bridge.on);
  if (btn){
    btn.disabled = !on;
    btn.style.opacity = on ? '' : '0.55';
    btn.style.background = on ? '' : '#565d6d';
    btn.style.color = on ? '' : '#cfd4de';
    btn.textContent = on ? '⟳ 从真台同步效果' : '⟳ 从真台同步效果（未桥接）';
  }
  if (pn && pn.dataset.b !== String(on)){
    pn.dataset.b = String(on);
    pn.textContent = on
      ? ('读回真台各效果槽的算法/开关/参数（已桥接 ' + (S.bridge.addr || '真台') + '）')
      : '读回真台各效果槽的算法/开关/参数（需先用 --bridge 桥接真台）';
  }
}
function fxPull(){
  fetch('/api/fx',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'pull'})})
   .then(r=>r.json()).then(d=>{
     if(!d.ok){ showToast(d.error||'同步失败'); return; }
     showToast('⟳ ' + (d.message || '已向真台请求效果状态'));
   }).catch(()=>showToast('同步失败'));
}
function fxSet(s, field, value){ api('/api/fx', {slot:s, field:field, value:value}); }
function fxToggle(s){
  const st = ((S.fx && S.fx.slots) || []).find(x => x.slot === s);
  if (!st) return;
  api('/api/fx', {slot:s, field:'on', value: st.on ? 0 : 1});
}
// 拖参数时 60ms 合并一次请求, 免得刷爆
const fxPend = {}; let fxPendT = null;
function fxSlide(s, field, el){
  const v = +el.value;
  const card = document.getElementById('fxc-' + s);
  const ve = card && card.querySelector(`.fxval[data-v="${field}"]`);
  // 小数位/单位在渲染时写进 dataset (不同算法量程差几个数量级, 不能写死)
  if (ve) ve.textContent = v.toFixed(+(el.dataset.dec === undefined ? 2 : el.dataset.dec))
    + (el.dataset.unit ? ' ' + el.dataset.unit : '');
  fxPend[s + '|' + field] = {slot:s, field:field, value:v};
  if (!fxPendT) fxPendT = setTimeout(() => {
    fxPendT = null;
    const arr = Object.keys(fxPend).map(k => fxPend[k]);
    for (const k in fxPend) delete fxPend[k];
    arr.forEach(p => api('/api/fx', p));
  }, 60);
}
function fxGroupSet(){
  const g = [];
  for (let s = 1; s <= 8; s++){
    const el = document.getElementById('fxg-' + s);
    if (el && el.querySelector('input').checked) g.push(s);
  }
  api('/api/fxsync', {group:g});
}
function fxSyncSet(){
  api('/api/fxsync', {mode: $('#fxMode').value, followTap: $('#fxFollow').checked,
                      note: $('#fxNote').value, dotted: $('#fxDotted').checked});
}
// Tap 计时放在服务端 (与协议规范同一份实现), 网页只管发点击
function tapHit(){
  fetch('/api/tap', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'tap'})})
  .then(r => r.json()).then(d => {
    if (!d || !d.ok) return;
    $('#tapBpm').textContent = d.bpm ? (d.bpm.toFixed(1) + ' BPM') : '— BPM';
    $('#tapInfo').textContent = '已点 ' + d.count + ' 下' + (d.locked ? ' · 已成拍' : ' · 再点一下定拍');
  }).catch(() => {});
}
function tapReset(){
  api('/api/tap', {action:'reset'});
  $('#tapBpm').textContent = '— BPM';
  $('#tapInfo').textContent = '连点 2 下以上定拍';
}
function tapApply(){
  const note = $('#fxNote') ? $('#fxNote').value : '1/4';
  const dotted = $('#fxDotted') ? $('#fxDotted').checked : false;
  api('/api/fxsync', {note: note, dotted: dotted});
  fetch('/api/tap', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'apply'})})
  .then(r => r.json()).then(d => {
    if (!d || !d.ok){ showToast('Tap 写入失败: ' + ((d && d.error) || '未知')); return; }
    showToast('🎛 按 ' + note + (dotted ? ' 附点' : '') + ' 换算 = ' + d.seconds
      + ' 秒, 已写入 ' + (d.written || []).length + ' 个混响槽');
  }).catch(() => showToast('Tap 写入失败: 网络异常'));
}

// ---- 电动推子平滑动画: 显示值以缓动逼近目标值 ----
// ⚠️ 这里必须用 forEach 逐个 try 之外, 还要先判空: 「控制键」/「卡片管理」等地方也用
// .row 做布局, 但里面根本没有 <input>。早期版本直接 `sl.dataset.target` —— 一旦这些
// 行被渲染出来(渲染发生在首帧之后), tick() 就抛 TypeError, 而
// `requestAnimationFrame(tick)` 写在函数末尾, 异常一抛**整个缓动循环永久停摆**
// (推子从"电动滑行"退化成"瞬移", 且只在控制台留一条报错)。改为跳过没有 input 的行。
const anim = {};
function tick(){
  document.querySelectorAll('.row').forEach(r=>{
    const sl=r.querySelector('input');
    if(!sl) return;
    if(document.activeElement===sl) return;
    const t=parseFloat(sl.dataset.target);
    if(isNaN(t)) return;
    let c=anim[r.id];
    if(c===undefined) c=t;
    const d=t-c;
    if(Math.abs(d)>0.001){
      c += d*0.16;
      anim[r.id]=c;
      sl.value=c;
      r.querySelector('.db').textContent=(c<=0.001?'-∞':dbOf(c).toFixed(1))+' dB';
    } else {
      delete anim[r.id];
      sl.value=t;
    }
  });
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

// ---- 卡片实时值: 从台面状态生成一行摘要 (E1: 卡片显示实时参数) ----
function cardLive(c){
  if(!S) return '';
  const parts=[];
  for(const a of (c.actions||[])){
    if(a.kind==='mute'){
      const ids=a.chs||(a.ch?[a.ch]:[]);
      if(!ids.length) continue;
      const muted=ids.every(i=>S.ch[i]&&!S.ch[i].on);
      parts.push(ids.map(i=>dispName('ch',i)).join('、')+(muted?' 已静音':' 出声中'));
    } else if(a.kind==='mute_bus'){
      const ids=a.buses||(a.bus?[a.bus]:[]);
      if(!ids.length) continue;
      const muted=ids.every(i=>S.bus[i]&&!S.bus[i].on);
      parts.push(ids.map(i=>dispName('bus',i)).join('、')+(muted?' 已静音':' 出声中'));
    } else if(a.kind==='fade_ch'||a.kind==='fade_from'){
      const i=(a.chs||[a.ch])[0];
      if(!i) continue;
      const f=S.ch[i]?S.ch[i].fader:0;
      parts.push(dispName('ch',i)+' '+Math.round(f*100)+'%'+(a.to!==undefined?'→'+Math.round(a.to*100)+'%':''));
    } else if(a.kind==='fade_bus'||a.kind==='fade_from_bus'){
      const i=(a.buses||[a.bus])[0];
      if(!i) continue;
      const f=S.bus[i]?S.bus[i].fader:0;
      parts.push(dispName('bus',i)+' '+Math.round(f*100)+'%'+(a.to!==undefined?'→'+Math.round(a.to*100)+'%':''));
    } else if(a.kind==='wait'){
      parts.push('停 '+(+a.duration||1)+'s');
    } else if(a.kind==='rel_db'){
      const g2=a.group||'ch';
      const ids=a[{ch:'chs',bus:'buses',dca:'dcas'}[g2]]||[];
      if(!ids.length) continue;
      const f=S[g2]&&S[g2][ids[0]]?S[g2][ids[0]].fader:0;
      parts.push(ids.map(i=>dispName(g2,i)).join('、')+' '+Math.round(f*100)+'%'
        +(a.delta_db>=0?' +':' −')+Math.abs(+a.delta_db||0)+'dB');
    }
    if(parts.length>=2) break;
  }
  return parts.join(' · ');
}
function undoLast(){ api('/api/undo',{}); }

// ---- 危险卡防误触: 含全体类动作的卡片需长按 0.8 秒 (P2) ----
const DANGER=['unmute_all','fade_all','fade_odd_even'];
function isDanger(c){ return (c.actions||[]).some(a=>DANGER.includes(a.kind)); }
let holdRAF=null, holdEl=null, holdFired=false;
function holdStart(ev,id){
  const c=(S.cards||[]).find(x=>x.id===id);
  if(!c) return;
  if(!isDanger(c)){ runShow(id); return; }
  holdEl=ev.currentTarget; holdFired=false;
  ev.preventDefault();
  const bar=document.createElement('div'); bar.className='holdbar';
  holdEl.appendChild(bar);
  const t0=Date.now();
  const step=()=>{
    const p=Math.min(1,(Date.now()-t0)/800);
    bar.style.width=(p*100)+'%';
    if(p>=1){ holdFired=true; holdCancel(); runShow(id); return; }
    holdRAF=requestAnimationFrame(step);
  };
  holdRAF=requestAnimationFrame(step);
}
function holdCancel(){
  if(holdRAF){ cancelAnimationFrame(holdRAF); holdRAF=null; }
  if(holdEl){
    const b=holdEl.querySelector('.holdbar'); if(b) b.remove();
    if(!holdFired) showToast('⚠️ 危险卡片 — 需长按 0.8 秒确认执行');
    holdEl=null;
  }
}
document.addEventListener('pointerup',()=>holdCancel());

// ---- 卡片状态高亮: 动作目标已生效的计数 (P2) ----
function actSatisfied(a){
  const T=0.02;
  if(a.kind==='mute'){
    const ids=a.chs||(a.ch?[a.ch]:[]); if(!ids.length) return null;
    return ids.every(i=>S.ch[i]&&!S.ch[i].on);
  }
  if(a.kind==='mute_bus'){
    const ids=a.buses||(a.bus?[a.bus]:[]); if(!ids.length) return null;
    return ids.every(i=>S.bus[i]&&!S.bus[i].on);
  }
  if(a.kind==='scene') return S.scene===a.scene;
  if(a.kind==='unmute_all'){
    const all=x=>Object.values(x).every(v=>v.on);
    return all(S.ch)&&all(S.bus)&&all(S.dca)&&all(S.auxin)&&all(S.usb)&&all(S.fxret)&&all(S.mtx)&&S.main.on;
  }
  if(a.kind==='fade_ch'||a.kind==='fade_from'||a.kind==='fade_bus'||a.kind==='fade_from_bus'){
    const g=a.kind.indexOf('bus')>=0?'bus':'ch';
    const ids=a.chs||a.buses||(a.ch||a.bus?[a.ch||a.bus]:[]);
    if(!ids.length) return null;
    return ids.every(i=>S[g]&&S[g][i]&&Math.abs(S[g][i].fader-(a.to||0))<T);
  }
  if(a.kind==='rel_db') return null;
  return null;
}
function cardReady(c){
  let ok=0,tot=0;
  for(const a of (c.actions||[])){
    const s=actSatisfied(a);
    if(s!==null){ tot++; if(s) ok++; }
  }
  return tot?[ok,tot]:null;
}
function readyBadge(c){
  const r=cardReady(c);
  if(!r) return '';
  const [ok,tot]=r;
  return ok===tot
    ?` <span class="readyok">✔ ${ok}/${tot} 已就绪</span>`
    :` <span class="readyn">✔ ${ok}/${tot} 已生效</span>`;
}

// ---- 卡片定时触发 (P2) ----
function schedAsk(id){
  const v=prompt('定时启动卡片:\n输入等待秒数(如 30)\n或今天的时刻(如 20:30)','30');
  if(!v) return;
  const s=v.trim();
  let payload;
  if(/^[0-2]?\d:[0-5]\d$/.test(s)) payload={id:id,mode:'clock',time:s};
  else{
    const n=parseFloat(s);
    if(!(n>0)){ alert('格式不对:输入秒数或 HH:MM'); return; }
    payload={id:id,mode:'delay',delay:n};
  }
  fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)});
}
function fmtCd(sec){
  sec=Math.max(0,Math.round(sec));
  const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s2=sec%60;
  return (h?h+':':'')+String(m).padStart(2,'0')+':'+String(s2).padStart(2,'0');
}
function renderSched(){
  const el=$('#schedRow'); if(!el) return;
  const list=S.schedules||[];
  if(!list.length){ if(el._sig!==''){el._sig='';el.innerHTML='';} return; }
  const html=list.map(s=>{
    const cd=fmtCd(s.runAt-Date.now()/1000);
    const when=s.mode==='clock'?timeStr(s.runAt):(' '+cd+' 后');
    return `<div class="schedrow">⏱ 定时 <b>${esc(s.name)}</b>
      <span class="cd">${s.mode==='clock'?'今天 '+when:when+' 启动'}</span>
      <button style="background:#565d6d;color:#fff;padding:5px 12px" onclick="api('/api/schedcancel',{sid:${s.sid}})">取消</button></div>`;
  }).join('');
  if(el._sig!==html){ el._sig=html; el.innerHTML=html; }
}
function timeStr(epoch){
  const d=new Date(epoch*1000);
  return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');
}

// ---- 配置导入 / 导出 (P2) ----
function doExport(){ window.open('/api/export','_blank'); }
function impFileChg(inp){
  const f=inp.files&&inp.files[0]; if(!f) return;
  const r=new FileReader();
  r.onload=()=>{
    $('#impText').style.display='block';
    $('#impText').value=r.result;
    $('#impRow').style.display='flex';
  };
  r.readAsText(f);
}
function doImport(){
  let cfg;
  try{ cfg=JSON.parse($('#impText').value); }
  catch(e){ alert('JSON 解析失败: '+e.message); return; }
  if(!cfg||typeof cfg!=='object'||!cfg.cards){ alert('不是有效的配置文件(缺 cards 字段)'); return; }
  if(!confirm('导入将覆盖现有的卡片/控制键/通道命名,确定继续?')) return;
  fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({config:cfg})})
  .then(r=>r.json()).then(d=>{
    if(d.ok){ $('#impText').value=''; $('#impRow').style.display='none'; $('#impFile').value=''; }
    else alert('导入失败: '+(d.error||'未知错误'));
  }).catch(()=>alert('导入失败: 网络异常'));
}

// ---- 桥接真台 ----
function brGo(){
  const addr=$('#brAddr').value.trim();
  if(!addr){ alert('请填写真台地址,格式 IP:端口'); return; }
  fetch('/api/bridge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({addr:addr})})
  .then(r=>r.json()).then(d=>{
    showToast(d.ok?('🌉 '+d.msg):('桥接失败: '+(d.msg||'')));
  });
}
// 局域网扫描: 广播 /xinfo 发现调音台, 点结果即连 (手机零安装的关键一步)
function scanConsoles(){
  const btn=$('#scanBtn'), hint=$('#scanHint'), box=$('#scanRes');
  btn.disabled=true; btn.textContent='🔍 扫描中…';
  hint.textContent='正在广播发现指令…';
  box.innerHTML='';
  fetch('/api/scan').then(r=>r.json()).then(d=>{
    const found=(d&&d.found)||[];
    if(!found.length){
      hint.textContent='没找到调音台 — 请确认台子已通电、网线接好、与电脑在同一网段';
      box.innerHTML='';
      return;
    }
    hint.textContent='发现 '+found.length+' 台,点一下即连';
    box.innerHTML=found.map(f=>`
      <button class="scenechip" style="cursor:pointer;font-size:13px;padding:8px 14px"
        onclick="brPick('${f.ip}:${f.port}')">
        🎛 ${esc(f.name||'调音台')} · ${esc(f.ip)}${f.local?' (本机模拟器)':''}
      </button>`).join('');
  }).catch(()=>{ hint.textContent='扫描失败: 网络异常'; })
   .finally(()=>{ btn.disabled=false; btn.textContent='🔍 扫描局域网调音台'; });
}
function brPick(addr){
  $('#brAddr').value=addr;
  brGo();
}

// ---- 冲突通知: 手动干预 → 只停该通道, 弹提示 (D 维安全策略) ----
let evtSeen=-1;
function showToast(t){
  const d=document.createElement('div'); d.className='toast'; d.textContent=t;
  document.body.appendChild(d);
  requestAnimationFrame(()=>d.classList.add('in'));
  setTimeout(()=>{ d.classList.remove('in'); setTimeout(()=>d.remove(),400); },6000);
}
function pollEvents(){
  const evs=S.events||[];
  if(evtSeen<0){ evtSeen=evs.length?evs[evs.length-1].id:0; return; }
  evs.forEach(e=>{ if(e.id>evtSeen){ evtSeen=e.id; showToast(e.time+' '+e.text); } });
}

function render(){
  if(!S) return;
  const upd=(p,map)=>{
    for(const k in map){
      const r=document.getElementById('r-'+p+'-'+k); if(!r) continue;
      const f=map[k].fader, on=map[k].on;
      const sl=r.querySelector('input');
      if(document.activeElement!==sl) sl.dataset.target=f;
      r.querySelector('.db').textContent=(f<=0.001?'-∞':dbOf(f).toFixed(1))+' dB';
      r.querySelector('.mute').classList.toggle('on',!on);
      const nmEl=r.querySelector('.nm2');
      if(nmEl){ const nm=nameOf(P2G[p],+k); nmEl.textContent=nm; }
      // 该通道有任一处理在启用 (反相/延时/低切/插入/噪声门) → 编号上点个灯,
      // 不用逐个点开就知道"哪几路带了处理"
      const cn=r.querySelector('.chnum');
      if(cn) cn.classList.toggle('on',
        !!(map[k].invert||map[k].delay_on||map[k].hpf_on||map[k].ins_on||map[k].gate_on));
    }
  };
  upd('c',S.ch); upd('b',S.bus); upd('d',S.dca); upd('m',{0:S.main});
  upd('a',S.auxin); upd('u',S.usb); upd('x',S.fxret); upd('t',S.mtx);
  chpSync();

  // 电平表: 前 32 路是输入通道,后 16 路是输出 Bus
  drawMeters();
  $('#scene').textContent='场景 '+S.scene;
  if($('#scene2')) $('#scene2').textContent='场景 '+S.scene;
  $('#subs').textContent=S.subscribers;
  renderConsole();
  renderPorts();
  $('#hdinfo').textContent='UDP '+((S.ports&&S.ports.udp)||10023)+' · 场景 '+S.scene+' · 订阅 '+S.subscribers
    +(S.bridge&&S.bridge.on?(' · 🌉'+(S.bridge.live?'真台':'真台?')+' '+S.bridge.addr):'');
  $('#conn').style.background=S?'#22c55e':'#ef4444';
  if($('#brStat')){
    const b=S.bridge||{};
    $('#brStat').textContent=!b.on?'未桥接':(b.live?'🌉 已连接 '+b.addr:'⏳ 等待真台 '+b.addr);
    $('#brStat').style.color=b.on&&b.live?'#22c55e':'';
    const inp=$('#brAddr');
    if(inp && document.activeElement!==inp && !inp.value && b.target) inp.value=b.target;
    if($('#scanHint') && b.target && !b.on)
      $('#scanHint').textContent='已记住 '+b.target+',服务启动时会自动重连';
  }
  renderSched();
  updFx();

  // 名称有变化时刷新命名编辑器与各处下拉 (避免每 250ms 重建破坏交互)
  if($('#nmGroup')){
    const sig=JSON.stringify(S.names||{});
    if(window._nmSig!==sig){ window._nmSig=sig; updNmIdx(); }
  }

  // 快捷键 (签名相同不重建, 避免打断交互)
  if($('#keylist') && S.quickKeys){
    $('#stepSel').value=String(S.keyCfg.step_db);
    $('#rampSel').value=String(S.keyCfg.ramp);
    setHTML('keylist',S.quickKeys.map(k=>`
      <div class="row" style="justify-content:space-between">
        <div style="font-size:15px;font-weight:600">${esc(k.icon)} ${esc(k.name)}</div>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="keybtn" onpointerdown="kpStart('${k.id}')" title="按住可连发">${k.sign>0?'＋':'－'} ${S.keyCfg.step_db} dB</button>
          <button style="background:#565d6d;color:#fff;padding:8px 10px" onclick="editKey('${k.id}')">✏️</button>
          <button style="background:#565d6d;color:#fff;padding:8px 10px" onclick="delKey('${k.id}')">🗑</button>
        </div>
      </div>`).join(''));
  }

  // 卡片 (置顶优先; 执行中显示当前动作)
  const rv=S.show.running;
  const cards=[...(S.cards||[])].sort((a,b)=>(b.pin?1:0)-(a.pin?1:0));
  setHTML('cardlist',cards.map(c=>{
    const run=rv===c.id;
    const nxt=c.next?(cards.find(x=>x.id===c.next)||{}).name:'';
    const danger=isDanger(c);
    return `<div class="card"${c.pin?' style="border-color:rgba(255,180,84,.45)"':''}><div class="nm">${c.pin?'📌 ':''}${esc(c.name)}${readyBadge(c)}</div>
      <div class="ds">${esc(c.desc)}${nxt?` <span style="color:var(--accent)">→ 接续「${esc(nxt)}」</span>`:''}</div>
      ${run
        ?`<button class="stop" onclick="stopShow()">■ 停止</button>
          <div class="bar"><i style="width:${Math.round(S.show.progress*100)}%"></i></div>
          <div class="prog">执行中 ${Math.round(S.show.progress*100)}%${S.show.step?' · '+S.show.step:''}</div>`
        :`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button ${danger?'title="危险卡片:长按 0.8 秒执行"':''}
              onpointerdown="holdStart(event,'${c.id}')" onpointerup="holdCancel()"
              onpointerleave="holdCancel()" oncontextmenu="event.preventDefault()">▶ 运行${danger?' ⚠️':''}</button>
            <button style="background:#565d6d;color:#fff" onclick="schedAsk('${c.id}')">⏱ 定时</button>
            <button style="background:#565d6d;color:#fff" onclick="api('/api/cardpin',{id:'${c.id}'})">📌 ${c.pin?'取消置顶':'置顶'}</button>
            <button style="background:#565d6d;color:#fff" onclick="editCard('${c.id}')">✏️ 编辑</button>
            <button style="background:#565d6d;color:#fff" onclick="delCard('${c.id}')">🗑 删除</button>
          </div>`}
    </div>`;
  }).join(''));

  // 快照列表 + 回归进度
  const rc=S.recall.running;
  setHTML('snaplist',S.snapshots.map(sp=>{
    const run=rc===sp.id;
    return `<div class="card"><div class="nm">📌 ${esc(sp.name)} <small style="color:var(--sub);font-weight:400">· ${sp.time}</small></div>
      <div class="ds" style="margin:2px 0 6px">${esc(sp.scopeText||'全部通道 · 推子+静音')} · ${sp.count||0} 个值</div>
      ${run
        ?`<button class="stop" onclick="stopRecall()">■ 停止回归</button>
          <div class="bar"><i style="width:${Math.round(S.recall.progress*100)}%"></i></div>
          <div class="prog">电动推子回归中 ${Math.round(S.recall.progress*100)}% — 切到"通道"页看全部推子滑动</div>`
        :`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button onclick="recallSnap(${sp.id},document.getElementById('dur-${sp.id}'))">⏪ 回归</button>
            <select id="dur-${sp.id}" style="padding:7px;border-radius:8px;border:1px solid var(--line);font-size:12px">
              <option value="1.5">1.5 秒</option><option value="3" selected>3 秒</option>
              <option value="6">6 秒</option><option value="10">10 秒</option>
            </select>
            <button style="background:#565d6d;color:#fff" onclick="delSnap(${sp.id})">删除</button>
          </div>`}
    </div>`;
  }).join('') || '<div class="ds" style="color:var(--sub);font-size:12px;padding:4px">还没有预设。调好台面后点上方"保存预设",再拖乱推子,点"回归"看它们自动滑回去。</div>');

  // 卡片遥控器页
  if($('#tileCards')){
    const rv2=S.show.running;
    setHTML('undoRow',S.canUndo
      ?`<div class="tile undo" onclick="undoLast()">↩<small>撤销上一次卡片执行 · 台面自动滑回执行前</small></div>`:'');
    setHTML('tileCards',cards.map(c=>{
      const run=rv2===c.id;
      const evts=run?`onclick="stopShow()"`
        :`onpointerdown="holdStart(event,'${c.id}')" onpointerup="holdCancel()"
          onpointerleave="holdCancel()" oncontextmenu="event.preventDefault()"`;
      return `<div class="tile ${run?'run':''}" ${evts}>${esc(c.name)}${isDanger(c)?' ⚠️':''}
        ${run?`<div class="bar"><i style="width:${Math.round(S.show.progress*100)}%"></i></div><small>${S.show.step||'点按停止'}</small>`
             :`<small class="live">${(cardLive(c)||'点按运行')+readyBadge(c)}</small>`}</div>`;
    }).join('') + `<div class="tile add" onclick="openCardModal()">➕<small>新建演出卡片</small></div>`);
    setHTML('tileKeys',S.quickKeys.map(k=>
      `<div class="tile" onpointerdown="kpStart('${k.id}')" title="按住可连发">${esc(k.icon)}<small>${esc(k.name)} ${k.sign>0?'＋':'－'}${S.keyCfg.step_db}dB</small></div>`).join('')
      + `<div class="tile add" onclick="openKeyModal()">➕<small>新增控制键</small></div>`);
  }

  const lg=$('#logbox');
  const txt=S.log.join('\n');
  if(lg.dataset.t!==txt){ lg.textContent=txt; lg.scrollTop=lg.scrollHeight; lg.dataset.t=txt; }
}

// 电平表单独刷新 (数据来自 /api/meters, 不进 /api/state 的内容指纹)
function drawMeters(){
  if(!S||!S.meters) return;
  const meters=[...document.querySelectorAll('#chlist .meter i'),
                ...document.querySelectorAll('#buslist .meter i')];
  meters.forEach((el,idx)=>{
    if(idx<S.meters.length){
      const db=S.meters[idx];
      el.style.height=(db<=-60?0:Math.min(100,(db+60)/60*100))+'%';
    }
  });
}

function poll(){
  // 条件轮询: 带上一次的状态指纹 v, 服务端没变更就只回 ~30 字节
  // (全量轮询曾达 271-300MB/h; 电平改走 /api/meters, 不进指纹)
  let q=(window._logSeq===undefined?'/api/state':('/api/state?logseq='+window._logSeq));
  q+=(q.indexOf('?')<0?'?':'&')+'v='+encodeURIComponent(window._stateVer||'');
  fetch(q).then(r=>r.json()).then(d=>{
    if(window._logSeq===undefined){ window._logAcc=(d.log||[]).slice(); }
    else if(d.log && d.log.length){
      window._logAcc=window._logAcc.concat(d.log);
      if(window._logAcc.length>300) window._logAcc=window._logAcc.slice(-300);
    }
    window._logSeq=d.logseq;
    if(d.unchanged){
      // 状态没变: 只更新日志框, 不重建界面 (打断输入/按钮交互的元凶)
      if(S){
        S.log=window._logAcc||[];
        const lg=$('#logbox'); const txt=S.log.join('\n');
        if(lg.dataset.t!==txt){ lg.textContent=txt; lg.scrollTop=lg.scrollHeight; lg.dataset.t=txt; }
      }
      $('#conn').style.background='#22c55e';
    }else{
      d.log=window._logAcc||d.log||[];
      if(d.ver) window._stateVer=d.ver;
      S=d; $('#conn').style.background='#22c55e'; pollEvents(); render();
    }
  }).catch(()=>{ $('#conn').style.background='#ef4444'; $('#hdinfo').textContent='连接断开,重试中…'; })
   .finally(()=>setTimeout(poll,pollMs(250)));
}

// 页面不可见时降频: 手机揣兜里、切到别的 App、锁屏, 都是"看不见屏幕"。
// 演出 4 小时里这类时间往往占七成, 而 4 Hz 的电平轮询是流量大头 ——
// 藏起来就降到 2s 一次 (状态也一起降), 回到前台立刻恢复。
const POLL_HIDDEN_MS=2000;
function pollMs(active){ return document.hidden?POLL_HIDDEN_MS:active; }
function pollMeters(){
  fetch('/api/meters').then(r=>r.json()).then(d=>{
    if(!S) return;
    if(document.hidden) return;          // 藏起来就别画了, 省一次 DOM 重建
    S.meters=d.meters||S.meters; drawMeters();
  }).catch(()=>{}).finally(()=>setTimeout(pollMeters,pollMs(250)));
}
// 局部刷新: 内容没变不重建 innerHTML (避免打断按钮交互/选中状态)
function setHTML(id,html){
  const el=document.getElementById(id);
  if(el && el._sig!==html){ el._sig=html; el.innerHTML=html; }
}
function loadInfo(){
  fetch('/api/info').then(r=>r.json()).then(d=>{
    const u = d.lanUrl || '';
    $('#lanurl').textContent = u;
    $('#lanip2').textContent = d.lanIp;
    // 二维码内容就是上面显示的地址（所见即所得）；后端只负责渲染，不联网。
    const img = $('#lanqr');
    if(img && u) img.src = '/qr.svg?u=' + encodeURIComponent(u.replace(/\/?$/, '/'));

    // App 配对码：和网页地址的区别是它带 **OSC 端口**。用户把 UDP 端口改成
    // 10025 之后，网页地址里的 8080 不能拿来当 OSC 端口用 —— 所以另给一张码。
    const pc = d.pairCode || '';
    const pimg = $('#pairqr'), pcell = $('#paircell');
    if(pc && pimg && pcell){
      pcell.style.display = '';
      $('#paircode').textContent = pc;
      $('#pairhint').textContent = '配对码里带着 OSC 端口，改过端口也能一次连对。';
      pimg.src = '/pair.svg?h=' + encodeURIComponent(d.lanIp || '') +
                 '&o=' + (d.udpPort || 10023) + '&w=' + (d.webPort || 8080);
    }else if(pcell){
      pcell.style.display = 'none';
      const code = $('#paircode'); if(code) code.textContent = '';
      const hint = $('#pairhint');
      if(hint) hint.textContent = '未探测到局域网地址（断 VPN / 连上 WiFi 后刷新），App 配对码暂不可用。';
    }
  }).catch(()=>{});
}
// 复制连接地址（老浏览器没有 clipboard API 时退回 execCommand）
function copyText(txt, btn){
  const flash = () => {
    if(!btn) return;
    const old = btn.textContent;
    btn.textContent = '✓ 已复制';
    setTimeout(()=>{ btn.textContent = old; }, 1200);
  };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(flash).catch(()=>{});
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = txt; document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); flash(); }catch(e){}
  document.body.removeChild(ta);
}
function copyLan(){
  copyText($('#lanurl').textContent || '', $('#lanCopy'));
}
function copyPair(){
  copyText($('#paircode').textContent || '', $('#pairCopy'));
}
init();
loadInfo();
if ('serviceWorker' in navigator) {
  try { navigator.serviceWorker.register('/sw.js').catch(()=>{}); } catch(e) {}
}


// ==================== 自动增益 / 自动推子 ====================
// 控制律在服务端 (x32sim/rider.py)，这里只管配置与显示。
// 速度三档: 慢/中/快 = 平均窗口 0.9/0.35/0.12 s、死区 0.6/0.4/0.3 dB、
// 每秒最大修正 1.5/5/15 dB —— 页面只暴露"体感"，不暴露这些数字。
const RIDER_SPD=[['slow','慢'],['medium','中'],['fast','快']];

function loadRider(){
  fetch('/api/rider').then(r=>r.json()).then(renderRider).catch(()=>{});
}
function riderPost(body){
  fetch('/api/rider',{method:'POST',headers:{'Content-Type':'application/json'},
                      body:JSON.stringify(body)})
    .then(r=>r.json()).then(d=>{ if(d && d.rider) renderRider(d.rider); }).catch(()=>{});
}
function riderGlobal(on){ riderPost({enabled:!!on}); }
function riderReset(){ riderPost({action:'reset'}); }
function riderAdd(){
  const el=$('#riderCh'); const v=parseInt(el && el.value, 10);
  if(!(v>=1 && v<=32)){ if(el) el.focus(); return; }
  riderPost({channel:v, on:true, target:-18, mode:'fader', speed:'medium'});
  if(el) el.value='';
}
function riderSet(ch,patch){ riderPost(Object.assign({channel:ch}, patch)); }
function riderDel(ch){ riderPost({channel:ch, remove:true}); }

function renderRider(cfg){
  window._rider=cfg;
  const sw=$('#riderOn'); if(sw) sw.checked=!!cfg.enabled;
  const box=$('#riderlist'); if(!box) return;
  const ks=Object.keys(cfg.channels||{}).sort((a,b)=>(+a)-(+b));
  const hint=$('#riderHint');
  if(hint) hint.textContent = ks.length
    ? '改动立即生效（下一拍 50ms 内按新参数跑）。电平取通道表，写入走台面同一条管道。'
    : '还没配置通道。填通道号（1-32）→ 添加 → 打开「启用」。';
  box.innerHTML = ks.map(k=>{
    const c=cfg.channels[k];
    const opts=RIDER_SPD.map(([v,l])=>`<option value="${v}"`+(c.speed===v?' selected':'')+`>${l}</option>`).join('');
    return '<div class="card ridercard" data-ch="'+k+'">'
      + '<div class="nm">CH'+String(k).padStart(2,'0')+' · '+(c.mode==='fader'?'自动推子':'自动增益')+'</div>'
      + '<div class="rrow">'
      +   '<label class="rsw"><input type="checkbox" '+(c.on?'checked':'')+' onchange="riderSet('+k+',{on:this.checked})"> 启用</label>'
      +   '<select onchange="riderSet('+k+',{mode:this.value})">'
      +     '<option value="fader"'+(c.mode==='fader'?' selected':'')+'>推子</option>'
      +     '<option value="gain"'+(c.mode==='gain'?' selected':'')+'>增益</option></select>'
      +   '<select onchange="riderSet('+k+',{speed:this.value})">'+opts+'</select>'
      +   '<label class="rtarget">目标 <input type="number" step="0.5" min="-60" max="0" value="'+c.target+'"'
      +     (c.curve?' disabled title="已启用目标曲线，固定目标不生效"':'')
      +     ' onchange="riderSet('+k+',{target:this.value})"> dBFS</label>'
      +   '<button class="rdel" onclick="riderDel('+k+')">删除</button>'
      + '</div>'
      + '<div class="rrow rrow2">'
      +   '<label class="rtarget">表源 <select onchange="riderSet('+k+',{tap:this.value})">'
      +     '<option value="post"'+(c.tap!=='pre'?' selected':'')+'>推子后</option>'
      +     '<option value="pre"'+(c.tap==='pre'?' selected':'')+'>推子前</option></select></label>'
      +   '<label class="rtarget">侧链 <input type="number" min="0" max="32" step="1" value="'+(c.sidechain||0)+'"'
      +     ' onchange="riderSet('+k+',{sidechain:this.value})"> <span class="ds">0=本通道</span></label>'
      + '</div>'
      + curveArea(k, c)
      + '<div class="rmeter"><span id="rbar-'+k+'"></span></div>'
      + '<div class="ds" id="rlv-'+k+'">等待电平…</div>'
      + '</div>';
  }).join('');
  riderLive();
}

function riderLive(){
  const r=(S && S.rider)||null; if(!r) return;   // ⚠️ S 是 let，不在 window 上
  const map={}; (r.on||[]).forEach(x=>{ map[x.ch]=x; });
  document.querySelectorAll('.ridercard').forEach(card=>{
    const ch=+card.dataset.ch, x=map[ch];
    const bar=document.getElementById('rbar-'+ch), lv=document.getElementById('rlv-'+ch);
    if(!bar||!lv) return;
    if(!r.enabled){ bar.style.width='0%'; lv.textContent='总开关已关'; return; }
    if(!x){ bar.style.width='0%'; lv.textContent='未启用'; return; }
    if(x.level===null||x.level===undefined){
      bar.style.width='0%'; lv.textContent='无信号（冻结中 · 不会往上推）'; return;
    }
    const pct=Math.max(0,Math.min(100,(x.level+60)/60*100));
    bar.style.width=pct.toFixed(1)+'%';
    const app=(x.applied===null||x.applied===undefined)?'—':(+x.applied).toFixed(1);
    lv.textContent='电平 '+(+x.level).toFixed(1)+' dBFS · 修正 '+app+' dB · 目标 '+(+x.target).toFixed(1)
                  +(x.curve?'（曲线）':'')
                  +(x.sidechain?' · 侧链 CH'+String(x.sidechain).padStart(2,'0'):'')
                  +(x.tap==='pre'?' · 推子前':'')
                  +(x.paused?' · 人工接管中':'');
  });
}


// ---------- 目标曲线编辑器（点击加点 / 拖动改点 / 双击删点）----------
const CURVE_W=300, CURVE_H=84;
function curveArea(ch, c){
  const cv=c.curve||null, pts=(cv&&cv.points)||[];
  const last=pts.length?pts[pts.length-1][0]:60;
  const T=Math.max(10, (cv&&cv.loop)||last);
  const pos=(t,db)=>[ (t/T)*CURVE_W, CURVE_H-((db+60)/60)*CURVE_H ];
  const poly=pts.map(p=>{const q=pos(p[0],p[1]);return q[0].toFixed(1)+','+q[1].toFixed(1);}).join(' ');
  const dots=pts.map((p,i)=>{const q=pos(p[0],p[1]);
    return '<circle class="cdot" data-ch="'+ch+'" data-i="'+i+'" cx="'+q[0].toFixed(1)+'" cy="'+q[1].toFixed(1)+'" r="5"></circle>';}).join('');
  return '<div class="curvebox">'
    +  '<div class="cfn"><span>目标曲线'+(pts.length?' · '+pts.length+' 点 · 循环 '+(cv.loop||0)+'s':'（点图加点，双击点删除）')+'</span>'
    +    '<button class="cbtn" onclick="riderCurveClear('+ch+')">清除</button></div>'
    +  '<svg class="curvesvg" data-ch="'+ch+'" data-t="'+T+'" viewBox="0 0 '+CURVE_W+' '+CURVE_H+'">'
    +    '<rect x="0" y="0" width="'+CURVE_W+'" height="'+CURVE_H+'" class="cgrid"></rect>'
    +    (poly?'<polyline class="cline" points="'+poly+'"></polyline>':'')
    +    dots
    +  '</svg>'
    +  '<div class="cfn"><span class="ds">横轴 0-'+(+T).toFixed(0)+'s · 纵轴 -60..0 dBFS</span>'
    +    '<label class="rtarget">循环 <input type="number" min="0" max="3600" step="1" value="'+((cv&&cv.loop)||0)+'"'
    +      ' onchange="riderCurveLoop('+ch+',this.value)"> s</label></div>'
    + '</div>';
}
function curveOf(ch){
  const c=window._rider&&window._rider.channels&&window._rider.channels[String(ch)];
  return (c&&c.curve)||null;
}
function riderCurveClear(ch){ riderSet(ch,{curve:null}); }
function riderCurveLoop(ch,v){
  const cv=curveOf(ch); if(!cv) return;
  riderSet(ch,{curve:{loop:parseFloat(v)||0, points:cv.points}});
}
const RCURVE_DRAG={ch:0,i:-1};
function curveXY(svg,e){
  const r=svg.getBoundingClientRect();
  const T=parseFloat(svg.dataset.t)||60;
  const t=Math.max(0,Math.min(T,(e.clientX-r.left)/r.width*T));
  // 上边 = 0 dBFS, 下边 = -60 dBFS（绘制时 db=0 在顶部，这里必须同向）
  const db=Math.max(-60,Math.min(0,-(e.clientY-r.top)/r.height*60));
  return [Math.round(t*10)/10, Math.round(db*10)/10];
}
function curveApply(ch, pts, loop){
  const c=window._rider.channels[String(ch)];
  c.curve={loop:loop||0, points:pts};
  renderRider(window._rider);
}
document.addEventListener('pointerdown', e=>{
  const dot=e.target.closest('.cdot');
  if(dot){ RCURVE_DRAG.ch=+dot.dataset.ch; RCURVE_DRAG.i=+dot.dataset.i; e.preventDefault(); return; }
  const svg=e.target.closest('.curvesvg');
  if(!svg) return;
  const ch=+svg.dataset.ch, cv=curveOf(ch);
  const [t,db]=curveXY(svg,e);
  const pts=(cv&&cv.points?cv.points.map(p=>[p[0],p[1]]):[]).concat([[t,db]]);
  pts.sort((a,b)=>a[0]-b[0]);
  curveApply(ch, pts, (cv&&cv.loop)||0);
  riderSet(ch,{curve:{loop:(cv&&cv.loop)||0, points:pts}});
});
document.addEventListener('pointermove', e=>{
  if(!RCURVE_DRAG.ch) return;
  const svg=document.querySelector('.curvesvg[data-ch="'+RCURVE_DRAG.ch+'"]');
  if(!svg) return;
  const cv=curveOf(RCURVE_DRAG.ch); if(!cv||!cv.points) return;
  const [t,db]=curveXY(svg,e);
  const pts=cv.points.map(p=>[p[0],p[1]]);
  const i=RCURVE_DRAG.i; if(i<0||i>=pts.length) return;
  pts[i]=[t,db];
  curveApply(RCURVE_DRAG.ch, pts, cv.loop||0);
});
document.addEventListener('pointerup', ()=>{
  if(!RCURVE_DRAG.ch) return;
  const ch=RCURVE_DRAG.ch; RCURVE_DRAG.ch=0; RCURVE_DRAG.i=-1;
  const cv=curveOf(ch);
  if(cv&&cv.points&&cv.points.length>=2){
    // 拖动时本地已渲染，这里只把结果落库
    riderPost({channel:ch, curve:{loop:cv.loop||0, points:cv.points}});
  }
});
document.addEventListener('dblclick', e=>{
  const dot=e.target.closest('.cdot'); if(!dot) return;
  const ch=+dot.dataset.ch, cv=curveOf(ch); if(!cv||!cv.points) return;
  const pts=cv.points.filter((_,idx)=>idx!==+dot.dataset.i);
  if(pts.length>=2) riderSet(ch,{curve:{loop:cv.loop||0, points:pts}});
  else riderSet(ch,{curve:null});
});

loadRider();
setInterval(riderLive, 300);

