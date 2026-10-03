"""Layer 6 paper execution. PAPER ONLY: no order is ever sent anywhere.

TAKER: at forecast time t, use the latest recorded Kalshi book (must be <= max_book_age old),
walk the recorded asks with the Layer 4 fill model, sized by Layer 5. Every decision, including
skips, is logged in paper_decisions.

PASSIVE (RESEARCH ONLY, reported separately): post a buy at best bid + 1c on the side the model
favors (buying NO at no-bid+1c is the same as offering YES at ask-1c). Counted as filled only if a
LATER recorded quote crosses the price (the opposite side's ask <= our limit) before close.
Kalshi trade prints are not recorded, and QUEUE POSITION IS UNKNOWN: a quote touching our price
does not mean we were filled, and we would be filled most often exactly when price moves against
us (adverse selection is partly but not fully captured). Results are OPTIMISTIC.
"""
from . import asof, costs, risk

MAX_BOOK_AGE = 5.0
SCHEMA = '''
CREATE TABLE IF NOT EXISTS paper_decisions(id INTEGER PRIMARY KEY, ts REAL NOT NULL, ticker TEXT NOT NULL, source TEXT NOT NULL,
  p_yes REAL, action TEXT NOT NULL, reason TEXT, contracts REAL, avg_price REAL, fee REAL, book_age REAL);
CREATE TABLE IF NOT EXISTS passive_orders(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, source TEXT NOT NULL, side TEXT NOT NULL,
  posted REAL NOT NULL, limit_price REAL NOT NULL, contracts REAL NOT NULL, filled REAL, fill_ts REAL, result TEXT, pnl REAL,
  UNIQUE(ticker, source));
'''


def setup(db):
    risk.setup(db); db.executescript(SCHEMA)


def _book(db, ticker, t):
    k = asof._kalshi(db, ticker, t, MAX_BOOK_AGE)
    return k[-1] if k else None


def taker(db, ticker, source, p_yes, t, cfg=risk.DEFAULTS):
    book = _book(db, ticker, t)
    if book is None:
        d = dict(action='skip', reason='no Kalshi book within %.0fs' % MAX_BOOK_AGE)
    else:
        d = risk.decide(db, ticker, source, p_yes, book['yes'], book['no'], t, cfg)
    f = d.get('fill') or {}
    db.execute('INSERT INTO paper_decisions(ts,ticker,source,p_yes,action,reason,contracts,avg_price,fee,book_age) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (t, ticker, source, p_yes, d['action'], d.get('reason'), f.get('filled'), f.get('avg_price'), f.get('fee'),
                t - book['recv_ts'] if book else None))
    db.commit()
    return d


def post_passive(db, ticker, source, p_yes, t, contracts=1, margin=risk.DEFAULTS['margin']):
    book = _book(db, ticker, t)
    if not book or not book['yes'] or not book['no']:
        return None
    best = None
    for side, p, bid, opp_bid in (('yes', p_yes, book['yes'][0][0], book['no'][0][0]), ('no', 1 - p_yes, book['no'][0][0], book['yes'][0][0])):
        limit = round(bid + 0.01, 2)
        if limit >= round(1 - opp_bid, 2):  # would cross the spread: not passive
            continue
        ev = p - limit - costs.MAKER_RATE * limit * (1 - limit)
        if ev > margin and (best is None or ev > best[2]):
            best = (side, limit, ev)
    if best is None:
        return None
    db.execute('INSERT OR IGNORE INTO passive_orders(ticker,source,side,posted,limit_price,contracts) VALUES(?,?,?,?,?,?)',
               (ticker, source, best[0], t, best[1], contracts))
    db.commit()
    return dict(side=best[0], limit=best[1], ev=best[2])


def resolve_passive(db, ticker, result, close_ts):
    """After close: mark fills from later quotes, then P&L with the maker fee. Unfilled orders have pnl 0."""
    for oid, side, posted, limit, n in db.execute('SELECT id,side,posted,limit_price,contracts FROM passive_orders WHERE ticker=? AND result IS NULL', (ticker,)).fetchall():
        fill_ts = None
        for ts, opp_best in db.execute('''SELECT s.recv_ts, MAX(l.price) FROM kalshi_snapshots s JOIN kalshi_levels l ON l.snapshot_id=s.id
                                          WHERE s.ticker=? AND s.recv_ts>? AND s.recv_ts<? AND l.side=? GROUP BY s.id ORDER BY s.recv_ts''',
                                       (ticker, posted, close_ts, 'no' if side == 'yes' else 'yes')):
            if round(1 - opp_best, 4) <= limit:  # opposite ask reached our limit: crossed
                fill_ts = ts; break
        if fill_ts is None:
            db.execute('UPDATE passive_orders SET filled=0,result=?,pnl=0 WHERE id=?', (result, oid))
        else:
            fee = costs.fee([(limit, n)], costs.MAKER_RATE)
            pnl = (n if side == result else 0) - limit * n - fee
            db.execute('UPDATE passive_orders SET filled=?,fill_ts=?,result=?,pnl=? WHERE id=?', (n, fill_ts, result, pnl, oid))
    db.commit()


def report(db):
    t = db.execute("SELECT COUNT(*),SUM(action!='skip') FROM paper_decisions").fetchone()
    tp = db.execute('SELECT COUNT(*),COALESCE(SUM(pnl),0) FROM paper_positions WHERE result IS NOT NULL').fetchone()
    reasons = dict(db.execute("SELECT reason,COUNT(*) FROM paper_decisions WHERE action='skip' GROUP BY reason").fetchall())
    p = db.execute('SELECT COUNT(*),COALESCE(SUM(filled>0),0),COALESCE(SUM(pnl),0) FROM passive_orders WHERE result IS NOT NULL').fetchone()
    return dict(taker=dict(decisions=t[0], trades=t[1] or 0, skips=t[0] - (t[1] or 0), skip_reasons=reasons, settled=tp[0], pnl=tp[1]),
                passive_research=dict(resolved=p[0], filled=p[1], fill_rate=p[1] / p[0] if p[0] else None, pnl=p[2],
                                      warning='OPTIMISTIC: queue position unknown; quote-cross fills only; not comparable to taker results'))
