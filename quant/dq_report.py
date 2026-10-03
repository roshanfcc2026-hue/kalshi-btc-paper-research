"""Data-quality report: gaps, stale feeds, outliers per exchange per UTC day."""
import datetime as dt, json, sys
from . import store as qstore

GAP_S = 30.0


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def report(db, gap_s=GAP_S):
    out = {}

    def cell(day, ex):
        return out.setdefault(day, {}).setdefault(ex, dict(ticks=0, gaps=0, max_gap_s=0.0, missing_s=0.0,
                                                          stale=0, outlier=0, future=0, invalid=0, missing=0, errors=0))
    last = {}
    for ex, ts in db.execute('SELECT exchange,recv_ts FROM exchange_ticks ORDER BY exchange,recv_ts'):
        c = cell(_day(ts), ex); c['ticks'] += 1
        if ex in last:
            g = ts - last[ex]
            if g > gap_s:
                c['gaps'] += 1; c['missing_s'] += g
            c['max_gap_s'] = max(c['max_gap_s'], g)
        last[ex] = ts
    for ex, ts, status in db.execute('SELECT exchange,ts,status FROM proxy_inputs'):
        if status != 'ok':
            cell(_day(ts), ex)[status] += 1
    for src, ts in db.execute('SELECT source,ts FROM feed_errors'):
        cell(_day(ts), src.split(':')[0])['errors'] += 1
    days = {}
    for ts, status in db.execute('SELECT ts,status FROM spot_proxy'):
        d = days.setdefault(_day(ts), dict(proxy_rows=0, proxy_insufficient=0, kalshi_snapshots=0))
        d['proxy_rows'] += 1; d['proxy_insufficient'] += status != 'ok'
    for (ts,) in db.execute('SELECT recv_ts FROM kalshi_snapshots'):
        days.setdefault(_day(ts), dict(proxy_rows=0, proxy_insufficient=0, kalshi_snapshots=0))['kalshi_snapshots'] += 1
    return dict(gap_threshold_s=gap_s, exchanges=out, days=days)


if __name__ == '__main__':
    db = qstore.connect(sys.argv[1] if len(sys.argv) > 1 else 'quant.sqlite')
    print(json.dumps(report(db), indent=2, sort_keys=True))
