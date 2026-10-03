import unittest
from quant import proxy, store as qstore

NOW = 1000.0
def o(ex, price, exch=NOW - 1, recv=NOW - 0.5): return dict(exchange=ex, price=price, exch_ts=exch, recv_ts=recv)


class ProxyTests(unittest.TestCase):
    def status(self, r): return {i['exchange']: i['status'] for i in r['inputs']}
    def test_median_of_four(self):
        r = proxy.compute([o('a', 100), o('b', 100.1), o('c', 100.2), o('d', 100.3)], NOW)
        self.assertAlmostEqual(r['price'], 100.15); self.assertEqual(r['status'], 'ok'); self.assertEqual(r['n_used'], 4)
    def test_stale_by_exchange_timestamp_and_by_receive(self):
        r = proxy.compute([o('a', 100), o('b', 100), o('c', 100, exch=NOW - 11), o('d', 100, exch=NOW - 1, recv=NOW - 11)], NOW)
        self.assertEqual(self.status(r), dict(a='ok', b='ok', c='stale', d='stale')); self.assertEqual(r['n_used'], 2)
    def test_outlier_excluded_and_median_recomputed(self):
        r = proxy.compute([o('a', 100), o('b', 100.1), o('c', 100.2), o('d', 101)], NOW)  # d is ~0.9% off
        self.assertEqual(self.status(r)['d'], 'outlier'); self.assertAlmostEqual(r['price'], 100.1)
    def test_just_inside_threshold_kept(self):
        r = proxy.compute([o('a', 100), o('b', 100), o('c', 100.29)], NOW)
        self.assertEqual(self.status(r)['c'], 'ok')
    def test_no_lookahead(self):
        late = o('a', 999, recv=NOW + 5, exch=NOW + 4)
        r = proxy.compute([o('a', 100), o('b', 100), late], NOW)
        self.assertEqual(r['price'], 100); self.assertEqual(self.status(r)['a'], 'ok')
    def test_future_exchange_timestamp_flagged(self):
        r = proxy.compute([o('a', 100), o('b', 100), o('c', 100, exch=NOW + 30)], NOW)
        self.assertEqual(self.status(r)['c'], 'future')
    def test_two_sources_far_apart_withholds_proxy(self):
        r = proxy.compute([o('a', 100), o('b', 101)], NOW)
        self.assertIsNone(r['price']); self.assertEqual(r['status'], 'insufficient')
    def test_missing_and_invalid(self):
        r = proxy.compute([o('a', 100), o('b', 100), o('c', float('nan'))], NOW, expected=('a', 'b', 'c', 'd'))
        self.assertEqual(self.status(r), dict(a='ok', b='ok', c='invalid', d='missing')); self.assertEqual(r['price'], 100)
    def test_single_source_is_insufficient(self):
        self.assertEqual(proxy.compute([o('a', 100)], NOW)['status'], 'insufficient')
    def test_latest_observation_per_exchange_used(self):
        r = proxy.compute([o('a', 90, recv=NOW - 3, exch=NOW - 3), o('a', 100), o('b', 100)], NOW)
        self.assertEqual(r['price'], 100)
    def test_store(self):
        db = qstore.connect(':memory:'); proxy.store(db, proxy.compute([o('a', 100), o('b', 100)], NOW)); db.commit()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM proxy_inputs').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
