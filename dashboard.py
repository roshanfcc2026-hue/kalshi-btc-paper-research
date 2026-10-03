"""Read-only localhost dashboard: presentation, status and first-call mistake ledger."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import sqlite3

from dashboard_mistakes import build_mistake_report
from dashboard_trading import build_trading_report
from dashboard_cycles import build_cycles_report

ROOT=Path(__file__).resolve().parent

def quant_state(path):
    """Read-only summary of the quant paper pipeline (quant.sqlite); raises if it does not exist yet."""
    from quant import dashboard as qd
    if not Path(path).exists(): raise OSError('quant.sqlite missing')
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try: return {k:v for k,v in qd.state(db).items() if k in ('trading_status','heartbeat_age','bankroll','pnl_total','max_drawdown','open_risk','proxy')}
    finally: db.close()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=self.path.split('?')[0]
        if path=='/quant-state.json':
            try:
                data=json.dumps(quant_state(ROOT/'quant.sqlite'),allow_nan=False).encode('utf-8')
            except (sqlite3.Error, OSError, ValueError):
                self.send_error(503,'Quant pipeline not running'); return
            content_type='application/json'
        elif path=='/cycles.json':
            try:
                data=json.dumps(build_cycles_report(ROOT/'research.sqlite',quant_path=ROOT/'quant.sqlite'),allow_nan=False).encode('utf-8')
            except (sqlite3.Error, OSError, ValueError):
                self.send_error(503,'Cycle report temporarily unavailable'); return
            content_type='application/json'
        elif path in ('/mistakes.json','/paper-signals.json'):
            try:
                builder=build_mistake_report if path=='/mistakes.json' else build_trading_report
                data=json.dumps(builder(ROOT/'research.sqlite'),allow_nan=False).encode('utf-8')
            except (sqlite3.Error, OSError, ValueError):
                self.send_error(503,'Research report temporarily unavailable'); return
            content_type='application/json'
        else:
            route={'/':('dashboard.html','text/html; charset=utf-8'),'/status.json':('status.json','application/json'),
                   '/analytics-workspace.js':('analytics-workspace.js','text/javascript; charset=utf-8'),
                   '/analytics-workspace.css':('analytics-workspace.css','text/css; charset=utf-8'),
                   '/midpoint-report.json':('midpoint-lock-v1/report.json','application/json'),
                   '/midpoint-panel.js':('midpoint-panel.js','text/javascript; charset=utf-8'),
                   '/remaining-report.json':('remaining-lock-v1/report.json','application/json'),
                   '/remaining-panel.js':('remaining-panel.js','text/javascript; charset=utf-8'),
                   '/mistakes-panel.js':('mistakes-panel.js','text/javascript; charset=utf-8'),
                   '/trading-panel.js':('trading-panel.js','text/javascript; charset=utf-8'),
                   '/futuristic-theme.css':('futuristic-theme.css','text/css; charset=utf-8'),
                   '/stats-panel.js':('stats-panel.js','text/javascript; charset=utf-8'),
                   '/cycles-panel.js':('cycles-panel.js','text/javascript; charset=utf-8')}.get(path)
            if not route: self.send_error(404); return
            try: data=(ROOT/route[0]).read_bytes()
            except FileNotFoundError: self.send_error(503,'Run the collector first'); return
            content_type=route[1]
        self.send_response(200); self.send_header('Content-Type',content_type); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self,*args): pass

if __name__=='__main__':
    print('Dashboard: http://127.0.0.1:8765',flush=True)
    HTTPServer(('127.0.0.1',8765),Handler).serve_forever()
