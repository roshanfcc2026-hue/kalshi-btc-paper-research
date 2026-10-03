import math, random, unittest
from quant import discipline as dis, monitor, store as qstore
from test_quant_combo import rows as synth


class WalkForwardTests(unittest.TestCase):
    def test_every_block_reported_and_train_precedes_test(self):
        r = dis.walk_forward(synth(600), ['s2_imb'], initial=200, block=100)
        self.assertEqual(r['total_blocks'], 4); self.assertEqual([b['train_markets'] for b in r['blocks']], [200, 300, 400, 500])
        rows = sorted(synth(600), key=lambda x: x['t'])
        for b in r['blocks']: self.assertGreater(b['test_first_ts'], rows[b['train_markets'] - 1]['t'])
        self.assertGreaterEqual(r['blocks_beating_market'], 3)  # informative signal
    def test_noise_does_not_consistently_beat_market(self):
        r = dis.walk_forward(synth(600), ['s1_z'], initial=200, block=100)
        self.assertTrue(all(b['ll_diff'] > -0.02 for b in r['blocks']))


class BootstrapTests(unittest.TestCase):
    def test_groups_by_market(self):
        ci = dis.grouped_bootstrap({'a': [1, 1, 1, 1], 'b': [0]})  # per-market totals 4 and 0
        self.assertTrue(0 <= ci[0] <= ci[1] <= 4)
        self.assertIsNone(dis.grouped_bootstrap({'a': [1]}))
    def test_ci_covers_and_excludes_zero(self):
        rng = random.Random(1)
        pos = {str(i): [rng.gauss(0.1, 0.3)] for i in range(500)}; noise = {str(i): [rng.gauss(0, 0.3)] for i in range(500)}
        self.assertGreater(dis.grouped_bootstrap(pos)[0], 0)
        covered = 0
        for seed in range(40):  # ~95% of intervals should contain the true mean 0
            g = random.Random(100 + seed); lo, hi = dis.grouped_bootstrap({str(i): [g.gauss(0, 0.3)] for i in range(200)}, n_boot=500)
            covered += lo < 0 < hi
        self.assertGreaterEqual(covered, 34)


class DecayTests(unittest.TestCase):
    def test_flags_sign_flip(self):
        rng = random.Random(3); rows = []
        for i in range(2000):
            t = i * 600.0; x = rng.gauss(0, 1); sign = 1 if t < 7 * 86400 else -1
            y = int(rng.random() < 1 / (1 + math.exp(-2 * sign * x)))
            rows.append(dict(ticker=str(i), t=t, p_market=0.5, y=y, features={'s2_imb': x}))
        r = dis.decay(rows, ['s2_imb'])
        self.assertGreater(len(r['windows']), 3); self.assertEqual(r['flags'][0]['kind'], 'sign flip')
    def test_stable_signal_not_flagged(self):
        rng = random.Random(4); rows = []
        for i in range(2000):
            x = rng.gauss(0, 1); rows.append(dict(ticker=str(i), t=i * 600.0, p_market=0.5, y=int(rng.random() < 1 / (1 + math.exp(-2 * x))), features={'s2_imb': x}))
        self.assertEqual(dis.decay(rows, ['s2_imb'])['flags'], [])


class PromotionTests(unittest.TestCase):
    def seed(self, n, edge):
        d = qstore.connect(':memory:'); monitor.setup(d); rng = random.Random(5)
        for i in range(n):
            y = rng.random() < 0.5; p = (0.5 + edge) if y else (0.5 - edge)
            d.execute('INSERT INTO forecasts VALUES(?,?,?,?,?)', ('M%d' % i, 'combo-v1', 100.0 + i, p, 0.5))
            d.execute('INSERT INTO market_results VALUES(?,?,?)', ('M%d' % i, 'yes' if y else 'no', 200.0 + i))
            pnl = 0.4 if rng.random() < 0.5 + edge else -0.6
            d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES(?,'combo-v1','yes',1,.5,.01,?,'yes',?,?)", ('M%d' % i, 100.0 + i, pnl, 200.0 + i))
        return d
    def test_needs_500_forward_markets(self):
        r = dis.promotion(self.seed(300, 0.3), 'combo-v1', 0)
        self.assertEqual(r['status'], 'not a candidate'); self.assertIn('only 300', r['reasons'][0]); self.assertFalse(r['auto_promoted'])
    def test_candidate_when_all_conditions_hold(self):
        r = dis.promotion(self.seed(600, 0.3), 'combo-v1', 0)
        self.assertEqual(r['status'], 'promotion candidate'); self.assertFalse(r['auto_promoted'])
    def test_pre_freeze_markets_excluded(self):
        self.assertEqual(dis.promotion(self.seed(600, 0.3), 'combo-v1', 100.0 + 299)['forward_markets'], 300)
    def test_no_edge_not_candidate(self):
        self.assertEqual(dis.promotion(self.seed(600, 0.0), 'combo-v1', 0)['status'], 'not a candidate')


if __name__ == '__main__':
    unittest.main()
