import math, unittest
from quant.signals import common, s1_vol_distance as s1, s2_exchange_imbalance as s2, s3_momentum as s3, s4_lead_lag as s4, s5_kalshi_book as s5, ALL

T = 36000.0  # whole minute


def proxy_series(price=100.0, drift=0.0, minutes=70, step=5):
    out, p = [], price
    for i in range(int(minutes * 60 / step) + 1):
        ts = T - minutes * 60 + i * step
        wiggle = 0.0005 * math.sin(i * 1.7)
        out.append((ts, price * math.exp(drift * (ts - T) / 60 + wiggle)))
    return out


def snap(**kw):
    base = dict(t=T, ticker='X', strike=100.0, close_ts=T + 600, proxy=proxy_series(), ticks={}, books={}, kalshi=[])
    base.update(kw); return base


class CommonTests(unittest.TestCase):
    def test_t_cdf_known_values(self):
        self.assertAlmostEqual(common.t_cdf(1, 1), 0.75, places=9)  # Cauchy
        x = 1.3; self.assertAlmostEqual(common.t_cdf(x, 2), 0.5 + x / (2 * math.sqrt(2 + x * x)), places=9)
        self.assertAlmostEqual(common.t_cdf(2.015048, 5), 0.95, places=5)
        self.assertAlmostEqual(common.t_cdf(-2.015048, 5), 0.05, places=5)
    def test_price_at_respects_tolerance_and_time(self):
        s = [(10, 1.0), (20, 2.0)]
        self.assertEqual(common.price_at(s, 25, 10), 2.0); self.assertIsNone(common.price_at(s, 31, 10)); self.assertIsNone(common.price_at(s, 5, 10))
    def test_minute_closes_stops_at_gap(self):
        s = [(T - 60 * k, 100.0) for k in range(10)] + [(T - 60 * k, 100.0) for k in range(12, 20)]
        self.assertEqual(len(common.minute_closes(sorted(s), T + 5)), 10)
    def test_market_mid(self):
        self.assertAlmostEqual(common.market_mid(dict(yes=[(0.40, 1)], no=[(0.55, 1)])), 0.425)
        self.assertIsNone(common.market_mid(dict(yes=[], no=[(0.5, 1)])))
    def test_every_signal_documents_reason_and_version(self):
        for s in ALL:
            self.assertTrue(s.REASON and s.VERSION and s.FEATURES)


class S1Tests(unittest.TestCase):
    def test_direction_and_bounds(self):
        up = s1.compute(snap(strike=99.0)); down = s1.compute(snap(strike=101.0)); at = s1.compute(snap(strike=proxy_series()[-1][1]))
        self.assertGreater(up['s1_p'], 0.5); self.assertLess(down['s1_p'], 0.5); self.assertAlmostEqual(at['s1_p'], 0.5, places=9)
        self.assertTrue(0.01 <= up['s1_p'] <= 0.99 and -4 <= up['s1_z'] <= 4)
    def test_more_time_means_less_certain(self):
        near = s1.compute(snap(strike=99.9, close_ts=T + 120)); far = s1.compute(snap(strike=99.9, close_ts=T + 900))
        self.assertGreater(near['s1_p'], far['s1_p'])
    def test_rejects_final_minute_short_history_and_stale(self):
        self.assertIsNone(s1.compute(snap(close_ts=T + 50)))
        self.assertIsNone(s1.compute(snap(proxy=proxy_series(minutes=10))))
        self.assertIsNone(s1.compute(snap(proxy=[r for r in proxy_series() if r[0] <= T - 30])))


class S2Tests(unittest.TestCase):
    def test_average_imbalance_and_staleness(self):
        books = dict(a=dict(recv_ts=T - 1, bids=[(1, 3)], asks=[(2, 1)]), b=dict(recv_ts=T - 1, bids=[(1, 1)], asks=[(2, 1)]),
                     c=dict(recv_ts=T - 60, bids=[(1, 100)], asks=[(2, 0.1)]))
        self.assertAlmostEqual(s2.compute(snap(books=books))['s2_imb'], 0.25)
        self.assertIsNone(s2.compute(snap(books=dict(a=books['a']))))


class S3Tests(unittest.TestCase):
    def test_sign_of_momentum(self):
        up = s3.compute(snap(proxy=proxy_series(drift=0.001)))
        self.assertTrue(all(up[k] > 0 for k in s3.FEATURES)); self.assertGreater(up['s3_r5'], up['s3_r1'])
        self.assertIsNone(s3.compute(snap(proxy=proxy_series(minutes=2))))


class S4Tests(unittest.TestCase):
    def test_fastest_exchange_deviation(self):
        flat = [(T - 30, 100.0), (T - 1, 100.0)]
        ticks = dict(a=flat, b=flat, c=[(T - 30, 100.0), (T - 1, 100.2)])
        r = s4.compute(snap(ticks=ticks, proxy=[(T - 1, 100.0)]))
        self.assertAlmostEqual(r['s4_dev'], math.log(1.002) * 1e4)
        self.assertIsNone(s4.compute(snap(ticks=dict(a=flat, b=flat), proxy=[(T - 1, 100.0)])))


class S5Tests(unittest.TestCase):
    def test_imbalance_and_drift(self):
        k = [dict(recv_ts=T - 190, yes=[(0.40, 5)], no=[(0.58, 5)]), dict(recv_ts=T - 5, yes=[(0.50, 6)], no=[(0.48, 2)])]
        r = s5.compute(snap(kalshi=k))
        self.assertAlmostEqual(r['s5_imb'], 0.5); self.assertAlmostEqual(r['s5_drift'], 0.51 - 0.41)
    def test_no_old_snapshot_or_stale(self):
        self.assertIsNone(s5.compute(snap(kalshi=[dict(recv_ts=T - 5, yes=[(0.5, 1)], no=[(0.4, 1)])])))
        self.assertIsNone(s5.compute(snap(kalshi=[dict(recv_ts=T - 190, yes=[(0.5, 1)], no=[(0.4, 1)]), dict(recv_ts=T - 60, yes=[(0.5, 1)], no=[(0.4, 1)])])))


if __name__ == '__main__':
    unittest.main()
