"""Kalshi order-book snapshots (top 5 levels, both sides) for every open BTC 15-minute market.

Kalshi books list resting BIDS: `yes_dollars` are bids to buy YES, `no_dollars` bids to buy NO.
Implied asks are 1 - best opposite bid; later layers derive them. Level 0 is the best (highest) bid.
Public endpoint, no credentials. `get` is injectable for tests; default is bot.get.
"""
import json, math, time
import urllib.parse

DEPTH = 5
SERIES = 'KXBTC15M'


def top_levels(rows, depth=DEPTH):
    out = []
    for p, q in rows:
        p, q = float(p), float(q)
        if math.isfinite(p) and math.isfinite(q) and 0 < p < 1 and q > 0:
            out.append((p, q))
    out.sort(key=lambda v: v[0], reverse=True)
    return out[:depth]


def parse(book, depth=DEPTH):
    b = book['orderbook_fp']
    yes, no = top_levels(b.get('yes_dollars', []), depth), top_levels(b.get('no_dollars', []), depth)
    if yes and no and yes[0][0] + no[0][0] > 1:
        raise ValueError('crossed book')
    return yes, no


def _ts(s):
    import datetime as dt
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def store(db, ticker, recv, request_s, close_ts, strike, yes, no):
    cur = db.execute('INSERT INTO kalshi_snapshots(ticker,recv_ts,request_s,close_ts,strike) VALUES(?,?,?,?,?)',
                     (ticker, recv, request_s, close_ts, strike))
    for side, rows in (('yes', yes), ('no', no)):
        for level, (p, q) in enumerate(rows):
            db.execute('INSERT INTO kalshi_levels VALUES(?,?,?,?,?)', (cur.lastrowid, side, level, p, q))
    return cur.lastrowid


def snapshot_all(db, get=None, clock=time.time, series=SERIES):
    """Snapshot every open market in the series. Returns (stored_count, errors)."""
    if get is None:
        import bot
        get = bot.get
    stored, errors, cursor = 0, [], ''
    while True:
        data = get('/markets', series_ticker=series, status='open', limit=100, cursor=cursor)
        for m in data['markets']:
            t0 = clock()
            try:
                raw = get('/markets/' + urllib.parse.quote(m['ticker'], safe='') + '/orderbook', depth=10)
                recv = clock()
                yes, no = parse(raw)
                close = _ts(m['close_time']) if m.get('close_time') else None
                strike = float(m['floor_strike']) if m.get('floor_strike') else None
                store(db, m['ticker'], recv, recv - t0, close, strike, yes, no)
                stored += 1
            except Exception as e:
                errors.append((m.get('ticker'), str(e)))
        cursor = data.get('cursor', '')
        if not cursor:
            break
    db.commit()
    return stored, errors
