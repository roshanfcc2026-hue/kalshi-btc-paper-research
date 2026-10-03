import io, json, unittest
from unittest.mock import patch
import dashboard_plus as plus


class PlusTests(unittest.TestCase):
    def test_inject_keeps_user_page_and_is_idempotent(self):
        page = '<html><head><title>x</title></head><body><nav>Day trial $400</nav></body></html>'
        out = plus.inject(page)
        self.assertIn('Day trial $400', out); self.assertIn('/futuristic-theme.css', out)
        self.assertIn('/stats-panel.js', out); self.assertIn('/cycles-panel.js', out)
        self.assertEqual(plus.inject(out), out)
        self.assertLess(out.index('futuristic-theme.css'), out.index('</head>'))

    def handler(self, path):
        h = object.__new__(plus.Handler); h.path = path; h.wfile = io.BytesIO(); h.sent = {}
        h.send_response = lambda c: setattr(h, 'code', c); h.send_header = lambda k, v: h.sent.update({k: v})
        h.end_headers = lambda: None; h.send_error = lambda c, m=None: setattr(h, 'code', c)
        return h

    def test_root_is_injected_and_other_routes_fall_through(self):
        h = self.handler('/'); h.do_GET(); self.assertEqual(h.code, 200); self.assertIn(b'BTC15 Cockpit', h.wfile.getvalue())
        h = self.handler('/classic'); h.do_GET(); self.assertEqual(h.code, 200); self.assertIn(b'cycles-panel.js', h.wfile.getvalue())
        h = self.handler('/stats-panel.js'); h.do_GET(); self.assertEqual(h.code, 200)
        h = self.handler('/research.sqlite'); h.do_GET(); self.assertEqual(h.code, 404)  # original allowlist still applies
        with patch.object(plus.dashboard, 'build_mistake_report', return_value={'ok': 1}):
            h = self.handler('/mistakes.json'); h.do_GET(); self.assertEqual(json.loads(h.wfile.getvalue()), {'ok': 1})


class RunStartTests(unittest.TestCase):
    def test_starts_at_next_boundary_persists_and_resets(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, patch.object(plus, 'START', Path(d) / 's.json'):
            self.assertEqual(plus.run_start(now=1000.0), 1800.0)
            self.assertEqual(plus.run_start(now=5000.0), 1800.0)          # persisted
            self.assertEqual(plus.run_start(reset=True, now=5000.0), 5400.0)


if __name__ == '__main__':
    unittest.main()
