"""Layer 5 sizing and risk. PAPER ONLY: decides paper position sizes; never sends orders.

- Trade only if expected value per contract after fees > margin (default 0.03).
- Size: quarter-Kelly, capped at 1% of paper bankroll per market.
- Limits: open risk <= 3% of bankroll; daily loss 5%; weekly loss 10% (of the bankroll at the start
  of that UTC day / rolling 7 days). A breach HALTS paper trading until a manual reset.
Days are UTC (stdlib only; Windows Python ships no time-zone database without the tzdata package).
Manual reset: python -m quant.risk reset --by "your name" --db quant.sqlite
"""
import argparse, json, math, time
from . import costs

DEFAULTS = dict(initial_bankroll=1000.0, margin=0.03, kelly_fraction=0.25, per_market_cap=0.01,
                max_open_risk=0.03, daily_loss_limit=0.05, weekly_loss_limit=0.10)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS paper_positions(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, source TEXT NOT NULL,
  side TEXT NOT NULL, contracts REAL NOT NULL, cost REAL NOT NULL, fee REAL NOT NULL, opened REAL NOT NULL,
  result TEXT, pnl REAL, settled REAL, UNIQUE(ticker, source));
CREATE TABLE IF NOT EXISTS risk_state(id INTEGER PRIMARY KEY CHECK(id=1), halted INTEGER NOT NULL, reason TEXT, since REAL);
CREATE TABLE IF NOT EXISTS risk_events(id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
'''


def setup(db):
    db.executescript(SCHEMA)
    db.execute('INSERT OR IGNORE INTO risk_state VALUES(1,0,NULL,NULL)')


def kelly(p, cost):
    """Kelly fraction for a $1-payout binary bought at all-in cost per contract."""
    return max(0.0, (p - cost) / (1 - cost)) if 0 < cost < 1 else 0.0


# ---- ledger ---------------------------------------------------------------------------
def realized_since(db, ts):
    return db.execute('SELECT COALESCE(SUM(pnl),0) FROM paper_positions WHERE settled>=?', (ts,)).fetchone()[0]


def bankroll(db, cfg):
    return cfg['initial_bankroll'] + db.execute('SELECT COALESCE(SUM(pnl),0) FROM paper_positions').fetchone()[0]


def open_risk(db):
    return db.execute('SELECT COALESCE(SUM(cost+fee),0) FROM paper_positions WHERE result IS NULL').fetchone()[0]


def halted(db):
    r = db.execute('SELECT halted,reason FROM risk_state WHERE id=1').fetchone()
    return (bool(r[0]), r[1]) if r else (False, None)


def halt(db, reason, now):
    if not halted(db)[0]:
        db.execute('UPDATE risk_state SET halted=1,reason=?,since=? WHERE id=1', (reason, now))
        db.execute('INSERT INTO risk_events(ts,kind,detail) VALUES(?,?,?)', (now, 'halt', reason))
        db.commit()


def reset(db, by, now=None):
    now = now or time.time()
    db.execute('UPDATE risk_state SET halted=0,reason=NULL,since=NULL WHERE id=1')
    db.execute('INSERT INTO risk_events(ts,kind,detail) VALUES(?,?,?)', (now, 'manual_reset', 'by ' + by))
    db.commit()


def day_start(now):
    return math.floor(now / 86400) * 86400


def check_limits(db, cfg, now):
    """Halt on daily/weekly loss breach. Returns the breach reason or None."""
    start_bank = bankroll(db, cfg) - realized_since(db, day_start(now))
    if -realized_since(db, day_start(now)) >= cfg['daily_loss_limit'] * start_bank:
        halt(db, 'daily loss limit', now); return 'daily loss limit'
    week_bank = bankroll(db, cfg) - realized_since(db, now - 7 * 86400)
    if -realized_since(db, now - 7 * 86400) >= cfg['weekly_loss_limit'] * week_bank:
        halt(db, 'weekly loss limit', now); return 'weekly loss limit'
    return None


# ---- sizing ---------------------------------------------------------------------------
def size(p_yes, yes_bids, no_bids, bank, room, cfg=DEFAULTS, rate=costs.TAKER_RATE):
    """Best side and contract count. Shrinks size until EV after fees > margin and cost fits the caps.
    Returns dict(action='yes'|'no'|'skip', ...)."""
    best = dict(action='skip', reason='no side clears margin after fees', ev=None)
    for side, p in (('yes', p_yes), ('no', 1 - p_yes)):
        # Only levels whose MARGINAL contract clears the margin after its fee share; averaging could hide losers.
        levels = [l for l in costs.ask_levels(yes_bids, no_bids, side) if p - l[0] - rate * l[0] * (1 - l[0]) > cfg['margin']]
        if not levels: continue
        f = kelly(p, costs.fill(levels, 1, rate)['cost_per_contract'])
        budget = min(cfg['kelly_fraction'] * f * bank, cfg['per_market_cap'] * bank, room)
        n = math.floor(budget / levels[0][0])
        while n >= 1:
            r = costs.fill(levels, n, rate)
            if r['unfilled'] == 0 and r['total_cost'] <= budget and p - r['cost_per_contract'] > cfg['margin']:
                ev = p - r['cost_per_contract']
                if best['ev'] is None or ev * n > best['ev'] * best['contracts']:
                    best = dict(action=side, contracts=n, ev=ev, kelly=f, budget=budget, fill=r)
                break
            n -= 1
    return best


def decide(db, ticker, source, p_yes, yes_bids, no_bids, now, cfg=DEFAULTS):
    """Full pre-trade check. Records a paper position if approved. Never sends an order."""
    if halted(db)[0]: return dict(action='skip', reason='halted: ' + halted(db)[1])
    if check_limits(db, cfg, now): return dict(action='skip', reason='halted: ' + halted(db)[1])
    if db.execute('SELECT 1 FROM paper_positions WHERE ticker=? AND source=?', (ticker, source)).fetchone():
        return dict(action='skip', reason='already traded this market')
    bank = bankroll(db, cfg); room = cfg['max_open_risk'] * bank - open_risk(db)
    if room <= 0: return dict(action='skip', reason='max open risk')
    d = size(p_yes, yes_bids, no_bids, bank, room, cfg)
    if d['action'] != 'skip':
        r = d['fill']
        db.execute('INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened) VALUES(?,?,?,?,?,?,?)',
                   (ticker, source, d['action'], r['filled'], r['notional'], r['fee'], now))
        db.commit()
    return d


def settle(db, ticker, result, now, cfg=DEFAULTS):
    """Apply an OFFICIAL yes/no result, then check loss limits (may halt)."""
    if result not in ('yes', 'no'): raise ValueError('official yes/no result required')
    for pid, side, n, cost, fee in db.execute('SELECT id,side,contracts,cost,fee FROM paper_positions WHERE ticker=? AND result IS NULL', (ticker,)).fetchall():
        pnl = (n if side == result else 0.0) - cost - fee
        db.execute('UPDATE paper_positions SET result=?,pnl=?,settled=? WHERE id=?', (result, pnl, now, pid))
    db.commit()
    return check_limits(db, cfg, now)


if __name__ == '__main__':
    from . import store
    a = argparse.ArgumentParser(); a.add_argument('command', choices=['status', 'reset']); a.add_argument('--by', default='')
    a.add_argument('--db', default='quant.sqlite'); args = a.parse_args()
    db = store.connect(args.db); setup(db)
    if args.command == 'reset':
        if not args.by: raise SystemExit('--by "your name" is required for a manual reset')
        reset(db, args.by)
    print(json.dumps(dict(halted=halted(db), bankroll=bankroll(db, DEFAULTS), open_risk=open_risk(db),
                          events=db.execute('SELECT ts,kind,detail FROM risk_events ORDER BY id DESC LIMIT 10').fetchall()), indent=2))
