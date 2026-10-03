"""Layer 7 monitoring: heartbeat, kill switch, daily scorecard (7:00 AM Pacific). PAPER ONLY.

Heartbeat: each component beats every ~30s; a component silent > 120s raises an alert
  (alerts table + console + monitor-alerts.log). Kill switch: halts paper trading (Layer 5 halt,
  manual reset required) on a risk-limit breach, a data-feed failure (no valid spot proxy or no
  Kalshi book for > FEED_FAIL_S), or more than 3 consecutive errors.
Pacific time uses the US DST rule computed in pure Python (Windows stdlib has no tz database).
"""
import datetime as dt, json, math, time
from pathlib import Path
from . import risk, execution, dq_report

HEARTBEAT_S, ALERT_AFTER_S, MAX_CONSECUTIVE_ERRORS, FEED_FAIL_S = 30.0, 120.0, 3, 60.0
ALERT_LOG = Path('monitor-alerts.log')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS heartbeats(component TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS error_streak(id INTEGER PRIMARY KEY CHECK(id=1), n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS forecasts(ticker TEXT NOT NULL, source TEXT NOT NULL, t REAL NOT NULL, p_yes REAL NOT NULL,
  p_market REAL NOT NULL, PRIMARY KEY(ticker, source));
CREATE TABLE IF NOT EXISTS market_results(ticker TEXT PRIMARY KEY, result TEXT NOT NULL, settled REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scorecards(day TEXT PRIMARY KEY, created REAL NOT NULL, body TEXT NOT NULL);
'''


def setup(db):
    execution.setup(db); db.executescript(SCHEMA)
    db.execute('INSERT OR IGNORE INTO error_streak VALUES(1,0)')


def alert(db, kind, detail, now):
    db.execute('INSERT INTO alerts(ts,kind,detail) VALUES(?,?,?)', (now, kind, detail)); db.commit()
    line = '%s ALERT %s: %s' % (dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), kind, detail)
    print(line, flush=True)
    try:
        with open(ALERT_LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except OSError:
        pass


# ---- heartbeat --------------------------------------------------------------------------
def beat(db, component, now):
    db.execute('INSERT INTO heartbeats VALUES(?,?) ON CONFLICT(component) DO UPDATE SET ts=excluded.ts', (component, now)); db.commit()


def check_heartbeats(db, now, components=('collector',)):
    missed = []
    for c in components:
        r = db.execute('SELECT ts FROM heartbeats WHERE component=?', (c,)).fetchone()
        if r is None or now - r[0] > ALERT_AFTER_S:
            missed.append(c); alert(db, 'heartbeat', '%s silent for %s' % (c, 'ever' if r is None else '%.0fs' % (now - r[0])), now)
    return missed


# ---- kill switch ------------------------------------------------------------------------
def record_cycle(db, ok, now, detail=''):
    n = 0 if ok else db.execute('SELECT n FROM error_streak WHERE id=1').fetchone()[0] + 1
    db.execute('UPDATE error_streak SET n=? WHERE id=1', (n,)); db.commit()
    if n > MAX_CONSECUTIVE_ERRORS:
        kill(db, 'more than %d consecutive errors: %s' % (MAX_CONSECUTIVE_ERRORS, detail), now)
    return n


def feed_ok(db, now):
    proxy = db.execute("SELECT MAX(ts) FROM spot_proxy WHERE status='ok' AND ts<=?", (now,)).fetchone()[0]
    book = db.execute('SELECT MAX(recv_ts) FROM kalshi_snapshots WHERE recv_ts<=?', (now,)).fetchone()[0]
    problems = []
    if proxy is None or now - proxy > FEED_FAIL_S: problems.append('no valid spot proxy for > %.0fs' % FEED_FAIL_S)
    if book is None or now - book > FEED_FAIL_S: problems.append('no Kalshi book for > %.0fs' % FEED_FAIL_S)
    return problems


def kill(db, reason, now):
    if not risk.halted(db)[0]:
        alert(db, 'kill_switch', reason, now)
    risk.halt(db, reason, now)


def after_cycle(db, cycle_result, now, cfg=risk.DEFAULTS, started=None):
    """Call once per collector cycle. Returns the halt reason if halted."""
    beat(db, 'collector', now)
    hard = [e for e in cycle_result.get('errors', []) if not e[0].startswith('kalshi:')]  # one market's book failing is not fatal
    record_cycle(db, len(hard) < 4, now, '; '.join('%s: %s' % e for e in hard)[:200])
    problems = [] if started is not None and now - started < FEED_FAIL_S else feed_ok(db, now)  # startup grace
    if problems: kill(db, 'data-feed failure: ' + '; '.join(problems), now)
    risk.check_limits(db, cfg, now)  # loss-limit breach halts inside Layer 5
    h = risk.halted(db)
    return h[1] if h[0] else None


# ---- Pacific time and the 7:00 AM scorecard ---------------------------------------------
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1); first = d + dt.timedelta(days=(6 - d.weekday()) % 7)
    return first + dt.timedelta(weeks=n - 1)


def pacific_offset_hours(ts):
    """-7 during US daylight time (2nd Sun Mar 2:00 local -> 1st Sun Nov 2:00 local), else -8."""
    year = dt.datetime.fromtimestamp(ts, dt.timezone.utc).year
    start = dt.datetime.combine(_nth_sunday(year, 3, 2), dt.time(10), dt.timezone.utc).timestamp()  # 2:00 PST = 10:00 UTC
    end = dt.datetime.combine(_nth_sunday(year, 11, 1), dt.time(9), dt.timezone.utc).timestamp()    # 2:00 PDT = 09:00 UTC
    return -7 if start <= ts < end else -8


def pacific(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=pacific_offset_hours(ts))))


def scorecard_due(db, now):
    """Due once per Pacific day, at or after 7:00 AM Pacific. Returns the Pacific date string or None."""
    local = pacific(now)
    if local.hour < 7: return None
    day = local.date().isoformat()
    return None if db.execute('SELECT 1 FROM scorecards WHERE day=?', (day,)).fetchone() else day


def _ll(p, y):
    p = min(1 - 1e-12, max(1e-12, p)); return -math.log(p if y else 1 - p)


def scorecard(db, now, cfg=risk.DEFAULTS):
    """Covers the previous 24 hours. First calls only (forecasts has one row per market per source)."""
    since = now - 86400; out = dict(generated=now, pacific=pacific(now).isoformat(), window_hours=24, sources={})
    rows = db.execute('''SELECT f.source,f.p_yes,f.p_market,r.result FROM forecasts f JOIN market_results r USING(ticker)
                         WHERE r.settled>=? AND r.settled<=?''', (since, now)).fetchall()
    for src in sorted({r[0] for r in rows}):
        g = [(p, pm, int(res == 'yes')) for s, p, pm, res in rows if s == src]
        n = len(g)
        out['sources'][src] = dict(markets=n, log_loss=sum(_ll(p, y) for p, _, y in g) / n,
                                   market_log_loss=sum(_ll(pm, y) for _, pm, y in g) / n,
                                   brier=sum((p - y) ** 2 for p, _, y in g) / n, market_brier=sum((pm - y) ** 2 for _, pm, y in g) / n)
    pos = db.execute('SELECT settled,pnl FROM paper_positions WHERE settled IS NOT NULL ORDER BY settled').fetchall()
    eq = peak = cfg['initial_bankroll']; dd = 0.0
    for _, pnl in pos:
        eq += pnl; peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
    dec = db.execute("SELECT COUNT(*),COALESCE(SUM(action!='skip'),0) FROM paper_decisions WHERE ts>=? AND ts<=?", (since, now)).fetchone()
    out['paper'] = dict(pnl_24h=sum(p for s, p in pos if since <= s <= now), pnl_total=eq - cfg['initial_bankroll'],
                        trades=dec[1], skips=dec[0] - dec[1], max_drawdown=dd, halted=risk.halted(db))
    out['passive_research'] = execution.report(db)['passive_research']
    out['risk_events'] = db.execute('SELECT ts,kind,detail FROM risk_events WHERE ts>=? AND ts<=?', (since, now)).fetchall()
    out['alerts'] = db.execute('SELECT ts,kind,detail FROM alerts WHERE ts>=? AND ts<=?', (since, now)).fetchall()
    dq = dq_report.report(db)
    out['data_quality'] = {d: v for d, v in dq['exchanges'].items() if d >= dt.datetime.fromtimestamp(since, dt.timezone.utc).date().isoformat()}
    out['note'] = 'Paper results only. Short windows are noisy; no profitability is implied.'
    return out


def run_scorecard_if_due(db, now, folder=Path('scorecards')):
    day = scorecard_due(db, now)
    if not day: return None
    body = scorecard(db, now)
    db.execute('INSERT INTO scorecards VALUES(?,?,?)', (day, now, json.dumps(body))); db.commit()
    try:
        folder.mkdir(exist_ok=True); (folder / ('scorecard-%s.json' % day)).write_text(json.dumps(body, indent=2))
    except OSError:
        pass
    return body


if __name__ == '__main__':
    # Watchdog (run in its own window): python -m quant.monitor --db quant.sqlite
    import argparse, os
    from . import store
    a = argparse.ArgumentParser(); a.add_argument('--db', default='quant.sqlite'); a.add_argument('--once', action='store_true')
    args = a.parse_args(); db = store.connect(args.db); setup(db)
    while True:
        check_heartbeats(db, time.time())
        if args.once or os.path.exists('STOP'): break
        time.sleep(HEARTBEAT_S)
