import math, random, unittest
from quant import logit as lg


class LogitTests(unittest.TestCase):
    def test_recovers_coefficients(self):
        rng = random.Random(1); X, y = [], []
        for _ in range(4000):
            x = rng.gauss(0, 1); X.append([x]); y.append(int(rng.random() < 1 / (1 + math.exp(-(0.5 + 1.5 * x)))))
        w = lg.fit(X, y, l2=0.0)
        self.assertAlmostEqual(w[0], 0.5, delta=0.12); self.assertAlmostEqual(w[1], 1.5, delta=0.15)
    def test_ridge_shrinks_but_not_intercept(self):
        rng = random.Random(2); X = [[rng.gauss(0, 1)] for _ in range(300)]; y = [int(x[0] > 0) for x in X]
        self.assertLess(abs(lg.fit(X, y, l2=100.0)[1]), abs(lg.fit(X, y, l2=0.1)[1]))
    def test_offset_only_model(self):
        # If the offset is already the truth, the extra coefficients stay near zero.
        rng = random.Random(3); X, y, off = [], [], []
        for _ in range(3000):
            o = rng.gauss(0, 1.5); off.append(o); X.append([rng.gauss(0, 1)]); y.append(int(rng.random() < 1 / (1 + math.exp(-o))))
        w = lg.fit(X, y, off, l2=1.0); self.assertLess(abs(w[0]), 0.12); self.assertLess(abs(w[1]), 0.12)
    def test_log_loss_and_standardize(self):
        self.assertAlmostEqual(lg.log_loss(0.5, 1), math.log(2)); self.assertLess(lg.log_loss(1.0, 0), 30)
        (m, sd), = lg.standardize([[1.0], [3.0]]); self.assertEqual(m, 2.0); self.assertAlmostEqual(sd, math.sqrt(2))
        self.assertEqual(lg.standardize([[5.0], [5.0]])[0][1], 1.0)


if __name__ == '__main__':
    unittest.main()
