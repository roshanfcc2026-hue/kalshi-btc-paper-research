"""Paper runner: once per market at its FIRST eligible call, record forecasts for each source,
make the paper taker decision and the passive research order; settle with OFFICIAL results read
read-only from research.sqlite. PAPER ONLY.

Sources recorded: market-mid (benchmark), s1 probability, and combo-v1 if a frozen combo exists
(verified against code hashes). Trading uses combo-v1 only; with no frozen combo nothing trades.
"""
import json
from pathlib import Path
from . import asof, combo, execution, risk
from .signals import ALL
from .signals.common import market_mid

TRADE_SOURCE = 'combo-v1'


def load_combo(path=combo.DEFAULT_OUT):
    return combo.verify_model(path) if Path(path).exists() else None


def forecast_market(db, ticker, t, model):
    snap = asof.snapshot(db, ticker, t)
    pm = market_mid(snap['kalshi'][-1]) if snap and snap['kalshi'] else None
    if pm is None or not 0 < pm < 1: return {}
    feats = {}
    for s in ALL:
        v = s.compute(snap) or {}
        feats.update({f: v.get(f) for f in s.FEATURES})
    out = {'market-mid': pm}
    if feats.get('s1_p') is not None: out['s1-' + ALL[0].VERSION] = feats['s1_p']
    if model and all(feats.get(f) is not None for f in model['features']):
        out[TRADE_SOURCE] = combo.predict(model, pm, feats)
    for src, p in out.items():
        db.execute('INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?,?)', (ticker, src, t, p, pm))
    db.commit()
    return out


def step(db, now, outcomes, model):
    """Forecast markets reaching their first call; settle markets with official results."""
    done = {r[0] for r in db.execute('SELECT DISTINCT ticker FROM forecasts')}
    for ticker, t in asof.first_call_times(db).items():
        if ticker in done or t > now: continue
        if now - t > 30: continue  # first call missed (collector was down): never backfill late
        fc = forecast_market(db, ticker, t, model)
        if TRADE_SOURCE in fc:
            execution.taker(db, ticker, TRADE_SOURCE, fc[TRADE_SOURCE], t)
            execution.post_passive(db, ticker, TRADE_SOURCE, fc[TRADE_SOURCE], t)
    for ticker, result in outcomes.items():
        if db.execute('SELECT 1 FROM market_results WHERE ticker=?', (ticker,)).fetchone(): continue
        close = db.execute('SELECT MAX(close_ts) FROM kalshi_snapshots WHERE ticker=?', (ticker,)).fetchone()[0]
        if close is None or close > now: continue
        db.execute('INSERT INTO market_results VALUES(?,?,?)', (ticker, result, now))
        risk.settle(db, ticker, result, now)
        execution.resolve_passive(db, ticker, result, close)
    db.commit()
