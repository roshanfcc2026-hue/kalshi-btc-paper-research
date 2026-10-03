"""One collection cycle: poll 4 exchanges, store ticks/books, store spot proxy, snapshot Kalshi.

PAPER/RESEARCH ONLY. Public GET endpoints only; no orders, no credentials.
Usage: python -m quant.collector --cycles 0 --interval 5 --db quant.sqlite
Create a file named STOP in the working directory to stop.
"""
import argparse, os, time
from . import feeds, kalshi_book, proxy, store as qstore


def run_cycle(db, fetch=feeds.fetch_json, clock=time.time, kalshi_get=None, with_kalshi=True, exchanges=feeds.EXCHANGES):
    """Returns dict(proxy=result, errors=[(source,message)], kalshi=count)."""
    errors, obs = [], []
    for ex in exchanges:
        try:
            tick, book = feeds.poll(ex, fetch, clock)
            feeds.store(db, tick, book)
            obs.append(tick)
        except Exception as e:  # one failing exchange must not stop the others
            errors.append((ex, str(e)))
    now = clock()
    result = proxy.compute(obs, now, expected=exchanges)
    proxy.store(db, result)
    count = 0
    if with_kalshi:
        try:
            count, kerrors = kalshi_book.snapshot_all(db, kalshi_get, clock)
            errors += [('kalshi:%s' % t, m) for t, m in kerrors]
        except Exception as e:
            errors.append(('kalshi', str(e)))
    for source, message in errors:
        db.execute('INSERT INTO feed_errors(ts,source,message) VALUES(?,?,?)', (now, source, message[:300]))
    db.commit()
    return dict(proxy=result, errors=errors, kalshi=count)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='quant.sqlite')
    p.add_argument('--cycles', type=int, default=1, help='0 = run until STOP file')
    p.add_argument('--interval', type=float, default=5.0)
    p.add_argument('--no-kalshi', action='store_true')
    a = p.parse_args(argv)
    db = qstore.connect(a.db)
    i = 0
    try:
        while a.cycles == 0 or i < a.cycles:
            if os.path.exists('STOP'):
                print('Stop requested'); break
            r = run_cycle(db, with_kalshi=not a.no_kalshi)
            print(time.strftime('%H:%M:%S'), 'proxy=%s n=%d/%d kalshi=%d errors=%d' % (
                r['proxy']['price'], r['proxy']['n_used'], r['proxy']['n_total'], r['kalshi'], len(r['errors'])), flush=True)
            i += 1
            if a.cycles == 0 or i < a.cycles:
                time.sleep(a.interval)
    finally:
        db.close()


if __name__ == '__main__':
    main()
