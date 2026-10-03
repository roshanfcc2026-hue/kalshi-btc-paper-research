"""Spot proxy = median across exchanges, after excluding stale/future/outlier prices.

Look-ahead safe: only observations with recv_ts <= `now` are used. Inputs are classified:
  ok | stale | future | outlier | invalid
Stale = no update in `stale_s` seconds (age measured from the exchange timestamp when
present, otherwise from local receive time; the local receive age is checked too).
Outlier = more than `outlier_frac` (0.3%) from the median of fresh prices; the median is
then recomputed without outliers. With only 2 fresh sources a disagreement under 0.6%
cannot be attributed to either, so neither is flagged; above 0.6% both are, and the proxy
is withheld (n_used < min_sources).
"""
import math, statistics

STALE_S = 10.0
OUTLIER_FRAC = 0.003
MAX_FUTURE_S = 2.0
MIN_SOURCES = 2


def latest_per_exchange(obs, now):
    """Latest observation per exchange received at or before `now`."""
    best = {}
    for o in obs:
        if o['recv_ts'] <= now and (o['exchange'] not in best or o['recv_ts'] > best[o['exchange']]['recv_ts']):
            best[o['exchange']] = o
    return best


def compute(obs, now, expected=(), stale_s=STALE_S, outlier_frac=OUTLIER_FRAC, min_sources=MIN_SOURCES):
    latest = latest_per_exchange(obs, now)
    inputs = []
    for ex in sorted(set(latest) | set(expected)):
        o = latest.get(ex)
        if o is None:
            inputs.append(dict(exchange=ex, price=None, age_s=None, status='missing')); continue
        p, ref = o.get('price'), (o['exch_ts'] if o.get('exch_ts') is not None else o['recv_ts'])
        age = now - ref
        if type(p) not in (int, float) or not math.isfinite(p) or p <= 0:
            status = 'invalid'
        elif o.get('exch_ts') is not None and o['exch_ts'] > now + MAX_FUTURE_S:
            status = 'future'
        elif age > stale_s or now - o['recv_ts'] > stale_s:
            status = 'stale'
        else:
            status = 'ok'
        inputs.append(dict(exchange=ex, price=p if status != 'invalid' else None, age_s=age, status=status))
    fresh = [i for i in inputs if i['status'] == 'ok']
    price = None
    if fresh:
        med = statistics.median(i['price'] for i in fresh)
        for i in fresh:
            if abs(i['price'] - med) / med > outlier_frac:
                i['status'] = 'outlier'
        fresh = [i for i in fresh if i['status'] == 'ok']
        if len(fresh) >= min_sources:
            price = statistics.median(i['price'] for i in fresh)
    return dict(ts=now, price=price, n_used=len(fresh), n_total=len(inputs),
                status='ok' if price is not None else 'insufficient', inputs=inputs)


def store(db, result):
    db.execute('INSERT INTO spot_proxy(ts,price,n_used,n_total,status) VALUES(?,?,?,?,?)',
               (result['ts'], result['price'], result['n_used'], result['n_total'], result['status']))
    for i in result['inputs']:
        db.execute('INSERT INTO proxy_inputs(ts,exchange,price,age_s,status) VALUES(?,?,?,?,?)',
                   (result['ts'], i['exchange'], i['price'], i['age_s'], i['status']))
