(function(){
'use strict';
function viewFor(hash){return ({'#paper-trading':'signals','#wrong-predictions':'performance','#performance':'performance','#research':'research','#activity':'activity','#locked-call':'locked','#remaining-calls':'locked'})[hash]||'overview';}
function metrics(s){const v=s?.forecast_evaluation?.['volatility-proxy-v1'];return v?{settled:v.settled_markets,correct:v.correct,wrong:v.wrong,brier:v.brier,logLoss:v.log_loss}:null;}
if(typeof module!=='undefined')module.exports={viewFor,metrics};
if(typeof document==='undefined')return;
const $=id=>document.getElementById(id),main=document.querySelector('main');
const header=document.querySelector('header');header.querySelector('h1').textContent='BTC Research Analytics';
header.querySelector('.intro').textContent='Live signals, model performance and paper research in one workspace.';
function tag(node,view){node.classList.add('analytics-pane');node.dataset.view=view;}
tag(document.querySelector('.forecast-grid'),'overview');tag($('paper-trading'),'signals');tag($('wrong-predictions'),'performance');
const groups={'OpenAI review':'research','Model comparison':'performance','Learning progress':'research','Recent predictions & official outcomes':'activity'};
for(const section of main.querySelectorAll(':scope > section')){const title=section.querySelector('h2')?.textContent;if(groups[title])tag(section,groups[title]);}
const nav=document.createElement('nav');nav.className='analytics-nav';nav.setAttribute('aria-label','Analytics pages');
for(const [id,label,hash] of [['overview','Overview','#overview'],['signals','Signal analysis','#paper-trading'],['locked','Locked call','#locked-call'],['performance','Performance','#performance'],['research','Research lab','#research'],['activity','Data explorer','#activity']]){
 const a=document.createElement('a');a.href=hash;a.textContent=label;a.dataset.page=id;a.addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));nav.append(a);
}header.after(nav);
const kpis=document.createElement('div');kpis.className='analytics-kpis';kpis.setAttribute('aria-label','Original forecast summary');
for(const [id,label] of [['settled','Settled markets'],['correct','Correct first calls'],['wrong','Wrong first calls'],['brier','Brier score ↓']]){const card=document.createElement('div'),labelEl=document.createElement('span'),value=document.createElement('strong');labelEl.textContent=label;value.id='analytics-'+id;value.textContent='—';card.append(labelEl,value);kpis.append(card);}nav.after(kpis);
const status=document.createElement('p');status.id='analytics-data-status';status.className='muted';kpis.after(status);
const chart=document.createElement('section');chart.className='card analytics-pane analytics-comparison';chart.dataset.view='performance';
chart.innerHTML='<div class="label">First calls only · lower error is better</div><h2>Model vs market</h2><p class="muted">Probability errors across recorded settled markets. Historical comparison does not prove a trading advantage.</p><div id="analytics-bars"></div><p class="muted" id="analytics-loss"></p>';
$('wrong-predictions').before(chart);
const explorer=$('recent').closest('section');const tools=document.createElement('div');tools.className='analytics-filters';
tools.innerHTML='<label>Search recorded rows<input id="analytics-search" type="search" placeholder="Contract, source or outcome"></label><p class="muted" id="analytics-filter-count"></p>';
explorer.querySelector('h2').after(tools);
function filter(){const query=$('analytics-search').value.toLowerCase().trim();const rows=[...$('recent').rows];let count=0;for(const row of rows){const match=row.textContent.toLowerCase().includes(query);row.hidden=!match;if(match)count++;}$('analytics-filter-count').textContent=count+' of '+rows.length+' recent snapshots shown · repeated updates are not independent markets';}
$('analytics-search').addEventListener('input',filter);new MutationObserver(filter).observe($('recent'),{childList:true});filter();
function navigate(){const view=viewFor(location.hash);for(const p of document.querySelectorAll('.analytics-pane'))p.hidden=p.dataset.view!==view;for(const a of nav.querySelectorAll('a')){if(a.dataset.page===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}}
addEventListener('hashchange',navigate);navigate();
let saved=null,failed=false,busy=false;
function health(){if(!saved){status.textContent='Waiting for the local report…';return;}const age=Date.now()/1000-saved.updated_at;status.textContent=(failed?'Connection unavailable · ':age<0||age>45?'Report stale · ':'Report current · ')+new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(saved.updated_at*1000))+' · Historical metrics stay visible · Real-money entry: SKIP';}
async function refresh(){if(busy)return;busy=true;try{const response=await fetch('/status.json',{cache:'no-store',signal:AbortSignal.timeout(4500)});if(!response.ok)throw Error('Unavailable');const s=await response.json();if(!Number.isFinite(s.updated_at))throw Error('Invalid timestamp');saved=s;failed=false;const m=metrics(s);for(const key of ['settled','correct','wrong','brier'])$('analytics-'+key).textContent=Number.isFinite(m?.[key])?(key==='brier'?m[key].toFixed(4):m[key]):'—';
 const bars=$('analytics-bars');bars.replaceChildren();for(const [source,label] of [['volatility-proxy-v1','Volatility model'],['market-mid-v1','Kalshi midpoint']]){const v=s.forecast_evaluation?.[source];const row=document.createElement('div');row.className='analytics-bar-row';const name=document.createElement('span');name.textContent=label;const track=document.createElement('div');track.className='analytics-track';const bar=document.createElement('div');bar.style.width=Number.isFinite(v?.brier)?Math.max(0,Math.min(1,v.brier))*100+'%':'0%';bar.className=source==='market-mid-v1'?'market-bar':'model-bar';track.append(bar);const value=document.createElement('strong');value.textContent=Number.isFinite(v?.brier)?v.brier.toFixed(4):'—';row.append(name,track,value);bars.append(row);}
 const model=s.forecast_evaluation?.['volatility-proxy-v1'],market=s.forecast_evaluation?.['market-mid-v1'];$('analytics-loss').textContent='Brier scale: 0–1. Log loss: model '+(Number.isFinite(model?.log_loss)?model.log_loss.toFixed(4):'—')+' / market '+(Number.isFinite(market?.log_loss)?market.log_loss.toFixed(4):'—')+'. Source sample sizes are listed below.';
 }catch{failed=true;}finally{busy=false;health();}}
refresh();setInterval(refresh,5000);setInterval(health,1000);
})();
