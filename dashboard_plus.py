"""Futuristic add-on for YOUR existing dashboard. Changes none of your files.

Serves your own dashboard.html (whatever version you have, e.g. with the Day trial tab) and injects
the futuristic theme, stats deck and 15-minute cycle panels into the page as it is sent to the browser.
Every other route is handled by your own dashboard.py unchanged. Read-only. Paper only.
Run:  py -3 -X utf8 dashboard_plus.py      then open http://127.0.0.1:8767 (Cockpit; /classic = your full dashboard)
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import json, sqlite3

import dashboard  # your existing dashboard.py
from dashboard_cycles import build_cycles_report

ROOT = Path(__file__).resolve().parent
PORT = 8767
START = ROOT / 'cockpit-start.json'  # local file: when the current "cycle 0" run began


def run_start(reset=False, now=None):
    """Start of the fresh run: the next 15-minute window boundary after the first launch (or a reset)."""
    import math, time
    if START.exists() and not reset:
        try:
            return float(json.loads(START.read_text())['start_ts'])
        except (ValueError, KeyError, TypeError):
            pass
    now = time.time() if now is None else now
    start = math.ceil(now / 900) * 900
    START.write_text(json.dumps(dict(start_ts=start, created=now)))
    return start
HEAD = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500'
        '&family=JetBrains+Mono:wght@400;500&display=swap"><link rel="stylesheet" href="/futuristic-theme.css">')
BODY = '<script src="/stats-panel.js"></script><script src="/cycles-panel.js"></script>'
ASSETS = {'/futuristic-theme.css': 'text/css; charset=utf-8', '/stats-panel.js': 'text/javascript; charset=utf-8',
          '/cycles-panel.js': 'text/javascript; charset=utf-8'}


def inject(html):
    """Add theme + panels once; leave the page untouched if they are already present."""
    if '/futuristic-theme.css' not in html:
        html = html.replace('</head>', HEAD + '</head>', 1)
    for tag in BODY.split('</script>')[:-1]:
        tag += '</script>'
        if tag not in html:
            html = html.replace('</body>', tag + '</body>', 1) if '</body>' in html else html + tag
    return html


def quant_state(path):
    from quant import dashboard as qd
    if not Path(path).exists():
        raise OSError('quant.sqlite missing')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return {k: v for k, v in qd.state(db).items()
                if k in ('trading_status', 'heartbeat_age', 'bankroll', 'pnl_total', 'max_drawdown', 'open_risk', 'proxy')}
    finally:
        db.close()


class Handler(dashboard.Handler):
    def _send(self, body, ctype):
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        try:
            if path == '/':
                page = b'<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">'
                return self._send(page + (ROOT / 'cockpit.html').read_bytes(), 'text/html; charset=utf-8')
            if path == '/classic':
                return self._send(inject((ROOT / 'dashboard.html').read_text(encoding='utf-8')).encode('utf-8'), 'text/html; charset=utf-8')
            if path in ASSETS:
                return self._send((ROOT / path.lstrip('/')).read_bytes(), ASSETS[path])
            if path == '/cycles.json':
                return self._send(json.dumps(build_cycles_report(ROOT / 'research.sqlite', quant_path=ROOT / 'quant.sqlite', since=run_start()),
                                             allow_nan=False).encode(), 'application/json')
            if path == '/quant-state.json':
                return self._send(json.dumps(quant_state(ROOT / 'quant.sqlite'), allow_nan=False).encode(), 'application/json')
        except (sqlite3.Error, OSError, ValueError):
            return self.send_error(503, 'Not available yet')
        return super().do_GET()  # everything else: your dashboard.py, unchanged


if __name__ == '__main__':
    import sys
    if '--reset-cycle' in sys.argv:
        import time
        print('Fresh run: cycle 0 starts at', time.strftime('%H:%M', time.localtime(run_start(reset=True))))
    else:
        run_start()
    print('Futuristic dashboard: http://127.0.0.1:%d  (your original stays on :8765)' % PORT, flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
