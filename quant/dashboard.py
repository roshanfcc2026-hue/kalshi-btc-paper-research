"""Read-only local dashboard for the PAPER pipeline. http://127.0.0.1:8766
Never writes to any database and has no trading controls. Binds to localhost only.
Run: python -m quant.dashboard --db quant.sqlite"""
import argparse, json, sqlite3, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import monitor, risk

HTML = (Path(__file__).resolve().parent / 'dashboard.html')


def _q(db, sql, args=()):
    try: return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError: return []  # table not created yet


def state(db, now=None, cfg=risk.DEFAULTS):
    now = now or time.time()
    hb = dict(_q(db, 'SELECT component,ts FROM heartbeats'))
    halt = _q(db, 'SELECT halted,reason,since FROM risk_state WHERE id=1')
    settled = _q(db, 'SELECT settled,pnl FROM paper_positions WHERE settled IS NOT NULL ORDER BY settled')
    eq, curve = cfg['initial_bankroll'], [[None, cfg['initial_bankroll']]]
    for ts, pnl in settled: eq += pnl; curve.append([ts, round(eq, 4)])
    peak, dd = cfg['initial_bankroll'], 0.0
    for _, v in curve: peak = max(peak, v); dd = max(dd, (peak - v) / peak)
    open_ = _q(db, 'SELECT COALESCE(SUM(cost+fee),0),COUNT(*) FROM paper_positions WHERE result IS NULL')
    proxy = _q(db, 'SELECT ts,price,n_used,n_total,status FROM spot_proxy ORDER BY ts DESC LIMIT 1')
    feeds = _q(db, '''SELECT p.exchange,p.status,p.price,p.age_s FROM proxy_inputs p
                      WHERE p.ts=(SELECT MAX(ts) FROM proxy_inputs) ORDER BY p.exchange''')
    trades = _q(db, '''SELECT p.opened,p.ticker,p.side,p.contracts,p.cost,p.fee,p.result,p.pnl FROM paper_positions p ORDER BY p.opened DESC LIMIT 25''')
    decisions = _q(db, 'SELECT ts,ticker,source,p_yes,action,reason FROM paper_decisions ORDER BY id DESC LIMIT 25')
    src = {}
    for s, p, pm, res in _q(db, 'SELECT f.source,f.p_yes,f.p_market,r.result FROM forecasts f JOIN market_results r USING(ticker)'):
        y = res == 'yes'; d = src.setdefault(s, [0, 0.0, 0.0])
        d[0] += 1; d[1] += monitor._ll(p, y); d[2] += monitor._ll(pm, y)
    combo_frozen = (Path(__file__).resolve().parent / 'registry' / 'combo-v1.json').exists()
    return dict(now=now, mode='PAPER ONLY - no live orders',
                trading_status=('HALTED: ' + halt[0][1]) if halt and halt[0][0] else ('ACTIVE (paper)' if combo_frozen else 'WAITING: combo-v1 not frozen; no trades'),
                heartbeat_age=(now - hb['collector']) if 'collector' in hb else None,
                bankroll=round(eq, 2), pnl_total=round(eq - cfg['initial_bankroll'], 2), max_drawdown=dd,
                open_risk=round(open_[0][0], 2) if open_ else 0, open_positions=open_[0][1] if open_ else 0,
                proxy=dict(zip(('ts', 'price', 'n_used', 'n_total', 'status'), proxy[0])) if proxy else None,
                feeds=[dict(zip(('exchange', 'status', 'price', 'age_s'), f)) for f in feeds],
                equity=curve,
                sources={s: dict(markets=n, log_loss=a / n, market_log_loss=b / n, vs_market=(a - b) / n) for s, (n, a, b) in src.items()},
                trades=[dict(zip(('opened', 'ticker', 'side', 'contracts', 'cost', 'fee', 'result', 'pnl'), t)) for t in trades],
                decisions=[dict(zip(('ts', 'ticker', 'source', 'p_yes', 'action', 'reason'), d)) for d in decisions],
                passive_research=_safe_passive(db), model=model_info(),
                alerts=[dict(zip(('ts', 'kind', 'detail'), a)) for a in _q(db, 'SELECT ts,kind,detail FROM alerts ORDER BY id DESC LIMIT 10')])


def model_info():
    """Frozen signal registry and combo-v1 (if frozen): what the model is and how it was fitted."""
    reg = Path(__file__).resolve().parent / 'registry'
    out = dict(signals=[], combo=None)
    try:
        doc = json.loads((reg / 'signals-v1.json').read_text())
        out['signals'] = [dict(name=e['name'], version=e['version'], features=e['features'], reason=e['reason'], sha=e['sha256'][:12]) for e in doc['signals']]
        out['signals_frozen_at'] = doc['frozen_at']
    except (OSError, ValueError, KeyError):
        pass
    try:
        c = json.loads((reg / 'combo-v1.json').read_text())
        out['combo'] = dict(features=c['features'], dropped=c['dropped'], coefficients=c['coefficients'], ci95=c['ci95'],
                            frozen_at=c.get('frozen_at'), training=dict((k, c['training'][k]) for k in ('markets', 'in_sample_log_loss', 'reliability_calibrated')))
    except (OSError, ValueError, KeyError):
        pass
    return out


def _safe_passive(db):
    r = _q(db, 'SELECT COUNT(*),COALESCE(SUM(filled>0),0),COALESCE(SUM(pnl),0) FROM passive_orders WHERE result IS NOT NULL')
    n, f, p = r[0] if r else (0, 0, 0)
    return dict(resolved=n, filled=f, fill_rate=f / n if n else None, pnl=p, warning='OPTIMISTIC research only')


def make_handler(db_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype):
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == '/':
                return self._send(200, b'<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">' + HTML.read_bytes(), 'text/html; charset=utf-8')
            if self.path == '/api/state':
                uri = Path(db_path).resolve().as_uri() + '?mode=ro'
                try:
                    db = sqlite3.connect(uri, uri=True)
                    try: body = state(db)
                    finally: db.close()
                except sqlite3.OperationalError as e:
                    body = dict(error='database not available yet: %s' % e, mode='PAPER ONLY - no live orders')
                return self._send(200, json.dumps(body).encode(), 'application/json')
            self._send(404, b'not found', 'text/plain')
    return H


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--db', default='quant.sqlite'); a.add_argument('--port', type=int, default=8766)
    args = a.parse_args()
    print('Paper dashboard: http://127.0.0.1:%d  (read-only, Ctrl+C to stop)' % args.port)
    ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(args.db)).serve_forever()
