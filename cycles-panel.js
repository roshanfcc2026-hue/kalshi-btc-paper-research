/* 15-minute cycle views: live cycle timeline, last-24h window tiles, results by Pacific hour. Read-only. */
(function(){
'use strict';
const WINDOW=900;
function windowFor(now){const open=Math.floor(now/WINDOW)*WINDOW;return {open,close:open+WINDOW}}
function marks(cur,win){const open=cur?cur.open:win.open,close=cur?cur.close:win.close;
  return [{at:open+480,label:'7:00 left lock'},{at:open+660,label:'4:00 left lock'},{at:close-60,label:'final minute'}]
    .concat(cur&&cur.first_call_ts?[{at:cur.first_call_ts,label:'prediction',first:true}]:[])
    .concat(cur&&cur.entry&&cur.entry.action!=='skip'?[{at:cur.entry.ts,label:'entry',entry:true}]:[])}
function callText(cur){if(!cur||cur.first_call_p==null)return {dir:'NO PREDICTION YET',detail:'waiting for the first call in this window',cls:'dim'};
  const up=cur.first_call_p>=.5,p=up?cur.first_call_p:1-cur.first_call_p;return {dir:(up?'UP / YES ':'DOWN / NO ')+(p*100).toFixed(0)+'%',detail:'first call · fixed for scoring',cls:up?'good':'bad'}}
function entryText(e){if(!e)return {dir:'NO PAPER ENTRY',detail:'combo-v1 not frozen or no decision yet',cls:'dim'};
  if(e.action==='skip')return {dir:'SKIP',detail:e.reason||'no edge after fees',cls:'warn'};
  return {dir:'BUY '+e.action.toUpperCase()+' ×'+e.contracts+' @ '+Math.round(e.avg_price*100)+'¢',detail:'paper only · fee $'+(e.fee||0).toFixed(2)+' · model '+(e.p_yes*100).toFixed(0)+'% yes',cls:'cy'}}
if(typeof module!=='undefined')module.exports={windowFor,marks,callText,entryText};
if(typeof document==='undefined')return;

const NS='http://www.w3.org/2000/svg';
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function svg(tag,a,text){const e=document.createElementNS(NS,tag);for(const k in a)e.setAttribute(k,a[k]);if(text!=null)e.textContent=text;return e}
const pt=ts=>new Date(ts*1000).toLocaleTimeString('en-US',{timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit'});
const mmss=s=>{s=Math.max(0,Math.round(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};

const box=node('section','stats-deck cycles');box.id='cycles';box.setAttribute('aria-label','15-minute cycles');
const tl=node('div','board'),tiles=node('div','board'),hours=node('div','board');
const h3=(t,s)=>{const h=node('h3');h.append(document.createTextNode(t),node('small',null,s));return h};
const readout=node('div','cycle-readout');
const tlHead=h3('Cycle timeline','current 15-minute window · Pacific'),tlSvg=svg('svg',{viewBox:'0 0 900 100',role:'img','aria-label':'Current 15-minute cycle timeline'});tl.append(tlHead,readout,tlSvg);
const tilesHead=h3('Last 24 hours','one tile per 15-minute window'),grid=node('div','cycle-grid'),legend=node('div','cycle-legend');tiles.append(tilesHead,grid,legend);
const hoursHead=h3('Results by hour','first-call accuracy by Pacific hour the window opened · bar opacity = sample size'),hSvg=svg('svg',{viewBox:'0 0 900 200',role:'img','aria-label':'Accuracy by hour of day'});hours.append(hoursHead,hSvg);
box.append(tl,tiles,hours);
(document.getElementById('stats-deck')||document.querySelector('.analytics-kpis')||document.getElementById('notice')).after(box);
const tip=node('div');tip.id='cycle-tip';tip.hidden=true;document.body.append(tip);
function showTip(e,t){tip.hidden=false;tip.textContent=t;tip.style.left=Math.min(e.clientX+14,innerWidth-260)+'px';tip.style.top=(e.clientY+14)+'px'}
let report=null;

function drawTimeline(){const now=Date.now()/1000,cur=report&&report.current,w=cur?{open:cur.open,close:cur.close}:windowFor(now);
  tlSvg.replaceChildren();const L=20,R=880,y=44,x=t=>L+(t-w.open)/(w.close-w.open)*(R-L);
  tlSvg.append(svg('rect',{x:L,y:y-4,width:R-L,height:8,fill:'var(--grid)'}));
  tlSvg.append(svg('rect',{x:x(w.close-60),y:y-4,width:R-x(w.close-60),height:8,fill:'rgba(255,107,122,.35)'}));
  const nx=Math.min(R,Math.max(L,x(now)));tlSvg.append(svg('rect',{x:L,y:y-4,width:nx-L,height:8,fill:'var(--cyan)',style:'filter:drop-shadow(0 0 4px rgba(67,212,255,.7))'}));
  for(let m=0;m<=15;m+=5)tlSvg.append(svg('text',{x:x(w.open+m*60),y:y+24,'text-anchor':m===0?'start':m===15?'end':'middle'},m===0?pt(w.open):m===15?pt(w.close):'+'+m+'m'));
  for(const k of marks(cur,w)){const kx=x(k.at),col=k.entry?'var(--cyan)':k.first?'var(--good)':'var(--ink2)',below=!!k.entry;
    tlSvg.append(svg('line',{x1:kx,x2:kx,y1:below?y-6:y-14,y2:below?y+34:y+6,stroke:col,'stroke-width':k.first||k.entry?2:1}));
    const anchor=kx<90?'start':kx>810?'end':'middle';
    tlSvg.append(svg('text',{x:kx,y:below?y+46:y-20,'text-anchor':anchor,style:'fill:'+col},k.label+(k.first||k.entry?' '+pt(k.at):'')))}
  tlSvg.append(svg('circle',{cx:nx,cy:y,r:6,fill:'var(--void)',stroke:'var(--cyan)','stroke-width':2.5}));
  const left=w.close-now;tlHead.lastChild.textContent=(cur?cur.ticker:'expected window')+' · '+mmss(left)+' left'+(left<=60?' · final averaging minute, no forecasts':'');
  readout.replaceChildren();
  for(const [k,t,ts] of [['Prediction',callText(cur),cur&&cur.first_call_ts],['Paper entry',entryText(cur&&cur.entry),cur&&cur.entry&&cur.entry.ts]]){
    const d=node('div','tile');d.append(node('div','k',k),node('div','v '+t.cls,t.dir),node('div','s',t.detail+(ts?' · '+pt(ts):'')));readout.append(d)}}

function drawTiles(){grid.replaceChildren();if(!report)return;
  const t=report.last_24h;if(!t.length){grid.append(node('div','dim','No windows closed in the last 24 hours yet.'))}
  t.forEach(x=>{const c=node('div','cycle-tile '+x.state);c.setAttribute('role','img');
    const label=pt(x.open)+' · '+x.state+(x.p_yes!=null?' · P(yes) '+(x.p_yes*100).toFixed(0)+'%':'')+(x.result?' · result '+x.result.toUpperCase():'');c.setAttribute('aria-label',label);
    c.onpointermove=e=>showTip(e,label);c.onpointerleave=()=>tip.hidden=true;grid.append(c)});
  const n=report.last_24h_counts,scored=n.correct+n.wrong;legend.replaceChildren();
  [['correct',n.correct],['wrong',n.wrong],['pending',n.pending],['skipped',n.skipped]].forEach(([k,v])=>{const s=node('span');s.append(node('i','cycle-tile '+k),document.createTextNode(k+' '+v));legend.append(s)});
  legend.append(node('span','cy',scored?'hit rate '+(n.correct/scored*100).toFixed(1)+'% of '+scored:'no scored windows'))}

function drawHours(){hSvg.replaceChildren();if(!report)return;const H=200,L=36,B=26,T=12,bw=(900-L-6)/24,y=v=>T+(1-v)*(H-T-B),maxn=Math.max(1,...report.by_hour.map(h=>h.n));
  for(const v of [0,.25,.5,.75,1]){hSvg.append(svg('line',{x1:L,x2:894,y1:y(v),y2:y(v),stroke:v===.5?'var(--line)':'var(--grid)','stroke-dasharray':v===.5?'4 4':''}));hSvg.append(svg('text',{x:L-5,y:y(v)+3,'text-anchor':'end'},v*100+'%'))}
  report.by_hour.forEach(h=>{const x=L+h.hour*bw+2;if(h.hour%3===0)hSvg.append(svg('text',{x:x+bw/2-2,y:H-8,'text-anchor':'middle'},((h.hour+11)%12+1)+(h.hour<12?'a':'p')));
    if(!h.n)return;const v=h.accuracy,r=svg('rect',{x,y:y(v),width:bw-4,height:H-B-y(v),fill:v>=.5?'var(--cyan)':'var(--bad)','fill-opacity':.25+.7*h.n/maxn,rx:2});
    const label=`${((h.hour+11)%12+1)}${h.hour<12?' AM':' PM'} PT · ${h.n} windows · accuracy ${(v*100).toFixed(1)}% · Brier ${h.brier.toFixed(3)}`+(h.paper_trades?` · paper ${h.paper_trades} trades, P&L ${h.paper_pnl>=0?'+':''}$${h.paper_pnl.toFixed(2)}`:'');
    r.onpointermove=e=>showTip(e,label);r.onpointerleave=()=>tip.hidden=true;r.setAttribute('aria-label',label);hSvg.append(r)})}

async function load(){try{const r=await fetch('/cycles.json',{cache:'no-store'});if(!r.ok)throw 0;report=await r.json()}catch(e){report=null}drawTiles();drawHours()}
load();setInterval(load,30000);drawTimeline();setInterval(drawTimeline,1000);
})();
