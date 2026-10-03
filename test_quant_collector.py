import unittest
from quant import collector, feeds, store as qstore
from test_quant_feeds import CB_T, CB_B, KR_TR, KR_DP, BS_T, BS_B, GM_T, GM_B
from test_quant_kalshi_book import BOOK, M

RESP = {}
for ex, (a, b) in dict(coinbase=(CB_T, CB_B), kraken=(KR_TR, KR_DP), bitstamp=(BS_T, BS_B), gemini=(GM_T, GM_B)).items():
    RESP[feeds.URLS[ex][0]], RESP[feeds.URLS[ex][1]] = a, b
KGET = lambda path, **kw: {'markets': [M], 'cursor': ''} if path == '/markets' else BOOK


class CollectorTests(unittest.TestCase):
    def test_cycle_stores_everything_and_proxy(self):
        db = qstore.connect(':memory:')
        r = collector.run_cycle(db, RESP.__getitem__, lambda: 1000.0, KGET)
        self.assertEqual(r['errors'], []); self.assertEqual(r['proxy']['n_used'], 4); self.assertEqual(r['kalshi'], 1)
        self.assertAlmostEqual(r["proxy"]["price"], 65001.0)  # median of 65000.1, 65001, 65001, 65002
        self.assertEqual(db.execute('SELECT COUNT(*) FROM exchange_ticks').fetchone()[0], 4)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM proxy_inputs').fetchone()[0], 4)
    def test_one_failing_exchange_is_recorded_not_fatal(self):
        def fetch(url):
            if 'kraken' in url: raise OSError('down')
            return RESP[url]
        db = qstore.connect(':memory:')
        r = collector.run_cycle(db, fetch, lambda: 1000.0, KGET)
        self.assertEqual([e[0] for e in r['errors']], ['kraken']); self.assertEqual(r['proxy']['n_used'], 3)
        self.assertEqual(db.execute("SELECT status FROM proxy_inputs WHERE exchange='kraken'").fetchone()[0], 'missing')
        self.assertEqual(db.execute('SELECT source FROM feed_errors').fetchone()[0], 'kraken')
    def test_kalshi_failure_does_not_block_exchange_data(self):
        def bad(path, **kw): raise OSError('kalshi down')
        db = qstore.connect(':memory:')
        r = collector.run_cycle(db, RESP.__getitem__, lambda: 1000.0, bad)
        self.assertEqual(r['proxy']['status'], 'ok'); self.assertEqual(r['errors'][0][0], 'kalshi')
    def test_no_order_or_credential_code(self):
        import inspect
        src = inspect.getsource(collector) + inspect.getsource(feeds)
        for word in ('POST', 'Authorization', 'api_key', 'portfolio/orders'): self.assertNotIn(word, src)


if __name__ == '__main__':
    unittest.main()
