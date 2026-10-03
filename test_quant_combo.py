import json, math, random, tempfile, unittest
from pathlib import Path
from quant import combo, isotonic, freeze


def rows(n=600, seed=5):
    rng = random.Random(seed); out = []
    for i in range(n):
        truth = rng.gauss(0, 1); hidden = rng.gauss(0, 1)
        y = int(rng.random() < 1 / (1 + math.exp(-(truth + 0.8 * hidden))))
        f = {k: rng.gauss(0, 1) for k in combo.CANDIDATES}; f['s2_imb'] = hidden * 0.3 + 0.01
        out.append(dict(ticker='M%d' % i, t=float(i), p_market=1 / (1 + math.exp(-truth)), y=y, features=f))
    return out


class IsotonicTests(unittest.TestCase):
    def test_monotone_and_pooled(self):
        steps = isotonic.fit([0.1, 0.2, 0.3, 0.4], [0, 1, 0, 1])
        vals = [s[1] for s in steps]; self.assertEqual(vals, sorted(vals)); self.assertEqual(vals, [0.0, 0.5, 1.0])
        self.assertEqual(isotonic.apply(steps, 0.25), 0.5); self.assertEqual(isotonic.apply(steps, 0.05), 0.01)
        self.assertEqual(isotonic.apply(steps, 0.9), 0.99)
    def test_reliability_buckets(self):
        r = isotonic.reliability([0.05, 0.15, 0.15, 1.0], [0, 1, 0, 1])
        self.assertEqual(len(r), 10); self.assertEqual(r[1]['n'], 2); self.assertEqual(r[1]['observed'], 0.5); self.assertEqual(r[9]['n'], 1)


class ComboTests(unittest.TestCase):
    def test_keeps_real_signal_drops_noise(self):
        m = combo.train(rows(), n_boot=150)
        self.assertIn('s2_imb', m['features'])
        self.assertLessEqual(len(m['features']), 3)  # most pure-noise features dropped
        lo, hi = m['ci95']['s2_imb']; self.assertGreater(lo, 0)
        self.assertGreater(m['coefficients']['b_market_logit'], 0.5)
        self.assertLess(m['training']['in_sample_log_loss']['raw'], m['training']['in_sample_log_loss']['market'])
        p = combo.predict(m, 0.5, rows(1)[0]['features']); self.assertTrue(0.01 <= p <= 0.99)
    def test_guards(self):
        with self.assertRaises(ValueError): combo.train(rows(50), n_boot=10)
        dup = rows(); dup.append(dict(dup[0]))
        with self.assertRaises(ValueError): combo.train(dup, n_boot=10)
    def test_incomplete_rows_excluded(self):
        r = rows(); r[0]['features']['s1_z'] = None
        self.assertEqual(combo.train(r, n_boot=20)['training']['markets'], 599)
    def test_freeze_never_overwrites_and_verifies(self):
        m = combo.train(rows(), n_boot=20)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'c.json'; combo.freeze_model(m, p); combo.verify_model(p)
            with self.assertRaises(freeze.FrozenMismatch): combo.freeze_model(m, p)
            doc = json.loads(p.read_text()); doc['code']['combo'] = 'x'; p.write_text(json.dumps(doc))
            with self.assertRaises(freeze.FrozenMismatch): combo.verify_model(p)


if __name__ == '__main__':
    unittest.main()
