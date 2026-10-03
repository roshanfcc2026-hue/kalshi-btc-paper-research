/* Stats deck: richer first-call statistics from status.json, plus the quant paper pipeline if running.
   Read-only presentation. Calculations are pure (computeStats) so they can be tested without a browser. */
(function(){
'use strict';
const MARKET='market-mid-v1', CHECKPOINT=200;
function wilson(k,n,z=1.96){if(!n)return null;const p=k/n,d=1+z*z/n,c=(p+z*z/(2*n))/d,h=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return [c-h,c+h];}
function computeStats(status){
  const ev=(status&&status.forecast_evaluation)||{},m=ev[MARKET]||null,rows=[];
  for(const [source,v] of Object.entries(ev)){
    const n=v.settled_markets||0;
    rows.push({source,n,correct:v.correct||0,accuracy:v.accuracy,acc95:wilson(v.correct||0,n),brier:v.brier,logLoss:v.log_loss,
      brierSkill:(m&&m.brier&&v.brier!=null&&source!==MARKET)?1-v.brier/m.brier:null,
      llDelta:(m&&m.log_loss!=null&&v.log_loss!=null&&source!==MARKET)?v.log_loss-m.log_loss:null,
      cohorts:Object.entries(v.horizon_cohorts||{}).map(([name,c])=>({name:name.split(' ')[0],n:c.settled_markets,brier:c.brier})),
      nearMisses:(v.mistakes||[]).filter(x=>x.weak_direction).length,misses:(v.mistakes||[]).length});
  }
  rows.sort((a,b)=>(a.llDelta??1e9)-(b.llDelta??1e9));
  const settled=Math.max(0,...rows.map(r=>r.n));
  const best=rows.find(r=>r.llDelta!=null&&r.n>=30)||null;
  const learning=status&&status.learning||{};
  return {rows,settled,best,checkpoint:{have:learning.live_training_markets??settled,need:learning.required_training_markets||CHECKPOINT},market:m};
}
if(typeof module!=='undefined')module.exports={computeStats,wilson};
if(typeof document==='undefined')return;

const f=(v,d=3)=>v==null||!isFinite(v)?'—':Number(v).toFixed(d), pct=v=>v==null?'—':(v*100).toFixed(1)+'%';
const money=v=>v==null?'—':(v<0?'-$':'$')+Math.abs(v).toFixed(2);
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function tile(k,v,s,cls,meter){const t=node('div','tile');t.append(node('div','k',k),node('div','v '+(cls||''),v),node('div','s',s||''));
  if(meter!=null){const m=node('div','meter'),b=node('span');b.style.width=Math.max(0,Math.min(100,meter*100))+'%';m.append(b);t.append(m)}return t}

const deck=node('section','stats-deck');deck.id='stats-deck';deck.setAttribute('aria-label','Statistics deck');
const tiles=node('div','tiles'),board=node('div','board'),quant=node('div','tiles');
const h=node('h3');h.append(document.createTextNode('Source scoreboard'),node('small',null,'first calls only · Δ vs market midpoint · lower log loss is better'));
const tbl=node('table');board.append(h,tbl);deck.append(tiles,board,quant);
const anchor=document.querySelector('.analytics-kpis')||document.getElementById('notice');
(anchor?anchor:document.querySelector('main').firstChild).after(deck);

function renderTable(rows){tbl.replaceChildren();const head=node('tr');
  ['Source','Settled','Accuracy','95% range','Brier','Brier skill','Log loss','Δ log loss','Brier by time left','Weak misses'].forEach(x=>head.append(node('th',null,x)));tbl.append(head);
  if(!rows.length){const tr=node('tr');tr.append(node('td','dim','No settled first calls yet'));tbl.append(tr);return}
  for(const r of rows){const tr=node('tr');const td=(t,c)=>tr.append(node('td',c,t));
    td(r.source,r.source===MARKET?'dim':'cy');td(String(r.n));td(pct(r.accuracy));td(r.acc95?pct(r.acc95[0])+' – '+pct(r.acc95[1]):'—','dim');
    td(f(r.brier));td(r.brierSkill==null?'benchmark':(r.brierSkill>0?'+':'')+(r.brierSkill*100).toFixed(1)+'%',r.brierSkill==null?'dim':r.brierSkill>0?'good':'bad');
    td(f(r.logLoss,4));td(r.llDelta==null?'benchmark':(r.llDelta>0?'+':'')+f(r.llDelta,4),r.llDelta==null?'dim':r.llDelta<0?'good':'bad');
    td(r.cohorts.map(c=>c.name+' '+f(c.brier,2)).join(' · ')||'—');td(r.misses?r.nearMisses+'/'+r.misses+' near 50%':'—','dim');tbl.append(tr)}}

async function load(){
  try{const s=await (await fetch('/status.json',{cache:'no-store'})).json(),st=computeStats(s);
    const cp=st.checkpoint;tiles.replaceChildren(
      tile('Settled first calls',String(st.settled),'one per market per source','cy'),
      tile('Training checkpoint',cp.have+' / '+cp.need,cp.have>=cp.need?'enough to evaluate a candidate':'collecting settled markets',cp.have>=cp.need?'good':'',cp.have/cp.need),
      tile('Best source vs market',st.best?st.best.source:'—',st.best?'Δ log loss '+(st.best.llDelta>0?'+':'')+f(st.best.llDelta,4)+' · n='+st.best.n:'needs 30+ settled markets',st.best&&st.best.llDelta<0?'good':'warn'),
      tile('Market benchmark',st.market?f(st.market.log_loss,4):'—',st.market?'midpoint log loss · Brier '+f(st.market.brier):'no midpoint record yet'));
    renderTable(st.rows);}
  catch(e){tiles.replaceChildren(tile('Statistics','offline','status.json not available yet','warn'))}
  try{const r=await fetch('/quant-state.json',{cache:'no-store'});if(!r.ok)throw 0;const q=await r.json();if(q.error)throw 0;
    const st=q.trading_status,cls=st.startsWith('HALTED')?'bad':st.startsWith('WAITING')?'warn':'good';
    quant.replaceChildren(tile('Quant pipeline',st.split(':')[0],'paper only · '+(q.heartbeat_age==null?'no heartbeat':'heartbeat '+Math.round(q.heartbeat_age)+'s ago'),cls),
      tile('Paper bankroll',money(q.bankroll),'P&L '+(q.pnl_total>=0?'+':'')+money(q.pnl_total)+' after fees',q.pnl_total>=0?'good':'bad'),
      tile('Max drawdown',(q.max_drawdown*100).toFixed(1)+'%','halts at 5% day / 10% week',q.max_drawdown>.05?'warn':''),
      tile('Spot proxy',q.proxy&&q.proxy.price?'$'+Math.round(q.proxy.price).toLocaleString():'offline',q.proxy?q.proxy.n_used+'/'+q.proxy.n_total+' exchanges · full view on :8766':'start quant.collector',q.proxy&&q.proxy.price?'cy':'warn'))}
  catch(e){quant.replaceChildren()}
}
load();setInterval(load,15000);
})();
