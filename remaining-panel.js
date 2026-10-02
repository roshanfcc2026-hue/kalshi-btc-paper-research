(function(scope){
'use strict';
const finite=n=>typeof n==='number'&&Number.isFinite(n);
function checkpoint(report,key,now){
 const empty={label:'WAITING FOR DATA',note:'Waiting for the checkpoint recorder.',decision:null,cutoff:null};
 if(!report||report.source!=='remaining-lock-v1'||!finite(now)||!finite(report.generated_at))return empty;
 if(report.status!=='running'||now-report.generated_at>45||now<report.generated_at)return {...empty,label:'MONITOR UNAVAILABLE',note:'Current recorder health is unavailable. Historical calls remain in the ledger.'};
 const a=report.active,c=a?.decisions?.[key];
 if(!a||!finite(a.open_ts)||!finite(a.close_ts)||now<a.open_ts||now>=a.close_ts||!c||!finite(c.cutoff))return empty;
 if(c.state==='not_registered')return {...empty,label:'STARTS NEXT CYCLE',note:'This cutoff passed before registration; no past call is invented.',cutoff:c.cutoff};
 if(c.state==='locked'){
  const d=c.decision;
  if(!d||!['UP / YES','DOWN / NO','SKIP'].includes(d.direction)||!finite(d.locked_at)||d.locked_at>now)return empty;
  if(d.direction!=='SKIP'&&(!finite(d.p_yes)||d.p_yes<0||d.p_yes>1||!finite(d.observed)||d.observed>c.cutoff||!finite(d.seen_at)||d.seen_at>c.cutoff))return empty;
  return {label:d.direction,note:d.direction==='SKIP'?'Fixed SKIP: '+d.reason:'LOCKED — this call stays fixed until settlement.',decision:d,cutoff:c.cutoff};
 }
 return {...empty,label:now<c.cutoff?'WAITING FOR CUTOFF':'RECORDING DECISION',note:'Will use the latest eligible estimate already seen before this cutoff.',cutoff:c.cutoff};
}
if(typeof module!=='undefined')module.exports={checkpoint};
if(!scope.document)return;
const host=document.getElementById('locked-call');if(!host)return;
const section=document.createElement('section');section.id='remaining-calls';section.style.cssText='margin:28px 0;border-top:1px solid #52677d;padding-top:20px';
section.innerHTML='<div class="label">New forward checkpoints · remaining-lock-v1</div><h3>7-minute and 4-minute locked decisions</h3><p id="remaining-window"></p><p class="muted">Each checkpoint has its own fixed call. The 4-minute decision can differ; it never rewrites the 7-minute decision. These are two checkpoints on the same markets, not twice as many independent outcomes.</p><div id="remaining-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px"></div><p class="muted">UP requires at least 60% YES; DOWN requires at most 40% YES; otherwise SKIP. Missing evidence also means SKIP. No trades are placed.</p><h4>Forward checkpoint results</h4><div style="overflow-x:auto"><table><thead><tr><th>Checkpoint</th><th>Correct</th><th>Wrong</th><th>Settled calls</th><th>Skips</th><th>Model Brier ↓</th><th>Market Brier ↓</th></tr></thead><tbody id="remaining-results"></tbody></table></div><h4>Saved checkpoint decisions</h4><div style="overflow-x:auto"><table><thead><tr><th>Market closes</th><th>Remaining</th><th>Fixed call</th><th>YES estimate</th><th>Locked at</th><th>Official result</th></tr></thead><tbody id="remaining-ledger"></tbody></table></div>';
const details=host.querySelector('details');if(details)details.before(section);else host.append(section);
for(const key of ['7','4']){const card=document.createElement('article');card.style.cssText='background:#101a28;border:1px solid #52677d;border-radius:10px;padding:20px';card.innerHTML='<h4>'+key+' minutes remaining</h4><div id="remaining-'+key+'-label" style="font-size:27px;font-weight:700;color:#9bdac8">WAITING</div><p id="remaining-'+key+'-note"></p><p id="remaining-'+key+'-prob" class="muted"></p><p id="remaining-'+key+'-time" class="muted"></p>';section.querySelector('#remaining-cards').append(card);}
const el=id=>document.getElementById(id),pct=p=>finite(p)?(p*100).toFixed(1)+'%':'—';
const clock=t=>finite(t)?new Date(t*1000).toLocaleTimeString('en-US',{timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}):'—';
let report=null,failed=false,busy=false;
function render(){const now=Date.now()/1000;
 el('remaining-window').textContent=report?.active?clock(report.active.open_ts)+' → '+clock(report.active.close_ts):'Waiting for market window';
 for(const key of ['7','4']){const s=checkpoint(failed?null:report,key,now),d=s.decision;
 el('remaining-'+key+'-label').textContent=s.label;el('remaining-'+key+'-note').textContent=s.note;
 el('remaining-'+key+'-prob').textContent=d&&finite(d.p_yes)?'Model estimate (unvalidated): YES '+pct(d.p_yes)+' / NO '+pct(1-d.p_yes):'No saved probability yet.';
 el('remaining-'+key+'-time').textContent='Cutoff '+clock(s.cutoff)+(d?' · Locked '+clock(d.locked_at)+' · Forecast '+clock(d.observed):'');}}
function addRow(body,values){const tr=document.createElement('tr');for(const v of values){const td=document.createElement('td');td.textContent=v;tr.append(td);}body.append(tr);}
async function refresh(){if(busy)return;busy=true;try{const res=await fetch('/remaining-report.json',{cache:'no-store',signal:AbortSignal.timeout(4500)});if(!res.ok)throw Error();report=await res.json();failed=false;
 const body=el('remaining-results');body.replaceChildren();for(const key of ['7','4']){const e=report.evaluation?.[key]||{};addRow(body,[key+' min',e.correct??0,e.wrong??0,e.settled_calls??0,e.skips??0,finite(e.model?.brier)?e.model.brier.toFixed(4):'—',finite(e.market?.brier)?e.market.brier.toFixed(4):'—']);}
 const ledger=el('remaining-ledger');ledger.replaceChildren();for(const d of [...(report.ledger||[])].reverse().slice(0,40))addRow(ledger,[clock(d.close_ts),d.remaining_minutes+' min',d.direction,pct(d.p_yes),clock(d.locked_at),d.result||(d.direction==='SKIP'?'No call':'Pending')]);if(!report.ledger?.length)addRow(ledger,['—','—','Waiting for the first decision','—','—','—']);
 }catch{failed=true;}finally{busy=false;render();}}
render();refresh();setInterval(refresh,5000);setInterval(render,1000);
})(typeof globalThis!=='undefined'?globalThis:this);
