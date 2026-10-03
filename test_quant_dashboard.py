import json, os, sqlite3, tempfile, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer
from quant import dashboard, monitor, store as qstore, proxy


class DashboardTests(unittest.TestCase):
    def test_state_on_empty_and_populated_db(self):
        d = qstore.connect(':memory:')
        s = dashboard.state(d, 1000); self.assertIn('PAPER ONLY', s['mode']); self.assertIsNone(s['heartbeat_age'])
        monitor.setup(d); monitor.beat(d, 'collector', 990)
        proxy.store(d, dict(ts=995, price=65000.0, n_used=3, n_total=4, status='ok', inputs=[dict(exchange='a', price=65000.0, age_s=1, status='ok')]))
        d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES('A','combo-v1','yes',5,2,0.1,900,'yes',2.9,950)")
        s = dashboard.state(d, 1000)
        self.assertEqual(s['heartbeat_age'], 10); self.assertEqual(s['bankroll'], 1002.9); self.assertEqual(s['equity'][-1], [950, 1002.9])
        self.assertTrue(s['trading_status'].startswith(('WAITING', 'ACTIVE'))); json.dumps(s)
        self.assertEqual(len(s['model']['signals']), 5)
    def test_halt_shown(self):
        d = qstore.connect(':memory:'); monitor.setup(d); monitor.risk.halt(d, 'daily loss limit', 5)
        self.assertEqual(dashboard.state(d, 10)['trading_status'], 'HALTED: daily loss limit')
    def test_server_is_read_only_and_localhost(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'q.sqlite'); d = qstore.connect(p); monitor.setup(d); d.close()
            before = os.path.getmtime(p)
            srv = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.make_handler(p)); th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
            try:
                base = 'http://127.0.0.1:%d' % srv.server_address[1]
                self.assertIn(b'PAPER ONLY', urllib.request.urlopen(base + '/').read().upper())
                self.assertIn('PAPER ONLY', json.load(urllib.request.urlopen(base + '/api/state'))['mode'])
                with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(base + '/nope')
            finally:
                srv.shutdown(); srv.server_close()
            self.assertEqual(os.path.getmtime(p), before)
    def test_no_trading_controls(self):
        html = dashboard.HTML.read_text()
        for word in ('<form', '<button', 'method="post"', 'fetch(\'/api/order'): self.assertNotIn(word, html)


if __name__ == '__main__':
    unittest.main()
