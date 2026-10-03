"""As-of reader: builds the inputs a signal may use at forecast time `t` from quant.sqlite.

NO LOOK-AHEAD: every query filters on recv_ts <= t (local receive time, i.e. when we could
actually have known it), never on the exchange's own timestamp alone.
Snapshot dict:
  t, ticker, strike, close_ts,
  proxy  = [(ts, price)] spot-proxy rows in the last hour,
  ticks  = {exchange: [(recv_ts, price)]} last 2 minutes,
  books  = {exchange: dict(recv_ts, bids, asks)} latest book per exchange,
  kalshi = [dict(recv_ts, yes=[(p,q)], no=[(p,q)])] last ~7 minutes, oldest first.
"""
PROXY_LOOKBACK = 3700.0
TICK_LOOKBACK = 120.0
KALSHI_LOOKBACK = 420.0


def _kalshi(db, ticker, t, lookback):
    out = []
    for sid, ts in db.execute('SELECT id,recv_ts FROM kalshi_snapshots WHERE ticker=? AND recv_ts<=? AND recv_ts>=? ORDER BY recv_ts',
                              (ticker, t, t - lookback)).fetchall():
        sides = {'yes': [], 'no': []}
        for side, p, q in db.execute('SELECT side,price,size FROM kalshi_levels WHERE snapshot_id=? ORDER BY level', (sid,)):
            sides[side].append((p, q))
        out.append(dict(recv_ts=ts, yes=sides['yes'], no=sides['no']))
    return out


def snapshot(db, ticker, t):
    meta = db.execute('SELECT close_ts,strike FROM kalshi_snapshots WHERE ticker=? AND recv_ts<=? ORDER BY recv_ts DESC LIMIT 1',
                      (ticker, t)).fetchone()
    if not meta or not meta[0] or not meta[1]:
        return None
    proxy = [tuple(r) for r in db.execute('SELECT ts,price FROM spot_proxy WHERE ts<=? AND ts>=? AND price IS NOT NULL ORDER BY ts',
                                          (t, t - PROXY_LOOKBACK))]
    ticks = {}
    for ex, ts, p in db.execute('SELECT exchange,recv_ts,price FROM exchange_ticks WHERE recv_ts<=? AND recv_ts>=? ORDER BY recv_ts',
                                (t, t - TICK_LOOKBACK)):
        ticks.setdefault(ex, []).append((ts, p))
    books = {}
    for (ex,) in db.execute('SELECT DISTINCT exchange FROM exchange_books WHERE recv_ts<=? AND recv_ts>=?', (t, t - 60)):
        ts = db.execute('SELECT MAX(recv_ts) FROM exchange_books WHERE exchange=? AND recv_ts<=?', (ex, t)).fetchone()[0]
        sides = {'bid': [], 'ask': []}
        for side, p, s in db.execute('SELECT side,price,size FROM exchange_books WHERE exchange=? AND recv_ts=? ORDER BY level', (ex, ts)):
            sides[side].append((p, s))
        books[ex] = dict(recv_ts=ts, bids=sides['bid'], asks=sides['ask'])
    return dict(t=t, ticker=ticker, close_ts=meta[0], strike=meta[1], proxy=proxy, ticks=ticks, books=books,
                kalshi=_kalshi(db, ticker, t, KALSHI_LOOKBACK))


def first_call_times(db, min_remaining=60.0, max_remaining=900.0):
    """First eligible forecast time per market: the first Kalshi snapshot with 60 < seconds-to-close <= 900.
    One call per market, as required for scoring."""
    out = {}
    for ticker, ts, close in db.execute('SELECT ticker,recv_ts,close_ts FROM kalshi_snapshots ORDER BY recv_ts,id'):
        if ticker not in out and close and min_remaining < close - ts <= max_remaining:
            out[ticker] = ts
    return out
