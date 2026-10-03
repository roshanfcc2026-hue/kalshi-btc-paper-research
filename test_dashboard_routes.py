import io
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import dashboard


class DashboardRouteTests(unittest.TestCase):
    def handler(self, path):
        handler=object.__new__(dashboard.Handler)
        handler.path=path
        handler.wfile=io.BytesIO()
        handler.headers_sent={}
        handler.send_response=lambda code:setattr(handler,'code',code)
        handler.send_header=lambda key,value:handler.headers_sent.update({key:value})
        handler.end_headers=lambda:None
        handler.send_error=lambda code,message=None:setattr(handler,'code',code)
        return handler

    def test_mistakes_route_returns_sanitized_json_and_no_cache(self):
        expected={'source':'volatility-proxy-v1','summary':{'wrong':1},'mistakes':[]}
        with patch.object(dashboard,'build_mistake_report',return_value=expected) as build:
            handler=self.handler('/mistakes.json?refresh=1');handler.do_GET()
        build.assert_called_once_with(dashboard.ROOT/'research.sqlite')
        self.assertEqual(handler.code,200)
        self.assertEqual(json.loads(handler.wfile.getvalue()),expected)
        self.assertEqual(handler.headers_sent['Cache-Control'],'no-store')

    def test_database_failure_returns_unavailable_without_internal_details(self):
        with patch.object(dashboard,'build_mistake_report',side_effect=sqlite3.OperationalError('private path')):
            handler=self.handler('/mistakes.json');handler.do_GET()
        self.assertEqual(handler.code,503);self.assertEqual(handler.wfile.getvalue(),b'')

    def test_paper_signals_remain_read_only_and_live_skip(self):
        expected={'version':'dashboard-paper-signals-v1','live_signal':'SKIP','snapshot_valid':False}
        with patch.object(dashboard,'build_trading_report',return_value=expected) as build:
            handler=self.handler('/paper-signals.json');handler.do_GET()
        build.assert_called_once_with(dashboard.ROOT/'research.sqlite')
        self.assertEqual(json.loads(handler.wfile.getvalue()),expected)
        self.assertEqual(handler.code,200)
        self.assertEqual(handler.headers_sent['Cache-Control'],'no-store')
        handler=self.handler('/trading-panel.js');handler.do_GET()
        self.assertEqual(handler.code,200)

    def test_only_explicit_files_are_served(self):
        for path in ('/research.sqlite','/config.json','/../config.json'):
            handler=self.handler(path);handler.do_GET();self.assertEqual(handler.code,404)
        handler=self.handler('/mistakes-panel.js');handler.do_GET()
        self.assertEqual(handler.code,200)
        self.assertIn(b'formatMistake',handler.wfile.getvalue())

    def test_remaining_routes_serve_only_report_and_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'remaining-lock-v1').mkdir()
            expected={'source':'remaining-lock-v1','active':None}
            (root/'remaining-lock-v1'/'report.json').write_text(json.dumps(expected))
            with patch.object(dashboard,'ROOT',root):
                handler=self.handler('/remaining-report.json?refresh=1');handler.do_GET()
                self.assertEqual(handler.code,200)
                self.assertEqual(json.loads(handler.wfile.getvalue()),expected)
                self.assertEqual(handler.headers_sent['Cache-Control'],'no-store')
                for path in ('/remaining-lock-v1/registry.json','/remaining-lock-v1/ledger.jsonl'):
                    handler=self.handler(path);handler.do_GET();self.assertEqual(handler.code,404)
        handler=self.handler('/remaining-panel.js');handler.do_GET()
        self.assertEqual(handler.code,200)
        self.assertIn(b'checkpoint',handler.wfile.getvalue())


if __name__=='__main__':
    unittest.main()


class FuturisticRouteTests(DashboardRouteTests):
    def test_theme_and_stats_assets_served_and_quant_state_degrades(self):
        for path in ('/futuristic-theme.css', '/stats-panel.js'):
            handler=self.handler(path);handler.do_GET();self.assertEqual(handler.code,200)
        with tempfile.TemporaryDirectory() as temporary, patch.object(dashboard,'ROOT',Path(temporary)):
            handler=self.handler('/quant-state.json');handler.do_GET();self.assertEqual(handler.code,503)
