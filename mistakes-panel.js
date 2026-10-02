(function (scope) {
  'use strict';
  const numeric = n => typeof n === 'number' && Number.isFinite(n);
  const money = n => numeric(n) ? new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(n) : 'Unavailable';
  const percent = n => numeric(n) && n >= 0 && n <= 1 ? (100*n).toFixed(1)+'%' : 'Unavailable';
  const localTime = n => numeric(n) ? new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles', hour:'numeric', minute:'2-digit', second:'2-digit', timeZoneName:'short'}).format(new Date(n*1000)) : 'Unavailable';
  const localDate = n => numeric(n) ? new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles', month:'short', day:'numeric', year:'numeric'}).format(new Date(n*1000)) : 'Unavailable';
  const direction = side => side === 'yes' ? 'UP / YES' : side === 'no' ? 'DOWN / NO' : 'Unavailable';

  function formatMistake(m) {
    return {
      number: '#'+m.market_number,
      contract: m.ticker,
      date: localDate(m.close_ts),
      window: localTime(m.close_ts-900)+' – '+localTime(m.close_ts),
      prediction: direction(m.predicted),
      probabilities: 'YES '+percent(m.p_yes)+' · NO '+percent(m.p_no),
      observed: 'First call '+localTime(m.observed),
      forecastId: 'Forecast ID '+m.forecast_id,
      outcome: direction(m.actual),
      target: money(m.target),
      settlement: money(m.official_settlement),
      gap: numeric(m.gap) ? (m.gap > 0 ? '+' : '')+money(m.gap) : 'Unavailable'
    };
  }

  function currentMarketNumber(report, current, now) {
    if (!report || !numeric(report.generated_at) || report.generated_at > now || now-report.generated_at > 45 ||
        !current || !current.confirmed || !numeric(current.close) ||
        current.close !== Math.floor(now/900)*900+900 || !Array.isArray(report.market_numbers)) return null;
    const match=report.market_numbers.find(m=>m.ticker===current.ticker && m.close_ts===current.close &&
      Number.isInteger(m.market_number) && m.market_number>0 && numeric(m.observed) && m.observed<=now);
    return match ? match.market_number : null;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {formatMistake,currentMarketNumber};
  if (!scope.document) return;
  const byId = id => scope.document.getElementById(id);
  let report = null, shown = 15, inFlight = false;
  scope.renderMarketNumber = function (current, now) {
    const number=currentMarketNumber(report,current,now);
    const target=byId('live-market-number');
    if (target) target.textContent=number===null ? 'Market number pending' :
      'Market #'+number+' · '+report.summary.settled+' settled · '+report.summary.correct+' correct · '+report.summary.wrong+' wrong';
  };
  function addCell(tr, main, secondary, extra) {
    const td=scope.document.createElement('td'), text=scope.document.createElement('div');
    text.textContent=main; td.append(text);
    for (const line of [secondary, extra]) if (line) {
      const small=scope.document.createElement('div'); small.className='muted mistake-detail'; small.textContent=line; td.append(small);
    }
    tr.append(td); return td;
  }
  function render() {
    const body=byId('wrong-prediction-rows'); body.replaceChildren();
    const mistakes=report.mistakes;
    byId('wrong-total').textContent=report.summary.wrong;
    byId('wrong-scored').textContent='of '+report.summary.settled+' settled first calls';
    const numberList=byId('wrong-market-numbers');
    numberList.replaceChildren();
    for (const number of mistakes.map(m=>m.market_number).sort((a,b)=>a-b)) {
      const chip=scope.document.createElement('span');chip.className='wrong-number-chip';chip.textContent='#'+number;numberList.append(chip);
    }
    if (!mistakes.length) numberList.textContent='None';
    byId('wrong-number-context').textContent='These '+report.summary.wrong+' market numbers were wrong out of '+report.summary.settled+' settled first calls.';
    const latest=mistakes[0];
    byId('wrong-latest').textContent=latest ? localDate(latest.close_ts)+' · '+localTime(latest.close_ts) : 'No recorded misses';
    byId('wrong-latest-detail').textContent=latest ? 'Market #'+latest.market_number+' · '+direction(latest.predicted)+' forecast → '+direction(latest.actual)+' result' : 'Only officially settled first calls are scored.';
    const excluded=report.summary.excluded_invalid_first || 0;
    byId('wrong-history-status').textContent='Read '+localTime(report.generated_at)+' · '+report.summary.pending+' awaiting official outcome'+(excluded ? ' · '+excluded+' invalid first calls excluded' : '')+'.';
    byId('wrong-history-status').dataset.state='ready';
    for (const m of mistakes.slice(0,shown)) {
      const view=formatMistake(m), tr=scope.document.createElement('tr');
      const market=addCell(tr,view.number,view.forecastId); market.title=view.contract;
      addCell(tr,view.date,view.window);
      addCell(tr,view.prediction,view.probabilities,view.observed);
      addCell(tr,view.outcome).className='wrong-result';
      addCell(tr,view.target); addCell(tr,view.settlement); addCell(tr,view.gap);
      const labels=['Market #','Market window','First prediction','Official result','Target','Settlement','Difference'];
      Array.from(tr.children).forEach((td,index)=>{td.dataset.label=labels[index];});
      body.append(tr);
    }
    byId('wrong-empty').hidden=mistakes.length > 0;
    byId('wrong-empty').textContent='No wrong first-call predictions among the settled records.';
    byId('wrong-show-more').hidden=shown >= mistakes.length;
    byId('wrong-show-more').textContent='Show older misses ('+Math.max(0,mistakes.length-shown)+' remaining)';
  }
  async function refresh() {
    if (inFlight) return;
    inFlight=true;
    try {
      const response=await fetch('/mistakes.json',{cache:'no-store',signal:AbortSignal.timeout(4500)});
      if (!response.ok) throw new Error('History unavailable');
      const value=await response.json();
      if (value.source !== 'volatility-proxy-v1' || !value.summary || !Array.isArray(value.mistakes) || !numeric(value.generated_at)) throw new Error('Malformed history');
      report=value; render();
    } catch (error) {
      byId('wrong-history-status').textContent='History unavailable. '+(report ? 'Showing the last successful read; retrying.' : 'Retrying every 5 seconds.');
      byId('wrong-history-status').dataset.state='unavailable';
      if (!report) { byId('wrong-empty').hidden=false; byId('wrong-empty').textContent='Waiting for the recorded history.'; }
    } finally { inFlight=false; }
  }
  byId('wrong-show-more').addEventListener('click',()=>{shown+=15;if(report)render();});
  refresh(); scope.setInterval(refresh,5000);
})(typeof globalThis !== 'undefined' ? globalThis : this);
