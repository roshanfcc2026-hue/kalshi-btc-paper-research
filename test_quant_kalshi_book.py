import unittest
from quant import kalshi_book as kb, store as qstore

BOOK = {'orderbook_fp': {'yes_dollars': [['0.40', '5'], ['0.45', '3'], ['0.30', '1'], ['0.20', '1'], ['0.10', '1'], ['0.05', '1'], ['0.02', '1']],
                         'no_dollars': [['0.50', '7'], ['0.40', '0'], ['0.35', '2']]}}
M = {'ticker': 'KXBTC15M-X', 'close_time': '1970-01-01T00:15:00Z', 'floor_strike': 65000}


class KalshiBookTests(unittest.TestCase):
    def test_top5_sorted_best_first_and_zero_size_dropped(self):
        yes, no = kb.parse(BOOK)
        self.assertEqual(len(yes), 5); self.assertEqual(yes[0], (0.45, 3.0)); self.assertEqual(no, [(0.50, 7.0), (0.35, 2.0)])
    def test_crossed_rejected_one_sided_allowed(self):
        with self.assertRaises(ValueError): kb.parse({'orderbook_fp': {'yes_dollars': [['0.6', '1']], 'no_dollars': [['0.6', '1']]}})
        self.assertEqual(kb.parse({'orderbook_fp': {'yes_dollars': [['0.6', '1']], 'no_dollars': []}})[1], [])
    def test_snapshot_all_stores_every_market_and_survives_a_bad_one(self):
        def get(path, **kw):
            if path == '/markets':
                return {'markets': [M, dict(M, ticker='KXBTC15M-Y'), dict(M, ticker='KXBTC15M-BAD')], 'cursor': ''}
            if path.endswith('BAD/orderbook'): raise RuntimeError('boom')
            return BOOK
        ticks = iter(range(100, 200))
        db = qstore.connect(':memory:')
        n, errors = kb.snapshot_all(db, get, lambda: next(ticks))
        self.assertEqual(n, 2); self.assertEqual(errors[0][0], 'KXBTC15M-BAD')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM kalshi_snapshots').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM kalshi_levels WHERE side="yes"').fetchone()[0], 10)
        self.assertEqual(db.execute('SELECT close_ts,strike FROM kalshi_snapshots LIMIT 1').fetchone(), (900.0, 65000.0))
    def test_pagination(self):
        pages = {'': {'markets': [M], 'cursor': 'c2'}, 'c2': {'markets': [dict(M, ticker='KXBTC15M-Z')], 'cursor': ''}}
        get = lambda path, **kw: pages[kw['cursor']] if path == '/markets' else BOOK
        db = qstore.connect(':memory:'); self.assertEqual(kb.snapshot_all(db, get, lambda: 1.0)[0], 2)


if __name__ == '__main__':
    unittest.main()
