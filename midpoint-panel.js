(function(scope){
'use strict';
const finite=n=>typeof n==='number'&&Number.isFinite(n);
function state(report,now){
 const empty={label:'WAITING FOR DATA',note:'Waiting for the locked-call monitor.',active:null};
 if(!report||report.source!=='midpoint-lock-v1'||!finite(now)||!finite(report.generated_at))return empty;
 if(report.status && report.status!=='running')return {label:'MONITOR STOPPED',note:'The study is stopped. Saved historical decisions remain in the ledger.',active:null};
 const raw=report.active;
 const a=raw?{...raw,...(raw.decision||{}),decision_ts:raw.lock_after,deadline_ts:raw.deadline,status:raw.state==='locked'?(raw.decision?.direction==='SKIP'?'SKIP':'LOCKED'):raw.state}:null;
 if(!a||!finite(a.open_ts)||!finite(a.close_ts)||now<a.open_ts||now>=a.close_ts)return {...empty,note:'Waiting for this market. A previous market’s call will not be reused.'};
 if(a.state==='not_registered')return {label:'STARTS NEXT CYCLE',note:'This market’s decision time passed before registration. No retrospective call will be made.',active:a};
 const stale=now-report.generated_at>45||now<report.generated_at;
 if(stale)return {label:'MONITOR STALE',note:'No current call shown. Check recorder health.',active:null};
 if(a.status==='LOCKED'&&['UP / YES','DOWN / NO'].includes(a.direction)&&finite(a.locked_at)&&a.locked_at<=now&&finite(a.p_yes)&&a.p_yes>=0&&a.p_yes<=1)return {label:a.direction,note:'LOCKED — this saved call will not change before settlement. It is not a current buy signal.',active:a};
 if(a.status==='SKIP')return {label:'SKIP',note:'No directional call for this market: '+(a.reason||'insufficient evidence')+'. This decision stays fixed.',active:a};
 if(now>=a.deadline_ts)return {label:'AWAITING RECORDED DECISION',note:'Decision window ended. No late replacement call is permitted.',active:a};
 return {label:now<a.decision_ts?'CALCULATING — WAIT':'CHECKING FRESH EVIDENCE',note:'One decision at halfway through the market, using the first fresh eligible observation.',active:a};
}
function timingSummary(report){
 if(!report||!finite(report.as_of))return null;
 const rows=[];
 for(const key of ['7','4']){
  const r=report.results?.[key],c=report.common_market_comparison?.[key];
  if(!r||!c)return null;
  const nums=[r.wins,r.losses,r.scored,r.eligible_settled_markets,r.missing_or_stale,r.invalid,c.wins,c.losses,c.scored];
  if(nums.some(n=>!Number.isInteger(n)||n<0)||r.wins+r.losses!==r.scored||r.scored+r.missing_or_stale+r.invalid!==r.eligible_settled_markets||c.wins+c.losses!==c.scored||c.scored>r.scored||c.wins>r.wins||c.losses>r.losses)return null;
  rows.push({minutes:Number(key),wins:r.wins,losses:r.losses,scored:r.scored,missing:r.missing_or_stale,invalid:r.invalid,accuracy:r.scored?r.wins/r.scored:null,commonWins:c.wins,commonLosses:c.losses,commonScored:c.scored});
 }
 if(rows[0].commonScored!==rows[1].commonScored)return null;
 return {asOf:report.as_of,rows};
}
if(typeof module!=='undefined')module.exports={state,timingSummary};
if(!scope.document)return;
const pane=document.createElement('section');pane.className='card analytics-pane';pane.dataset.view='locked';pane.id='locked-call';
pane.innerHTML=`<div class="label">midpoint-lock-v1 · separate experimental call</div><h2>One call. Then it stays fixed.</h2><p><a href="#remaining-calls">View the 7-minute / 4-minute fixed decisions ↓</a></p><p id="locked-window">Waiting for market window</p><div id="locked-label" style="font-size:42px;font-weight:700;margin:18px 0;color:#9bdac8">WAITING FOR DATA</div><p id="locked-note"></p><p id="locked-prob" class="muted"></p><p id="locked-timing" class="muted"></p><p id="locked-prices" class="muted"></p><div class="strategy"><strong>Paper research only · live entry SKIP</strong><span>This forecast timing study records observations only. Its accuracy is unvalidated and it does not place trades.</span></div><details open><summary>When the call is made</summary><ol><li>Wait until 7 minutes 30 seconds after the market opens.</li><li>Capture the first fresh eligible estimate, without waiting for a favorable probability.</li><li>Lock UP at 60% YES or higher; lock DOWN at 40% YES or lower. Otherwise lock SKIP.</li><li>If fresh evidence is unavailable by 8 minutes 30 seconds after open, record SKIP. Never backfill missed calls.</li><li>Score against the official result and the market midpoint captured with that call. Opening-call scores stay separate.</li></ol><p class="muted">The 60% cutoff is experimental. Waiting longer does not guarantee accuracy; this remains the same unvalidated volatility model.</p></details><h3>Forward results at the same timestamps</h3><p id="locked-counts">No settled evidence yet.</p><div style="overflow-x:auto"><table><thead><tr><th>Source</th><th>Brier ↓</th><th>Log loss ↓</th></tr></thead><tbody id="locked-metrics"></tbody></table></div><h3>Locked decisions</h3><div style="overflow-x:auto"><table><thead><tr><th>Market closes (Pacific)</th><th>Call / reason</th><th>YES probability</th><th>Locked at</th><th>Official result</th></tr></thead><tbody id="locked-ledger"></tbody></table></div>`;
document.querySelector('main').append(pane);
const clock=t=>finite(t)?new Date(t*1000).toLocaleTimeString('en-US',{timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}):'—';
const pct=p=>finite(p)?(100*p).toFixed(1)+'%':'—';
const money=p=>finite(p)?(100*p).toFixed(1)+'¢':'—';
const el=id=>document.getElementById(id);
let report=null,failed=false,busy=false;
function visible(){pane.hidden=!['#locked-call','#remaining-calls'].includes(location.hash);}addEventListener('hashchange',visible);visible();
function render(){const now=Date.now()/1000,v=state(failed?null:report,now),a=v.active;
 el('locked-label').textContent=v.label;el('locked-note').textContent=v.note;
 el('locked-window').textContent=a?clock(a.open_ts)+' → '+clock(a.close_ts):'Waiting for current market';
 el('locked-prob').textContent=a&&finite(a.p_yes)?'model estimate (unvalidated) · YES '+pct(a.p_yes)+' / NO '+pct(1-a.p_yes):'Probability appears only when the decision is recorded.';
 el('locked-timing').textContent=a?.state==='not_registered'?'First scheduled call: '+clock(report.registry.start_open_ts+450)+' (next eligible market).':a?'Decision time '+clock(a.decision_ts)+' · evidence deadline '+clock(a.deadline_ts)+(a.locked_at?' · Locked '+clock(a.locked_at):''):'';
 el('locked-prices').textContent=a?.quotes?'Paired YES / NO asks: '+money(a.quotes.yes_ask)+' / '+money(a.quotes.no_ask)+' · Historical quotes at lock time.':'';
}
function row(body,values){const tr=document.createElement('tr');for(const v of values){const td=document.createElement('td');td.textContent=v;tr.append(td);}body.append(tr);}
async function refresh(){if(busy)return;busy=true;try{const res=await fetch('/midpoint-report.json',{cache:'no-store',signal:AbortSignal.timeout(4500)});if(!res.ok)throw Error();report=await res.json();failed=false;const m=report.evaluation||{};
 el('locked-counts').textContent=(m.calls??0)+' calls · '+(m.skips??0)+' skips · '+(m.settled_calls??0)+' settled calls · '+(m.correct??0)+' correct / '+(m.wrong??0)+' wrong · Coverage '+pct(m.coverage)+'. Abstentions are not wins.';
 const body=el('locked-metrics');body.replaceChildren();for(const [key,label]of[['model','Locked volatility estimate'],['market','Paired Kalshi midpoint']])row(body,[label,finite(m[key]?.brier)?m[key].brier.toFixed(4):'—',finite(m[key]?.log_loss)?m[key].log_loss.toFixed(4):'—']);
 const ledger=el('locked-ledger');ledger.replaceChildren();for(const d of [...(report.ledger||[])].reverse().slice(0,40))row(ledger,[clock(d.close_ts),d.direction+' / '+d.reason,pct(d.p_yes),clock(d.locked_at),d.result||(d.direction==='SKIP'?'No call':'Pending')]);if(!report.ledger?.length)row(ledger,['—','Waiting for the first decision','—','—','—']);
 }catch{failed=true;}finally{busy=false;render();}}
render();refresh();setInterval(refresh,5000);setInterval(render,1000);
})(typeof globalThis!=='undefined'?globalThis:this);
