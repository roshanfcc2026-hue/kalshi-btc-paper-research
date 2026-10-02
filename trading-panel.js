(function(scope){
  'use strict';
  const finite=n=>typeof n==='number' && Number.isFinite(n);
  const cents=n=>finite(n)?(n*100).toFixed(2)+'¢':'—';
  const signedCents=n=>finite(n)?(n>0?'+':'')+cents(n):'—';
  const percent=n=>finite(n)?(n*100).toFixed(1)+'%':'—';
  const clock=n=>finite(n)?new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(n*1000)):'Unavailable';
  const actionLabel=action=>({BUY_YES:'BUY YES · paper',BUY_NO:'BUY NO · paper',SELL_YES:'SELL / EXIT YES · paper',SELL_NO:'SELL / EXIT NO · paper',HOLD:'HOLD · paper',SKIP:'SKIP'}[action]||'SKIP');
  const reasonText=reason=>({
    weak_direction:'The estimate is too close to 50% for a new paper entry.',
    insufficient_buy_edge:'Neither ask offers more than 3¢ expected edge after estimated fees.',
    net_buy_edge_exceeds_margin:'The selected ask offers more than 3¢ expected edge after estimated fees.',
    net_sale_exceeds_expected_settlement:'Net sale proceeds exceed the model’s expected settlement value by more than 3¢.',
    sale_has_no_margin_over_hold:'Selling at this bid does not improve on the model’s expected settlement value by more than 3¢.',
    missing_active_contract:'Waiting for this cycle’s contract to be recorded.',
    ambiguous_active_contract:'The current contract could not be identified reliably.',
    missing_forecast:'Waiting for this market’s forecast.',
    stale_forecast:'The forecast is older than 45 seconds.',
    stale_current_quote:'Recorded quote is older than 3 seconds. Wait for a new book snapshot.',
    final_averaging_minute:'Final averaging minute: buy/sell guidance is unsupported.',
    cycle_ended:'This market has ended. Waiting for the next market.',
    contract_not_open:'This contract is no longer open.',
    insufficient_one_contract_depth:'The paired book does not show enough depth for the one-contract scenario.',
    fee_waiver_requires_review:'This market’s fee waiver needs review before using the paper fee scenario.',
    different_fee_scenario:'This market’s fees differ from the paper scenario.',
    missing_or_ambiguous_exact_snapshot:'The original forecast could not be paired with exactly one recorded order book.',
    detail_quote_mismatch:'The forecast and recorded quotes do not match.',
    detail_forecast_mismatch:'The saved forecast details do not match.',
    raw_book_quote_or_depth_mismatch:'Recorded prices or depth do not match the saved order book.',
    future_or_invalid_forecast:'The forecast has invalid or future evidence.',
    malformed_forecast_or_book:'Forecast or order-book data failed validation.'
  }[reason] || (typeof reason==='string' && reason.includes('_')?'Recorded evidence failed validation.':reason));

  function panelState(report,position,now){
    const result={action:'SKIP',reason:'Waiting for paired forecast and order book.',recorded:null,valid:false};
    if (!finite(now) || !report || report.version!=='dashboard-paper-signals-v1' || !['none','yes','no'].includes(position)) return result;
    if (!finite(report.generated_at) || report.generated_at>now || now-report.generated_at>45) {
      result.reason='The paper report is stale or has an invalid timestamp.';return result;
    }
    if (!finite(report.open_ts) || !finite(report.close_ts) || now<report.open_ts || now>=report.close_ts) {
      result.reason='Waiting for a forecast for the active market.';return result;
    }
    if (report.close_ts-now<=60) {
      result.reason='Final averaging minute: buy/sell guidance is unsupported.';return result;
    }
    if (!report.snapshot_valid || !finite(report.observed) || !finite(report.completed) || report.completed<report.observed ||
        report.completed>now || report.observed<report.open_ts || now-report.observed>45) {
      result.reason=report.reason || 'Forecast evidence is unavailable or older than 45 seconds.';return result;
    }
    const scenario=report.snapshot_scenarios?.[position];
    if (!scenario || !['BUY_YES','BUY_NO','SELL_YES','SELL_NO','HOLD','SKIP'].includes(scenario.action)) return result;
    if ((position==='none' && ['SELL_YES','SELL_NO','HOLD'].includes(scenario.action)) ||
        (position==='yes' && !['SELL_YES','HOLD','SKIP'].includes(scenario.action)) ||
        (position==='no' && !['SELL_NO','HOLD','SKIP'].includes(scenario.action))) return result;
    result.recorded=scenario;result.valid=true;
    if (!finite(report.quote_received) || report.quote_received>report.observed || now-report.quote_received>3 || !report.current_quote_valid) {
      result.reason='Recorded quote is older than 3 seconds. Wait for a new book snapshot.';return result;
    }
    result.action=scenario.action;result.reason=scenario.reason;return result;
  }
  function entryState(report,now){
    const view=panelState(report,'none',now),out={action:'SKIP',label:'NO EDGE',reason:view.reason};
    if(!['BUY_YES','BUY_NO'].includes(view.action))return out;
    const p=report.forecasts?.p_yes,q=report.quotes,margin=report.policy?.margin;
    if(!finite(p)||p<0||p>1||!finite(margin)||margin<0)return out;
    const side=view.action==='BUY_YES'?'yes':'no',ask=q?.[side+'_ask'],fee=report.values?.[side]?.buy_fee;
    if(!finite(ask)||ask<=0||ask>=1||!finite(fee)||fee<0)return out;
    const ev=(side==='yes'?p:1-p)-ask-fee;
    if(!(ev>margin+1e-12))return out;
    return {action:view.action,label:actionLabel(view.action),reason:view.reason,ev};
  }
  function predictionState(report,now){
    const result={active:false,first:null,latest:null,changed:false};
    if(!finite(now) || !report || report.version!=='dashboard-paper-signals-v1' ||
        !finite(report.open_ts) || !finite(report.close_ts) || now<report.open_ts || now>=report.close_ts)return result;
    result.active=true;
    const first=report.first_call;
    if(first && finite(first.p_yes) && first.p_yes>=0 && first.p_yes<=1 && finite(first.observed) &&
        first.observed>=report.open_ts && first.observed<=now && first.close_ts===report.close_ts){
      result.first={side:first.p_yes>=.5?'YES':'NO',p_yes:first.p_yes,observed:first.observed};
    }
    const view=panelState(report,'none',now),p=report.forecasts?.p_yes;
    if(view.valid && finite(p) && p>=0 && p<=1){
      result.latest={side:p>=.5?'YES':'NO',p_yes:p,observed:report.observed};
    }
    result.changed=!!(result.first && result.latest && result.first.side!==result.latest.side);
    return result;
  }
  if (typeof module!=='undefined' && module.exports) module.exports={panelState,predictionState,entryState,actionLabel,cents};
  if (!scope.document) return;
  const el=id=>scope.document.getElementById(id);
  let report=null, unavailable=false, inFlight=false, lastTicker=null;
  scope.paperEntryFor=(ticker,now)=>!unavailable && report?.ticker===ticker?entryState(report,now):{action:'SKIP',label:'NO EDGE'};
  function render(){
    const now=Date.now()/1000, position=el('paper-position').value;
    const view=panelState(unavailable?null:report,position,now);
    el('paper-action').textContent=actionLabel(view.action);
    el('paper-action').dataset.action=view.action;
    el('paper-reason').textContent=unavailable?'Local paper report unavailable. Retrying every 5 seconds.':reasonText(view.reason);
    el('paper-recorded-signal').textContent=view.recorded?
      'At the recorded quote ('+clock(report.quote_received)+'): '+actionLabel(view.recorded.action)+'. '+reasonText(view.recorded.reason)+' Snapshot analysis only; no simulated fill.':
      'No eligible snapshot analysis for this market.';
    el('paper-contract').textContent=report?.ticker || 'Waiting for active contract';
    el('paper-quote-time').textContent=view.valid?'Forecast '+clock(report.observed)+' · quote '+clock(report.quote_received)+' · '+Math.max(0,Math.floor(now-report.quote_received))+'s old':'No current paired quote';
    const prediction=predictionState(report,now),first=prediction.first,latest=unavailable?null:prediction.latest;
    el('paper-first-call').textContent=first?(first.side==='YES'?'UP / YES':'DOWN / NO'):'Not yet recorded';
    el('paper-first-call').dataset.side=first?.side || '';
    el('paper-first-detail').textContent=first?'YES '+percent(first.p_yes)+' / NO '+percent(1-first.p_yes)+' · First recorded '+clock(first.observed):'Waiting for this market’s original prediction.';
    el('paper-prediction-window').textContent=prediction.active?clock(report.open_ts)+' → '+clock(report.close_ts):'Waiting for the active market';
    el('paper-official-outcome').textContent=prediction.active?'Official outcome: PENDING — known only after Kalshi settles.':'Official outcome: unavailable for the active market.';
    el('paper-latest-call').textContent=latest?(latest.side==='YES'?'UP / YES':'DOWN / NO'):'Unavailable';
    el('paper-latest-call').dataset.side=latest?.side || '';
    el('paper-live-detail').textContent=latest?'YES '+percent(latest.p_yes)+' / NO '+percent(1-latest.p_yes)+' · Updated '+clock(latest.observed)+' · '+Math.max(0,Math.floor(now-latest.observed))+'s ago':'No fresh eligible forecast. Waiting for data outside the final averaging minute.';
    el('paper-change-note').textContent=latest && prediction.changed?
      'DIRECTIONS DIFFER: the live estimate changed. The original call remains fixed for scoring.':
      latest && first?'Live direction currently agrees with the original call. It can change as prices move.':
      latest?'The original call is unavailable. This is only the latest changing estimate.':'Live evidence is unavailable, stale, or in the final averaging minute. The original call is a saved forecast, not a fresh signal.';
    for (const side of ['yes','no']){
      const v=view.valid?report.values?.[side]:null,q=view.valid?report.quotes:null;
      el('paper-'+side+'-value').textContent=cents(v?.probability);
      el('paper-'+side+'-ask').textContent=cents(q?.[side+'_ask']);
      el('paper-'+side+'-bid').textContent=cents(q?.[side+'_bid']);
      el('paper-'+side+'-buy-ev').textContent=signedCents(v?.buy_ev);
      el('paper-'+side+'-sell-ev').textContent=signedCents(v?.sell_edge);
    }
  }
  async function refresh(){
    if(inFlight)return;inFlight=true;
    try{
      const response=await fetch('/paper-signals.json',{cache:'no-store',signal:AbortSignal.timeout(4500)});
      if(!response.ok)throw Error('Paper report unavailable');
      const value=await response.json();
      if(value.version!=='dashboard-paper-signals-v1' || value.live_signal!=='SKIP')throw Error('Unexpected policy');
      if(value.ticker!==lastTicker){el('paper-position').value='none';lastTicker=value.ticker;}
      report=value;unavailable=false;
    }catch(error){unavailable=true;}
    finally{inFlight=false;render();}
  }
  el('paper-position').addEventListener('change',render);
  render();refresh();scope.setInterval(refresh,5000);scope.setInterval(render,1000);
})(typeof globalThis!=='undefined'?globalThis:this);
